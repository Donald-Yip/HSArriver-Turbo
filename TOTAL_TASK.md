# HSLegendArriver Total Task Book

> 必须时刻保持规划。每次修改前先判断：这个修改是否只是浮于表面？有没有更为根本的问题？优先解决根本问题，保持模块隔离、最小可用、接口清晰、小步验证。

## Mission

在不改变现有外部行为的前提下，建立 domain/application/adapters 分层架构，并完成一个真实单局闭环：

```text
主菜单 -> 进入传统对战 -> 选择卡组 -> 换牌 -> 自动对局 -> 结算 -> 停止
```

## Current Phase

R16A has synchronized and independently approved original-repository 2026.10.27 changes. R15 UI parity remains in progress pending the administrator scheduled-task rebind.

## Branch And Environment

- Integration branch: `develop`
- Current feature branch: `feature/R16A-upstream-sync-1027`, based on `feature/R15-ui-parity` at `06a85ce`
- Runtime/tooling: uv only
- Canonical remote: `origin` (personal repository)
- Upstream remote: `upstream` (read-only)

## Non-Negotiable Constraints

- Existing HTTP/CLI/MCP/config/log contracts remain unchanged during MVP.
- Coordinates, delays, thresholds, and click sequences must not change.
- New architecture must not import root legacy modules internally.
- Domain layer has no I/O or external-library dependency.
- Demo/fake tests are headless; native Windows tests are opt-in.
- Security-sensitive Web work is blocked until R15.

## Task Status

| ID | Status | Goal | Depends |
|---|---|---|---|
| R00 | done | Governance and branch baseline | - |
| R01 | done | uv environment baseline | R00 |
| R02A | done | Test inventory and classification | R01 |
| R02B | done | Architecture, scope, CI and PR gates | R02A |
| R02C1 | done | Safe click behavior and CLI contract tests | R02B |
| R02C2 | done | Overlay test replacement | R02C1 |
| R02C3 | done | Non-overlay test replacement | R02C2 |
| R02C4 | done | Native and Node isolation | R02C3 |
| R02C5 | done | Headless finalization | R02C4 |
| R03 | done | Domain contracts | R02C5 |
| R04 | done | Application ports and runtime controls | R03 |
| R05 | done | Plan and execution pipeline | R04 |
| R06 | done | Data adapters | R05 |
| R07 | done | External-system adapters | R06 |
| R08 | done | Enter-battle vertical slice | R07 |
| R02D | done | Test portfolio audit and cleanup ledger | R08 |
| R02E | done | Test portfolio cleanup execution | R02D |
| R09 | done | Mulligan vertical slice | R02E |
| R10 | done | Turn loop and reconciliation | R09 |
| R11 | done | Settlement and one-game stop | R10 |
| R12 | done | CLI and fake one-game E2E | R11 |
| R13A | done | Native anti-corruption adapter bridge | R12 |
| R13 | in_progress | Native one-game smoke and main cutover | R13A |
| R14 | planned | Static plugin registry expansion | R05 |
| R15 | in_progress | Full-feature UI parity and reliable startup | R13A |
| R16A | done | Sync original repository 2026.10.27 update | R15 |
| R16 | planned | Legacy migration and cleanup | R15 |
| R17 | in_progress | Layered recommendation semantics | R16A |

## Key Interfaces

- Domain: `GameSnapshot`, `EntityRef`, `CardDefinition`, `ActionIntent`, `ActionPlan`, `ValidationResult`, `PostconditionResult`.
- Application: `ConfigRepositoryPort`, `CardCatalogPort`, `RecommendationSourcePort`, `GameStateSourcePort`, `LobbyStatePort`, `ActionDispatchPort`, `RunJournalPort`, `ArtifactStorePort`.
- Application/runtime: `ExecutionRecord`, `RunContext`, `StopToken`, `ActionGate`.
- Plugins: `PlanPlugin` returning declarative `ActionPlan` only.
- Composition root: `src/hslegend/bootstrap/container.py`.

Details: `docs/interfaces.md`.

## Risks

- uv/Paddle/OCR platform compatibility.
- Windows-only timing and window/foreground behavior.
- Action duplication after ambiguous execution.
- Config/card data shadow state.
- Legacy imports leaking into the new architecture.
- Scope drift during long-running subagent tasks.
- Native tests are unavailable in ordinary headless CI.

## Required Gates

R00 gate: required files exist, task board JSON parses, staged diff check passes, and no production files change.

R01 gate:

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python --version
uv run python -m unittest tests.test_uv_environment -v
```

R02+ gates:

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task R03
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
```

R12/R13 gate adds:

```powershell
uv run hslegend-onegame --help
```

Native gate is explicit and never part of the default suite:

```powershell
uv run python tools/run_headless_tests.py --opt-in
```

## Decisions

See `docs/agent/DECISIONS.md`.

## Next Action

Commit R16A and report the concrete optimizations over the original 2026.10.27 tree. The remaining UI host action is the administrator rebind of the highest-privilege scheduled task, followed by one clean startup verification.
