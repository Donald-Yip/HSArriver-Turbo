# R18A Choice Panel Turn-Start Gate

## Task ID

R18A

## Goal

回合开始的触发效果弹出「进行选择」面板（实测：克苏恩仪式/克苏恩的子嗣的四选一）
盖住右侧「结束回合」按钮时，脚本不再整回合卡在「尚未检测到结束回合」，而是按盒子
推荐点掉选项后继续出牌。

## Scope

- `run_automatic_battle_step` 的「按钮未出现」旁路（modal bypass）按日志判断我方
  「进行选择」面板是否开着（`discover_choice_count` 或 `general_choice_pending`）。
- 面板开着时，盒子AI 除原有 `USE_ABILITY` 外再放行 `CHOOSE_DISCOVER`（盒子此时
  写的是「选择我方N号位卡牌」）与 `ASSEMBLE_CHOICE`（「拼装 左一 中二」）。
- 普通出牌/攻击/结束回合仍然必须等右侧按钮出现，避免回合开始动画期误点。
- 面板盖住按钮期间的诊断文案改为「「进行选择」面板盖住了右侧按钮，等待可执行的
  选项推荐。」，与普通等待按钮区分。

## Non-Goals

- 不改点击坐标、不新增面板布局（4 选项坐标已在 `click.DISCOVER_CARD_X`）。
- 不改盒子推荐解析/适配（「选择我方N号位卡牌」→ CHOOSE_DISCOVER 链路已有测试）。
- 不改 `log_state.py` 的选项解析、不改本地策略搜索。
- 不处理盒子完全不给选项推荐时的兜底点击（宁可等人，不随机点）。

## Inputs

- 实测 Power.log：`Hearthstone_2026_10_30_21_44_40` 中 `GameState.DebugPrintEntityChoices()
  - id=4 ... ChoiceType=GENERAL` + 4 个 `Entities[i]`（克苏恩的愤怒/领域/凝视/仆从），
  `ChoiceCardMgr.WaitThenShowChoices() - id=4 BEGIN`。
- 同局 21:48:44 截图：面板盖住右侧按钮，盒子面板写「打法参考A / 选择我方2号位卡牌 /
  克苏恩的领域」。
- 基分支 `feature/R18-local-strategy-hybrid` @ `47f2ee4`。

## Outputs

- `FSM_action.py`：`_choice_panel_open()`、旁路放行集合、诊断文案。
- `tests/test_turn_start_readiness.py`：面板开/半开（pending）/关、盒子AI 与本地策略
  四组放行集合断言，以及诊断文案断言。

## Interfaces

- Consumes: `log_state.discover_choice_count`、`log_state.general_choice_pending`、
  `ActionKind`、`RecommendationFlow.run_player_turn_step(allowed_action_kinds=...)`。
- Produces: 无新接口（仅旁路判定与文案）。

## Dependencies And Impact

- Depends on: R17/R18 的推荐流程与旁路。
- Impacted modules: `FSM_action.run_automatic_battle_step` 的回合开始分支。
- Public API impact: 无。

## Allowed Files

- `FSM_action.py`
- `tests/test_turn_start_readiness.py`
- `docs/tasks/R18A-choice-panel-gate.md`
- `README.md`

## Forbidden Files

- `log_state.py`
- `click.py`
- `manual_controller.py`
- `src/hslegend/**`
- `web_ui.py`

## Acceptance

```powershell
uv run python -m unittest tests.test_turn_start_readiness -v
uv run python tools/run_headless_tests.py
uv run python tools/check_architecture.py
uv run python tools/check_card_rules.py --check
uv run python tools/check_task_scope.py --task R18A
```

## Risk And Rollback

- Risk: 面板判定依赖日志；日志延迟到 BEGIN 之后才有选项时，靠 `general_choice_pending`
  兜住；若面板其实没开而盒子写了「选择我方N号位卡牌」，适配层会因
  `discover_choice_count` 缺失走 `_adapt_choice_target_only` 拒绝，不会乱点。
- Rollback: 还原 `FSM_action.py` 的旁路为「盒子AI 只放行 USE_ABILITY」。

## Garbage-Code Check

- In-scope garbage to refactor: 无（旁路注释同步更新为中文说明）。
- Out-of-scope discoveries to register: 拼装/回溯面板的端到端实机验证仍待真机复测。

## Git Requirement

One commit:

```text
fix(turn): let the choice panel through the turn-start gate
```

## Report Paths

- Implementer: `docs/agent/reports/R18A-implementer.md`
- Reviewer: `docs/agent/reviews/R18A-review.md`
