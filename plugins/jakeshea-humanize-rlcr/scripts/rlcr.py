#!/usr/bin/env python3
"""Executable entrypoint for the bundled Humanize RLCR controller."""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from controller.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
