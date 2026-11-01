# R18I Refresh Before Retry

## Task ID

R18I

## Goal

用户 2026-10-31 反馈：「有的时候对场面的认识还是会出错，每次操作重试前，都要刷新
各个信息，然后再重试，而不是一直用之前的信息」。修掉盒子AI路径两处“用旧信息”
的真问题：适配动作用的是入口时读到的旧 `state`（与随后校验/执行的 `fresh_state`
不是同一份），以及待确认超时重试时不清日志、不重判后置条件就重执行同一条动作。

## Scope

- `src/hslegend/application/recommendation_flow.py`（`run_player_turn_step` 盒子AI
  路径）：
  - **A. 先刷新再适配**：面板读取/解析、早退判定之后，先
    `fresh_state, fresh_revision = self._wait_for_hand_animation()`；`adapter`、
    pending 的 confirmed 判定、`consumed.key_for`、攻击可重复判定、
    `validator.validate`、`controller.execute`、`_set_pending`、`_verify_result`
    全部改用同一份 `fresh_state/fresh_revision`；删除原来“先 adapter 再等待”的
    旧顺序。行为语义不变（只是不再混用两份状态）。
  - **B. `_refresh_before_retry(min_revision, timeout=None)`**：先取一次
    `(state, revision)`；revision 未超过 `min_revision` 时 sleep 0.2s 再取，直到
    revision 前进 / 等待超过 `RETRY_REFRESH_TIMEOUT`(1.5s) / `stopped()`；返回最新
    `(state, revision)`。真的等待过才用 `controller.output` 打一行
    `[SYS] 重试前刷新：日志 revision A→B（等待 X.Xs）`（异常吞掉）。
  - **C. 待确认超时重试**：先 `_refresh_before_retry(pending.before_revision)` 再
    重判 postcondition；动作其实已生效（revision 前进且后置条件成立）→ 标记
    consumed、清 pending、返回 OBSERVE `waiting_recommendation_update`，**不再点
    第二次**；仍未生效才 `attempts+1` 重执行，并继续用刷新后的 state。
  - **D. `_retry(code)`**：对状态类重试码 `revision_changed` /
    `duplicate_board_position` / `turn_changed`，先
    `_refresh_before_retry(self._last_revision, timeout=0.8)`，再
    `self.reader.clear_cache()`（reader 没有该方法时跳过），最后返回 RETRY；
    `run_player_turn_step` 入口把本次 revision 记进 `self._last_revision`。
    其它重试码（`recommendation_not_stable`、`already_consumed`、异常串等）不等待、
    不清缓存。
- `tests/test_retry_refresh.py`（新增 8 个用例）：刷新后的 state 传给 adapter 与
  validator；超时后已生效不重复点击；超时后仍未生效只重执行一次且用刷新后 state；
  `_retry` 状态类等待并清缓存、非状态类不等；`_refresh_before_retry` 的超时上限、
  已前进不等待、`stopped()` 提前退出。
- `docs/tasks/R18I-refresh-before-retry.md`（本文件）。

## Non-Goals

- 不改本地策略路径（`_run_local_step` 已经是“先 `fresh_state` 再 adapter”的正确
  顺序，本次不动）。
- 不改 OCR 预处理/ROI/解析、点击坐标、待确认超时与重试次数配置。
- 不改 `FSM_action.py` 的集成钩子与浮窗/网页面板（Lead 负责）。
- 不重做 pending 状态机；超时重试仍是“最多 `max_pending_retries` 次”的原策略，
  只是重试前多一次刷新与后置条件复查。

## Inputs

- 用户 2026-10-31 反馈原话（见 Goal）。
- 代码走查：`run_player_turn_step` 入口 `state, revision = self.state_supplier()`
  → 面板 OCR/解析 → `adapted = self.adapter(proposed, state)`（旧 state）→
  `_wait_for_hand_animation()` 拿 fresh_state 后才校验/执行；本地路径
  `_run_local_step` 则是先 fresh_state 再 adapter。
- 待确认超时分支原逻辑：3s 未确认直接 `_clear_pending()` 重执行同一 `proposed`，
  不刷新日志、不重判 postcondition；Power.log 写得慢时动作其实已生效 → 重复点击。
