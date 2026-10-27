"""Locate Node for explicit frontend integration tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


NODE_ENV = "HSL_NODE"


def discover_node() -> str | None:
    configured = os.environ.get(NODE_ENV, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(configured)
        if found:
            return found
        raise FileNotFoundError(f"{NODE_ENV} does not name a Node executable: {configured}")

    for command in ("node", "node.exe"):
        found = shutil.which(command)
        if found:
            return found

    candidates: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "nodejs" / "node.exe")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "nodejs" / "node.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None
