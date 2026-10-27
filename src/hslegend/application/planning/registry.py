"""Deterministic static plugin resolution."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .contracts import PlanContext, PlanPlugin


PluginPredicate = Callable[[PlanContext], bool]


class PluginRegistryError(RuntimeError):
    """Base class for deterministic plugin-resolution failures."""


class DuplicatePluginRegistration(PluginRegistryError):
    """Raised when the same plugin id is registered twice."""


class PluginNotFoundError(PluginRegistryError):
    """Raised when no registered plugin supports a context."""


class PluginConflictError(PluginRegistryError):
    """Raised when more than one plugin matches at the winning priority."""


def _as_tuple(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class PluginRegistration:
    plugin: PlanPlugin
    card_ids: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    default: bool = False
    predicate: PluginPredicate | None = None

    def __post_init__(self) -> None:
        if not self.plugin.plugin_id:
            raise ValueError("plugin_id must not be empty")
        object.__setattr__(
            self, "card_ids", tuple(value.strip() for value in _as_tuple(self.card_ids))
        )
        object.__setattr__(
            self,
            "capabilities",
            tuple(value.strip().lower() for value in _as_tuple(self.capabilities)),
        )
        object.__setattr__(
            self,
            "families",
            tuple(value.strip().lower() for value in _as_tuple(self.families)),
        )


class PluginRegistry:
    """Small static registry with exact > capability/family > default priority.

    Every matching tier is collected completely. Ambiguity inside the highest
    matching tier raises instead of selecting by insertion order.
    """

    def __init__(self, registrations: Iterable[PluginRegistration] = ()) -> None:
        self._registrations: list[PluginRegistration] = []
        for registration in registrations:
            self._add(registration)

    @property
    def registrations(self) -> tuple[PluginRegistration, ...]:
        return tuple(self._registrations)

    def register(
        self,
        plugin: PlanPlugin,
        *,
        card_ids: Iterable[str] = (),
        capabilities: Iterable[str] = (),
        families: Iterable[str] = (),
        default: bool = False,
        predicate: PluginPredicate | None = None,
    ) -> PluginRegistration:
        registration = PluginRegistration(
            plugin=plugin,
            card_ids=_as_tuple(card_ids),
            capabilities=_as_tuple(capabilities),
            families=_as_tuple(families),
            default=default,
            predicate=predicate,
        )
        self._add(registration)
        return registration

    def register_registration(self, registration: PluginRegistration) -> None:
        self._add(registration)

    def _add(self, registration: PluginRegistration) -> None:
        if not isinstance(registration, PluginRegistration):
            raise TypeError("registration must be a PluginRegistration")
        plugin_id = registration.plugin.plugin_id
        if any(item.plugin.plugin_id == plugin_id for item in self._registrations):
            raise DuplicatePluginRegistration(f"plugin already registered: {plugin_id}")
        self._registrations.append(registration)

    def resolve(self, context: PlanContext) -> PlanPlugin:
        if not isinstance(context, PlanContext):
            raise TypeError("context must be a PlanContext")

        exact = [
            item
            for item in self._registrations
            if context.card_id is not None
            and context.card_id in item.card_ids
            and self._predicate_matches(item, context)
        ]
        if exact:
            return self._select(exact, context, "exact_card")

        specific = [
            item
            for item in self._registrations
            if (
                any(context.capability(name) for name in item.capabilities)
                or self._normalize(context.intent.action_type) in item.families
            )
            and self._predicate_matches(item, context)
        ]
        if specific:
            return self._select(specific, context, "capability_or_family")

        fallback = [
            item
            for item in self._registrations
            if item.default and self._predicate_matches(item, context)
        ]
        if fallback:
            return self._select(fallback, context, "default")

        raise PluginNotFoundError(
            f"no plugin for action={context.intent.action_type!r} "
            f"card={context.card_id!r}"
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return value.strip().lower()

    def _predicate_matches(
        self, registration: PluginRegistration, context: PlanContext
    ) -> bool:
        return registration.predicate is None or bool(registration.predicate(context))

    def _select(
        self,
        candidates: list[PluginRegistration],
        context: PlanContext,
        tier: str,
    ) -> PlanPlugin:
        if len(candidates) != 1:
            ids = ", ".join(sorted(item.plugin.plugin_id for item in candidates))
            raise PluginConflictError(
                f"ambiguous {tier} plugins for action={context.intent.action_type!r}: {ids}"
            )
        return candidates[0].plugin


__all__ = [
    "DuplicatePluginRegistration",
    "PluginConflictError",
    "PluginNotFoundError",
    "PluginPredicate",
    "PluginRegistration",
    "PluginRegistry",
    "PluginRegistryError",
]
