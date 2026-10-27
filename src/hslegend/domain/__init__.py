"""Public pure-domain API."""

from .actions import ActionIntent, ActionPlan, Condition, PlanStep
from .cards import CardCapabilitySet, CardDefinition
from .models import (
    SUPPORTED_SCHEMA_VERSIONS,
    ChoiceView,
    EntityRef,
    GameSnapshot,
    PlayerView,
)
from .primitives import canonical_json, deep_freeze_primitive, to_primitive
from .results import ActionVerdict, PostconditionResult, ValidationResult
from .rules import PlanRuleValidator, PostconditionEvaluator, TargetResolver

__all__ = [
    "ActionIntent",
    "ActionPlan",
    "ActionVerdict",
    "CardCapabilitySet",
    "CardDefinition",
    "ChoiceView",
    "Condition",
    "EntityRef",
    "GameSnapshot",
    "PlanRuleValidator",
    "PlanStep",
    "PlayerView",
    "PostconditionEvaluator",
    "PostconditionResult",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TargetResolver",
    "ValidationResult",
    "canonical_json",
    "deep_freeze_primitive",
    "to_primitive",
]
