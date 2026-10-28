"""Compatibility exports for recommendation models.

Semantic action types live in ``hslegend.domain``; frame/OCR evidence lives in
the legacy adapter package.
"""

from hslegend.adapters.legacy.evidence import FrameEvidence, OcrEvidence, OcrLine
from hslegend.domain.recommendation import (
    ActionKind,
    ProposedAction,
    SlotRef,
    ValidationResult,
)

__all__ = [
    "ActionKind",
    "FrameEvidence",
    "OcrEvidence",
    "OcrLine",
    "ProposedAction",
    "SlotRef",
    "ValidationResult",
]
