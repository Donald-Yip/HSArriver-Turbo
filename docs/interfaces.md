# Internal Interfaces

This document defines the only supported cross-layer contracts for the new MVP architecture.

## Dependency Direction

```text
domain <- application <- adapters
             ^
          plugins
bootstrap wires all implementations
entrypoints call bootstrap/application
```

Forbidden imports:

- `domain` must not import application, adapters, bootstrap, legacy root modules, Win32, Tk, PaddleOCR, or filesystem code.
- `application` must not import adapters, bootstrap, entrypoints, `web_ui`, `FSM_action`, `manual_controller`, or `click`.
- `adapters` must not import use cases, bootstrap, or entrypoints.
- `plugins` must not import adapters, bootstrap, runtime, or perform I/O.

## Domain Contracts

### GameSnapshot

Immutable game observation. No pixels, OCR text, coordinates, or configuration.

Required fields:

- `schema_version`, `snapshot_id`, `log_revision`, `game_generation`, `observed_at`
- `turn_number`, `is_my_turn`, `is_end`
- `my_player`, `opponent`, `choices`

### EntityRef

Stable semantic identity:

- `entity_id`, `card_id`, `side`, `zone`, `zone_pos`

### CardDefinition / CardCapabilitySet

`CardDefinition` contains static metadata only. `CardCapabilitySet` contains normalized booleans for targetable, hand_target, board_target, choose_one, discover, starship, location, trade, and timeline.

### Recommendation Semantics

`ActionKind`, `SlotRef`, `ProposedAction`, and `ValidationResult` are pure domain
contracts. `CardDefinition` extends static metadata with `TargetScope`,
`target_card_types`, `choose_one_options`, and `ability_panel`. Code must not
key behavior on concrete card IDs; measured facts that cannot be derived from
`cards.json` belong in `rules/card_rules.json` and are validated by the card
adapter at startup.

Application recommendation contracts are `AdaptedAction`,
`RecommendationContext`, `RecommendationAdapterPort`, and `CardSemanticsPort`.
The application flow owns retry/evidence progression and may only depend on
injected ports. Recommendation failures always remain retryable; turn deadlines,
explicit stop controls, and liveness checks are the only loop terminators. The adapter layer owns `cards.json` loading,
rule-pack validation, recommendation parsing, recommendation adaptation, OCR
evidence DTOs, and legacy click-action DTOs. Root modules under `src/game_state`
and `src/parser` are compatibility facades only.

### ActionIntent / ActionPlan

`ActionIntent` describes the semantic intent. It carries an optional zero-based `destination` placement index; the R10 parser only sets it for `放置于我方N号位` on a play-card instruction. `ActionPlan` is an ordered list of declarative `PlanStep` values with preconditions, postconditions, timeout, `wait_for_change`, and an optional `confirmation_required` plan flag. A non-waiting step is dispatched sequentially but does not poll for a log revision or evaluate postconditions. The final step must wait; plans with `confirmation_required=True` must also carry a non-empty final step or plan postcondition. Plans must not contain coordinates or executable callbacks.

### Domain Results

- `ValidationResult`: accepted/rejected plus stable reason code.
- `PostconditionResult`: confirmed/unconfirmed/failed plus evidence references.
- `ActionVerdict`: domain-level action verdict.

## Application DTOs

All application DTOs are frozen slots dataclasses, normalize collections to tuples, and serialize deterministically through `to_primitive()` / `to_json()`.

- `ConfigSnapshot`: canonical immutable config values plus schema version; nested non-finite floats are rejected at construction.
- `RecommendationObservation`: recommendation mode, normalized instruction, confidence, evidence ID, and source snapshot ID.
- `GameObservation`: `GameSnapshot` plus source confidence; the authoritative revision remains `snapshot.log_revision`.
- `LobbyState`: normalized lobby phase, observation time, confidence, and evidence ID.
- `DispatchResult`: `sent`, `rejected`, or `failed`; this is not the overall business result.
- `ExecutionRecord`: correlation/session/game/intent/plan/action IDs, before/after snapshot IDs, execution status, step results, error code, elapsed time, evidence IDs, journal metadata (ID, durable, duplicate, journal evidence), and `release_error`.
- `JournalAppendResult`: durable journal record ID, durability flag, duplicate flag, and evidence IDs.
- `OneGameCommand`: command ID, deck index, total deadline, child-operation budgets, maximum turn slices, actions per turn, and poll interval.
- `OneGameResult`: command/session IDs, terminal status/reason, last lobby phase, turn/action counts, ordered unique evidence, and the exact child results.

