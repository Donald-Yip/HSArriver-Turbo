# R18B Log Entity-Name Noise

## Task ID

R18B

## Goal

启动脚本重放当前 Power.log 时，酒馆战棋日志里 `TAG_CHANGE Entity=<随从/英雄名>` 不再
逐条打 `[WARN] Invalid entity_id: 0` + 整块 line container，控制台保持安静。

## Scope

- `log_state.update_state` 的 `LOG_LINE_TAG_CHANGE`：非数字 Entity 只有在“像玩家名”时
  才映射到玩家实体（`MY_NAME` 命中、含 `#数字` 的战网昵称、`UNKNOWN HUMAN PLAYER`
  这类占位名）；其余（战棋随从/英雄名）直接跳过这一行，不告警。
- 去掉 `assert int(entity_id) <= 3`：酒馆战棋 8 个玩家的实体 id 会大于 3，旧断言在
  重放战棋日志时抛 `AssertionError`（实测 3768 次）；映射不到时由后面的
  `entity_dict` 检查兜住。
- 数字 Entity 解析不到实体时，同一实体每局只告警一次（`_invalid_entity_warned`，
  `flush()` 每局清空），避免重放历史日志刷屏。

## Non-Goals

- 不新增酒馆战棋支持；不改战棋对局里的行为。
- 不改 `log_op.py` 的行解析、不改 `surrender_rules.py`。
- 不吞掉“数字实体确实解析不到”的告警（保留每局一次）。

## Inputs

- 用户 2026-10-31 00:04 反馈：打开脚本满屏 `[WARN] Invalid entity_id: 0`，
  `entity: 暗影之鱼人` / `tag: BACON_OLD_GOD_ATTACK` / `PLAYSTATE`。
- 实测 Power.log（`Hearthstone_2026_10_30_21_44_40`）：
  `TAG_CHANGE Entity=暗影之鱼人 tag=BACON_OLD_GOD_ATTACK value=0`、
  `TAG_CHANGE Entity=调酒师鲍勃 tag=CURRENT_PLAYER value=1`。
- 基分支 `feature/R18-local-strategy-hybrid` @ `6472df5`。

## Outputs

- `log_state.py`：`_looks_like_player_name()`、TAG_CHANGE 名称分支、
  `_invalid_entity_warned` 去重、去掉 `<= 3` 断言。
- `tests/test_log_state_entity_names.py`：战棋名字静默跳过、真实玩家名仍映射、
  战棋玩家实体 id >3 不再抛断言、未知数字实体每局一次、`flush()` 清空。
- 验证：把 `Hearthstone_2026_10_30_21_44_40`（539395 行，含一局战棋）整份重放，
  告警 0 条、异常 0 条。

## Interfaces

- Consumes: `MY_NAME`、`LogState.player_id_map_dict`、`warn_print`。
- Produces: 无新接口（仅解析行为与告警节奏）。

## Dependencies And Impact

- Depends on: 现有 Power.log 解析链路。
- Impacted modules: `log_state.update_state` 的 TAG_CHANGE 分支。
- Public API impact: 无。

## Allowed Files

- `log_state.py`
- `tests/test_log_state_entity_names.py`
- `docs/tasks/R18B-log-entity-name-noise.md`

## Forbidden Files

- `log_op.py`
- `surrender_rules.py`
- `FSM_action.py`
- `src/hslegend/**`

## Acceptance

```powershell
uv run python -m unittest tests.test_log_state_entity_names -v
uv run python tools/run_headless_tests.py
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R18B
```

## Risk And Rollback

- Risk: 若真实玩家名既不含 `#` 也不含 `UNKNOWN` 且不等于配置昵称，会被当成战棋名字跳过
  （战网昵称一律带 `#数字`，占位名是 `UNKNOWN HUMAN PLAYER`，正常不会命中）。
- Rollback: 还原 TAG_CHANGE 名称分支为“一律当玩家名”。

## Garbage-Code Check

- In-scope garbage to refactor: 旧代码对未知实体逐行 `warn_print` 的写法改为每局一次。
- Out-of-scope discoveries to register: 战棋对局本身仍不支持（脚本只打传统对战）。

## Git Requirement

One commit:

```text
fix(log): ignore name-only entities from non-constructed power logs
```

## Report Paths

- Implementer: `docs/agent/reports/R18B-implementer.md`
- Reviewer: `docs/agent/reviews/R18B-review.md`
