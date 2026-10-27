"""Stable multi-frame HSAng OCR bridge for recommendation requests."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from hslegend.adapters.hsang import (
    HsangAdapterError,
    HsangCaptureError,
    HsangObservationError,
    HsangParseError,
    HsangReadError,
)
from hslegend.application import RecommendationMode, RecommendationObservation
from hslegend.domain import GameSnapshot


_MULLIGAN_LINE = re.compile(r"^替换[1-9]\d*号位卡牌$")
_KEEP_ALL = "保留全部卡牌"


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
        raise HsangObservationError(
            "invalid_observation", f"{field_name} must be non-empty text"
        )
    return value.strip()


def _confidence(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HsangObservationError(
            "invalid_confidence", f"{field_name} must be numeric"
        )
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise HsangObservationError(
            "invalid_confidence", f"{field_name} must be between 0 and 1"
        )
    return parsed


def _instruction_is_mulligan(instruction: str) -> bool:
    lines = [line.strip() for line in instruction.splitlines() if line.strip()]
    if not lines:
        return False
    if _KEEP_ALL in lines:
        return len(lines) == 1
    return all(_MULLIGAN_LINE.fullmatch(line) is not None for line in lines)


class StableHsangRecommendationSource:
    """Run one stable, multi-frame OCR read per recommendation request."""

    def __init__(
        self,
        capture: Callable[[], Any],
        reader: Any,
        parse: Callable[[Any, int, int], Any],
        *,
        mulligan_roi_supplier: Callable[[Any], Any],
        turn_roi_supplier: Callable[[Any], Any],
    ) -> None:
        for name, value in (
            ("capture", capture),
            ("parse", parse),
            ("mulligan_roi_supplier", mulligan_roi_supplier),
            ("turn_roi_supplier", turn_roi_supplier),
        ):
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        if not callable(getattr(reader, "read", None)):
            raise TypeError("reader must provide read(frame_supplier, roi_supplier)")
        self._capture = capture
        self._reader = reader
        self._parse = parse
        self._mulligan_roi = mulligan_roi_supplier
        self._turn_roi = turn_roi_supplier

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
        frames: list[Any] = []

        def frame_supplier() -> Any:
            try:
                frame = self._capture()
            except HsangAdapterError:
                raise
            except Exception as exc:
                raise HsangCaptureError("capture_failed", str(exc)) from exc
            if frame is None:
                raise HsangCaptureError("missing_frame", "capture returned no frame")
            frames.append(frame)
            return frame

        roi_supplier = (
            self._mulligan_roi if mode is RecommendationMode.MULLIGAN else self._turn_roi
        )
        try:
            ocr = self._reader.read(frame_supplier, roi_supplier)
        except HsangAdapterError:
            raise
        except Exception as exc:
            reason = "ocr_not_stable" if "not_stable" in str(exc).lower() or "not stable" in str(exc).lower() else "read_failed"
            raise HsangReadError(reason, str(exc)) from exc
        if ocr is None:
            raise HsangReadError("missing_ocr", "stable reader returned no OCR evidence")
        normalized_text = _required_text(
            _field(ocr, "normalized_text"), "ocr.normalized_text"
        )

        try:
            parsed = self._parse(ocr, snapshot.turn_number, snapshot.log_revision)
        except HsangAdapterError:
            raise
        except Exception as exc:
            raise HsangParseError("parse_failed", str(exc)) from exc

        parsed_mode = _field(parsed, "mode")
        if parsed_mode is not None:
            try:
                normalized_mode = (
                    parsed_mode
                    if isinstance(parsed_mode, RecommendationMode)
                    else RecommendationMode(parsed_mode)
                )
            except (TypeError, ValueError) as exc:
                raise HsangObservationError("invalid_mode", str(exc)) from exc
            if normalized_mode is not mode:
                raise HsangObservationError(
                    "mode_mismatch", "parsed recommendation mode differs"
                )

        parsed_snapshot_id = _field(parsed, "snapshot_id")
        if parsed_snapshot_id is not None and parsed_snapshot_id != snapshot_id:
            raise HsangObservationError(
                "snapshot_mismatch", "parsed recommendation belongs to another snapshot"
            )
        turn_number = _field(parsed, "turn_number")
        if turn_number is not None and turn_number != snapshot.turn_number:
            raise HsangObservationError("stale_turn", "parsed turn differs from snapshot")
        revision = _field(parsed, "log_revision")
        if revision is not None and revision != snapshot.log_revision:
            raise HsangObservationError("stale_revision", "parsed revision differs from snapshot")

        instruction = _first_field(
            parsed, ("instruction", "normalized_instruction", "raw_instruction")
        )
        instruction = _required_text(instruction, "instruction")
        action = _field(parsed, "action")
        action_value = str(getattr(action, "value", action) or "").strip().lower()
        is_mulligan = _instruction_is_mulligan(instruction) or action_value == "mulligan"
        if mode is RecommendationMode.MULLIGAN and not is_mulligan:
            raise HsangObservationError("mode_mismatch", "parser did not return a mulligan")
        if mode is RecommendationMode.TURN and is_mulligan:
            raise HsangObservationError("mode_mismatch", "parser returned a mulligan")

        confidence = _field(parsed, "confidence")
        if confidence is None:
            candidates = [
                _field(parsed, "ocr_confidence"),
                _field(parsed, "semantic_confidence"),
            ]
            values = [
                _confidence(value, "confidence")
                for value in candidates
                if value is not None
            ]
            confidence = min(values) if values else _field(ocr, "confidence")
        confidence = _confidence(confidence, "confidence")
        if not frames:
            raise HsangCaptureError("missing_frame", "reader produced no captured frame")
        frame = frames[-1]
        stable_token = str(
            getattr(frame, "exact_hash", "")
            or getattr(ocr, "frame_id", "")
            or getattr(frame, "frame_id", "")
        )
        evidence_id = "hsang:" + hashlib.sha256(
            (
                f"{snapshot_id}|{mode.value}|{stable_token}|"
                f"{normalized_text}|{confidence:.12f}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        return RecommendationObservation(
            mode=mode,
            instruction=instruction,
            confidence=confidence,
            evidence_id=evidence_id,
            snapshot_id=snapshot_id,
        )


__all__ = ["StableHsangRecommendationSource"]
