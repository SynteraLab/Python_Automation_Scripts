#!/usr/bin/env python3
"""
Auto Subtitle AI — Terminal User Interface

Launch:
    python scripts/tui.py
    python -m scripts.tui
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _prefer_project_venv() -> None:
    """Re-exec under the project virtualenv when available."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return

    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return

    current = Path(sys.executable).absolute()
    if current == venv_python.absolute():
        return

    os.execv(str(venv_python), [str(venv_python), *sys.argv])


_prefer_project_venv()

from app.core.logging_config import setup_logging


def main() -> None:
    setup_logging(level="WARNING")  # suppress noisy logs inside TUI
    from app.tui.app import SubtitleApp
    app = SubtitleApp()
    app.run()


if __name__ == "__main__":
    main()
