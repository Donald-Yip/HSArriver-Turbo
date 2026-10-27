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

### ActionIntent / ActionPlan

`ActionIntent` describes the semantic intent. `ActionPlan` is an ordered list of declarative `PlanStep` values with preconditions, postconditions, and timeout. It must not contain coordinates or executable callbacks.

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
- `ExecutionRecord`: correlation/session/game/intent/plan/action IDs, before/after snapshot IDs, execution status, step results, error code, elapsed time, and evidence IDs.
- `JournalAppendResult`: durable journal record ID, durability flag, duplicate flag, and evidence IDs.
- `OneGameCommand`: command ID, deck ID, mode, and an optional maximum turn count that must be an exact positive `int` when provided.
- `OneGameResult`: command/session IDs, terminal run status, reason code, game ID, journal record ID, and evidence IDs.

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

`get_state() -> LobbyState`. Lobby phases are normalized to main menu, deck select, matching, in game, result, or unknown.

### ActionDispatchPort

`dispatch(step: PlanStep, snapshot: GameSnapshot) -> DispatchResult`. It reports only whether the input was sent, rejected, or failed; it does not decide the overall business result.

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
through `ActionDispatchPort`, waits for a later `log_revision`, and evaluates
step/plan postconditions with the domain `PostconditionEvaluator`.
`plan.timeout_seconds` is a total execution budget measured by the injected
monotonic clock; each wait is clamped to the smaller of the step timeout and
remaining plan budget.

It appends exactly one `ExecutionRecord` to `RunJournalPort` per ledger attempt,
including confirmed, unconfirmed, rejected, failed, and cancelled outcomes. The
`ActionGate` lease is acquired before `ExecutionLedger.begin()` and is held
through ledger completion, record construction, and the journal append; release
happens only in the outer `finally`. The ledger is completed exactly once before
journaling; a journal exception propagates unchanged and does not trigger a
second completion. StopToken and remaining plan budget are checked after step
validation and immediately before dispatch. Cancellation before any dispatch
records `cancelled`; any exception, postcondition failure, timeout, or
cancellation after dispatch records `executed_unconfirmed` with the original
reason code, so immediate replay remains blocked.

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
- Recognizer output may be a normalized phase string, `LobbyPhase`, `LobbyState`,
  or a mapping/object with `phase`/`state` plus optional confidence/evidence.
- Unknown aliases fail closed to `LobbyPhase.UNKNOWN` with default confidence
  `0.0`; invalid numeric fields raise `LobbyStateAdapterError`.

### LegacyActionDispatch

- Constructor: semantic-name mapping to `handler(step, snapshot)` callables plus
  an optional deterministic dispatch ID factory.
- A handler may return `DispatchResult`, a mapping with dispatch fields, or
  `None` (legacy success). Handler exceptions become failed dispatch results.
- Unknown action names return `DispatchResult.rejected("unknown_action")` before
  a handler is invoked. No adapter path clicks directly or stores coordinates.

## Compatibility

Legacy public HTTP/CLI/MCP/config/log behavior remains unchanged until the explicit cutover task. New internal contracts may evolve only through the interface document and contract tests.
