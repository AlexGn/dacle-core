"""Path access diagnostics for runtime permission failures."""

from __future__ import annotations

import grp
import os
import pwd
from pathlib import Path


def describe_path(path: str | Path) -> dict[str, object]:
    target = Path(path)
    info: dict[str, object] = {
        "path": str(target),
        "exists": False,
        "kind": "missing",
    }

    try:
        st = target.stat()
    except FileNotFoundError:
        return info
    except PermissionError:
        info["exists"] = "unknown"
        info["kind"] = "inaccessible"
        return info
    except OSError as exc:
        info["exists"] = "unknown"
        info["kind"] = "error"
        info["stat_error"] = f"{type(exc).__name__}: {exc}"
        return info

    info["exists"] = True
    info["uid"] = st.st_uid
    info["gid"] = st.st_gid
    info["mode"] = oct(st.st_mode & 0o777)
    if target.is_dir():
        info["kind"] = "dir"
    elif target.is_file():
        info["kind"] = "file"
    else:
        info["kind"] = "other"

    try:
        info["owner"] = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        info["owner"] = str(st.st_uid)
    try:
        info["group"] = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        info["group"] = str(st.st_gid)
    return info


def permission_context(
    path: str | Path,
    *,
    operation: str,
    exc: BaseException | None = None,
) -> str:
    target = Path(path)
    parent = target.parent if target.parent != target else target
    parts = [
        f"operation={operation}",
        f"target={describe_path(target)}",
    ]
    if parent != target:
        parts.append(f"parent={describe_path(parent)}")
    parts.append(f"euid={os.geteuid()}")
    parts.append(f"egid={os.getegid()}")
    if exc is not None:
        parts.append(f"error={type(exc).__name__}: {exc}")
    return " ".join(parts)
