# R03 Immutable Domain Contracts And Pure Rules

## Task ID

R03

## Goal

Create the minimum pure domain package required by the one-game MVP, with no I/O or infrastructure dependencies.

## Scope

- Add immutable `GameSnapshot`, `EntityRef`, `PlayerView`, and `ChoiceView`.
- Add `CardDefinition` and `CardCapabilitySet`.
- Add `ActionIntent`, `ActionPlan`, `PlanStep`, and declarative conditions.
- Add domain validation/postcondition result types.
- Add pure `TargetResolver`, `PlanRuleValidator`, and `PostconditionEvaluator` minimal implementations.
- Add domain tests for immutability, serialization, entity lookup, stale snapshot, target validity, and plan validation.

## Non-Goals

- No OCR, capture, Power.log, Win32, Tk, filesystem, network, configuration, or thread code.
- No application ports/use cases.
- No card plugin registry.
- No legacy root imports.

## Inputs

- `docs/interfaces.md`
- `docs/architecture/layers.md`
- `docs/architecture/dependency-rules.md`
- Approved domain schema in the refactor plan.

## Outputs

- `src/hslegend/domain/**`
- `tests/domain/**`
- Domain interface documentation update if exact names drift.

## Interfaces

- Consumes: only Python standard library.
- Produces:
  - `GameSnapshot`, `EntityRef`, `PlayerView`, `ChoiceView`
  - `CardDefinition`, `CardCapabilitySet`
  - `ActionIntent`, `ActionPlan`, `PlanStep`
  - `ValidationResult`, `PostconditionResult`, `ActionVerdict`
  - `TargetResolver`, `PlanRuleValidator`, `PostconditionEvaluator`
- No public product API change.

## Dependencies And Impact

- Depends on: R02C5.
- Impacted modules: new `src/hslegend/domain` only.
- Existing runtime behavior: unchanged.

## Allowed Files

- `src/hslegend/__init__.py`
- `src/hslegend/domain/**`
- `tests/domain/**`
- `tests/test_architecture_domain.py`
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/agent/**`
- `docs/tasks/R03-*.md`
- `TOTAL_TASK.md`

## Forbidden Files

- All legacy root modules.
- `web_ui.py`, `FSM_action.py`, `manual_controller.py`, `click.py`, OCR/capture code.
- Application, adapters, bootstrap, plugins.
- `pyproject.toml` and `uv.lock` unless a test-only package issue is proven.

## Acceptance

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python -m unittest discover -s tests/domain -t . -v
uv run python tools/run_headless_tests.py
```

- Domain tests pass.
- Domain modules import only stdlib and `hslegend.domain`.
- No import-time I/O or thread creation.
- Snapshot and plan objects are frozen/immutable.
- JSON/primitive serialization is deterministic and tested.
- Stale snapshot revision and invalid target cases are rejected.
- Headless suite remains green.

## Risk And Rollback

- Risk: over-modeling before adapters exist.
- Mitigation: only implement fields and rules needed by the MVP contracts.
- Rollback: delete the new domain package/tests; legacy code is untouched.

## Git Requirement

```text
feat(R03): add immutable domain contracts and pure rules
```

## Report Paths

- Implementer: `docs/agent/reports/R03-implementer.md`
- Reviewer: `docs/agent/reviews/R03-review.md`
