"""Pure controller-owned consensus rules."""

from __future__ import annotations

from collections.abc import Sequence

from .config import REVIEWER_LANES
from .domain import LaneResult


def has_required_lanes(results: Sequence[LaneResult]) -> bool:
    return (
        len(results) == len(REVIEWER_LANES)
        and tuple(result.lane for result in results) == REVIEWER_LANES
    )


def required_criterion_gaps(
    results: Sequence[LaneResult], required: Sequence[str]
) -> tuple[str, ...]:
    """Return required plan criteria the specification lane did not pass."""

    if not required:
        return ()
    specification = next(
        (result for result in results if result.lane == "specification"),
        None,
    )
    if specification is None or specification.payload is None:
        return tuple(required)
    statuses = {
        str(check.get("criterion_id")): check.get("status")
        for check in specification.payload.get("checks", [])
        if isinstance(check, dict)
    }
    return tuple(criterion for criterion in required if statuses.get(criterion) != "pass")


def is_accepted(results: Sequence[LaneResult], criterion_gaps: Sequence[str]) -> bool:
    return not criterion_gaps and all(
        result.payload is not None and result.payload.get("candidate_verdict") == "accept"
        for result in results
    )
