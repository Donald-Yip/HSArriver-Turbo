# R18H 我方回合内持续扫描 + OCR 框不再压住日志

## Task ID

R18H

## Goal

用户 2026-10-31：「直到扫描到回合结束并点击，否则应该一直扫描并执行」「ocr 的扫描和
日志不应该重叠」。

1. **我方回合内一直扫描**：两次读屏间隔 ≤ 2 秒，直到「结束回合」被点掉/回合切换；
   原本“活人感悬停（0.5~5s）/抽牌额外延时（每张最多 2s，实测一次 9s）/动作结果等待”
   这些空窗完全不读屏，浮窗 OCR 框会空掉，用户以为卡住。
2. **OCR 框不再压住日志框**：隐藏 Tk 实测窗口 263×802、内容请求高度 **1023**，
   日志正文 `Text(height=12)` 请求 256px 却抢不到空间，被 pack 塞到 `y=0,h=1`
   叠在品牌行上，OCR 框独占下半屏。
3. **面板为空时说明原因**：`（最近 12s 没有 OCR 扫描 · 当前：等待对手操作）`；
   Power.log 静默 ≥90s（超过对手烧绳）追加 `· 日志 95s 未更新（可能卡住）` 并转橙。

## Scope

- `FSM_action.py`
  - 心跳：`_TURN_SCAN_INTERVAL=2.0`、`_last_scan_tick_at`、`_my_turn_active`、
    `_scan_tick(force=False)`（只读，不点击；未进局/对手回合不动）、
    `_scan_aware_sleep(seconds)`（切片 ≤0.25s，按实际耗时扣减，停止指令提前退出）。
  - `RecommendationFlow(..., sleep=_scan_aware_sleep)`：流程内的操作后延时/抽牌额外
    延时/结果等待/未确认重试全部带心跳。
  - `run_automatic_battle_step()`：每轮更新 `_my_turn_active`；对手回合清零
    `_last_scan_tick_at`；我方回合先 `_scan_tick(force=True)`，流程跑完再补一拍。
  - 空状态诊断：`_DIAGNOSTIC_LABELS`、`_log_revision_at`（`update_log_state()` 里
    记录 revision 变化时刻）、`automation_phase_state()`、`_diagnostic_label()`。
- `click.py`：`human_like_pause(..., tick=None)` 每段悬停后回调一次（心跳），异常吞掉。
- `log_overlay.py`
  - 布局：日志正文 `Text(height=12)` → `height=2`（请求高度只当最小行数，实际由
    `expand` 撑满）；`OCR_PANEL_MAX_LINES` 8 → 4；首次布局做一次
    `winfo_reqheight` 自检，超窗写运行日志。
  - 空状态：`phase_callback` 注入（`_PHASE`）、`ocr_panel_lines(..., phase=None)`、
    `_ocr_empty_row()`、`OCR_PANEL_LOG_IDLE_WARN_SECONDS=90`。
- `web_ui.py`：`_overlay_ocr_phase()` 转发 `FSM_action.automation_phase_state()`；
  `_bind_overlay()` 传 `phase_callback=`。
- `tests/helpers/fake_tk.py`：`_CALLBACK_NAMES` 加 `_PHASE`。
- 测试：`tests/test_turn_scan_heartbeat.py`（新，18 例）、
  `tests/test_ocr_scan_panel.py`（空状态 5 例 + 布局 3 例）。

## Non-Goals

- 不在**对手回合**加 OCR（既有设计：对手回合只悬停；面板用“当前：等待对手操作”
  说明），也不改门禁判定/点击逻辑/推荐解析。
- 不改浮窗总高度（802）、不改状态行/按钮区布局；不改 `src/hslegend`。
- 不新增 `ui_config.json` 配置项（心跳间隔、可见行数都是模块常量）。
- 不做进程级剖析（内存观测见 R18F）。

## Inputs

- 用户 2026-10-31 反馈 + `ui_log_last.txt`（16:02–19:20 会话）：
  - 258 个我方回合里 31 个存在 >15s 的 OCR 空窗，最大 45s（换牌/抽牌延时叠加）；
  - 隐藏 Tk 实测：窗口 263×802，内容请求 1023；OCR 框满内容时日志正文被分到
    `y=0,h=1`（叠在品牌行上）。
