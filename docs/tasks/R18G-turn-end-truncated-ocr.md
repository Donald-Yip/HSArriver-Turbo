# R18G Turn-End Gate: Truncated OCR Read

## Task ID

R18G

## Goal

修「回合开始门禁」把「结束回合」按钮读残了还当没就绪、每回合白等 6 秒的问题：
OCR 把最后一个字裁掉（实测读到「结束回」）或把四个字拆成两行时，也要认成就绪；
主框读不到时用更宽的兜底框再读一次。用户 2026-10-31：「脚本运行久了…另外看日志，
解决最新的问题」。

## Scope

- `FSM_action.py`
  - `_turn_end_lines_ready()`（新）：把**置信度 ≥ 0.5** 的 OCR 行拼成一段再判断——
    出现「结束回合/結束回合/End Turn/endturn」→ 就绪；出现「对手/對方/对方/敌方/敵人」
    → 一律不就绪；只剩「结束回/結束回」（末字被裁）→ 也认就绪（至少 3 个字，
    避免「结束」这种半截字误判）。
  - `end_turn_button_present()`：先读可校准主框，失败再读 `_TURN_END_FALLBACK_ROI`
    = `(1340, 400, 1740, 660)`（客户区坐标，把整个按钮连同倒计时框进来）；两次读到
    的原文都写进 `_turn_end_last_text` 供诊断。
  - `_player_turn_ready()`：诊断行里的 OCR 原文截断到 80 字符，别把浮窗诊断行撑爆。
- `tests/test_turn_start_readiness.py`：截断/拆行/倒计时/两字半截/低置信/英文/
  兜底框二次读取/主框命中不读兜底/诊断原文，共 9 个新用例。
- `README.md`：校准表「结束回合」那一段补一句“读残了也认 + 兜底框”。
- `SKILL.md`：排障口径补一句。

## Non-Goals

- 不改主框默认值 `(1380, 430, 1600, 570)`（用户可校准，`screen_regions` 与
  `RecommendationConfig` 的同源测试不动）。
- 不改点击逻辑/推荐解析/本地策略；不动 `src/hslegend`。
- 不处理 14:48–15:59 那批「结算界面 40 轮没出现「开始」按钮」——那是上一轮会话的
  另一类问题（16:00 重启炉石后未再复现），本次只登记不修。

## Inputs

- `ui_log_last.txt`（16:02–19:20 会话，35 局）：
  - 63 次我方回合开始，门禁耗时 median 6s / p90 9s / max 13s，累计白等 333s；
  - 999 次「尚未检测到「结束回合」」，OCR 读到 `结束回`（末字被裁）、`对手回合`、
    `01:07`、`灵魂弹幕`（悬停卡名）等；用户 13:47 截图里同框读到过
    `01:07 | 结束回合`（说明框正好卡在按钮边缘）。
- 基分支 `feature/R18-local-strategy-hybrid`（R18D/R18E 未提交改动之上）。

## Outputs

- 门禁容忍截断/拆行 + 兜底框二次读取；诊断日志仍带 OCR 原文。

## Interfaces

- Consumes: `_ocr_lines(box, scale, tag)`、`normalize_card_name`。
- Produces: `_turn_end_lines_ready()`（内部）；`end_turn_button_present()` 语义不变
  （返回 bool，副作用是写 `_turn_end_last_text`）。

## Dependencies And Impact

- Depends on: R18D 的诊断行（`（OCR 读到：…）`）。
- Impacted modules: 回合开始门禁（传统对战）；每次失败多一次兜底框 OCR（~200ms）。
- Public API impact: 无（新函数以下划线开头）。

## Allowed Files

- `FSM_action.py`
- `tests/test_turn_start_readiness.py`
- `docs/tasks/R18G-turn-end-truncated-ocr.md`
- `README.md`
- `SKILL.md`

## Forbidden Files

- `src/hslegend/**`
- `ai/**`
- `search/**`
- `log_state.py`
- `click.py`
- `config.py`
- `screen_regions.py`
- `log_overlay.py`
- `web_ui.py`
- `src/ocr/stable_reader.py`
- `src/ocr/paddle_adapter.py`

## Acceptance

```powershell
uv run python -m unittest tests.test_turn_start_readiness -v
uv run python tools/run_headless_tests.py
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R18G
uv run python -m compileall -q src tests tools FSM_action.py
```

手工：打一局，回合开始时日志应更快出现「已检测到「结束回合」」；卡住时诊断行里的
`（OCR 读到：…）` 能看到主框+兜底框读到的原文。

## Risk And Rollback

- Risk: 截断容忍把「结束」两字以上、不含「对手」的误读当就绪——按钮区域内的其它
  文字几乎不可能含「结束回」，且点击早了最多被客户端忽略，不会点到牌上（门禁之后
  仍然要读推荐再点）。兜底框更大，失败时多一次 ~200ms 的 OCR。
- Rollback: 恢复 `end_turn_button_present()` 只读主框 + 只认完整关键字。

## Garbage-Code Check

- In-scope garbage to refactor: 旧的“逐行完整关键字”匹配被 `_turn_end_lines_ready()`
  取代，避免两套判定并存。
- Out-of-scope discoveries to register: 结算界面「开始」按钮 40 轮超时（14:48–15:59，
  39 次）未修，需要另开任务（同样缺 OCR 原文诊断）。

## Git Requirement

One commit:

```text
fix(turn): accept a truncated end-turn read and retry with a wider box
```

## Report Paths

- Implementer: `docs/agent/reports/R18G-implementer.md`
- Reviewer: `docs/agent/reviews/R18G-review.md`
