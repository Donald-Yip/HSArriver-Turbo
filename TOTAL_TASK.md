# HSLegendArriver Total Task Book

> 必须时刻保持规划。每次修改前先判断：这个修改是否只是浮于表面？有没有更为根本的问题？优先解决根本问题，保持模块隔离、最小可用、接口清晰、小步验证。

## Mission

在不改变现有外部行为的前提下，建立 domain/application/adapters 分层架构，并完成一个真实单局闭环：

```text
主菜单 -> 进入传统对战 -> 选择卡组 -> 换牌 -> 自动对局 -> 结算 -> 停止
```

## Current Phase

R05: plan execution pipeline fix round 2 complete; ready for re-review.

## Branch And Environment

- Integration branch: `develop`
- Current feature branch: `feature/R05-plan-execution`, based on `develop` at `637639e`
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
| R07 | planned | External-system adapters | R06 |
| R08 | planned | Enter-battle vertical slice | R07 |
| R09 | planned | Mulligan vertical slice | R08 |
| R10 | planned | Turn loop and reconciliation | R09 |
| R11 | planned | Settlement and stop | R10 |
| R12 | planned | CLI and fake one-game E2E | R11 |
| R13 | planned | Native one-game and main cutover | R12 |
| R14 | planned | Static plugin registry expansion | R05 |
| R15 | planned | Control-plane security | R13 |
| R16 | planned | Legacy migration and cleanup | R15 |

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

Fresh R05 reviewer verifies fix round 2 and the complete staged file set; Controller commits only after domain, application, plugins, headless, compile, architecture, and scope gates are confirmed.
