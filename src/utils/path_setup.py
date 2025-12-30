#!/usr/bin/env python3
"""
Path Setup Helper for Scripts

Standardizes Python path manipulation for all scripts in /scripts directory.
Eliminates the duplicate sys.path.insert() calls found in 41+ scripts.
Session 267: Migrated from scripts/helpers/path_setup.py to src/utils/path_setup.py

Usage:
    # Option 1: Auto-setup (adds project root and src/ to path)
    from src.utils.path_setup import setup_path
    setup_path()

    # Option 2: Manual path retrieval
    from src.utils.path_setup import get_project_root, get_src_dir
    PROJECT_ROOT = get_project_root()
    SRC_DIR = get_src_dir()

Common Patterns This Replaces:
    # OLD (found in 41+ scripts):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    sys.path.append(str(Path(__file__).parent.parent / "src"))

    # NEW:
    from src.utils.path_setup import setup_path
    setup_path()

Created: 2025-11-19 (Phase 1: Codebase Cleanup)
"""

import sys
from pathlib import Path
from typing import List

# Calculate paths relative to this file's location
# This file is at: /src/utils/path_setup.py
# Project root is: ../../ (two levels up)
UTILS_DIR = Path(__file__).parent
SRC_DIR = UTILS_DIR.parent
PROJECT_ROOT = SRC_DIR.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"
REPORTS_DIR = PROJECT_ROOT / "reports"

# Track if setup has been called
_path_setup_complete = False


def get_project_root() -> Path:
    """
    Get the DACLE project root directory.

    Returns:
        Path: Absolute path to project root

    Example:
        ```python
        from scripts.helpers.path_setup import get_project_root

        root = get_project_root()
        print(f"Project root: {root}")
        ```
    """
    return PROJECT_ROOT.resolve()


def get_src_dir() -> Path:
    """
    Get the src/ directory.

    Returns:
        Path: Absolute path to src directory

    Example:
        ```python
        from scripts.helpers.path_setup import get_src_dir

        src = get_src_dir()
        print(f"Source directory: {src}")
        ```
    """
    return SRC_DIR.resolve()


def get_scripts_dir() -> Path:
    """
    Get the scripts/ directory.

    Returns:
        Path: Absolute path to scripts directory
    """
    return SCRIPTS_DIR.resolve()


def get_data_dir() -> Path:
    """
    Get the data/ directory.

    Returns:
        Path: Absolute path to data directory

    Example:
        ```python
        from scripts.helpers.path_setup import get_data_dir

        data_dir = get_data_dir()
        json_file = data_dir / "enriched_projects.json"
        ```
    """
    return DATA_DIR.resolve()


def get_docs_dir() -> Path:
    """
    Get the docs/ directory.

    Returns:
        Path: Absolute path to docs directory
    """
    return DOCS_DIR.resolve()


def get_reports_dir() -> Path:
    """
    Get the reports/ directory.

    Returns:
        Path: Absolute path to reports directory

    Example:
        ```python
        from scripts.helpers.path_setup import get_reports_dir

        reports = get_reports_dir()
        gaib_report = reports / "GAIB" / "report-viewer.html"
        ```
    """
    return REPORTS_DIR.resolve()


def setup_path(
    include_src: bool = True,
    include_scripts: bool = True,
    include_root: bool = True,
    force: bool = False,
) -> List[Path]:
    """
    Add DACLE directories to Python path for imports.

    This is the main function to call at the top of your script.
    It's idempotent - calling multiple times has no negative effect.

    Args:
        include_src: Add src/ directory to path (default: True)
        include_scripts: Add scripts/ directory to path (default: True)
        include_root: Add project root to path (default: True)
        force: Force re-setup even if already called (default: False)

    Returns:
        List[Path]: Paths that were added to sys.path

    Example:
        ```python
        # At top of your script (most common usage)
        from scripts.helpers.path_setup import setup_path
        setup_path()

        # Now you can import from src/
        from knowledge.supabase_client import get_knowledge_base
        from utils.logger import get_logger

        # Or only add specific directories
        setup_path(include_src=True, include_root=False)
        ```
    """
    global _path_setup_complete

    if _path_setup_complete and not force:
        # Already set up, no need to do again
        return []

    added_paths: List[Path] = []

    # Add paths in order of priority (most specific first)
    paths_to_add = []

    if include_src:
        paths_to_add.append(SRC_DIR.resolve())

    if include_scripts:
        paths_to_add.append(SCRIPTS_DIR.resolve())

    if include_root:
        paths_to_add.append(PROJECT_ROOT.resolve())

    # Add to sys.path if not already present
    for path in paths_to_add:
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
            added_paths.append(path)

    _path_setup_complete = True
    return added_paths


def verify_imports() -> bool:
    """
    Verify that common DACLE imports work after path setup.

    Returns:
        bool: True if imports successful, False otherwise

    Example:
        ```python
        from scripts.helpers.path_setup import setup_path, verify_imports

        setup_path()
        if not verify_imports():
            print("❌ Import verification failed")
            sys.exit(1)
        ```
    """
    try:
        # Try importing common modules
        import src.utils.config  # noqa: F401
        from knowledge import supabase_client  # noqa: F401

        return True
    except ImportError as e:
        print(f"❌ Import verification failed: {e}")
        print(f"   sys.path: {sys.path[:3]}")
        return False


def print_paths() -> None:
    """
    Print all DACLE paths for debugging.

    Example:
        ```python
        from scripts.helpers.path_setup import print_paths

        print_paths()
        ```
    """
    print("\n" + "=" * 70)
    print("DACLE Path Configuration")
    print("=" * 70)
    print(f"Project Root:   {get_project_root()}")
    print(f"Source Dir:     {get_src_dir()}")
    print(f"Scripts Dir:    {get_scripts_dir()}")
    print(f"Data Dir:       {get_data_dir()}")
    print(f"Docs Dir:       {get_docs_dir()}")
    print(f"Reports Dir:    {get_reports_dir()}")
    print("\nPython sys.path (first 5 entries):")
    for i, path in enumerate(sys.path[:5], 1):
        print(f"  {i}. {path}")
    print("=" * 70 + "\n")


# Auto-setup on import (can be disabled by importing specific functions)
# This makes the module even easier to use:
#   import scripts.helpers.path_setup  # Auto-runs setup_path()
def _auto_setup():
    """Auto-run setup_path() when module is imported"""
    # Only auto-setup if not already done
    if not _path_setup_complete:
        setup_path()


# Convenience exports
__all__ = [
    "setup_path",
    "get_project_root",
    "get_src_dir",
    "get_scripts_dir",
    "get_data_dir",
    "get_docs_dir",
    "get_reports_dir",
    "verify_imports",
    "print_paths",
    "PROJECT_ROOT",
    "SRC_DIR",
    "DATA_DIR",
    "DOCS_DIR",
    "REPORTS_DIR",
]

# Run auto-setup
_auto_setup()
