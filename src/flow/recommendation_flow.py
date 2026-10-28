"""Compatibility shim for the application recommendation flow."""

from hslegend.application.recommendation_flow import (
    SAME_RECOMMENDATION_RETRY_LIMIT,
    FlowStepResult,
    FlowStepStatus,
    ResultStatus,
    _UNSET_TURN,
    _accepts_action_kind,
)
from hslegend.application.recommendation_flow import (
    RecommendationFlow as _RecommendationFlow,
)


def _warn(message):
    from print_info import warn_print
    warn_print(message)


class RecommendationFlow(_RecommendationFlow):
    """Bind the legacy warning sink while keeping the application flow."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("warn", _warn)
        super().__init__(*args, **kwargs)


__all__ = [
    "SAME_RECOMMENDATION_RETRY_LIMIT",
    "FlowStepResult",
    "FlowStepStatus",
    "RecommendationFlow",
    "ResultStatus",
    "_UNSET_TURN",
    "_accepts_action_kind",
]
