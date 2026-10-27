# R13 Native One-Game Acceptance And Cutover

## Task ID

R13

## Goal

Add an explicit live-run safety gate and preflight, then obtain user approval for
one bounded native Hearthstone game, verify the real R08-R13A path from the main
menu through RESULT and stop, and cut the stable minimum flow to `main` only if
the controlled acceptance passes.

## Preconditions Requiring User Authorization

Do not start the live phase until the user explicitly authorizes a native run
and provides the controlled deck index. Before the command that can click:

- Hearthstone and HSAng must be running.
- The user must place Hearthstone at a known starting state, preferably main
  menu, and confirm it visually.
- The selected deck index must be supplied by the user.
- The process must run in an elevated context accepted by Hearthstone.
- No other automation/console job may be playing concurrently.
- The operator must be ready to use the existing emergency stop.

The agent must not kill/restart Hearthstone, clear logs, start another game, or
dismiss a result screen unless the user explicitly authorizes that exact action.

## Scope

### Phase A - Safety Gate And Preflight

- Require an explicit `--allow-live` flag for native execution.
- Add `--preflight --json` that inspects readiness without clicking.
- Preflight must report: legacy runtime availability, config identity/log root,
  Power.log discovery, Hearthstone/HSAng process presence, foreground/UI evidence,
  admin status, deck-index validity, and the exact live command that would run.
- `--preflight` must not launch, click, screenshot-save, OCR-infer, or mutate game
  state. Read-only screen recognition may be injected and tested with fakes.
- `--fake` continues to work without `--allow-live` or native imports.
- Missing readiness exits non-zero with machine-readable reasons and no clicks.

### Phase B - Controlled Native Smoke (User Authorized)

- Run exactly one game on the user-selected deck with a bounded timeout/turn cap.
- Preserve the command, result JSON, Power.log revision/generation evidence,
  dispatch journal, and failure taxonomy.
- Stop at `RESULT`; never start a rematch or second game.
- On first unsafe or ambiguous action, stop rather than retry by guesswork.
- Root integration fixes are allowed only in R13 allowed files and must have a
  failing regression test before the fix.

### Phase C - Cutover

- Run all headless, architecture, scope, opt-in, import, CLI and diff gates.
- Complete independent review of the final R13 diff and live evidence.
- Only after user-approved live acceptance, fast-forward the minimal stable flow
  to local `main`; do not push shared branches or force history.
- Record any remaining non-MVP limitations explicitly.

## Non-Goals

- No result-screen dismissal, rematch, second game, continuous farming,
  scheduler, watchdog, surrender, Web or overlay.
- No changes to coordinates, delays, click sequences, thresholds, config schema,
  or persistent data formats.
- No dynamic plugins, hot reload, async rewrite, new dependency, database, or
  public HTTP/MCP changes.
- No weakening of Power.log/action fail-closed behavior.
- No automatic native run in CI.

## Inputs

- R13A commit `32a9e04`.
- Existing native bridge, CLI, fake E2E, and evidence journal.
- User authorization, selected deck index, and confirmed starting state.
- Active Hearthstone/HSAng on the controlled Windows machine.

## Outputs

- CLI `--allow-live` and `--preflight` contracts.
- Read-only preflight adapter and tests.
- Native live acceptance evidence under ignored runtime/log paths.
- R13 implementer/reviewer records and final architecture/acceptance report.
- Minimal stable main branch after successful approval.

## CLI Contract

```text
uv run hslegend-onegame --help
uv run hslegend-onegame --fake --json
uv run hslegend-onegame --preflight --json
uv run hslegend-onegame --preflight --deck-index N --json
uv run hslegend-onegame --allow-live --deck-index N --max-turns M --json
```

- Native execution without `--allow-live` exits 2 with zero adapter/game clicks.
- Preflight exits 0 only when all required readiness checks pass; otherwise exit 1
  with stable reason codes.
- Live result exits 0 only for a completed game; all non-completed outcomes exit
  1 and preserve the typed result when available.
- `--fake` must never require the live gate.

## Live Acceptance Criteria

- Start from the user-confirmed main menu/controlled state.
- Enter traditional battle, select the requested deck, start one match, mulligan,
  play turns, reach authoritative `RESULT`, and stop.
- Exactly one game generation is accepted; a generation change after entry is a
  terminal failure with no further clicks.
- No dispatch occurs after `end_turn` evidence confirms the local turn ended.
- Every dispatch has an execution record and durable journal evidence.
- A second match is never started.
- The process terminates and reports a typed final result.

## Failure And Rollback

- Preflight failure: no click; report missing reasons.
- Native failure before game start: stop; keep main unchanged.
- Ambiguous action/generation/revision: stop; no retry by guesswork.
- Dangerous/live behavior outside expectations: use emergency stop; do not try
  to salvage the run automatically.
- Cutover rollback: keep R13A/fake path on `develop`; revert R13 commit or reset
  local main only after recording the failure. Never force-push.

## Allowed Files

- `src/hslegend/entrypoints/onegame.py`
- `src/hslegend/adapters/legacy/**`
- `src/hslegend/bootstrap/native.py`
- `src/hslegend/bootstrap/__init__.py`
- `tests/adapters/legacy/**`
- `tests/test_one_game_e2e.py`
- `tests/native_live/**`
- `tools/check_architecture.py`
- `docs/interfaces.md`
- `docs/architecture/layers.md`
- `docs/tasks/R13-native-cutover.md`
- `docs/agent/reports/R13-*.md`
- `docs/agent/reviews/R13-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `docs/agent/DECISIONS.md`
- `TOTAL_TASK.md`
- `README.md`

## Forbidden Files

- Root legacy god modules and unrelated legacy modules unless a live failure
  directly proves an adapter contract defect and the controller approves scope.
- R08-R12 application/domain behavior, plugins, pyproject, uv.lock, CI workflows,
  coordinates, delays, click sequences, config data, and external API contracts.
- Any second-game/rematch/result-dismissal implementation.

## Acceptance Gates

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task R13 --json
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
uv run python -m unittest discover -s tests -t .
uv run python tools/run_headless_tests.py --opt-in
uv run hslegend-onegame --help
uv run hslegend-onegame --fake --json
uv run hslegend-onegame --preflight --json
git diff --cached --check
```

The native live command is separate, explicit, and user-authorized.

## Git Requirement

```text
feat(R13): run one native game and cut over the minimum flow
```

## Report Paths

- Implementer: `docs/agent/reports/R13-implementer.md`
- Reviewer: `docs/agent/reviews/R13-review.md`
- Live acceptance: `docs/agent/reports/R13-native-acceptance.md`