## Application Ports

### ConfigRepositoryPort

`load() -> ConfigSnapshot`. The adapter owns environment-specific loading; the application receives only canonical immutable values.

### CardCatalogPort

`get_card(card_id) -> CardDefinition | None`. `CardDefinition.capabilities` carries the normalized `CardCapabilitySet`.

### RecommendationSourcePort

`recommend(snapshot: GameSnapshot, mode: RecommendationMode) -> RecommendationObservation`. Modes are `mulligan` and `turn`. Results must not contain click objects, coordinates, or callbacks.

### GameStateSourcePort

- `current() -> GameObservation`
- `wait_for_revision_change(after_revision, timeout_seconds) -> GameObservation | None`

The authoritative revision is `snapshot.log_revision`. `None` means the timeout elapsed without a later revision.

### LobbyStatePort

`get_state(*, timeout_seconds: float) -> LobbyState`. The adapter owns bounded recognition for the supplied remaining budget; a `TimeoutError` means the observation could not be produced before that budget expired. Lobby phases are normalized to main menu, deck select, matching, in game, result, or unknown.

### ActionDispatchPort

`dispatch(step: PlanStep, snapshot: GameSnapshot, *, timeout_seconds: float) -> DispatchResult`. The adapter owns bounded dispatch for the supplied remaining budget; a `TimeoutError` means the transport did not complete before that budget expired. It reports only whether the input was sent, rejected, or failed; it does not decide the overall business result.

### RunJournalPort

`append(record: ExecutionRecord) -> JournalAppendResult`. The adapter is responsible for durable storage.

### ArtifactStorePort

`store(source_ref, *, kind="evidence") -> str`. The returned string is the durable artifact reference.

## Runtime Contracts

### StopToken

- `cancel(reason)` is thread-safe and idempotent; the first reason wins exactly once under concurrent calls.
- `is_cancelled()`
- `wait(timeout) -> bool` returns `True` on cancellation and `False` on timeout.
- `raise_if_cancelled()` raises `OperationCancelled`.

A synchronization lock protects cancellation state. Waiters are cooperative and poll the token without creating application threads or using I/O devices.

### ActionGate

- `acquire(owner, operation_id, *, timeout_seconds=None, now=None) -> ActionLease`
- `release(lease)`
- `reap_expired(now) -> bool`
- `current_lease(*, now=None) -> ActionLease | None`

A reentrant synchronization lock protects all gate state. Only one action plan may hold execution ownership at a time. A held gate rejects a second acquire. Release requires the exact live lease; wrong owners raise `WrongLeaseOwner`, stale/forged leases raise `StaleActionLease`, and releasing without an active lease raises `NoActiveLease`. Expired leases are reaped before a new acquisition or explicitly through `reap_expired`.

### RunContext

Owns one run's session ID, StopToken, ActionGate, `ConfigSnapshot`, correlation ID, and an immutable plugin-registry placeholder. Concrete port/service wiring belongs to bootstrap in later tasks.

### ExecutionRecord

Required fields:

- correlation/session/game/intent/plan/action IDs
- before/after snapshot IDs
- status
- step results
- error code
- elapsed time
- evidence IDs

Allowed status values: `not_executed`, `executed_confirmed`, `executed_unconfirmed`, `rejected`, `failed`, `cancelled`.

## Planning And Execution Contracts

### PlanPlugin / PlanContext

`PlanPlugin` is the application-level extension contract. It exposes a stable
`plugin_id` and `build(context: PlanContext) -> ActionPlan`. `PlanContext`
contains the immutable `GameSnapshot`, requested `ActionIntent`, and optional
catalog `CardDefinition`; it never contains clicks, coordinates, callbacks, or
configuration globals. Plugins cannot perform I/O, create threads, use the
network, or import adapters.

