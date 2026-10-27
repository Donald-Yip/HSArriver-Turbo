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

## Plugin Contract

`PlanPlugin` is the application-level extension contract. Plugins receive a read-only `PlanContext` and return a declarative `ActionPlan`. They cannot click, read files, access configuration globals, create threads, use the network, or import adapters.

## Compatibility

Legacy public HTTP/CLI/MCP/config/log behavior remains unchanged until the explicit cutover task. New internal contracts may evolve only through the interface document and contract tests.
