"""Private, atomic state storage and cross-process locking."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any


RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


class StoreError(RuntimeError):
    """Raised when controller state cannot be read or written safely."""


def state_home() -> Path:
    override = os.environ.get("JAKESHEA_HUMANIZE_RLCR_STATE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return (Path(base) / "HumanizeRLCR").resolve()
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return (Path(xdg_state).expanduser() / "jakeshea-humanize-rlcr").resolve()
    return (Path.home() / ".local" / "state" / "jakeshea-humanize-rlcr").resolve()


def active_pointer_exists(project_root: Path) -> bool:
    """Check for an existing run without creating state directories."""

    canonical = project_root.resolve()
    project_key = hashlib.sha256(os.fsencode(str(canonical))).hexdigest()
    return (state_home() / "projects" / f"{project_key}.json").is_file()


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def _secure_file(path: Path) -> None:
    if os.name != "nt" and path.exists():
        path.chmod(0o600)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    _secure_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _secure_file(path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(path, encoded)


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError(f"unable to read valid controller state at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StoreError(f"controller state is not a JSON object: {path}")
    return value


class FileLock(AbstractContextManager["FileLock"]):
    """Small portable exclusive lock with a bounded acquisition wait."""

    def __init__(self, path: Path, timeout: float = 3.0) -> None:
        self.path = path
        self.timeout = timeout
        self._handle: Any = None

    def __enter__(self) -> "FileLock":
        _secure_directory(self.path.parent)
        self._handle = self.path.open("a+b")
        _secure_file(self.path)
        if self.path.stat().st_size == 0:
            self._handle.write(b"0")
            self._handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise StoreError("another RLCR controller operation is in progress")
                time.sleep(0.05)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class StateStore:
    """State paths keyed by the canonical project root."""

    def __init__(self, project_root: Path, *, create: bool = True) -> None:
        self.project_root = project_root.resolve()
        self.root = state_home()
        self.project_key = hashlib.sha256(os.fsencode(str(self.project_root))).hexdigest()
        self.projects_dir = self.root / "projects"
        self.runs_dir = self.root / "runs" / self.project_key
        self.locks_dir = self.root / "locks"
        self.pointer_path = self.projects_dir / f"{self.project_key}.json"
        self.lock_path = self.locks_dir / f"{self.project_key}.lock"
        if create:
            for directory in (self.root, self.projects_dir, self.runs_dir, self.locks_dir):
                _secure_directory(directory)

    def lock(self, timeout: float = 3.0) -> FileLock:
        return FileLock(self.lock_path, timeout=timeout)

    def run_dir(self, run_id: str) -> Path:
        if RUN_ID_RE.fullmatch(run_id) is None:
            raise StoreError(f"invalid RLCR run id: {run_id!r}")
        return self.runs_dir / run_id

    def create_run_dir(self, run_id: str) -> Path:
        path = self.run_dir(run_id)
        if path.exists():
            raise StoreError(f"RLCR run already exists: {run_id}")
        _secure_directory(path)
        _secure_directory(path / "rounds")
        _secure_directory(path / "harness")
        return path

    def load_active(self) -> tuple[dict[str, Any], Path] | None:
        if not self.pointer_path.is_file():
            return None
        pointer = read_json_object(self.pointer_path)
        if pointer.get("project_root") != str(self.project_root):
            raise StoreError("RLCR project pointer does not match the current repository")
        run_id = pointer.get("run_id")
        if not isinstance(run_id, str):
            raise StoreError("RLCR project pointer has no valid run id")
        run_dir = self.run_dir(run_id)
        state_path = run_dir / "state.json"
        if not state_path.is_file():
            raise StoreError(f"RLCR project pointer references missing state: {state_path}")
        state = read_json_object(state_path)
        return state, run_dir

    def save_new(self, state: dict[str, Any], run_dir: Path) -> None:
        run_id = state.get("run_id")
        if run_dir != self.run_dir(str(run_id)):
            raise StoreError("refusing to save state outside its run directory")
        self.save_state(state, run_dir)
        atomic_write_json(
            self.pointer_path,
            {
                "project_root": str(self.project_root),
                "run_id": run_id,
                "updated_at": state.get("updated_at"),
            },
        )

    def save_state(self, state: dict[str, Any], run_dir: Path) -> None:
        if state.get("project_root") != str(self.project_root):
            raise StoreError("refusing to save state for a different repository")
        state_path = run_dir / "state.json"
        atomic_write_json(state_path, state)
        if self.pointer_path.is_file():
            pointer = read_json_object(self.pointer_path)
            if pointer.get("run_id") == state.get("run_id"):
                pointer["updated_at"] = state.get("updated_at")
                atomic_write_json(self.pointer_path, pointer)

    def append_event(self, run_dir: Path, event: dict[str, Any]) -> None:
        journal = run_dir / "journal.jsonl"
        encoded = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        descriptor = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _secure_file(journal)