### PluginRegistry

`PluginRegistry` is a static, explicit registry. Resolution priority is:

1. exact card ID;
2. capability or action family;
3. default.

All matches at the highest matching priority are considered. If more than one
plugin remains, resolution raises `PluginConflictError`; no insertion-order
tie-break exists. `build_default_registry()` registers the MVP plugins for
mulligan, play card, targeted card, attack, hero power, location, and end turn.

### PlanBuilder / PlanValidator

`PlanBuilder` resolves exactly one plugin through a `CardCatalogPort`, passes a
read-only `PlanContext`, and returns the plugin's declarative `ActionPlan`.
`PlanValidator` supplies the catalog card definition to the pure domain
`PlanRuleValidator`; a named card absent from the catalog is rejected with
`card_not_found`. `validate_plan()` checks plan structure and plan-level
preconditions, while `validate_step()` checks one step's preconditions against
the current snapshot immediately before that step is dispatched.

### ExecutionLedger

`ExecutionLedger` tracks immutable `LedgerAttempt` values per stable action key.
`NOT_EXECUTED` means in flight. A confirmed action is never replayed. An
`EXECUTED_UNCONFIRMED` action requires explicit
`authorize_retry(..., evidence_id=...)` before another attempt. Failed,
rejected, and cancelled attempts may be retried within `max_attempts`. An
in-flight `NOT_EXECUTED` attempt rejects another `begin()` with
`InFlightAttemptError`. Archived actions cannot be started.

### PlanExecutor

`PlanExecutor` acquires one `ActionGate` lease, reads the current
`GameStateSourcePort` observation, validates plan structure and each step's
preconditions against the current snapshot, dispatches each approved step
through `ActionDispatchPort`, and waits for a later `log_revision` only after a
step with `wait_for_change=True`. Non-waiting steps dispatch sequentially and
cannot carry postconditions. The final step must be a waiting step; a plan whose
final step is non-waiting fails closed with `confirmation_step_required`. When
`confirmation_required=True`, a non-empty final step/plan postcondition is
mandatory and missing confirmation fails with
`confirmation_postcondition_required`. Waiting steps evaluate their
postconditions, and the final waiting step also evaluates plan-level
postconditions, with the domain `PostconditionEvaluator`.
`plan.timeout_seconds` is a total execution budget measured by the injected
monotonic clock; each wait and dispatch receives the remaining plan budget, and
waits are clamped to the smaller of the step timeout and remaining plan budget.

It appends exactly one `ExecutionRecord` to `RunJournalPort` per ledger attempt,
including confirmed, unconfirmed, rejected, failed, and cancelled outcomes. The
`ActionGate` lease is acquired before `ExecutionLedger.begin()` and is held
through ledger completion, record construction, and the journal append; release
happens only in the outer `finally`. The ledger is completed exactly once before
journaling. Unexpected executor failures are wrapped as `PlanExecutorError`
with the eventual release result; ledger exceptions retain their domain type and
receive a `release_error` attribute. A failure does not trigger a second ledger
completion. StopToken and remaining plan budget are checked after step
validation and immediately before dispatch. Cancellation before any dispatch
records `cancelled`; any exception, postcondition failure, timeout, or
cancellation after dispatch records `executed_unconfirmed` with the original
reason code, so immediate replay remains blocked.

### EnterBattleUseCase

`EnterBattleUseCase` is the first end-to-end application slice. It is constructed
only from `LobbyStatePort`, `ActionDispatchPort`, `ActionGate`, `StopToken`, and
an injected monotonic `clock` plus deterministic `sleep`. Semantic lobby actions
are `enter_traditional`, `select_deck(index)`, and `start_match`. Every lobby
read and dispatch receives the remaining total budget as `timeout_seconds`; the
use case translates `TimeoutError` into a terminal `TIMED_OUT` result instead of
making an unbounded call.

Execution order is fixed:

1. Acquire the action gate; contention returns `GATE_BUSY` with
   `action_gate_busy` before any lobby read.
