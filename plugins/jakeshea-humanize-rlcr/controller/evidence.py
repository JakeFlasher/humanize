"""Explicit deterministic check execution and evidence verification."""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CHECK_NAME_RE
from .domain import canonical_json, sha256_digest
from .processes import terminate_process
from .review import current_head, require_clean_worktree
from .storage import atomic_write_json, read_json_object

MAX_EVIDENCE_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_EVIDENCE_TIMEOUT = 1800
MAX_EVIDENCE_TIMEOUT = 7200


class EvidenceError(RuntimeError):
    """Evidence could not be produced or verified safely."""


@dataclass(frozen=True)
class EvidenceResult:
    record: dict[str, Any]
    relative_path: str
    digest: str


def run_check(
    *,
    name: str,
    argv: Sequence[str],
    project_root: Path,
    run_dir: Path,
    timeout_seconds: int,
) -> EvidenceResult:
    """Run an explicitly requested argv without a shell and attest its boundary."""

    if CHECK_NAME_RE.fullmatch(name) is None:
        raise EvidenceError(f"invalid evidence name: {name!r}")
    if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
        raise EvidenceError("evidence command must be a non-empty NUL-free argv")
    if not 1 <= timeout_seconds <= MAX_EVIDENCE_TIMEOUT:
        raise EvidenceError(
            f"evidence timeout must be between 1 and {MAX_EVIDENCE_TIMEOUT} seconds"
        )
    evidence_dir = run_dir / "evidence"
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise EvidenceError("private evidence directory is missing or replaced")
    require_clean_worktree(project_root)
    before = current_head(project_root)
    started_wall = time.time()
    started = time.monotonic()
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise EvidenceError(f"unable to launch evidence command: {exc}") from exc
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process(process)
        stdout, stderr = process.communicate()
    duration = time.monotonic() - started
    after = current_head(project_root)
    if after != before:
        raise EvidenceError("evidence command changed HEAD; its result was not recorded")
    require_clean_worktree(project_root)
    full_stdout_digest = sha256_digest(stdout)
    full_stderr_digest = sha256_digest(stderr)
    stdout_clipped = stdout[-MAX_EVIDENCE_OUTPUT_BYTES:]
    stderr_clipped = stderr[-MAX_EVIDENCE_OUTPUT_BYTES:]
    record: dict[str, Any] = {
        "schema_version": "rlcr.evidence.v1",
        "name": name,
        "argv": list(argv),
        "head_sha": before,
        "started_at_epoch": started_wall,
        "duration_seconds": duration,
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "return_code": process.returncode,
        "passed": process.returncode == 0 and not timed_out,
        "stdout": stdout_clipped.decode("utf-8", "replace"),
        "stderr": stderr_clipped.decode("utf-8", "replace"),
        "stdout_digest": full_stdout_digest,
        "stderr_digest": full_stderr_digest,
        "stdout_truncated": len(stdout) > len(stdout_clipped),
        "stderr_truncated": len(stderr) > len(stderr_clipped),
    }
    filename = f"{name}-{int(started_wall * 1000)}-{secrets.token_hex(3)}.json"
    path = evidence_dir / filename
    atomic_write_json(path, record)
    relative = path.relative_to(run_dir).as_posix()
    return EvidenceResult(
        record=record,
        relative_path=relative,
        digest=sha256_digest(canonical_json(record)),
    )


def evidence_manifest(
    *, state: Mapping[str, Any], run_dir: Path, head_sha: str
) -> tuple[bytes, list[str]]:
    """Return verified same-commit evidence and required-check failures."""

    raw_evidence = state.get("evidence")
    if not isinstance(raw_evidence, dict):
        raise EvidenceError("evidence state is malformed")
    required = state.get("required_checks")
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise EvidenceError("required evidence state is malformed")
    selected: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for name, descriptor in raw_evidence.items():
        if not isinstance(name, str) or not isinstance(descriptor, dict):
            raise EvidenceError("evidence descriptor is malformed")
        path_value = descriptor.get("path")
        digest = descriptor.get("digest")
        if not isinstance(path_value, str) or not isinstance(digest, str):
            raise EvidenceError(f"evidence descriptor is malformed: {name}")
        lexical = run_dir / path_value
        if lexical.is_symlink():
            raise EvidenceError(f"evidence file was replaced by a symbolic link: {name}")
        cursor = lexical.parent
        while cursor != run_dir and cursor != cursor.parent:
            if cursor.is_symlink():
                raise EvidenceError(f"evidence path traverses a symbolic link: {name}")
            cursor = cursor.parent
        path = lexical.resolve()
        try:
            path.relative_to(run_dir)
        except ValueError as exc:
            raise EvidenceError(f"evidence path escapes its run: {name}") from exc
        if path.is_symlink() or not path.is_file():
            raise EvidenceError(f"evidence file is missing or replaced: {name}")
        record = read_json_object(path)
        if sha256_digest(canonical_json(record)) != digest:
            raise EvidenceError(f"evidence digest mismatch: {name}")
        if record.get("name") != name:
            raise EvidenceError(f"evidence identity mismatch: {name}")
        if descriptor.get("head_sha") != record.get("head_sha") or descriptor.get(
            "passed"
        ) != record.get("passed"):
            raise EvidenceError(f"evidence descriptor disagrees with its record: {name}")
        if record.get("head_sha") == head_sha:
            selected[name] = record
    for name in required:
        record = selected.get(name)
        if record is None:
            errors.append(f"required check {name!r} has no evidence for {head_sha}")
        elif record.get("passed") is not True:
            errors.append(f"required check {name!r} did not pass for {head_sha}")
    manifest = {
        "schema_version": "rlcr.evidence-manifest.v1",
        "head_sha": head_sha,
        "checks": [selected[name] for name in sorted(selected)],
    }
    return canonical_json(manifest) + b"\n", errors
