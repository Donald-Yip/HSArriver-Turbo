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

## Application Ports

### ConfigRepositoryPort

Input: none. Output: typed immutable application config snapshot.

### CardCatalogPort

Input: `card_id`. Output: `CardDefinition` and `CardCapabilitySet`.

### RecommendationSourcePort

Input: `GameSnapshot` and recommendation mode (`mulligan` or `turn`).
Output: `RecommendationObservation` with normalized instruction, confidence, and evidence ID. It must not return click objects.

### GameStateSourcePort

Output: `GameObservation = { snapshot: GameSnapshot, source_confidence }`.
The authoritative revision is `snapshot.log_revision`. It must not be duplicated in the wrapper.
Must support waiting for a revision change with timeout.

### LobbyStatePort

Output: lobby state such as main menu, deck select, matching, or unknown.

### ActionDispatchPort

Input: one `PlanStep` and the current `GameSnapshot`.
Output: `DispatchResult` describing whether the input was sent, rejected, or failed. It must not decide the overall business result.

### RunJournalPort

Input: typed `RunRecord` / `ExecutionRecord`. Output: durable append result.

### ArtifactStorePort

Input: frame/evidence reference. Output: durable artifact reference.

## Runtime Contracts

### StopToken

- `cancel(reason)`
- `is_cancelled()`
- `wait(timeout)`
- `raise_if_cancelled()`

All waits and side effects check the same token.

### ActionGate

- `acquire(owner, operation_id) -> ActionLease`
- `release(lease)`

Only one action plan may hold execution ownership at a time.

### RunContext

Owns one run's session ID, StopToken, ActionGate, typed config snapshot, plugin registry, and correlation context.

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
