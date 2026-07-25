"""Runtime helpers for executable and development modes."""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """Return True when running from a bundled executable."""
    return bool(getattr(sys, "frozen", False))


def get_runtime_dir() -> str:
    """Directory where logs and runtime artifacts should be created."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.getcwd())


def resolve_resource_path(relative_path: str) -> str:
    """Resolve resource path for both source and PyInstaller bundle."""
    if hasattr(sys, "_MEIPASS"):
        base_path = getattr(sys, "_MEIPASS")
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