2. Poll for `MAIN_MENU` until the total command deadline.
3. Dispatch `enter_traditional`.
4. Poll for `DECK_SELECT`.
5. Dispatch `select_deck` with the command's `choice_index`.
6. Optionally dispatch `start_match`.
7. Return a typed `EnterBattleResult`.

`UNKNOWN` lobby observations fail immediately with `lobby_unknown` and no
dispatch. Dispatch rejection, failure, exception, invalid output, cancellation,
lobby-read failure, clock failure, and timeouts stop the sequence. No action is
dispatched after cancellation or after the total deadline. If cancellation is
observed after a dispatch result has been returned, that dispatch and its
evidence remain in the cancelled result with `detail_code =
"cancelled_after_dispatch"` and `failed_action` set to the affected action.
Gate release failures are reported in `release_error` while preserving the
primary terminal status and reason. Queueing is treated as command success
immediately after a sent `start_match`; the use case does not claim an observed
`MATCHING` or `IN_GAME` state without another lobby read.

`bootstrap.build_container(lobby, dispatch, ...)` is a composition helper that
constructs this use case from injected adapters and optional runtime controls. It
does not import or construct legacy root-module adapters.

### MulliganUseCase

`MulliganUseCase` consumes one `RecommendationObservation` for the current
snapshot, validates it against a fresh `GameSnapshot`, builds one pinned
`ActionPlan`, and executes it through the injected `PlanExecutor` and
`ExecutionLedger`. `MulliganCommand(command_id, timeout_seconds)` and
`MulliganResult(...)` are immutable; selected indexes are sorted unique
zero-based hand indexes.

Instruction parsing is exact and deterministic: replacement lines match
`替换N号位卡牌`, while `保留全部卡牌` selects no cards. Legacy bounds are
preserved: a three-card hand exposes slots 1-3 and a five-card hand exposes slots
1-4. Unknown/empty/mixed instructions, an unsupported hand size, an
out-of-range slot, a missing `mulligan`/`mulligan_choice` choice surface, wrong
recommendation mode, a recommendation tied to another snapshot, or a snapshot
that changes during validation is rejected before any dispatch.

Replacement steps carry the exact hand `EntityRef` and use
`wait_for_change=False`; the final confirm step uses `wait_for_change=True` with
a `snapshot_changed` postcondition. Keep-all dispatches only confirm. The use
case maps executor outcomes to `completed`, `executed_unconfirmed`, `rejected`,
`failed`, `cancelled`, or `gate_busy`. An `executed_unconfirmed` outcome is
terminal and is never replayed automatically; the ledger blocks an immediate
second execution unless a caller explicitly authorizes retry outside this use
case. The journal record includes the recommendation evidence plus dispatch and
snapshot evidence. `bootstrap.build_mulligan_use_case(...)` wires the service
from injected ports without importing legacy modules.

### PlayTurnUseCase

`TurnIntentParser` is a pure deterministic parser over one immutable `GameSnapshot`. It accepts only the R10 MVP lines: `结束回合`, `使用英雄技能`, `打出N号位随从|法术|武器|地标|英雄`, `操作N号位随从攻击`, `操作我方英雄攻击`, `操作N号位地标`, target lines `目标是对方/敌方/我方/己方N号位[随从|地标|武器|英雄]`, hero target lines, and `放置于我方N号位`. Text slots and destinations are one-based; resolved hand/board slots are ordered by `(zone_pos, entity_id)` and the intent stores zero-based `choice_index`/`destination`. Unknown lines, multiple actions, duplicate targets/destinations, invalid slots, missing attack targets, ambiguous `zone_pos`, malformed side/zone ownership, unsupported or missing target kinds, and missing heroes/weapons fail closed with a stable `TurnIntentError.reason_code`. Target-only/two-phase recommendations are deferred to R13/R14 dispatch integration; R10 accepts only action+target in the same recommendation.

