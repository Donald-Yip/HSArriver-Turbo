"""Machine-specific paths derived from the stable legacy configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True, slots=True)
class LegacyPaths:
    """Paths needed by native adapters, with no hard-coded machine values."""

    root: Path
    config_path: Path
    cards_path: Path
    log_root: Path | None
    journal_path: Path
    artifact_root: Path


def legacy_paths(config_module: ModuleType | None = None) -> LegacyPaths:
    """Resolve paths through the legacy config module at call time."""

    if config_module is None:
        import config as config_module
    root = Path(config_module.ROOT).expanduser().resolve()
    runtime = root / "runtime"
    raw_log_root = str(getattr(config_module, "HEARTHSTONE_LOG_ROOT", "") or "")
    return LegacyPaths(
        root=root,
        config_path=Path(config_module.CONFIG_PATH).expanduser(),
        cards_path=root / "cards.json",
        log_root=Path(raw_log_root).expanduser() if raw_log_root else None,
        journal_path=runtime / "onegame-journal.jsonl",
        artifact_root=runtime / "artifacts",
    )


__all__ = ["LegacyPaths", "legacy_paths"]
