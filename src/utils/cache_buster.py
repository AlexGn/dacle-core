"""
Cache Busting Utility for TGE Reports Dashboard

DEPRECATED: Use src.utils module instead.
Session 256: Marked for migration to src/utils/

Automatically updates version numbers in index.html to force browser cache refresh.
Uses semantic versioning (3.5 -> 3.6 -> 3.7) for human-readable version tracking.
"""

import warnings
warnings.warn(
    "scripts.helpers.cache_buster is deprecated. "
    "Use src.utils module instead.",
    DeprecationWarning,
    stacklevel=2
)

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple


def update_dashboard_version(
    index_path: Path,
    context: Optional[str] = None,
    force_version: Optional[float] = None
) -> Tuple[bool, Optional[float]]:
    """
    Auto-increment version number in index.html for cache busting.

    Args:
        index_path: Path to reports/index.html
        context: Optional context string (e.g., "GAIB 10.0/10 EXECUTE")
        force_version: Optional version to set (bypasses auto-increment)

    Returns:
        Tuple[bool, Optional[float]]: (success, new_version)

    Example:
        Before: <!-- Version: 3.5 - Cache Bust Nov 20, 2025 (GAIB Conviction Fixed) -->
        After:  <!-- Version: 3.6 - Auto-updated Nov 20, 2025 (GAIB 10.0/10) -->
    """
    if not index_path.exists():
        print(f"❌ Error: {index_path} not found")
        return False, None

    # Read current content
    content = index_path.read_text(encoding="utf-8")

    # Extract current version
    current_version = get_current_version(index_path)

    if current_version is None:
        print("⚠️  Warning: Version comment not found in HTML")
        return False, None

    # Calculate new version
    if force_version is not None:
        new_version = force_version
    else:
        new_version = round(current_version + 0.1, 1)

    # Generate timestamp
    date_str = datetime.now().strftime("%b %d, %Y")

    # Build version comment
    if context:
        version_comment = f"<!-- Version: {new_version:.1f} - Auto-updated {date_str} ({context}) -->"
    else:
        version_comment = f"<!-- Version: {new_version:.1f} - Auto-updated {date_str} -->"

    # Replace version comment (matches any existing format)
    pattern = r"<!--\s*Version:.*?-->"
    updated_content = re.sub(pattern, version_comment, content, count=1)

    if updated_content == content:
        print("⚠️  Warning: Version pattern not found in HTML")
        return False, None

    # Also update JavaScript DASHBOARD_VERSION constant
    js_pattern = r"const DASHBOARD_VERSION = '[^']+'"
    js_replacement = f"const DASHBOARD_VERSION = '{new_version:.1f}'"
    updated_content = re.sub(js_pattern, js_replacement, updated_content, count=1)

    # Write updated content
    index_path.write_text(updated_content, encoding="utf-8")

    print(f"✅ Version updated: {current_version:.1f} → {new_version:.1f}")
    print(f"   • HTML comment updated")
    print(f"   • JavaScript constant updated")
    if context:
        print(f"   • Context: {context}")

    return True, new_version


def get_current_version(index_path: Path) -> Optional[float]:
    """
    Extract current version number from index.html.

    Args:
        index_path: Path to reports/index.html

    Returns:
        float: Current version number or None if not found
    """
    if not index_path.exists():
        return None

    content = index_path.read_text(encoding="utf-8")

    # Match version number (e.g., "Version: 3.5")
    match = re.search(r"<!--\s*Version:\s*(\d+\.\d+)", content)

    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None

    return None


if __name__ == "__main__":
    # Test the utility
    from pathlib import Path

    project_root = Path(__file__).parent.parent.parent
    index_path = project_root / "reports" / "index.html"

    print("🧪 Testing cache buster utility...\n")

    # Show current version
    current = get_current_version(index_path)
    print(f"Current version: {current}")

    # Update with context
    success = update_dashboard_version(
        index_path,
        context="Test Update"
    )

    if success:
        new_version = get_current_version(index_path)
        print(f"New version: {new_version}")
        print("\n✅ Test passed!")
    else:
        print("\n❌ Test failed!")
