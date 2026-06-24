from __future__ import annotations

from pathlib import Path
import sys

from dotenv import load_dotenv


def safe_load_project_env(project_root: Path, *, warning_prefix: str = "dotenv") -> bool:
    """Best-effort dotenv load with inherited-env fallback on permission drift."""
    try:
        return bool(load_dotenv(project_root / ".env"))
    except PermissionError:
        sys.stderr.write(
            f"[{warning_prefix}] Warning: cannot read {project_root / '.env'}; "
            "continuing with inherited environment.\n"
        )
        return False
