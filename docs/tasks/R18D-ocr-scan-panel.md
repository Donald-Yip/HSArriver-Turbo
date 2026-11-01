# R18D OCR Scan Panel

## Task ID

R18D

## Goal

浮窗日志框下面加一个 **「OCR 扫描」框**，实时显示各 OCR 区域最近一次读到的原文，
解决「日志里只有 `行 2 置信 0.91`、看不出到底认成了什么」的问题（用户 2026-10-31：
「看看最新日志怎么老是检测不到结束回合？」「可以在浮窗日志框下面再加一个框，
实时显示 OCR 扫描结果」）。

## Scope

- `src/ocr/status.py`（新增，legacy 侧旁路数据）：`record()` / `snapshot()` /
  `clear()`，按区域（tag）保留最近一次扫描的尺寸、文本、置信度、耗时；同一段
  文字重复读到累加 `count`（浮窗显示 `×N`），最多保留 `MAX_TAGS=8` 个区域。
- `src/ocr/paddle_adapter.py`：`PaddleOcrAdapter.recognize()` 每次识别后写一条记录
  （**唯一写入方**；记录异常被吞掉，绝不影响识别本身）。
- `log_overlay.py`：日志正文下方新增只读「OCR 扫描」框（标题 + `OCR_PANEL_ROWS=4`
  行 + 标题右侧「隐藏/显示」折叠）；`ocr_panel_rows()` 纯函数负责渲染，
  「结束回合框」钉第一行（绿=读到「结束回合」，橙=读到别的东西）；
  `start()` 新增 `ocr_callback`（沿用 human_like/liveness/logs 的注入式回调）。
- `web_ui.py`：`_overlay_ocr()` 把 `src.ocr.status.snapshot()` 转发给浮窗
  （导入/数据异常返回 `None`，浮窗显示「等待 OCR 数据…」）。
- `FSM_action.py`：`end_turn_button_present()` 记下这次读到的原文，
  `_player_turn_ready()` 的「尚未检测到结束回合」诊断行带上 `（OCR 读到：…）`，
  让落盘的 `ui_log_last.txt` 事后也能看到当时认成了什么。
- `tests/helpers/fake_tk.py`：`_CALLBACK_NAMES` 加 `_OCR`，浮窗测试可注入。

## Non-Goals

- 不改「结束回合」判据本身（仍然只认 `结束回合/結束回合/endturn` + 置信度 ≥ 0.5）：
  本轮只做**可观测性**，读错字要不要放宽匹配等看到真实 OCR 原文再决定。
- 不改 OCR 引擎/预处理候选/ROI 坐标，不改点击逻辑、推荐解析、本地策略路径。
- 不做历史回放/落盘（浮窗是实时视图，诊断原文进运行日志就够）。
- 不动 `src/hslegend`（分层包）：本功能是 legacy 外壳的排障视图，不是应用端口。

## Inputs

- 用户 2026-10-31 反馈与 `ui_log_last.txt` 证据：13:43:38–13:44:06 我方回合里
  `(turn_end)` 每次都能读到 2 行高置信度文字，但没有一行含「结束回合」，脚本整回合
  卡在「尚未检测到「结束回合」」；同一局 13:42:41 起 OCR 输入尺寸从 440×280 变成
  572×364（炉石客户区被缩到 1477×830）。
- 基分支 `feature/R18-local-strategy-hybrid` @ `81c2736`。

## Outputs

- 浮窗日志区下方的「OCR 扫描」框（4 行，可折叠，不增加浮窗总高度）。
- 诊断日志：`[SYS] 尚未检测到「结束回合」（OCR 读到：…），每 2s 复查一次。`
- `src/ocr/status.py` 的旁路记录接口（供浮窗与后续排障复用）。

## Interfaces

- Consumes: `PaddleOcrAdapter.recognize()` 的 `preprocessing` tag 与识别结果；
  `web_ui._bind_overlay()` 的注入式回调机制。
- Produces: `log_overlay.start(ocr_callback=…)`；`log_overlay.ocr_panel_rows()`；
  `src.ocr.status.record/snapshot/clear`。

## Dependencies And Impact

- Depends on: 现有浮窗（`log_overlay`）与 OCR 适配器（`src/ocr`）。
- Impacted modules: 浮窗渲染、OCR 识别出口、回合开始诊断日志。
- Public API impact: `log_overlay.start()` 多一个可选参数（默认 `None`，旧调用不受影响）。

## Allowed Files

- `src/ocr/status.py`
- `src/ocr/paddle_adapter.py`
- `log_overlay.py`
- `web_ui.py`
- `FSM_action.py`
- `tests/test_ocr_scan_panel.py`
- `tests/helpers/fake_tk.py`
- `README.md`
- `SKILL.md`
- `AGENT_CODE_MAP.md`
- `docs/tasks/R18D-ocr-scan-panel.md`

## Forbidden Files

- `src/hslegend/**`
- `ai/**`
- `search/**`
- `log_state.py`
- `click.py`
- `config.py`
- `screen_regions.py`

## Acceptance

```powershell
uv run python -m unittest tests.test_ocr_scan_panel -v
uv run python tools/run_headless_tests.py
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R18D
uv run python -m compileall -q src tests tools
```

## Risk And Rollback

- Risk: 浮窗每 350ms 读一次快照（最多 8 条 dict），开销可忽略；OCR 适配器多一次
  `try/except` 记录，异常被吞掉不会影响识别。面板占 4 行，日志区相应少 4 行，
  可用「隐藏」折叠找回（浮窗总高度不变，避免高 DPI 屏装不下）。
- Rollback: 删掉面板 UI 与 `ocr_callback` 注入、去掉适配器里的 `ocr_status.record()`
  与诊断行里的原文即可；`src/ocr/status.py` 无其它调用方。

## Garbage-Code Check

- In-scope garbage to refactor: 无（浮窗回调注入、行渲染都沿用现有 `*_row()` 纯函数写法）。
- Out-of-scope discoveries to register: 「结束回合」框在客户区被缩小后读到 2 行却都不含
  「结束回合」—— 等面板显示真实原文后另开任务决定是否放宽匹配或调整 ROI。

## Git Requirement

One commit:

```text
feat(overlay): show the latest OCR scan under the log panel
```

## Report Paths

- Implementer: `docs/agent/reports/R18D-implementer.md`
- Reviewer: `docs/agent/reviews/R18D-review.md`
