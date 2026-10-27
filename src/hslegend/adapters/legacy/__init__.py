"""Public anti-corruption bridge surface with lazy legacy imports."""

from __future__ import annotations

import importlib
from typing import Any

from .ports import NativePorts


_LAZY_EXPORTS = {
    "AuthoritativeLobbyStateAdapter": (".lobby", "AuthoritativeLobbyStateAdapter"),
    "LegacyClickActionDispatcher": (".input", "LegacyClickActionDispatcher"),
    "LegacyLobbyAdapterError": (".lobby", "LegacyLobbyAdapterError"),
    "LegacyLogStateSnapshotMapper": (".powerlog", "LegacyLogStateSnapshotMapper"),
    "LegacyPaths": (".paths", "LegacyPaths"),
    "LegacyPowerLogError": (".powerlog", "LegacyPowerLogError"),
    "LegacyPowerLogPump": (".powerlog", "LegacyPowerLogPump"),
    "LegacyRuntimeUnavailable": (".factory", "LegacyRuntimeUnavailable"),
    "StableHsangRecommendationSource": (".hsang", "StableHsangRecommendationSource"),
    "build_native_ports": (".factory", "build_native_ports"),
    "legacy_paths": (".paths", "legacy_paths"),
    "resolve_legacy_root": (".factory", "resolve_legacy_root"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, attribute)
    globals()[name] = value
    return value


__all__ = ["NativePorts", *_LAZY_EXPORTS]