`PlayTurnUseCase` consumes one fresh TURN recommendation against the current snapshot, rejects wrong mode or a recommendation for another snapshot, re-reads the snapshot before dispatch, and builds every action through the injected `PlanBuilder`. The default bootstrap registry supplies the turn-family plugins. The use case executes exactly one plan at a time through the shared `PlanExecutor`, `ActionGate`, ledger, and journal, and never starts the next action after `executed_unconfirmed`, `rejected`, `failed`, `cancelled`, `gate_busy`, or a missing action gate lease. `PlayTurnCommand(command_id, max_actions, timeout_seconds)` bounds the turn; the total deadline is propagated by clamping each plan's execution budget, and the executor remains the sole dispatch/confirmation authority. The loop stops as `cancelled` when cancellation wins after a confirmed action, as `ended` after a confirmed `end_turn` or a post-action snapshot showing `is_my_turn=False`, as `completed` when `max_actions` is reached, and as a typed terminal failure otherwise. `PlayTurnResult` preserves the action count, terminal reason, deduplicated evidence IDs, per-action `ExecutionRecord` values, `ended_turn`, and the optional release error. A confirmed action may only be followed by another action after a strictly later revision in the same game generation; stale post-action state, a generation change, or a release failure stops the loop before another dispatch. An unconfirmed identical action is stopped by the ledger, not retried.

`bootstrap.build_turn_use_case(...)` wires the service from injected ports, a supplied/default `PlanBuilder`, `PlanValidator`, `ExecutionLedger`, and `PlanExecutor`; it imports the static default plugin registry but no legacy module or adapter.
### FinishBattleUseCase

`FinishBattleUseCase` is observation-only. It polls `LobbyStatePort.get_state(timeout_seconds=remaining)` with an injected clock/sleeper until the authoritative `LobbyPhase.RESULT` is observed. It has no `ActionDispatchPort`, action gate or ledger dependency and cannot click start, continue, rematch or deck selection. `FinishBattleCommand(command_id, timeout_seconds, poll_interval_seconds)` bounds the wait. `FinishBattleResult` reports completed/cancelled/timed_out/failed, last observed phase, unique evidence IDs and elapsed seconds. Adapter, clock, and sleeper failures are typed without leaking exceptions.

A cancellation observed before the start clock is sampled returns `cancelled`
with `elapsed_seconds` exactly `0.0` and does not read the clock.

The command deadline is computed from the injected monotonic clock at command
start. `timed_out` is returned only when the injected clock shows that this
deadline was reached before `RESULT`; an adapter `TimeoutError` observed before
the deadline is a typed `failed` outcome with reason `lobby_state_timeout`, while
the same error observed at or after the deadline is `timed_out` with reason
`timeout`. Generic adapter failures are `lobby_state_error`, invalid observations
are `lobby_state_invalid`, clock failures are `clock_error`, and sleeper failures
are `sleep_error`, all preserving the last observed phase and evidence.

Cancellation is checked before polling and immediately after every completed or
failed `get_state` call, including `TimeoutError`, generic exceptions, and invalid
observations, before that outcome is classified. It is also checked after the
sleeper completes or fails and immediately before terminal `completed` or
`timed_out` results. Therefore cancellation that occurs while a port call or
sleeper is in flight takes precedence over that call's failure and returns
`cancelled` with the last successfully observed phase and evidence.

### OneGameUseCase

`OneGameUseCase` is the application-level coordinator for exactly one game. It composes the injected `EnterBattleUseCase`, `MulliganUseCase`, `PlayTurnUseCase`, and `FinishBattleUseCase`; it does not reinterpret their internal plans or retry their unsafe outcomes. `OneGameCommand` carries the deck index, total deadline, child-operation budgets, maximum turn slices, actions per turn, and poll interval. The total monotonic deadline clamps every child call.

The flow enters traditional battle once, waits for `LobbyPhase.IN_GAME`, runs mulligan at most once, delegates each detected local turn once, and invokes settlement at most once after game end or a directly observed result. `PlayTurnStatus.ENDED/local_turn_ended` waits for a later game-state revision; `PlayTurnStatus.ENDED/game_ended` moves to settlement. `PlayTurnStatus.COMPLETED` is fail-closed as `turn_action_budget_exhausted` rather than silently continuing an unfinished turn. Any cancelled child result makes the game result `cancelled`; any failed, rejected, unconfirmed, or gate-busy result makes it `failed`. No path retries an unsafe action or starts another game.

