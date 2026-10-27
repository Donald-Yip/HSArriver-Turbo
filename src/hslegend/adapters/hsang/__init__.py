"""HSAng recommendation adapter.

The adapter deliberately accepts capture, OCR read, and recommendation parse
callables. Legacy modules are therefore injected at the composition boundary
instead of imported by the new architecture.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from hslegend.application import RecommendationMode, RecommendationObservation
from hslegend.domain import GameSnapshot


class HsangAdapterError(RuntimeError):
    """Typed failure raised by the HSAng adapter."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


class HsangCaptureError(HsangAdapterError):
    """Capture failed before OCR could run."""


class HsangReadError(HsangAdapterError):
    """OCR reading failed."""


class HsangParseError(HsangAdapterError):
    """Recommendation parsing failed."""


class HsangObservationError(HsangAdapterError):
    """Parsed output could not be converted to a valid observation."""


def _field(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _first_field(source: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = _field(source, name)
        if value is not None:
            return value
    return None


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HsangObservationError("invalid_observation", f"{field_name} must be non-empty text")
    return value.strip()


def _finite_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HsangObservationError("invalid_confidence", "confidence must be numeric")
    confidence = float(value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise HsangObservationError(
            "invalid_confidence",
            "confidence must be finite and between 0 and 1",
        )
    return confidence


class HsangRecommendationAdapter:
    """Adapt injected HSAng capture/read/parse functions to the application port.

    ``parse`` is called as ``parse(ocr, snapshot.turn_number,
    snapshot.log_revision)``, matching the legacy recommendation parser.
    """

    def __init__(
        self,
        capture: Callable[[], Any],
        read: Callable[[Any], Any],
        parse: Callable[[Any, int, int], Any],
    ) -> None:
        for name, callable_value in (
            ("capture", capture),
            ("read", read),
            ("parse", parse),
        ):
            if not callable(callable_value):
                raise TypeError(f"{name} must be callable")
        self._capture = capture
        self._read = read
        self._parse = parse

    def recommend(
        self,
        snapshot: GameSnapshot,
        mode: RecommendationMode,
    ) -> RecommendationObservation:
        if not isinstance(snapshot, GameSnapshot):
            raise HsangObservationError("invalid_snapshot", "snapshot must be a GameSnapshot")
        if not isinstance(mode, RecommendationMode):
            try:
                mode = RecommendationMode(mode)
            except (TypeError, ValueError) as exc:
                raise HsangObservationError("invalid_mode", str(exc)) from exc
        snapshot_id = _required_text(snapshot.snapshot_id, "snapshot_id")

        try:
            frame = self._capture()
        except Exception as exc:
            raise HsangCaptureError("capture_failed", str(exc)) from exc
        try:
            ocr = self._read(frame)
        except Exception as exc:
            raise HsangReadError("read_failed", str(exc)) from exc
        try:
            parsed = self._parse(
                ocr,
                snapshot.turn_number,
                snapshot.log_revision,
            )
        except Exception as exc:
            raise HsangParseError("parse_failed", str(exc)) from exc

        if isinstance(parsed, RecommendationObservation):
            if not isinstance(parsed.mode, RecommendationMode):
                raise HsangObservationError("invalid_mode", "parsed observation mode is invalid")
            if parsed.mode is not mode:
                raise HsangObservationError("mode_mismatch", "parsed observation mode differs")
            _required_text(parsed.instruction, "instruction")
            _required_text(parsed.evidence_id, "evidence_id")
            parsed_snapshot_id = _required_text(parsed.snapshot_id, "snapshot_id")
            if parsed_snapshot_id != snapshot_id:
                raise HsangObservationError(
                    "snapshot_mismatch",
                    "parsed observation belongs to another snapshot",
                )
            _finite_confidence(parsed.confidence)
            return parsed

        parsed_snapshot_id = _field(parsed, "snapshot_id")
        if parsed_snapshot_id is not None and parsed_snapshot_id != snapshot_id:
            raise HsangObservationError(
                "snapshot_mismatch",
                "parsed recommendation belongs to another snapshot",
            )
        self._reject_stale(parsed, snapshot)

        instruction = _first_field(
            parsed,
            ("instruction", "normalized_instruction", "raw_instruction"),
        )
        instruction = _required_text(instruction, "instruction")

        confidence = _field(parsed, "confidence")
        if confidence is None:
            confidence = self._combined_confidence(parsed, ocr)
        confidence = _finite_confidence(confidence)

        evidence_id = _first_field(parsed, ("evidence_id", "frame_id"))
        if evidence_id is None:
            evidence_id = _first_field(ocr, ("evidence_id", "frame_id"))
        if evidence_id is None:
            evidence_id = _field(frame, "frame_id")
        evidence_id = _required_text(evidence_id, "evidence_id")

        return RecommendationObservation(
            mode=mode,
            instruction=instruction,
            confidence=confidence,
            evidence_id=evidence_id,
            snapshot_id=snapshot_id,
        )

    @staticmethod
    def _reject_stale(parsed: Any, snapshot: GameSnapshot) -> None:
        turn_number = _field(parsed, "turn_number")
        if turn_number is not None and turn_number != snapshot.turn_number:
            raise HsangObservationError("stale_turn", "parsed turn differs from snapshot")
        revision = _field(parsed, "log_revision")
        if revision is not None and revision != snapshot.log_revision:
            raise HsangObservationError(
                "stale_revision",
                "parsed revision differs from snapshot",
            )

    @staticmethod
    def _combined_confidence(parsed: Any, ocr: Any) -> float:
        ocr_confidence = _field(parsed, "ocr_confidence")
        semantic_confidence = _field(parsed, "semantic_confidence")
        values = [
            _finite_confidence(value)
            for value in (ocr_confidence, semantic_confidence)
            if value is not None
        ]
        if not values:
            read_confidence = _field(ocr, "confidence")
            if read_confidence is not None:
                values.append(_finite_confidence(read_confidence))
        if not values:
            raise HsangObservationError("missing_confidence", "confidence is required")
        return min(values)


__all__ = [
    "HsangAdapterError",
    "HsangCaptureError",
    "HsangObservationError",
    "HsangParseError",
    "HsangReadError",
    "HsangRecommendationAdapter",
]
