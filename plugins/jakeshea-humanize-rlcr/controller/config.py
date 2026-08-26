"""Trusted defaults and immutable run-configuration construction."""

from __future__ import annotations

import re

from .domain import LaneSpec, RunConfig

RUN_SCHEMA_VERSION = "rlcr.run.v2"
LEGACY_RUN_SCHEMA_VERSION = "rlcr.run.v1"
REVIEW_SCHEMA_VERSION = "rlcr.review.v1"
REVIEWER_MODEL = "gpt-5.6-sol"
REVIEWER_EFFORT = "xhigh"
REVIEWER_LANES = ("specification", "correctness")

DEFAULT_MAX_ROUNDS = 6
MAX_MAX_ROUNDS = 12
DEFAULT_REVIEW_TIMEOUT = 900
MIN_REVIEW_TIMEOUT = 30
MAX_REVIEW_TIMEOUT = 900
DEFAULT_MAX_MINUTES = 120
MAX_MAX_MINUTES = 1440
MAX_INFRA_FAILURES = 3

DEFAULT_MAX_INPUT_TOKENS = 8_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 1_000_000
DEFAULT_MAX_REASONING_TOKENS = 1_000_000
MAX_TOKEN_BUDGET = 100_000_000

CHECK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def default_lane_specs() -> tuple[LaneSpec, ...]:
    return tuple(
        LaneSpec(
            name=lane,
            prompt_file=f"prompts/{lane}.md",
            model=REVIEWER_MODEL,
            effort=REVIEWER_EFFORT,
        )
        for lane in REVIEWER_LANES
    )


def build_run_config(
    *,
    max_rounds: int,
    review_timeout_seconds: int,
    max_minutes: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_reasoning_tokens: int,
    required_checks: tuple[str, ...] = (),
    contract_original: str = "",
    contract_digest: str = "",
) -> RunConfig:
    """Validate user bounds and build the policy captured for a new run."""

    if not 1 <= max_rounds <= MAX_MAX_ROUNDS:
        raise ValueError(f"--max-rounds must be between 1 and {MAX_MAX_ROUNDS}")
    if not MIN_REVIEW_TIMEOUT <= review_timeout_seconds <= MAX_REVIEW_TIMEOUT:
        raise ValueError(
            "--review-timeout must be between "
            f"{MIN_REVIEW_TIMEOUT} and {MAX_REVIEW_TIMEOUT} seconds"
        )
    if not 1 <= max_minutes <= MAX_MAX_MINUTES:
        raise ValueError(f"--max-minutes must be between 1 and {MAX_MAX_MINUTES}")
    for name, value in (
        ("--max-input-tokens", max_input_tokens),
        ("--max-output-tokens", max_output_tokens),
        ("--max-reasoning-tokens", max_reasoning_tokens),
    ):
        if not 1 <= value <= MAX_TOKEN_BUDGET:
            raise ValueError(f"{name} must be between 1 and {MAX_TOKEN_BUDGET}")
    if len(set(required_checks)) != len(required_checks):
        raise ValueError("--require-check names must be unique")
    invalid = [name for name in required_checks if CHECK_NAME_RE.fullmatch(name) is None]
    if invalid:
        raise ValueError(
            "--require-check names must contain only letters, digits, dot, dash, and "
            f"underscore: {invalid[0]!r}"
        )

    lanes = default_lane_specs()
    return RunConfig(
        lanes=lanes,
        max_rounds=max_rounds,
        review_timeout_seconds=review_timeout_seconds,
        max_minutes=max_minutes,
        max_reviewer_calls=(max_rounds * len(lanes) + MAX_INFRA_FAILURES * len(lanes)),
        max_infrastructure_failures=MAX_INFRA_FAILURES,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_reasoning_tokens=max_reasoning_tokens,
        required_checks=required_checks,
        contract_original=contract_original,
        contract_digest=contract_digest,
    )
