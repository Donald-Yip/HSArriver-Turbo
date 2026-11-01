# R18E OCR Panel Layout

## Task ID

R18E

## Goal

重做浮窗「OCR 扫描」框的排版：**每个区域一块、完整显示识别到的每一行原文**
（不再用 `…` 截断），并且**过期区域自动隐藏**（换牌确认这类只在特定阶段扫一次的
区域不再一直挂在上面）。用户 2026-10-31 看到面板后要求：「设计一下排版，应该显示
完整的扫描内容，换牌确认不用一直放在上面」。

## Scope

- `log_overlay.py`
  - 常量换成新语义：`OCR_PANEL_TTL_SECONDS=12.0`、`OCR_PANEL_MIN_LINES=2`、
    `OCR_PANEL_MAX_LINES=8`、`OCR_PANEL_RENDER_LIMIT=60`、`OCR_PANEL_ORDER`、
    `OCR_TAG_GROUP`（tag → 显示区域名，推荐的 3 个预处理变体合并成「推荐」）、
    `OCR_LINE_TAGS`（`match/warn/text/dim` 四种颜色 tag）、`OCR_PANEL_INDENT="  ▏"`。
  - `ocr_panel_rows()` → `ocr_panel_lines(entries, now=None, ttl=None, limit=None)`：
    纯函数，输出 `[{"text", "tag", "head"}]`；负责 TTL 过滤、变体合并、固定排序、
    标题行 + 逐行原文拼装、渲染上限兜底。
  - 新增 `ocr_panel_status(entries, now=None, ttl=None)`：标题行右侧「最近 Ns 前」/
    「没有扫描」。
  - 控件：4 个 `tk.Label` 换成只读 `tk.Text(wrap="char")` + 竖向滚动条（和日志区同一套
    做法）；标题行右侧新增新鲜度 label；高度按内容 `clamp(2..8)` 自适应；只在内容指纹
    变化时重建，重建前后保存/恢复 `yview` 比例；「隐藏/显示」折叠保留（展开后强制重画）。
- `src/ocr/status.py`：`MAX_TEXT` 80 → 200（单行原文的安全上限，不再是显示截断）。
- `tests/helpers/fake_tk.py`：`FakeWidget` 补 `yview_moveto()`（记录滚动位置）。
- `tests/test_ocr_scan_panel.py`：面板用例按新排版重写（分块/完整原文/TTL/合并/排序/
  颜色/自适应高度/滚动位置/折叠重建），store 与 web 接线用例保留。
- `tests/test_overlay_eye_and_colors.py`：浮窗现在有两个 `Text`（日志正文 `wrap="word"`
  与 OCR 框 `wrap="char"`），该用例改为按 `wrap` 精确选中日志正文。
- 文档：`README.md`（重写「🔍 OCR 扫描框」小节）、`SKILL.md`、`AGENT_CODE_MAP.md`。

## Non-Goals

- 不改「结束回合」判定（仍然只认 `结束回合/結束回合/endturn` + 置信度 ≥ 0.5），
  绿/橙只是**显示**语义。
- 不改 OCR 引擎/预处理候选/ROI 坐标、点击逻辑、推荐解析、本地策略路径。
- 不改 `web_ui._overlay_ocr()` 的接口（仍然只转发 `snapshot()`）。
- 不动 `src/hslegend`（分层包）；不新增 `ui_config.json` 配置项（TTL/行数是模块常量）。
- 不把面板做进网页控制台。

## Inputs

- 用户 2026-10-31 截图（浮窗 OCR 框）：
  `结束回合框 2行0.85 01:07 | 结束回合` / `推荐 4行0.99 打出2号位法术 | 灵魂之…` /
  `胜率 3行0.87 AI胜率 95% | 10…` / `换牌确认 0行0.00 （没读到字）`。
  两个问题：原文被截断成 `…`；换牌阶段的旧区域一直占一行。
- 基分支 `feature/R18-local-strategy-hybrid`（R18D 面板落地后）。

## Outputs

- 浮窗「OCR 扫描」框新排版：区域块 + 完整原文逐行 + 12s TTL + 自适应高度 + 滚动。
- 纯函数 `ocr_panel_lines()` / `ocr_panel_status()`（可脱离 Tk 单测）。

## Interfaces

- Consumes: `src.ocr.status.snapshot()` 的记录结构
  （`tag/at/lines/line_count/confidence/count`）。
- Produces: `log_overlay.ocr_panel_lines()`、`log_overlay.ocr_panel_status()`；
  `log_overlay.start(ocr_callback=…)` 不变。

## Dependencies And Impact

- Depends on: R18D（`src/ocr/status.py` + `ocr_callback` 注入）。
- Impacted modules: 浮窗渲染（`log_overlay`）；`src/ocr/status.py` 的常量。
- Public API impact: `ocr_panel_rows` 改名为 `ocr_panel_lines`（仅浮窗内部 + 测试使用）。

## Allowed Files

> R18D（面板落地）与 R18E（排版重做）在同一个工作区里一起交付，所以这里列的是
> 两者并集；要拆成两个提交时，按 R18D 的 task book 先提交前一批即可。

- `log_overlay.py`
- `src/ocr/status.py`
- `src/ocr/paddle_adapter.py`
- `web_ui.py`
- `FSM_action.py`
- `tests/test_ocr_scan_panel.py`
- `tests/test_overlay_eye_and_colors.py`
- `tests/helpers/fake_tk.py`
- `README.md`
- `SKILL.md`
- `AGENT_CODE_MAP.md`
- `docs/tasks/R18D-ocr-scan-panel.md`
- `docs/tasks/R18E-ocr-panel-layout.md`

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
uv run python -m unittest tests.test_overlay_eye_and_colors -v
uv run python tools/run_headless_tests.py
uv run python tools/run_headless_tests.py --opt-in
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R18E
uv run python -m compileall -q src tests tools FSM_action.py log_overlay.py web_ui.py
```

手工：重启控制台打一局，确认 ① 原文完整、不再出现 `…`；② `换牌确认` 开打 12s 后消失；
③ 面板可折叠、可滚动、滚动时不被顶回顶部；④ 结束回合框仍是第一块、绿/橙语义不变。

## Risk And Rollback

- Risk: 面板可见高度 2~8 行随内容变化，布局会有轻微跳动（只在内容变化时发生，
  不是每 350ms）；内容特别长时靠滚动条。滚动位置恢复是比例恢复，极端情况下
  内容长度变化很大时会略偏。
- Rollback: 恢复 `ocr_panel_rows` + 4 个 Label 的旧实现（git 回退本提交即可）；
  `src/ocr/status.py` 的 `MAX_TEXT` 可单独改回 80。

## Garbage-Code Check

- In-scope garbage to refactor: `_clip_panel_text` / `_ocr_panel_row` / `_ocr_panel_color`
  （按单行截断的旧渲染）随新排版删除，避免两套渲染逻辑并存。
- Out-of-scope discoveries to register: 「结束回合框」在客户区缩小后读到 2 行却都不含
  「结束回合」的问题仍未修（面板现在能显示真实原文，等实测后再决定放宽匹配或调 ROI）。

## Git Requirement

One commit:

```text
feat(overlay): show full OCR text in per-region blocks
```

## Report Paths

- Implementer: `docs/agent/reports/R18E-implementer.md`
- Reviewer: `docs/agent/reviews/R18E-review.md`
