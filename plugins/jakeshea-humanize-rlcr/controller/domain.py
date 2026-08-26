"""Typed domain values shared by the RLCR application layers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

RUN_CONFIG_SCHEMA_VERSION = "rlcr.run-config.v1"


def canonical_json(value: object) -> bytes:
    """Encode a JSON-compatible value deterministically."""

    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_digest(value: bytes) -> str:
    """Return a namespaced SHA-256 digest."""

    return f"sha256:{hashlib.sha256(value).hexdigest()}"


class Phase(str, Enum):
    """Every persisted lifecycle phase."""

    ACTIVE = "active"
    REVIEWING = "reviewing"
    CORRECTING = "correcting"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    EXHAUSTED = "exhausted"


ACTIVE_PHASES = frozenset({Phase.ACTIVE.value, Phase.REVIEWING.value, Phase.CORRECTING.value})
TERMINAL_PHASES = frozenset(
    {
        Phase.ACCEPTED.value,
        Phase.BLOCKED.value,
        Phase.CANCELED.value,
        Phase.EXHAUSTED.value,
    }
)


class FailureKind(str, Enum):
    """Machine-readable reviewer failure classes."""

    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    CONFIGURATION = "configuration"
    POLICY = "policy"
    CANCELED = "canceled"

    @property
    def retryable(self) -> bool:
        return self in {
            FailureKind.TRANSIENT,
            FailureKind.TIMEOUT,
            FailureKind.INVALID_OUTPUT,
        }


@dataclass(frozen=True)
class TokenUsage:
    """Codex token counters emitted by a completed turn."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> TokenUsage:
        if value is None:
            return cls()

        def counter(name: str) -> int:
            raw = value.get(name, 0)
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                return 0
            return raw

        return cls(
            input_tokens=counter("input_tokens"),
            cached_input_tokens=counter("cached_input_tokens"),
            output_tokens=counter("output_tokens"),
            reasoning_output_tokens=counter("reasoning_output_tokens"),
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_output_tokens=(self.reasoning_output_tokens + other.reasoning_output_tokens),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
        }


@dataclass(frozen=True)
class LaneSpec:
    """Immutable configuration for one independent reviewer lane."""

    name: str
    prompt_file: str
    model: str
    effort: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "prompt_file": self.prompt_file,
            "model": self.model,
            "effort": self.effort,
        }


@dataclass(frozen=True)
class RunConfig:
    """The complete immutable policy captured when a run starts."""

    lanes: tuple[LaneSpec, ...]
    max_rounds: int
    review_timeout_seconds: int
    max_minutes: int
    max_reviewer_calls: int
    max_infrastructure_failures: int
    max_input_tokens: int
    max_output_tokens: int
    max_reasoning_tokens: int
    required_checks: tuple[str, ...] = ()
    contract_original: str = ""
    contract_digest: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": RUN_CONFIG_SCHEMA_VERSION,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "max_rounds": self.max_rounds,
            "review_timeout_seconds": self.review_timeout_seconds,
            "max_minutes": self.max_minutes,
            "max_reviewer_calls": self.max_reviewer_calls,
            "max_infrastructure_failures": self.max_infrastructure_failures,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_reasoning_tokens": self.max_reasoning_tokens,
            "required_checks": list(self.required_checks),
            "contract_original": self.contract_original,
            "contract_digest": self.contract_digest,
        }

    @property
    def digest(self) -> str:
        return sha256_digest(canonical_json(self.to_dict()))


@dataclass(frozen=True)
class Artifact:
    """One immutable committed candidate and its trusted evidence."""

    digest: str
    start_sha: str
    head_sha: str
    plan_digest: str
    changed_paths: tuple[str, ...]
    diff_bytes: int
    patch_digest: str
    config_digest: str = ""
    contract_digest: str = ""
    evidence_digest: str = ""
    diff_content: bytes = field(default=b"", repr=False)
    patch_path: Path | None = None
    evidence_path: Path | None = None


@dataclass(frozen=True)
class LaneResult:
    """The normalized outcome of one reviewer lane."""

    lane: str
    payload: dict[str, Any] | None
    error: str | None
    duration_seconds: float
    command: tuple[str, ...]
    usage: TokenUsage = TokenUsage()
    failure_kind: str | None = None
    cache_key: str = ""
    cached: bool = False

    def to_cache_dict(self) -> dict[str, object]:
        if self.payload is None or self.error is not None:
            raise ValueError("only successful lane results can be cached")
        return {
            "lane": self.lane,
            "payload": self.payload,
            "duration_seconds": self.duration_seconds,
            "usage": self.usage.to_dict(),
            "cache_key": self.cache_key,
        }

    @classmethod
    def from_cache_dict(cls, value: Mapping[str, Any]) -> LaneResult:
        lane = value.get("lane")
        payload = value.get("payload")
        cache_key = value.get("cache_key")
        duration = value.get("duration_seconds", 0.0)
        if (
            not isinstance(lane, str)
            or not isinstance(payload, dict)
            or not isinstance(cache_key, str)
            or not isinstance(duration, (int, float))
            or isinstance(duration, bool)
        ):
            raise ValueError("cached lane result is malformed")
        usage = value.get("usage")
        return cls(
            lane=lane,
            payload=dict(payload),
            error=None,
            duration_seconds=float(duration),
            command=(),
            usage=TokenUsage.from_mapping(usage if isinstance(usage, dict) else None),
            cache_key=cache_key,
            cached=True,
        )


@dataclass(frozen=True)
class Outcome:
    """A controller decision returned to either the CLI or the Stop hook."""

    action: str
    phase: str
    message: str
    system_message: str
    exit_code: int