`OneGameResult` preserves the command/session identity, terminal status and reason, last lobby phase, turn/action counts, ordered unique evidence, and the child `EnterBattleResult`, `MulliganResult`, turn results, and `FinishBattleResult`. The names `OneGameCommand`, `OneGameStatus`, and `OneGameResult` remain exported from `hslegend.application`, but their real definition lives with the one-game use case.

R12 wires a deterministic demo adapter through the real R08-R11 use cases. The CLI contract is:

```text
uv run hslegend-onegame --help
uv run hslegend-onegame --fake --json
```

`--fake` remains the deterministic headless path. Native execution requires the explicit `--allow-live` gate added by R13; omitted consent exits 2 before constructing the native runtime or clicking. A completed game exits 0, other terminal results exit 1, and CLI usage errors exit 2. The entrypoint may import only the standard library, `hslegend.application`, and `hslegend.bootstrap`; adapter/plugin composition belongs in bootstrap.

### Native Adapter Bridge

R13A introduces `hslegend.adapters.legacy` as the sole anti-corruption package
allowed to import stable root legacy and legacy `src.*` systems. It exposes a
`NativePorts` bundle containing only application ports: config, cards, mulligan
and turn recommendation sources, game state, lobby state, action dispatch,
journal, and artifact store. The bundle must not contain use cases, bootstrap
objects, plugins, or entrypoints.

`build_native_ports(...)` lives in the anti-corruption package; bootstrap's
`build_native_one_game()` imports that public factory, constructs the same real
R08-R11 use cases as the fake path, and returns `OneGameUseCase`. Root legacy
imports are forbidden in every other `hslegend` package and in the three legacy
god modules `FSM_action.py`, `manual_controller.py`, and `web_ui.py`.

The Power.log bridge maps mutable legacy `LogState` into immutable
`GameSnapshot`; complete game state is authoritative `RESULT`. The lobby bridge
combines that authoritative state with injected UI recognition. The HSAng bridge
preserves stable multi-frame OCR and then calls the existing recommendation
parser. The action bridge maps only the existing declarative action families to
injected legacy click primitives and rejects unknown actions without blind
clicks. R13A performs no live click or native smoke; R13 owns that controlled
cutover.

The bridge modules are responsibility-scoped: `paths.py` derives machine paths
from legacy config, `powerlog.py` pumps containers and maps immutable snapshots,
`lobby.py` applies authoritative phase precedence, `hsang.py` performs one
multi-frame stable read per request, `input.py` maps pinned intents to injected
click primitives, and `factory.py` returns `NativePorts`. Native legacy modules
and the repository-root `sys.path` entry are loaded only when the native builder
is called; importing bootstrap, the CLI entrypoint, `--help`, or `--fake` does
not load them. A source-tree runtime is discovered from the checkout or current
working directory; `HSLEGEND_LEGACY_ROOT` or an explicit builder override may
select another complete legacy root. Missing runtime components raise
`legacy_runtime_unavailable`.

The HSAng factory uses no OCR header beacon, applies the parser's
`normalize_action_text` normalizer, and uses a mulligan stability key based on
parsed replacement slots with normalized text as fallback. The native action
dispatcher also owns the three EnterBattle families: `enter_traditional` clicks
only recognized `traditional.point` evidence, `select_deck` converts the
zero-based choice index to legacy's one-based index and injects a
runtime-click-backed callback, and `start_match` requires recognized deck-select
evidence before invoking `match_opponent` through the injected runtime click
module. Missing evidence rejects without clicking. Power.log container errors
make prior state non-authoritative for lobby recognition until the next
successful INFO container restores it.

The default CLI selects `build_native_one_game`; `--fake` selects the
deterministic demo builder. Tests inject both builders and never populate the
real game graph.

### Native Live Gate And Preflight

R13 native execution is a separate, explicitly authorized operation:

```text
uv run hslegend-onegame --preflight --json
uv run hslegend-onegame --preflight --deck-index N --json
uv run hslegend-onegame --allow-live --deck-index N --max-turns M --json
```

