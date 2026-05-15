#!/usr/bin/env python3
"""Artifact verdict engine for RLCR research loops (solbench adapter, v1).

Reads a per-round structured objective sidecar at
``.humanize/rlcr/<loop>/round-<N>-objectives.json``, validates identity,
loads the manifest referenced by the sidecar, computes a verdict tuple
against the documented severity partition, appends one JSONL row to
``.humanize/rlcr/<loop>/solbench-progress.jsonl``, and exits with one of:

    0 - continue (the wrapper proceeds with the original state mutation)
    1 - hard-block (the wrapper refuses to mutate state, rename, or create
        next-round artifacts)
    2 - soft-warn (the wrapper increments the drift counter but proceeds)

The contract is documented in ``docs/solbench-verdict-engine-schema.md``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "1.0"
SUPPORTED_ADAPTERS = {"solbench"}
ALWAYS_HARD_BLOCK_RULES = {
    "rule_1_no_ncu",
    "rule_2_no_clock_lock",
    "rule_3_no_privileged_cupti",
    "rule_4_no_host_driver_work",
    "rule_5_submission_language",
    "rule_6_no_evaluator_state_exploit",
    "rule_8_precision_contract",
}
CONDITIONAL_RULES = {
    "rule_7_default_stream",
    "rule_9_iiswc_no_access",
}
ALL_RULE_IDS = ALWAYS_HARD_BLOCK_RULES | CONDITIONAL_RULES
RULE_FULL_TO_SHORT = {
    "rule_1_no_ncu": "rule_1",
    "rule_2_no_clock_lock": "rule_2",
    "rule_3_no_privileged_cupti": "rule_3",
    "rule_4_no_host_driver_work": "rule_4",
    "rule_5_submission_language": "rule_5",
    "rule_6_no_evaluator_state_exploit": "rule_6",
    "rule_7_default_stream": "rule_7",
    "rule_8_precision_contract": "rule_8",
    "rule_9_iiswc_no_access": "rule_9",
}
REQUIRED_IDENTITY_FIELDS = (
    "schema_version",
    "loop_id",
    "round",
    "adapter",
    "objective_id",
    "objective_hash",
    "manifest_path",
    "manifest_hash",
    "generated_at",
)
SIDECAR_MAX_AGE_HOURS = 24
LOGGED_ONLY_TERMINAL_REASONS = {
    "finalize_completion": "complete",
    "stop_marker": "stop",
    "maxiter": "maxiter",
    "mainline_drift": "mainline_drift",
    "review_start": "review_start",
    "complete_at_maxiter": "complete_at_maxiter",
}
GATED_TRANSITIONS = {"next_round", "review_fix", "enter_finalize"}
KNOWN_TRANSITIONS = GATED_TRANSITIONS | set(LOGGED_ONLY_TERMINAL_REASONS.keys())


class VerdictError(Exception):
    """Base for verdict-engine block-causing errors. The ``reason`` attribute
    maps to a documented ``block_reason`` enum value."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


class SidecarMalformedError(VerdictError):
    pass


class SidecarIdentityError(VerdictError):
    pass


class ManifestError(VerdictError):
    pass


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _parse_rfc3339(value: str) -> _dt.datetime:
    """Parse a permissive RFC 3339 timestamp string into an aware datetime."""
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def detect_adapter(loop_dir: Path, project_root: Path) -> Tuple[bool, str]:
    """Return ``(adapter_active, adapter_name)``.

    The adapter is considered "active" if ANY of the three predicates is
    positive. The first matching positive predicate determines the adapter
    name; the engine fail-closes on absent/malformed sidecars whenever the
    adapter is active.

    Predicates:
      1. ``.humanize/adapter-config.json`` declares a supported adapter.
      2. ``.claude/knowledge/problems/`` exists and contains at least one
         ``.md`` file.
      3. A ``round-<N>-objectives.json`` file exists in the loop dir.
    """
    adapter_name = "solbench"

    config_path = project_root / ".humanize" / "adapter-config.json"
    if config_path.exists():
        try:
            cfg = json.loads(_safe_read_text(config_path))
        except json.JSONDecodeError:
            return True, adapter_name
        declared = cfg.get("adapter")
        if isinstance(declared, str) and declared in SUPPORTED_ADAPTERS:
            return True, declared

    problems_dir = project_root / ".claude" / "knowledge" / "problems"
    if problems_dir.is_dir():
        if any(p.suffix == ".md" for p in problems_dir.iterdir()):
            return True, adapter_name

    if loop_dir.is_dir():
        for entry in loop_dir.iterdir():
            name = entry.name
            if name.startswith("round-") and name.endswith("-objectives.json"):
                return True, adapter_name

    return False, adapter_name