- 基分支 `feature/R18-local-strategy-hybrid`（R18D~G 未提交改动之上）。

## Outputs

- 我方回合内每 ≤2s 一次读屏（含所有等待），回合切换/点掉结束回合即停。
- 日志框与 OCR 框不再重叠：OCR 满 4 行时日志仍可见 ~3 行，OCR 2 行时 ~5 行；
  实测 content 请求高度 746 ≤ 802、相邻控件无重叠。
- 空状态行带“当前阶段”，日志静默 ≥90s 转橙。

## Interfaces

- Consumes: `end_turn_button_present()`（只读探针）、`log_state.revision`、
  `last_automation_diagnostic`、`click.human_like_pause`。
- Produces: `FSM_action._scan_tick/_scan_aware_sleep/automation_phase_state`；
  `log_overlay.start(phase_callback=…)`、`ocr_panel_lines(..., phase=)`；
  `click.human_like_pause(tick=…)`。

## Dependencies And Impact

- Depends on: R18D 的 OCR 面板与诊断行、R18G 的容忍截断判定。
- Impacted modules: 回合内等待路径（多出每 2s 一次只读 OCR，~100~200ms）、
  浮窗布局、浮窗空状态。
- Public API impact: `click.human_like_pause` 与 `log_overlay.start` 各多一个
  可选参数；其余为内部函数。

## Allowed Files

- `FSM_action.py`
- `click.py`
- `log_overlay.py`
- `web_ui.py`
- `tests/test_turn_scan_heartbeat.py`
- `tests/test_ocr_scan_panel.py`
- `tests/helpers/fake_tk.py`
- `README.md`
- `SKILL.md`
- `AGENT_CODE_MAP.md`
- `docs/tasks/R18H-ocr-panel-heartbeat.md`

## Forbidden Files

- `src/hslegend/**`
- `ai/**`
- `search/**`
- `log_state.py`
- `config.py`
- `screen_regions.py`
- `src/ocr/**`
- `memory_watch.py`

## Acceptance

```powershell
uv run python -m unittest tests.test_turn_scan_heartbeat tests.test_ocr_scan_panel -v
uv run python tools/run_headless_tests.py
uv run python tools/run_headless_tests.py --opt-in
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R18H
uv run python -m compileall -q src tests tools FSM_action.py log_overlay.py web_ui.py click.py
```

隐藏 Tk 几何复测（`withdraw` + `winfo_reqheight`）：OCR 满 4 行时 content 请求高度
≤ 802、相邻子控件 `y` 区间无重叠、日志正文可见高度 ≥ 3 行。

手工：重启控制台打一局，确认 ① 我方回合里浮窗 OCR 框持续有读数；② 日志框不再被
压住；③ 对手回合面板写「当前：等待对手操作」。

## Risk And Rollback

- Risk: 我方回合内每 2s 多一次只读 OCR（含兜底框时 ~200ms），CPU 略升；等待总长
  不变（按实际耗时扣减）。OCR 满 4 行时日志只剩 ~3 行（可滚动，折叠 OCR 框可恢复）。
- Rollback: 去掉 `sleep=_scan_aware_sleep` 注入与 `_scan_tick` 调用；日志正文
  `height` 与 `OCR_PANEL_MAX_LINES` 改回 12/8（会重新出现重叠，不建议）。

## Garbage-Code Check

- In-scope garbage to refactor: 无（心跳/空状态都复用现有诊断码与 OCR 探针）。
- Out-of-scope discoveries to register: 结算界面「开始」按钮 40 轮超时（14:48–15:59，
  39 次）仍未修；对手回合不读屏是否也要心跳，等用户实测后再定。

## Git Requirement

One commit:

```text
feat(turn): keep scanning during my turn and stop squeezing the log panel
```

## Report Paths

- Implementer: `docs/agent/reports/R18H-implementer.md`
- Reviewer: `docs/agent/reviews/R18H-review.md`
