"""Immutable result contracts for validation and postconditions."""

from __future__ import annotations

from dataclasses import dataclass

from .primitives import PrimitiveMixin


@dataclass(frozen=True, slots=True)
class ValidationResult(PrimitiveMixin):
    """Accepted/rejected decision with a stable reason code."""

    accepted: bool
    reason_code: str = "accepted"
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", tuple(self.details))

    def __bool__(self) -> bool:
        return self.accepted

    @classmethod
    def allow(cls, details: tuple[str, ...] = ()) -> "ValidationResult":
        return cls(accepted=True, reason_code="accepted", details=details)

    @classmethod
    def accept(cls, details: tuple[str, ...] = ()) -> "ValidationResult":
        return cls.allow(details)

    @classmethod
    def reject(cls, reason_code: str, *details: str) -> "ValidationResult":
        return cls(accepted=False, reason_code=reason_code, details=tuple(details))


@dataclass(frozen=True, slots=True)
class PostconditionResult(PrimitiveMixin):
    """Pure judgement over before/after snapshots."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    FAILED = "failed"

    status: str
    reason_code: str
    evidence_refs: tuple[str, ...] = ()
    failed_condition_indexes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(
            self, "failed_condition_indexes", tuple(self.failed_condition_indexes)
        )

    @property
    def confirmed(self) -> bool:
        return self.status == self.CONFIRMED

    @classmethod
    def confirm(cls, *evidence_refs: str) -> "PostconditionResult":
        return cls(cls.CONFIRMED, "postcondition_confirmed", tuple(evidence_refs))

    @classmethod
    def unconfirmed(
        cls, reason_code: str, *evidence_refs: str
    ) -> "PostconditionResult":
        return cls(cls.UNCONFIRMED, reason_code, tuple(evidence_refs))

    @classmethod
    def fail(
        cls,
        reason_code: str,
        *evidence_refs: str,
        failed_condition_indexes: tuple[int, ...] = (),
    ) -> "PostconditionResult":
        return cls(
            cls.FAILED,
            reason_code,
            tuple(evidence_refs),
            failed_condition_indexes,
        )


@dataclass(frozen=True, slots=True)
class ActionVerdict(PrimitiveMixin):
    """Domain-level outcome for validation or postcondition judgement."""

    accepted: bool
    reason_code: str
    validation: ValidationResult | None = None
    postcondition: PostconditionResult | None = None

    @classmethod
    def from_validation(cls, result: ValidationResult) -> "ActionVerdict":
        return cls(result.accepted, result.reason_code, validation=result)

    @classmethod
    def from_postcondition(cls, result: PostconditionResult) -> "ActionVerdict":
        return cls(result.confirmed, result.reason_code, postcondition=result)
