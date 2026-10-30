# R18C Repeat Identical Recommendation

## Task ID

R18C

## Goal

盒子把同一条推荐连续给两遍、而日志说明这个动作还能再做时（风怒第二击、连续打
同一号位），脚本立刻再执行一次，不再当成“已完成”干等盒子更新面板。

## Scope

- `RecommendationFlow.run_player_turn_step`：待确认动作已被日志确认（confirmed）
  且当前读到的推荐与待确认动作完全一致时，若 `_repeat_allowed()` 允许，就解除
  消费标记、清掉待确认并立刻再执行一次；不再返回 `waiting_recommendation_update`
  干等面板。
- `_repeat_allowed()`：只放行「再做一次一定还是同一个动作」的类型 ——
  攻击（要求日志里攻击者还能出手：EXHAUSTED == 0；风怒第一击后 EXHAUSTED 仍是 0，
  第二击才置 1）、出牌/交易（连续打同一号位）、古神技能（本回合次数 < 2）；
  面板/手势类动作（结束回合、发现、拼装、选目标…）不在这里重做，仍走原有超时重试。
  每回合同一「推荐 + 号位」最多连续 6 次，防盒子面板卡死时无限重复。
- 没有 pending（上一步被判完成）但攻击仍在重复、且消费标记已存在时，同样解除
  标记后执行（风怒第二击走这条兜底）。
- 未确认（点击被客户端丢掉）时保持原有 3s 超时重试，不改成无脑连点。

## Non-Goals

- 不改盒子推荐解析/适配、不改点击坐标、不改本地策略路径。
- 不做“面板卡住时随机点一个”的兜底；到上限后照旧等盒子更新。
- 发现/拼装/选目标/结束回合等面板类动作不在本次重做范围内（重复点容易点空，
  仍由 3s 超时重试兜底）。
- 不改待确认超时/重试次数配置。

## Inputs

- 用户 2026-10-31 实测：风怒随从只攻击一次；盒子第一击/第二击的推荐行完全一致。
- 实测 Power.log `Hearthstone_2026_10_31_00_06_15`：影焰猎豹 id=96 第一击
  （00:10:33）只写 `NUM_ATTACKS_THIS_TURN=1`，第二击（00:10:48）才写
  `EXHAUSTED=1`；盒子两次给的是同一行「操作N号位随从攻击 / 目标是对方M号位」。
- 基分支 `feature/R18-local-strategy-hybrid` @ `ea04be1`。

## Outputs

- `src/hslegend/application/recommendation_flow.py`：
  `_MAX_IDENTICAL_REPEATS`、`_attack_source_available()`、`_repeat_key()`、
  `_repeat_allowed()`、`_note_repeat()`，以及执行分支里的重做判定。
- `tests/test_repeat_attack.py`：风怒第二击立刻执行、普通攻击耗尽后不重复、
  盒子面板卡住时的重复上限、连续打同一号位继续出牌。

## Interfaces

- Consumes: `PendingRecommendationAction`、`ConsumedActionStore`、
  `_postcondition`、`state.my_minions/my_locations/my_hero` 的 `exhausted`。
- Produces: 无新公开接口。

## Dependencies And Impact

- Depends on: R17 推荐流程与 5b9389e 的待确认机制。
- Impacted modules: 盒子AI 推荐执行循环（传统对战）。
- Public API impact: 无。

## Allowed Files

- `src/hslegend/application/recommendation_flow.py`
- `tests/test_repeat_attack.py`
- `docs/tasks/R18C-repeat-identical-recommendation.md`

## Forbidden Files

- `log_state.py`
- `FSM_action.py`
- `click.py`
- `src/hslegend/adapters/**`
- `web_ui.py`

## Acceptance

```powershell
uv run python -m unittest tests.test_repeat_attack -v
uv run python tools/run_headless_tests.py
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R18C
```

## Risk And Rollback

- Risk: 盒子面板卡住（一直显示同一行）时最多重复 6 次；攻击还有 EXHAUSTED 兜底，
  不会对着已耗尽的随从反复点。连续同号位出牌依赖盒子在动作后 0.5~3s 内刷新面板
  （实测如此），极端情况下可能多打一张同号位牌。
- Rollback: 删掉 `_repeat_allowed` 分支，恢复“confirmed 就等面板更新”。

## Garbage-Code Check

- In-scope garbage to refactor: 无。
- Out-of-scope discoveries to register: 古神技能每回合 2 次（克苏恩，帝国之眼）同样
  会出现「同一行推荐」，本次靠通用重做规则覆盖，未单独加可用次数校验。

## Git Requirement

One commit:

```text
fix(flow): re-run an unchanged recommendation that is still possible
```

## Report Paths

- Implementer: `docs/agent/reports/R18C-implementer.md`
- Reviewer: `docs/agent/reviews/R18C-review.md`
