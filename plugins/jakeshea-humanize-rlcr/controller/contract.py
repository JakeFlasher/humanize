"""Structured plan-contract validation and immutable loading."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import CHECK_NAME_RE
from .domain import canonical_json, sha256_digest

MAX_CONTRACT_BYTES = 1024 * 1024
CRITERION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ContractError(ValueError):
    """A structured plan contract cannot be trusted or interpreted."""


def read_contract(contract_path: Path, project_root: Path) -> tuple[dict[str, Any], bytes, str]:
    """Read and normalize a non-symlink contract inside the repository."""

    lexical = contract_path if contract_path.is_absolute() else project_root / contract_path
    if lexical.is_symlink():
        raise ContractError(f"plan contract must not be a symbolic link: {lexical}")
    candidate = lexical.resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ContractError("plan contract must stay inside the repository") from exc
    if not candidate.is_file():
        raise ContractError(f"plan contract is not a regular file: {candidate}")
    cursor = lexical.parent
    while cursor != project_root and cursor != cursor.parent:
        if cursor.is_symlink():
            raise ContractError(f"plan contract path must not traverse a symbolic link: {cursor}")
        cursor = cursor.parent
    raw = candidate.read_bytes()
    if not raw or len(raw) > MAX_CONTRACT_BYTES:
        raise ContractError(f"plan contract must contain 1 to {MAX_CONTRACT_BYTES} bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"plan contract is not valid UTF-8 JSON: {exc}") from exc
    normalized = validate_contract(value)
    payload = canonical_json(normalized) + b"\n"
    relative = candidate.relative_to(project_root).as_posix()
    return normalized, payload, relative


def validate_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "goal",
        "criteria",
    }:
        raise ContractError("plan contract has missing or unsupported top-level fields")
    if value["schema_version"] != "rlcr.plan.v1":
        raise ContractError("plan contract schema_version must be rlcr.plan.v1")
    goal = _text(value["goal"], "goal", maximum=8000)
    raw_criteria = value["criteria"]
    if not isinstance(raw_criteria, list) or not 1 <= len(raw_criteria) <= 100:
        raise ContractError("plan contract criteria must contain 1 to 100 entries")
    criteria: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_criteria):
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "description",
            "required",
            "required_checks",
        }:
            raise ContractError(f"criterion {index} has an invalid shape")
        criterion_id = _text(raw["id"], f"criteria[{index}].id", maximum=128)
        if CRITERION_ID_RE.fullmatch(criterion_id) is None:
            raise ContractError(f"criterion {criterion_id!r} has an invalid id")
        if criterion_id in seen:
            raise ContractError(f"criterion id is duplicated: {criterion_id}")
        seen.add(criterion_id)
        required = raw["required"]
        if not isinstance(required, bool):
            raise ContractError(f"criterion {criterion_id} required must be boolean")
        raw_checks = raw["required_checks"]
        if not isinstance(raw_checks, list) or len(raw_checks) > 20:
            raise ContractError(
                f"criterion {criterion_id} required_checks must have at most 20 entries"
            )
        checks: list[str] = []
        for check in raw_checks:
            name = _text(check, f"criterion {criterion_id} check", maximum=128)
            if CHECK_NAME_RE.fullmatch(name) is None:
                raise ContractError(f"criterion {criterion_id} has invalid check name {name!r}")
            if name not in checks:
                checks.append(name)
        criteria.append(
            {
                "id": criterion_id,
                "description": _text(
                    raw["description"],
                    f"criteria[{index}].description",
                    maximum=4000,
                ),
                "required": required,
                "required_checks": checks,
            }
        )
    return {
        "schema_version": "rlcr.plan.v1",
        "goal": goal,
        "criteria": criteria,
    }


def required_criteria(contract: dict[str, Any] | None) -> tuple[str, ...]:
    if contract is None:
        return ()
    return tuple(
        str(criterion["id"]) for criterion in contract["criteria"] if criterion["required"]
    )


def required_checks(contract: dict[str, Any] | None) -> tuple[str, ...]:
    if contract is None:
        return ()
    found: list[str] = []
    for criterion in contract["criteria"]:
        if not criterion["required"]:
            continue
        for name in criterion["required_checks"]:
            if name not in found:
                found.append(name)
    return tuple(found)


def contract_digest(payload: bytes | None) -> str:
    return sha256_digest(payload or b"")


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractError(f"{field} must be a non-empty string up to {maximum} chars")
    if "\x00" in value:
        raise ContractError(f"{field} contains a NUL byte")
    return value.strip()
