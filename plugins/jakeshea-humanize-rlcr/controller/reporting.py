"""Read-only run history, reports, and trace exports."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import EVENT_FILE_RE, StoreError, read_json_object

REPORT_SCHEMA_VERSION = "rlcr.report.v1"


def state_summary(state: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Return a stable, non-secret history row."""

    return {
        "run_id": state.get("run_id"),
        "phase": state.get("phase"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "plan": state.get("plan_original"),
        "last_head": state.get("last_head"),
        "rounds_completed": state.get("rounds_completed"),
        "max_rounds": state.get("max_rounds"),
        "reviewer_calls": state.get("reviewer_calls"),
        "max_reviewer_calls": state.get("max_reviewer_calls"),
        "token_usage": state.get("token_usage"),
        "terminal_reason": state.get("terminal_reason"),
        "resumable": state.get("resumable", False),
        "run_dir": str(run_dir),
    }


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    """Read the validated event-file shape while omitting recovery snapshots."""

    events_dir = run_dir / "events"
    if events_dir.is_symlink():
        raise StoreError("event directory was replaced by a symbolic link")
    if not events_dir.is_dir():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(events_dir.iterdir()):
        if not path.is_file() or EVENT_FILE_RE.fullmatch(path.name) is None:
            continue
        record = read_json_object(path)
        events.append(
            {
                key: value
                for key, value in record.items()
                if key not in {"_state_after", "session_id"}
            }
        )
    return events


def build_report(state: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Build a portable report from trusted state and its append-only events."""

    evidence = state.get("evidence")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "summary": state_summary(state, run_dir),
        "configuration": state.get("run_config", {}),
        "config_digest": state.get("config_digest"),
        "plan_digest": state.get("plan_digest"),
        "contract_digest": state.get("contract_digest"),
        "required_criteria": state.get("required_criteria", []),
        "required_checks": state.get("required_checks", []),
        "evidence": evidence if isinstance(evidence, dict) else {},
        "events": read_events(run_dir),
    }


def markdown_report(report: dict[str, Any]) -> str:
    """Render a compact human-readable report."""

    summary = report["summary"]
    usage = summary.get("token_usage") or {}
    lines = [
        f"# Humanize RLCR report: {summary.get('run_id')}",
        "",
        f"- Phase: {summary.get('phase')}",
        f"- Plan: {summary.get('plan')}",
        f"- Commit: {summary.get('last_head')}",
        f"- Rounds: {summary.get('rounds_completed')}/{summary.get('max_rounds')}",
        f"- Reviewer calls: {summary.get('reviewer_calls')}/{summary.get('max_reviewer_calls')}",
        "- Tokens: "
        f"input {usage.get('input_tokens', 0)}, "
        f"output {usage.get('output_tokens', 0)}, "
        f"reasoning {usage.get('reasoning_output_tokens', 0)}",
        f"- Result: {summary.get('terminal_reason') or 'in progress'}",
        f"- Resumable: {'yes' if summary.get('resumable') else 'no'}",
        "",
        "## Evidence",
        "",
    ]
    evidence = report.get("evidence") or {}
    if evidence:
        for name, record in sorted(evidence.items()):
            status = "passed" if isinstance(record, dict) and record.get("passed") else "failed"
            lines.append(f"- {name}: {status}")
    else:
        lines.append("- No evidence recorded.")
    lines.extend(("", "## Event timeline", ""))
    for event in report.get("events", []):
        lines.append(
            f"- {event.get('at', '?')} — {event.get('event', 'unknown')} "
            f"(phase: {event.get('phase', '?')})"
        )
    return "\n".join(lines) + "\n"


def chrome_trace(events: Iterable[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """Export state transitions as Chrome/Perfetto instant trace events."""

    trace_events: list[dict[str, Any]] = []
    for event in events:
        timestamp = _timestamp_micros(event.get("at"))
        if timestamp is None:
            continue
        args = {
            key: value
            for key, value in event.items()
            if key
            not in {
                "at",
                "event",
                "event_digest",
                "previous_event_digest",
                "state_digest",
            }
        }
        trace_events.append(
            {
                "name": event.get("event", "rlcr_event"),
                "cat": "humanize.rlcr",
                "ph": "i",
                "s": "g",
                "ts": timestamp,
                "pid": run_id,
                "tid": "controller",
                "args": args,
            }
        )
    return {"traceEvents": trace_events, "displayTimeUnit": "ms"}


def encode_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _timestamp_micros(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp() * 1_000_000)
