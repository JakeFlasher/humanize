"""Public constants for the Codex-native Humanize RLCR controller."""

from __future__ import annotations

import json
from pathlib import Path

from .config import (
    LEGACY_RUN_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    REVIEWER_EFFORT,
    REVIEWER_LANES,
    REVIEWER_MODEL,
    RUN_SCHEMA_VERSION,
)


def plugin_manifest() -> dict[str, object]:
    """Read the installed manifest, the single source of package metadata."""

    path = Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"plugin manifest is not a JSON object: {path}")
    return value


def _plugin_version() -> str:
    version = plugin_manifest().get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("plugin manifest has no valid version")
    return version


PLUGIN_VERSION = _plugin_version()


__all__ = [
    "LEGACY_RUN_SCHEMA_VERSION",
    "PLUGIN_VERSION",
    "REVIEWER_EFFORT",
    "REVIEWER_LANES",
    "REVIEWER_MODEL",
    "REVIEW_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "plugin_manifest",
]
