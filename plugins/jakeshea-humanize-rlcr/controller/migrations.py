"""One-way readers for persisted controller schemas."""

from __future__ import annotations

import math
from typing import Any

from .config import (
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_REASONING_TOKENS,
    LEGACY_RUN_SCHEMA_VERSION,
    REVIEWER_EFFORT,
    REVIEWER_LANES,
    REVIEWER_MODEL,
    RUN_SCHEMA_VERSION,
    default_lane_specs,
)
from .domain import RunConfig, TokenUsage

LEGACY_SNAPSHOT_FILES = [
    "plan.md",
    "review-v1.schema.json",
    "harness/AGENTS.md",
    "harness/specification.md",
    "harness/correctness.md",
]


def migrate_state(value: dict[str, Any]) -> dict[str, Any]:
    """Return a v2-compatible in-memory view without mutating a v1 state file."""

    if value.get("schema_version") != LEGACY_RUN_SCHEMA_VERSION:
        return value
    state = dict(value)
    max_rounds = _integer(state.get("max_rounds"), 6)
    max_calls = _integer(state.get("max_reviewer_calls"), max_rounds * 2 + 6)
    max_failures = _integer(state.get("max_infrastructure_failures"), 3)
    timeout = _integer(state.get("review_timeout_seconds"), 900)
    created_at = state.get("created_at")
    deadline_at = state.get("deadline_at")
    max_minutes = 120
    if isinstance(created_at, str) and isinstance(deadline_at, str):
        try:
            from datetime import datetime

            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
            max_minutes = max(1, math.ceil((deadline - created).total_seconds() / 60))
        except ValueError:
            pass
    config = RunConfig(
        lanes=default_lane_specs(),
        max_rounds=max_rounds,
        review_timeout_seconds=timeout,
        max_minutes=max_minutes,
        max_reviewer_calls=max_calls,
        max_infrastructure_failures=max_failures,
        max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        max_reasoning_tokens=DEFAULT_MAX_REASONING_TOKENS,
    )
    state.update(
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "migrated_from": LEGACY_RUN_SCHEMA_VERSION,
            "run_config": config.to_dict(),
            "config_digest": config.digest,
            "reviewer_model": state.get("reviewer_model", REVIEWER_MODEL),
            "reviewer_effort": state.get("reviewer_effort", REVIEWER_EFFORT),
            "reviewer_lanes": state.get("reviewer_lanes", list(REVIEWER_LANES)),
            "token_usage": TokenUsage().to_dict(),
            "max_input_tokens": DEFAULT_MAX_INPUT_TOKENS,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "max_reasoning_tokens": DEFAULT_MAX_REASONING_TOKENS,
            "required_checks": [],
            "required_criteria": [],
            "contract_original": "",
            "contract_digest": "",
            "evidence": {},
            "lane_cache": {},
            "last_event_digest": "",
            "adoptions": 0,
            "resumable": False,
            "snapshot_files": list(LEGACY_SNAPSHOT_FILES),
        }
    )
    return state


def _integer(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback
