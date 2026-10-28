"""Compatibility shim for the legacy recommendation parser."""

from hslegend.adapters.legacy.recommendation_parser import (
    RecommendationParseError,
    RecommendationParser,
)

__all__ = ["RecommendationParseError", "RecommendationParser"]
