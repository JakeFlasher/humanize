"""Bounded subprocess identity, registration, and cancellation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from .storage import StoreError, atomic_write_json, read_json_object

ATTEMPT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate one process group and reap it within a fixed bound."""

    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def register_process(
    path: Path,
    *,
    process: subprocess.Popen[bytes],
    command: Sequence[str],
    attempt_id: str,
    lane: str,
) -> None:
    identity = process_identity(process.pid)
    if not identity:
        raise OSError("the reviewer process has no stable platform identity")
    atomic_write_json(
        path,
        {
            "schema_version": "rlcr.process.v1",
            "pid": process.pid,
            "attempt_id": attempt_id,
            "lane": lane,
            "command_digest": _command_digest(command),
            "identity": identity,
        },
    )


def unregister_process(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)


def mark_attempt_canceled(run_dir: Path, attempt_id: str) -> None:
    """Publish a tombstone that closes cancellation races around process launch."""

    atomic_write_json(
        _cancellation_path(run_dir, attempt_id),
        {
            "schema_version": "rlcr.cancellation.v1",
            "attempt_id": attempt_id,
        },
    )


def attempt_is_canceled(run_dir: Path, attempt_id: str) -> bool:
    path = _cancellation_path(run_dir, attempt_id)
    return path.is_symlink() or path.is_file()


def terminate_registered(run_dir: Path, attempt_id: str) -> int:
    """Terminate only live processes whose PID identity still matches the attempt."""

    attempt = attempt_id[:16]
    stopped = 0
    signaled: list[tuple[Path, int, dict[str, Any]]] = []
    for path in sorted(run_dir.glob(f"rounds/round-*/attempt-{attempt}/*.process.json")):
        try:
            record = read_json_object(path)
        except (OSError, StoreError):
            continue
        if record.get("attempt_id") != attempt_id:
            continue
        pid = record.get("pid")
        identity = record.get("identity")
        if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(identity, dict):
            continue
        current = process_identity(pid)
        if not current or current != identity:
            continue
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            else:
                os.killpg(pid, signal.SIGTERM)
            stopped += 1
            signaled.append((path, pid, identity))
        except (OSError, subprocess.TimeoutExpired):
            continue
    if os.name != "nt" and signaled:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not any(process_identity(pid) == identity for _, pid, identity in signaled):
                break
            time.sleep(0.05)
        for path, pid, identity in signaled:
            if process_identity(pid) == identity:
                with suppress(OSError):
                    os.killpg(pid, signal.SIGKILL)
            if not process_identity(pid):
                unregister_process(path)
    return stopped


def process_identity(pid: int) -> dict[str, Any]:
    """Return a PID-reuse-resistant identity where the platform exposes one."""

    if pid < 1:
        return {}
    if os.name != "nt":
        proc = Path("/proc") / str(pid)
        try:
            stat_fields = (proc / "stat").read_text(encoding="utf-8").split()
            return {
                "start_token": stat_fields[21],
                "process_group": os.getpgid(pid),
            }
        except (OSError, IndexError, UnicodeDecodeError):
            try:
                result = subprocess.run(
                    ["ps", "-o", "lstart=", "-o", "command=", "-p", str(pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return {}
            if result.returncode != 0 or not result.stdout.strip():
                return {}
            return {"ps_digest": hashlib.sha256(result.stdout.strip()).hexdigest()}
    return _windows_process_identity(pid)


def _command_digest(command: Sequence[str]) -> str:
    encoded = json.dumps(list(command), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cancellation_path(run_dir: Path, attempt_id: str) -> Path:
    if ATTEMPT_ID_RE.fullmatch(attempt_id) is None:
        raise StoreError("invalid reviewer attempt id")
    return run_dir / "canceled-attempts" / f"{attempt_id}.json"


def _windows_process_identity(pid: int) -> dict[str, Any]:
    """Read the kernel creation timestamp for PID-reuse-safe Windows matching."""

    import ctypes

    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return {}

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    try:
        kernel32 = win_dll("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_process.restype = ctypes.c_void_p
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        get_process_times.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return {}
        creation = FileTime()
        exit_time = FileTime()
        kernel_time = FileTime()
        user_time = FileTime()
        try:
            if not get_process_times(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return {}
        finally:
            close_handle(handle)
        return {"creation_filetime": (creation.high << 32) | creation.low}
    except (AttributeError, OSError, TypeError, ValueError):
        return {}