Native execution without `--allow-live` is a usage error and must not construct
the native runtime or click. `--fake` does not require the live gate.

`--preflight` is read-only. It reports stable readiness fields for legacy runtime,
config identity/log root, Power.log discovery, Hearthstone/HSAng process presence,
foreground/UI evidence, admin status, and deck-index validity. It may use injected
read-only probes but must not launch, click, save screenshots, run OCR inference,
or mutate game state. Preflight exits 0 only when all required checks pass and
otherwise exits 1 with machine-readable reasons.

The live command is run only after explicit user authorization, a user-selected
deck index, and a confirmed safe starting state. It must stop at authoritative
`RESULT` and must never dismiss into or start a second game.
## External Adapter Contracts

R07 adapters are injection-only seams around legacy perception and input. New
architecture modules do not import root legacy modules; bootstrap or tests pass
callables into these constructors.

### HsangRecommendationAdapter

- Constructor: `capture()`, `read(frame)`, `parse(ocr, turn_number, log_revision)`.
- `recommend(snapshot, mode)` returns a `RecommendationObservation`.
- Parsed instruction, confidence, evidence ID, turn, and revision are validated.
- Capture/read/parse failures raise typed `HsangAdapterError` subclasses.

### PowerLogGameStateAdapter

- Constructor: `snapshot_provider() -> GameSnapshot | GameObservation` and
  `revision_provider() -> int`.
- `current()` returns `GameObservation`; injected confidence is used only for a
  raw `GameSnapshot`.
- `wait_for_revision_change(after_revision, timeout_seconds)` polls the revision
  provider, returns a later observation, or returns `None` on timeout or
  cancellation. Cancellation is optional through an injected `stop_token` with
  `is_cancelled()`.
- Provider failures and invalid revisions raise typed `PowerLogAdapterError`
  subclasses.

### LegacyLobbyStateAdapter

- Constructor: `recognizer()`, optional monotonic `clock()`.
- `get_state(timeout_seconds=...)` runs the recognizer on a daemon worker with a
  bounded join. Timeout-aware recognizers also receive the remaining budget.
  `TimeoutError` propagates unchanged without waiting for an ignoring callback;
  other recognizer failures propagate through the worker and become typed adapter
  errors. Two-argument legacy recognizers remain supported.
- Recognizer output may be a normalized phase string, `LobbyPhase`, `LobbyState`,
  or a mapping/object with `phase`/`state` plus optional confidence/evidence.
- Unknown aliases fail closed to `LobbyPhase.UNKNOWN` with default confidence
  `0.0`; invalid numeric fields raise `LobbyStateAdapterError`.

### LegacyActionDispatch

- Constructor: semantic-name mapping to `handler(step, snapshot)` callables plus
  an optional deterministic dispatch ID factory.
- `dispatch(step, snapshot, timeout_seconds=...)` runs the handler on a daemon
  worker with a bounded join. Timeout-aware handlers also receive the remaining
  budget. `TimeoutError` propagates unchanged without waiting for an ignoring
  callback; workers capture handler exceptions for the caller. A timed-out
  dispatch is ambiguous: the application records no automatic retry and stops.
  Legacy two-argument handlers remain supported.
- A handler may return `DispatchResult`, a mapping with dispatch fields, or
  `None` (legacy success).
- Unknown action names return `DispatchResult.rejected("unknown_action")` before
  a handler is invoked. No adapter path clicks directly or stores coordinates.

## Compatibility

Legacy public HTTP/CLI/MCP/config/log behavior remains unchanged until the explicit cutover task. New internal contracts may evolve only through the interface document and contract tests.

### Full Web UI Startup Contract

The complete pre-refactor Web control UI remains the primary operator surface at
`web/index.html` served by `web_ui.py`. Its existing controls, HTTP endpoints,
manual automation, scheduling, overlay, calibration, surrender, delay and log
behavior are preserved.

All launch paths (`web_ui.py`, `hsctl serve start`, elevated launch, and the
highest-privilege scheduled task) force UTF-8 stdio so localized warnings cannot
crash on Chinese Windows. The scheduled-task launcher also sets
`PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`. No UI feature depends on the new
one-game CLI.
