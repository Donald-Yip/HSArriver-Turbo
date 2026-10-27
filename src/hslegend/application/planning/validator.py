"""Application wrapper around the pure domain plan validator."""

from __future__ import annotations

from dataclasses import replace

from hslegend.application.ports import CardCatalogPort
from hslegend.domain import (
    ActionPlan,
    CardDefinition,
    GameSnapshot,
    PlanRuleValidator,
    PlanStep,
    ValidationResult,
)


class PlanValidator:
    """Attach catalog metadata to domain validation without side effects."""

    def __init__(
        self,
        card_catalog: CardCatalogPort,
        rules: PlanRuleValidator | None = None,
    ) -> None:
        self._card_catalog = card_catalog
        self._rules = rules or PlanRuleValidator()

    @property
    def rules(self) -> PlanRuleValidator:
        return self._rules

    def validate(self, plan: ActionPlan, snapshot: GameSnapshot) -> ValidationResult:
        """Run complete domain validation, including the first step preconditions."""

        if not isinstance(plan, ActionPlan):
            raise TypeError("plan must be an ActionPlan")
        if not isinstance(snapshot, GameSnapshot):
            raise TypeError("snapshot must be a GameSnapshot")
        card = self._card_for(plan)
        if isinstance(card, ValidationResult):
            return card
        return self._rules.validate(plan, snapshot, card=card)

    def validate_plan(
        self, plan: ActionPlan, snapshot: GameSnapshot
    ) -> ValidationResult:
        """Validate plan structure and plan-level conditions.

        Step preconditions are intentionally deferred until immediately before
        that step is dispatched by ``PlanExecutor``.
        """

        if not isinstance(plan, ActionPlan):
            raise TypeError("plan must be an ActionPlan")
        if not isinstance(snapshot, GameSnapshot):
            raise TypeError("snapshot must be a GameSnapshot")
        card = self._card_for(plan)
        if isinstance(card, ValidationResult):
            return card
        structural_plan = replace(
            plan,
            steps=tuple(
                replace(step, preconditions=()) for step in plan.steps
            ),
        )
        return self._rules.validate(structural_plan, snapshot, card=card)

    def validate_step(
        self,
        plan: ActionPlan,
        step: PlanStep,
        snapshot: GameSnapshot,
    ) -> ValidationResult:
        """Validate one step's preconditions against the current snapshot."""

        if not isinstance(plan, ActionPlan):
            raise TypeError("plan must be an ActionPlan")
        if not isinstance(step, PlanStep):
            raise TypeError("step must be a PlanStep")
        if not isinstance(snapshot, GameSnapshot):
            raise TypeError("snapshot must be a GameSnapshot")
        card = self._card_for(plan)
        if isinstance(card, ValidationResult):
            return card
        step_plan = ActionPlan(
            plan_id=f"{plan.plan_id}:{step.step_id}:current",
            snapshot_id=snapshot.snapshot_id,
            snapshot_revision=snapshot.log_revision,
            game_generation=snapshot.game_generation,
            intent=plan.intent,
            steps=(step,),
            timeout_seconds=plan.timeout_seconds,
            schema_version=plan.schema_version,
        )
        return self._rules.validate(step_plan, snapshot, card=card)

    def _card_for(self, plan: ActionPlan) -> CardDefinition | ValidationResult | None:
        if plan.intent.card_id is None:
            return None
        card = self._card_catalog.get_card(plan.intent.card_id)
        if card is None:
            return ValidationResult.reject("card_not_found", plan.intent.card_id)
        return card


__all__ = ["PlanValidator"]
