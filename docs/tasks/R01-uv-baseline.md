# R01 Uv Environment Baseline

## Task ID

R01

## Goal

Make uv the only supported dependency and execution manager for development, tests, and CI.

## Scope

- Create `pyproject.toml` and `uv.lock`.
- Migrate existing direct dependencies from `requirements.txt`.
- Update execution documentation to uv commands.
- Add a minimal import/version smoke test.
- Remove pip/Conda execution paths after uv verification.

## Non-Goals

- No runtime dependency upgrades.
- No production refactor.
- No test-suite rationalization (R02).

## Inputs

- `requirements.txt`
- Current Python 3.12 compatibility requirements.
- `CORE_PHILOSOPHY.md` and `TOTAL_TASK.md`.

## Outputs

- `pyproject.toml`
- `uv.lock`
- uv smoke test.
- Updated development documentation.

## Interfaces

- Consumes: existing pinned dependency versions.
- Produces: `uv sync`, `uv run`, and locked environment contract.

## Dependencies And Impact

- Depends on: R00.
- Impacted modules: environment, build metadata, docs.
- Public API impact: none.

## Allowed Files

- `pyproject.toml`
- `uv.lock`
- `requirements.txt`
- `README.md`
- `SKILL.md`
- `AGENTS.md`
- `docs/**`
- `tests/test_uv_environment.py`

## Forbidden Files

- Production behavior modules.
- CI workflows (R02).
- User config and logs.

## Acceptance

```powershell
uv sync
uv run python --version
uv run python -c "import cv2, numpy, PIL, win32api, paddleocr"
uv run python -m unittest tests.test_uv_environment -v
```

- Uv-managed environment is reproducible.
- No pip/Conda/system Python remains in active instructions.
- `uv.lock` is committed.

## Risk And Rollback

- Risk: Paddle/OCR wheel incompatibility under uv.
- Rollback: revert R01 and keep existing environment instructions temporarily; do not mix alternate package managers into a partial state.

## Garbage-Code Check

- Remove duplicate dependency/version declarations once uv metadata is authoritative.

## Git Requirement

```text
build(R01): manage dependencies and runtime with uv
```

## Report Paths

- Implementer: `docs/agent/reports/R01-implementer.md`
- Reviewer: `docs/agent/reviews/R01-review.md`
