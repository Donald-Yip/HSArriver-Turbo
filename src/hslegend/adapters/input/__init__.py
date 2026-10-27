"""Semantic action dispatch over injected legacy handlers."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

from hslegend.application import DispatchResult, DispatchStatus
from hslegend.domain import GameSnapshot, PlanStep


class LegacyActionDispatchError(RuntimeError):
    """Typed configuration or handler-output error."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


def _normalize_action(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


class LegacyActionDispatch:
    """Route semantic action names to injected handlers.

    Handlers are called as ``handler(step, snapshot)``. A handler may return a
    ``DispatchResult``, a mapping with dispatch fields, or ``None`` for legacy
    click functions that report success by returning nothing. Unknown action
    names are rejected before any handler is consulted.
    """

    def __init__(
        self,
        handlers: Mapping[str, Callable[[PlanStep, GameSnapshot], Any]],
        *,
        dispatch_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(handlers, Mapping):
            raise TypeError("handlers must be a mapping")
        normalized: dict[str, Callable[[PlanStep, GameSnapshot], Any]] = {}
        for action_type, handler in handlers.items():
            if not isinstance(action_type, str) or not action_type.strip():
                raise LegacyActionDispatchError(
                    "invalid_action_type",
                    "handler keys must be non-empty strings",
                )
            if not callable(handler):
                raise LegacyActionDispatchError(
                    "invalid_handler",
                    f"handler for {action_type!r} must be callable",
                )
            key = _normalize_action(action_type)
            if key in normalized:
                raise LegacyActionDispatchError(
                    "duplicate_action_type",
                    f"duplicate normalized action: {key}",
                )
            normalized[key] = handler
        self._handlers = normalized
        self._dispatch_id_factory = dispatch_id_factory or (
            lambda: f"dispatch-{uuid.uuid4()}"
        )

    def dispatch(
        self,
        step: PlanStep,
        snapshot: GameSnapshot,
    ) -> DispatchResult:
        if not isinstance(step, PlanStep):
            raise LegacyActionDispatchError("invalid_step", "step must be a PlanStep")
        if not isinstance(snapshot, GameSnapshot):
            raise LegacyActionDispatchError(
                "invalid_snapshot",
                "snapshot must be a GameSnapshot",
            )
        if not isinstance(step.intent.action_type, str) or not step.intent.action_type.strip():
            raise LegacyActionDispatchError(
                "invalid_action_type",
                "step.intent.action_type must be non-empty text",
            )
        action_type = _normalize_action(step.intent.action_type)
        handler = self._handlers.get(action_type)
        if handler is None:
            return DispatchResult.rejected("unknown_action")

        try:
            raw = handler(step, snapshot)
        except Exception:
            return DispatchResult.failed("handler_exception")
        return self._normalize_result(raw)

    def _normalize_result(self, raw: Any) -> DispatchResult:
        if raw is None:
            return DispatchResult.sent(self._new_dispatch_id())
        if isinstance(raw, DispatchResult):
            if raw.status is DispatchStatus.SENT and not raw.dispatch_id:
                return DispatchResult.sent(
                    self._new_dispatch_id(),
                    *raw.evidence_ids,
                )
            return raw
        if not isinstance(raw, Mapping):
            raise LegacyActionDispatchError(
                "invalid_handler_result",
                "handler must return DispatchResult, mapping, or None",
            )

        status_value = raw.get("status", raw.get("outcome"))
        try:
            status = (
                status_value
                if isinstance(status_value, DispatchStatus)
                else DispatchStatus(status_value)
            )
        except (TypeError, ValueError) as exc:
            raise LegacyActionDispatchError(
                "invalid_handler_result",
                "status must be sent, rejected, or failed",
            ) from exc
        reason_code = raw.get("reason_code", "sent" if status is DispatchStatus.SENT else "")
        result_dispatch_id = raw.get("dispatch_id", "")
        evidence_ids = raw.get("evidence_ids", ())
        if not isinstance(reason_code, str):
            raise LegacyActionDispatchError(
                "invalid_handler_result",
                "reason_code must be text",
            )
        if not isinstance(result_dispatch_id, str):
            raise LegacyActionDispatchError(
                "invalid_handler_result",
                "dispatch_id must be text",
            )
        if isinstance(evidence_ids, (str, bytes)) or not isinstance(
            evidence_ids,
            (list, tuple, set, frozenset),
        ):
            raise LegacyActionDispatchError(
                "invalid_handler_result",
                "evidence_ids must be a sequence of strings",
            )
        if isinstance(evidence_ids, (set, frozenset)):
            normalized_evidence = tuple(sorted(evidence_ids, key=repr))
        else:
            normalized_evidence = tuple(evidence_ids)
        if any(not isinstance(item, str) for item in normalized_evidence):
            raise LegacyActionDispatchError(
                "invalid_handler_result",
                "evidence_ids must contain only strings",
            )

        if status is DispatchStatus.SENT:
            return DispatchResult.sent(
                result_dispatch_id or self._new_dispatch_id(),
                *normalized_evidence,
            )
        if status is DispatchStatus.REJECTED:
            return DispatchResult.rejected(reason_code or "rejected", *normalized_evidence)
        return DispatchResult.failed(reason_code or "failed", *normalized_evidence)

    def _new_dispatch_id(self) -> str:
        try:
            value = self._dispatch_id_factory()
        except Exception as exc:
            raise LegacyActionDispatchError("dispatch_id_failed", str(exc)) from exc
        if not isinstance(value, str) or not value.strip():
            raise LegacyActionDispatchError(
                "invalid_dispatch_id",
                "dispatch_id_factory must return non-empty text",
            )
        return value


__all__ = ["LegacyActionDispatch", "LegacyActionDispatchError"]