- 基分支 `feature/R18-local-strategy-hybrid` @ `81c2736`。

## Outputs

- 盒子路径的执行/校验统一使用面板后刷新的同一份 state。
- 超时重试前的一次日志刷新与后置条件复查（已生效不再点）。
- `[SYS] 重试前刷新：日志 revision A→B（等待 X.Xs）` 诊断行。
- 状态类重试前清空 OCR 帧缓存，强制下一次重新识别面板。

## Interfaces

- Consumes: `state_supplier()`、`_wait_for_hand_animation()`、`_postcondition()`、
  `ConsumedActionStore`、`reader.clear_cache()`（可选，缺失时跳过）。
- Produces: 模块常量 `RETRY_REFRESH_TIMEOUT`；方法
  `RecommendationFlow._refresh_before_retry()`；实例属性 `_last_revision`。
  `_retry` 由 `@staticmethod` 改为实例方法（源码内所有调用点都是 `self._retry(...)`，
  无外部调用方）。

## Dependencies And Impact

- Depends on: R17/R18C 的推荐流程与待确认机制、R18F 的 `clear_cache()`。
- Impacted modules: 盒子AI 推荐执行循环（传统对战）；本地策略路径只受
  `_retry` 状态类等待/清缓存影响（同一实例方法）。
- Public API impact: 无（`_retry`/`_refresh_before_retry` 均为私有方法）。

## Allowed Files

- `src/hslegend/application/recommendation_flow.py`
- `tests/test_retry_refresh.py`
- `docs/tasks/R18I-refresh-before-retry.md`
- `README.md`（Lead 集成时补排障口径）
- `SKILL.md`（Lead 集成时补重试前刷新说明）

## Forbidden Files

- `FSM_action.py`
- `log_overlay.py`
- `web_ui.py`
- `config.py`
- `src/ocr/**`
- `src/hslegend/domain/**`
- `src/hslegend/adapters/**`
- `src/hslegend/plugins/**`
- `src/hslegend/bootstrap/**`
- `src/hslegend/entrypoints/**`

## Acceptance

```powershell
uv run python -m unittest tests.test_retry_refresh tests.test_recommendation_retry tests.test_repeat_attack -v
uv run python tools/run_headless_tests.py
uv run python tools/check_architecture.py
uv run python -m compileall -q src tests tools
```

结果：unit 25/25 OK；headless run=1804 passed=1804；architecture PASS（67 files）；
compileall OK。

## Risk And Rollback

- Risk: `_refresh_before_retry` 会真的 sleep（上限 1.5s / 状态类重试 0.8s），状态类
  失败连续发生时每轮最多多 0.8s；等待时长按实际 sleep 累加（不依赖注入时钟），
  冻结的假时钟不会空转。超时分支多一次 postcondition 复查，若后置条件本身对
  “已生效”判定过宽，可能把真正被丢弃的点击当成已生效（原 `_postcondition` 逻辑
  未改，只换用刷新后的 state）。清 OCR 帧缓存会让下一次同帧多一次推理，开销可忽略。
- Rollback: 把 `adapted = self.adapter(proposed, fresh_state)` 移回等待之前、删除
  `_refresh_before_retry` 与待确认分支的复查、`_retry` 恢复为 `@staticmethod` 即可。

## Garbage-Code Check

- In-scope garbage to refactor: 抽出 `_refresh_before_retry` 统一“等待 revision /
  诊断行 / 返回最新状态”，避免超时分支与 `_retry` 各写一遍轮询。
- Out-of-scope discoveries to register: 本地策略路径重试（`search_failed` /
  `no_local_action`）同样会重复用上一轮搜索结果；本次只按任务范围处理状态类重试码，
  本地搜索路径是否也要“重试前重搜”另开任务评估。
- 现有 `tests/test_recommendation_retry.py` 未修改：其用例锁的是行为（重试次数、
  执行次数、诊断码），不是 state 读取顺序，改后原样通过。

## Git Requirement

One commit:

```text
fix(flow): refresh state and re-check the postcondition before retrying
```

## Report Paths

- Implementer: `docs/agent/reports/R18I-implementer.md`
- Reviewer: `docs/agent/reviews/R18I-review.md`
