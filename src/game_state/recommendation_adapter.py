"""Compatibility shim for the legacy recommendation adapter."""

from hslegend.adapters.legacy.recommendation import *  # noqa: F401,F403
from hslegend.adapters.legacy.recommendation import (
    _CHOICE_TARGET_TEXT,
    __all__ as _adapter_all,
)

__all__ = [*_adapter_all, "_CHOICE_TARGET_TEXT"]
