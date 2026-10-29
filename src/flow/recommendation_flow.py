"""Compatibility shim for the application recommendation flow."""

from hslegend.application.recommendation_flow import (
    FlowStepResult,
    FlowStepStatus,
    PendingRecommendationAction,
    RecommendationFlow,
    ResultStatus,
    _UNSET_TURN,
    _accepts_action_kind,
)

__all__ = [
    "FlowStepResult",
    "FlowStepStatus",
    "PendingRecommendationAction",
    "RecommendationFlow",
    "ResultStatus",
    "_UNSET_TURN",
    "_accepts_action_kind",
]
