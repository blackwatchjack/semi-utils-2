from __future__ import annotations

import os
import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent


def get_runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return _PROJECT_ROOT


def resolve_resource_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate

    candidate_str = str(candidate)
    if candidate_str.startswith("./"):
        candidate_str = candidate_str[2:]
    normalized = Path(candidate_str)
    search_roots = [
        Path.cwd(),
        get_runtime_base_dir(),
        _PROJECT_ROOT,
    ]

    for root in search_roots:
        resolved = (root / normalized).resolve()
        if resolved.exists():
            return resolved

    return (get_runtime_base_dir() / normalized).resolve()


def resolve_exiftool_env_path() -> Path | None:
    exiftool_env = os.getenv("SEMI_EXIFTOOL_PATH")
    if not exiftool_env:
        return None
    return Path(exiftool_env).expanduser().resolve()