def sidecar_path(loop_dir: Path, round_number: int) -> Path:
    return loop_dir / f"round-{round_number}-objectives.json"


def read_sidecar(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SidecarMalformedError(
            "adapter_active_sidecar_absent",
            f"Sidecar not found at {path}",
        )
    try:
        raw = _safe_read_text(path)
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SidecarMalformedError(
            "sidecar_malformed",
            f"Sidecar JSON parse error: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise SidecarMalformedError(
            "sidecar_malformed",
            "Sidecar top-level value is not a JSON object",
        )
    return parsed


def validate_sidecar_identity(
    sidecar: Dict[str, Any],
    *,
    loop_dir: Path,
    expected_round: int,
    project_root: Path,
) -> None:
    """Verify the 9 identity fields, schema_version, age, and manifest hash."""
    for field in REQUIRED_IDENTITY_FIELDS:
        if field not in sidecar:
            raise SidecarIdentityError(
                f"sidecar_identity_invalid:missing_{field}",
                f"Sidecar missing required identity field: {field}",
            )

    schema_version = sidecar.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SidecarIdentityError(
            "sidecar_identity_invalid:bad_schema_version",
            f"Unexpected schema_version: {schema_version!r}",
        )

    if sidecar.get("loop_id") != loop_dir.name:
        raise SidecarIdentityError(
            "sidecar_identity_mismatch:loop_id",
            f"Sidecar loop_id {sidecar.get('loop_id')!r} != "
            f"loop dir basename {loop_dir.name!r}",
        )

    if sidecar.get("round") != expected_round:
        raise SidecarIdentityError(
            "sidecar_identity_mismatch:round",
            f"Sidecar round {sidecar.get('round')!r} != "
            f"current_round {expected_round}",
        )

    adapter = sidecar.get("adapter")
    if not isinstance(adapter, str) or adapter not in SUPPORTED_ADAPTERS:
        raise SidecarIdentityError(
            "sidecar_identity_invalid:bad_adapter",
            f"Unsupported adapter: {adapter!r}",
        )

    generated_at_raw = sidecar.get("generated_at")
    try:
        generated_at = _parse_rfc3339(str(generated_at_raw))
    except (ValueError, TypeError) as exc:
        raise SidecarIdentityError(
            "sidecar_identity_invalid:bad_generated_at",
            f"Sidecar generated_at not parseable: {exc}",
        ) from exc

    age = _now_utc() - generated_at
    if age > _dt.timedelta(hours=SIDECAR_MAX_AGE_HOURS):
        raise SidecarIdentityError(
            "sidecar_identity_stale",
            f"Sidecar generated_at is {age} old (> {SIDECAR_MAX_AGE_HOURS}h)",
        )

    manifest_path = sidecar.get("manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise SidecarIdentityError(
            "sidecar_identity_invalid:bad_manifest_path",
            "manifest_path must be a non-empty string",
        )

    manifest_full = Path(manifest_path)
    if not manifest_full.is_absolute():
        manifest_full = (project_root / manifest_path).resolve()
    if not manifest_full.exists():
        raise SidecarIdentityError(
            "sidecar_identity_invalid:manifest_not_found",
            f"Manifest not found at resolved path {manifest_full}",
        )

    actual_hash = _file_sha256(manifest_full)
    declared_hash = sidecar.get("manifest_hash")
    if actual_hash != declared_hash:
        raise SidecarIdentityError(
            "sidecar_identity_mismatch:manifest_hash",
            f"manifest_hash mismatch: declared {declared_hash!r} "
            f"vs computed {actual_hash!r}",
        )


def read_manifest(manifest_path_str: str, project_root: Path) -> Dict[str, Any]:
    manifest_full = Path(manifest_path_str)
    if not manifest_full.is_absolute():
        manifest_full = (project_root / manifest_path_str).resolve()
    if not manifest_full.exists():
        raise ManifestError(
            "manifest_not_found",
            f"Manifest not found at {manifest_full}",
        )
    try:
        return json.loads(_safe_read_text(manifest_full))
    except json.JSONDecodeError as exc:
        raise ManifestError(
            "manifest_malformed",
            f"Manifest JSON parse error: {exc}",
        ) from exc


def extract_per_surface_manifest(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the surface entries verbatim. Never collapse to ``failed_count``."""
    raw_surfaces = manifest.get("surfaces", [])
    if not isinstance(raw_surfaces, list):
        return []
    preserved: List[Dict[str, Any]] = []
    for entry in raw_surfaces:
        if not isinstance(entry, dict):
            continue
        preserved.append(
            {
                "name": entry.get("name"),
                "status": entry.get("status"),
                "waiver_reason": entry.get("waiver_reason"),
                "substatus": entry.get("substatus"),
                "evidence_label_contribution": entry.get(
                    "evidence_label_contribution"
                ),
            }
        )
    return preserved


def _surface_status_map(surfaces: List[Dict[str, Any]]) -> Dict[str, str]:
    return {
        s.get("name"): s.get("status")
        for s in surfaces
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    }


def compute_verdict_tuple(
    sidecar: Dict[str, Any],
    surfaces: List[Dict[str, Any]],
) -> Tuple[int, Optional[str], str, bool]:
    """Return ``(exit_code, block_reason, computed_verdict, warned)``.

    Exit codes follow the documented contract: 0 continue, 1 hard-block,
    2 soft-warn. The ``computed_verdict`` is a short label (``advanced``,
    ``stalled``, ``regressed``, ``blocked``).
    """
    correctness = sidecar.get("correctness")
    if not isinstance(correctness, dict):
        return 1, "correctness_block_missing", "blocked", False
    if correctness.get("passed") is not True:
        return 1, "correctness_failed", "blocked", False

    required_surfaces = sidecar.get("required_surfaces", [])
    if not isinstance(required_surfaces, list):
        required_surfaces = []
    status_map = _surface_status_map(surfaces)
    for surface_name in required_surfaces:
        if status_map.get(surface_name) == "failed":
            return (
                1,
                f"required_surface_failed:{surface_name}",
                "blocked",
                False,
            )

    latency = sidecar.get("latency", {}) or {}
    latency_required = bool(latency.get("required"))
    delta_pct = latency.get("delta_pct")
    threshold_pct = latency.get("threshold_pct")

    if latency_required and isinstance(delta_pct, (int, float)) and isinstance(
        threshold_pct, (int, float)
    ):
        if delta_pct < threshold_pct:
            return (
                1,
                "required_latency_threshold_breach",
                "blocked",
                False,
            )

    sol_score = sidecar.get("sol_score", {}) or {}
    sol_value = sol_score.get("value")
    leaderboard_required = bool(sidecar.get("leaderboard_comparable_required"))
    if leaderboard_required and sol_value == "unknown_t_sol":
        return (
            1,
            "leaderboard_comparable_required_but_unknown",
            "blocked",
            False,
        )

    rule_compliance = sidecar.get("rule_compliance", {}) or {}
    rule_required = sidecar.get("rule_required_by_objective", {}) or {}
    block = _check_rule_compliance(rule_compliance, rule_required)
    if block is not None:
        return 1, block, "blocked", False

    warned = False
    if not latency_required and isinstance(delta_pct, (int, float)):
        if isinstance(threshold_pct, (int, float)) and delta_pct < threshold_pct:
            warned = True
        elif not isinstance(threshold_pct, (int, float)) and delta_pct < -10:
            warned = True

    if warned:
        return 2, None, "stalled", True
    return 0, None, "advanced", False


def _is_rule_required(
    rule_required: Dict[str, Any],
    rule_id: str,
) -> bool:
    """A rule is required if the sidecar marks it true under either the full
    id (``rule_7_default_stream``) or the short alias (``rule_7``)."""
    if rule_required.get(rule_id) is True:
        return True
    short = RULE_FULL_TO_SHORT.get(rule_id)
    if short and rule_required.get(short) is True:
        return True
    return False


def _check_rule_compliance(
    rule_compliance: Dict[str, Any],
    rule_required: Dict[str, Any],
) -> Optional[str]:
    """Return a ``block_reason`` string when a rule violation hard-blocks,
    otherwise ``None``."""
    for rule_id in ALWAYS_HARD_BLOCK_RULES:
        entry = rule_compliance.get(rule_id, {})
        status = entry.get("status") if isinstance(entry, dict) else None
        if status == "violated":
            return f"rule_violation:{rule_id}"
        if status == "not_evaluated" and _is_rule_required(rule_required, rule_id):
            return f"rule_not_evaluated:{rule_id}"

    for rule_id in CONDITIONAL_RULES:
        entry = rule_compliance.get(rule_id, {})
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status == "violated":
            if _is_rule_required(rule_required, rule_id):
                return f"rule_violation:{rule_id}"
            evidence = entry.get("evidence")
            if rule_id == "rule_9_iiswc_no_access" and isinstance(evidence, str) and evidence.strip():
                return f"rule_violation:{rule_id}"
        if status == "not_evaluated" and _is_rule_required(rule_required, rule_id):
            return f"rule_not_evaluated:{rule_id}"

    return None


def write_jsonl_row(progress_path: Path, row: Dict[str, Any]) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(row, sort_keys=True, ensure_ascii=False)
    with progress_path.open("a", encoding="utf-8") as fh:
        fh.write(encoded + "\n")


def _build_jsonl_row(
    *,
    transition: str,
    mode: str,
    sidecar: Optional[Dict[str, Any]],
    surfaces: List[Dict[str, Any]],
    engine_exit_code: int,
    block_reason: Optional[str],
    computed_verdict: str,
    warned: bool,
    codex_verdict: Optional[str],
    loop_id: str,
    round_number: int,
    terminal_reason: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sidecar = sidecar or {}
    row: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "loop_id": loop_id,
        "round": round_number,
        "transition": transition,
        "mode": mode,
        "adapter": sidecar.get("adapter"),
        "engine_exit_code": engine_exit_code,
        "verdict_blocked": engine_exit_code == 1,
        "verdict_warned": warned,
        "block_reason": block_reason,
        "codex_verdict": codex_verdict,
        "computed_verdict": computed_verdict,
        "verdict_mismatch": _verdict_mismatch(codex_verdict, computed_verdict),
        "correctness": sidecar.get("correctness"),
        "latency": sidecar.get("latency"),
        "sol_score": sidecar.get("sol_score"),
        "leaderboard_comparable_required": sidecar.get(
            "leaderboard_comparable_required"
        ),
        "required_surfaces": sidecar.get("required_surfaces"),
        "surfaces": surfaces,
        "rule_compliance": sidecar.get("rule_compliance"),
        "rule_required_by_objective": sidecar.get("rule_required_by_objective"),
        "ac_deltas": sidecar.get("ac_deltas"),
        "objective_id": sidecar.get("objective_id"),
        "objective_hash": sidecar.get("objective_hash"),
        "manifest_hash": sidecar.get("manifest_hash"),
        "terminal_reason": terminal_reason,
    }
    if extra:
        row.update(extra)
    return row


def _verdict_mismatch(
    codex_verdict: Optional[str], computed_verdict: Optional[str]
) -> bool:
    if not codex_verdict or not computed_verdict:
        return False
    cv = codex_verdict.strip().lower()
    pv = computed_verdict.strip().lower()
    if cv == "advanced" and pv == "blocked":
        return True
    if cv == "advanced" and pv == "stalled":
        return True
    if cv == "stalled" and pv == "advanced":
        return True
    return False


def progress_path(loop_dir: Path) -> Path:
    return loop_dir / "solbench-progress.jsonl"


def resolve_project_root(loop_dir: Path, override: Optional[str]) -> Path:
    if override:
        return Path(override).resolve()
    return loop_dir.parent.parent.parent.resolve()


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="solbench_verdict_engine",
        description="Artifact verdict engine for RLCR research loops.",
    )
    parser.add_argument("--loop-dir", required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument(
        "--transition",
        default="next_round",
        choices=sorted(KNOWN_TRANSITIONS),
    )
    parser.add_argument(
        "--mode",
        default="gated",
        choices=("gated", "logged_only"),
    )
    parser.add_argument("--codex-verdict", default=None)
    parser.add_argument(
        "--project-root",
        default=None,
        help="Override project root; defaults to three levels above the loop dir.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    loop_dir = Path(args.loop_dir).resolve()
    project_root = resolve_project_root(loop_dir, args.project_root)
    loop_id = loop_dir.name
    round_number = args.round
    transition = args.transition
    mode = args.mode
    codex_verdict = args.codex_verdict
    terminal_reason = LOGGED_ONLY_TERMINAL_REASONS.get(transition)

    adapter_active, _ = detect_adapter(loop_dir, project_root)

    if not adapter_active:
        row = _build_jsonl_row(
            transition=transition,
            mode="skipped_no_adapter",
            sidecar=None,
            surfaces=[],
            engine_exit_code=0,
            block_reason=None,
            computed_verdict="unknown",
            warned=False,
            codex_verdict=codex_verdict,
            loop_id=loop_id,
            round_number=round_number,
            terminal_reason=terminal_reason,
        )
        write_jsonl_row(progress_path(loop_dir), row)
        return 0

    sidecar_file = sidecar_path(loop_dir, round_number)
    try:
        if not sidecar_file.exists():
            raise SidecarMalformedError(
                "adapter_active_sidecar_absent",
                f"Sidecar absent at {sidecar_file}",
            )
        sidecar = read_sidecar(sidecar_file)
        validate_sidecar_identity(
            sidecar,
            loop_dir=loop_dir,
            expected_round=round_number,
            project_root=project_root,
        )
        manifest = read_manifest(sidecar["manifest_path"], project_root)
        surfaces = extract_per_surface_manifest(manifest)
    except VerdictError as exc:
        row = _build_jsonl_row(
            transition=transition,
            mode="blocked" if mode == "gated" else "logged_only",
            sidecar=locals().get("sidecar"),
            surfaces=locals().get("surfaces", []),
            engine_exit_code=0 if mode == "logged_only" else 1,
            block_reason=exc.reason,
            computed_verdict="blocked",
            warned=False,
            codex_verdict=codex_verdict,
            loop_id=loop_id,
            round_number=round_number,
            terminal_reason=terminal_reason,
        )
        write_jsonl_row(progress_path(loop_dir), row)
        if mode == "logged_only":
            return 0
        sys.stderr.write(f"verdict engine: {exc.reason}: {exc}\n")
        return 1

    exit_code, block_reason, computed_verdict, warned = compute_verdict_tuple(
        sidecar, surfaces
    )

    effective_mode = mode
    effective_exit_code = exit_code

    if mode == "logged_only":
        effective_exit_code = 0

    row = _build_jsonl_row(
        transition=transition,
        mode=effective_mode,
        sidecar=sidecar,
        surfaces=surfaces,
        engine_exit_code=effective_exit_code,
        block_reason=block_reason if exit_code == 1 else None,
        computed_verdict=computed_verdict,
        warned=warned,
        codex_verdict=codex_verdict,
        loop_id=loop_id,
        round_number=round_number,
        terminal_reason=terminal_reason,
    )
    write_jsonl_row(progress_path(loop_dir), row)

    if mode == "logged_only":
        return 0
    if exit_code == 1 and block_reason:
        sys.stderr.write(f"verdict engine hard-block: {block_reason}\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
