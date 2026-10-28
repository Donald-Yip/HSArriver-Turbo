"""OCR and frame evidence types owned by legacy recommendation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FrameEvidence:
    frame_id: str
    captured_at: float
    desktop_size: tuple[int, int]
    dpi: int
    window_handle: int
    foreground: bool
    recommendation_roi: tuple[int, int, int, int]
    exact_hash: str
    perceptual_hash: str
    panel_visible: bool
    pixels: Any = None
    client_size: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class OcrEvidence:
    frame_id: str
    created_at: float
    lines: tuple[OcrLine, ...]
    normalized_text: str
    confidence: float
    backend: str
    preprocessing: str


__all__ = ["FrameEvidence", "OcrEvidence", "OcrLine"]
