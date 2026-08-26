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

from .domain import canonical_json, sha256_digest
from .migrations import migrate_state

RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
EVENT_FILE_RE = re.compile(r"^[0-9]{8}\.json$")


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
    pointer = state_home() / "projects" / f"{project_key}.json"
    return pointer.is_symlink() or pointer.is_file()


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise StoreError(f"controller directory was replaced by a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise StoreError(f"controller directory is not a directory: {path}")
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
    if path.is_symlink() or not path.is_file():
        raise StoreError(f"controller JSON file is missing or replaced: {path}")
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

    def __enter__(self) -> FileLock:
        _secure_directory(self.path.parent)
        if self.path.is_symlink():
            raise StoreError(f"controller lock was replaced by a symbolic link: {self.path}")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise StoreError(f"unable to open controller lock safely: {self.path}") from exc
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        self._handle = os.fdopen(descriptor, "r+b")
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
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise StoreError("another RLCR controller operation is in progress") from exc
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
        _secure_directory(path / "events")
        _secure_directory(path / "evidence")
        return path

    def load_active(self) -> tuple[dict[str, Any], Path] | None:
        if self.pointer_path.is_symlink():
            raise StoreError("RLCR project pointer was replaced by a symbolic link")
        if not self.pointer_path.is_file():
            return None
        pointer = read_json_object(self.pointer_path)
        if pointer.get("project_root") != str(self.project_root):
            raise StoreError("RLCR project pointer does not match the current repository")
        run_id = pointer.get("run_id")
        if not isinstance(run_id, str):
            raise StoreError("RLCR project pointer has no valid run id")
        return self.load_run(run_id)

    def load_run(self, run_id: str) -> tuple[dict[str, Any], Path]:
        """Load one historical run without changing the active pointer."""

        run_dir = self.run_dir(run_id)
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise StoreError(f"RLCR run directory is missing or replaced: {run_dir}")
        state_path = run_dir / "state.json"
        if not state_path.is_file() or state_path.is_symlink():
            raise StoreError(f"RLCR run has no valid state snapshot: {state_path}")
        state = migrate_state(read_json_object(state_path))
        if state.get("schema_version") == "rlcr.run.v2":
            state = self._recover_state(state, run_dir)
        if state.get("project_root") != str(self.project_root):
            raise StoreError("RLCR run state belongs to a different repository")
        if state.get("run_id") != run_id:
            raise StoreError("RLCR run state does not match its directory")
        return state, run_dir

    def list_runs(self) -> list[tuple[dict[str, Any], Path]]:
        """Return every readable run newest first."""

        if self.runs_dir.is_symlink():
            raise StoreError("RLCR runs directory was replaced by a symbolic link")
        if not self.runs_dir.is_dir():
            return []
        runs: list[tuple[dict[str, Any], Path]] = []
        for path in sorted(self.runs_dir.iterdir(), reverse=True):
            if RUN_ID_RE.fullmatch(path.name) is None:
                continue
            if path.is_symlink() or not path.is_dir():
                raise StoreError(f"RLCR run directory is missing or replaced: {path}")
            runs.append(self.load_run(path.name))
        return runs

    def _event_files(self, run_dir: Path) -> list[Path]:
        events_dir = run_dir / "events"
        if events_dir.is_symlink():
            raise StoreError(f"event directory was replaced by a symbolic link: {events_dir}")
        if not events_dir.is_dir():
            return []
        files: list[Path] = []
        for path in events_dir.iterdir():
            if EVENT_FILE_RE.fullmatch(path.name) is None:
                continue
            if path.is_symlink() or not path.is_file():
                raise StoreError(f"event file is missing or replaced: {path}")
            files.append(path)
        return sorted(files)

    def _recover_state(self, state: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        """Finish a state commit whose authoritative event landed before its snapshot."""

        previous = ""
        previous_sequence: int | None = None
        last_record: dict[str, Any] | None = None
        event_files = self._event_files(run_dir)
        if not event_files and (
            state.get("last_event_digest") or state.get("migrated_from") != "rlcr.run.v1"
        ):
            raise StoreError("authoritative event journal is missing")
        for path in event_files:
            record = read_json_object(path)
            file_sequence = int(path.stem)
            if record.get("sequence") != file_sequence:
                raise StoreError(f"event sequence does not match its filename: {path}")
            if previous_sequence is not None and file_sequence != previous_sequence + 1:
                raise StoreError(f"event sequence has a gap before: {path}")
            event_digest = record.get("event_digest")
            if not isinstance(event_digest, str):
                raise StoreError(f"event has no digest: {path}")
            if record.get("previous_event_digest") != previous:
                raise StoreError(f"event digest chain is broken: {path}")
            core = {
                key: value
                for key, value in record.items()
                if key not in {"event_digest", "state_digest"}
            }
            if sha256_digest(canonical_json(core)) != event_digest:
                raise StoreError(f"event digest does not match its contents: {path}")
            state_after = record.get("_state_after")
            if not isinstance(state_after, dict):
                raise StoreError(f"event has no recoverable state: {path}")
            previous = event_digest
            previous_sequence = file_sequence
            last_record = record

        if last_record is None:
            return state
        event_state = dict(last_record["_state_after"])
        event_state["last_event_digest"] = last_record["event_digest"]
        expected_state_digest = last_record.get("state_digest")
        if (
            not isinstance(expected_state_digest, str)
            or sha256_digest(canonical_json(event_state)) != expected_state_digest
        ):
            raise StoreError("latest event does not attest its recoverable state")
        state_sequence = state.get("sequence")
        event_sequence = event_state.get("sequence")
        if not isinstance(state_sequence, int) or not isinstance(event_sequence, int):
            raise StoreError("state or event sequence is invalid")
        if state_sequence > event_sequence:
            raise StoreError("state snapshot is ahead of the authoritative event journal")
        if state_sequence < event_sequence:
            self.save_state(event_state, run_dir)
            return event_state
        if canonical_json(state) != canonical_json(event_state):
            self.save_state(event_state, run_dir)
            return event_state
        return state

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

    def commit_new(self, state: dict[str, Any], run_dir: Path, event: dict[str, Any]) -> None:
        """Commit the first event/state pair and then publish the project pointer."""

        self.commit(state, run_dir, event)
        atomic_write_json(
            self.pointer_path,
            {
                "project_root": str(self.project_root),
                "run_id": state.get("run_id"),
                "updated_at": state.get("updated_at"),
            },
        )

    def commit(self, state: dict[str, Any], run_dir: Path, event: dict[str, Any]) -> None:
        """Persist a recoverable, hash-chained event before its state snapshot."""

        sequence = state.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise StoreError("refusing to commit an invalid state sequence")
        if event.get("sequence") != sequence:
            raise StoreError("event and state sequences do not match")
        previous = state.get("last_event_digest", "")
        if not isinstance(previous, str):
            raise StoreError("state has an invalid previous-event digest")
        recoverable = dict(state)
        recoverable["last_event_digest"] = previous
        core = {
            **event,
            "previous_event_digest": previous,
            "_state_after": recoverable,
        }
        event_digest = sha256_digest(canonical_json(core))
        final_state = dict(state)
        final_state["last_event_digest"] = event_digest
        record = {
            **core,
            "event_digest": event_digest,
            "state_digest": sha256_digest(canonical_json(final_state)),
        }
        event_path = run_dir / "events" / f"{sequence:08d}.json"
        if event_path.exists() or event_path.is_symlink():
            raise StoreError(f"event sequence already exists: {sequence}")
        atomic_write_json(event_path, record)
        summary = {key: value for key, value in record.items() if key != "_state_after"}
        self.append_event(run_dir, summary)
        self.save_state(final_state, run_dir)
        state.clear()
        state.update(final_state)

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
        if journal.is_symlink():
            raise StoreError("event summary journal was replaced by a symbolic link")
        encoded = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(journal, flags, 0o600)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
