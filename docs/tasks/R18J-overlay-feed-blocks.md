# R18J Overlay Feed Blocks

## Task ID

R18J

## Goal

用户 2026-10-31 要求：

1. 把浮窗的「OCR 扫描」和「日志」两个框**合并成一个**，面积稍大一些；
2. 块内显示顺序固定为 **【OCR 内容 → 执行内容】**，这是一个整体；
3. 每当产生新内容，**整块置顶**；分块清晰；
4. **执行要写清楚执行什么**（不是只写“开始点击”）；
5. 改功能注意架构（分层包依赖方向不变，新模块放在 legacy 外壳侧）。

## Scope

### 1) 执行描述（适配层 + 应用层）

- `src/hslegend/adapters/legacy/recommendation.py`（适配层）：
  - `AdaptedAction` 加纯数据字段 `description: str = ""`；
  - 新增 `describe_adapted(adapted, proposed=None)`：按 `actions.py` 的 click DTO 生成一句
    中文（`打出2号位法术「灵魂之匣」→ 我方1号位`、`3号位随从「影焰猎豹」攻击 → 对方1号位随从`、
    `使用英雄技能「生命分流」`、`结束回合（点右侧按钮）`、`选择第N个选项`、`拼装：选第N个部件`…），
    卡名拿不到退回 `card_id`，整体认不出退回推荐原文；
  - `RecommendationAdapter.__call__` 填 `description`（`replace(...)` 分支保留该字段）。
- `src/hslegend/application/recommendation_flow.py`（应用层，**不 import 适配层**）：
  - 两处 `print("[执行] 开始点击。")` → `print(f"[执行] {getattr(adapted, 'description', '') or proposed.normalized_instruction}")`；
  - 执行成功后追加 `print(f"[执行] 结果：{result.message}")`（空消息不打印）。

### 2) 分块流（legacy 外壳侧）

- `action_feed.py`（新，root legacy，只 import stdlib）：块模型 + 归块规则 + `render()`：
  - 一块 = 一步动作：块头 `HH:MM:SS · 我方出牌/对手回合/换牌/对局结束`，块内 `OCR` 段 →
    `执行` 段，块间 `─` 分隔，**新块在最前**；
  - 归块：主推荐行/`[SYS] 尚未检测到「结束回合」（OCR 读到：…）` → OCR 段（同文去重）；
    `[执行] …`/结果/延时/抽牌/重试/告警 → 执行段；裸续行（`生命分流` 等）归上一条推荐；
    阶段标记关闭当前块并记录下一块阶段；`[OCR] 图 …`/内存 RSS/投降检测/自检等不进块；
  - 块建立时取一次 `ocr_provider()` 快照（`ocr_panel_lines()` 结果）进 OCR 段；
  - `MAX_BLOCKS=60`、每段 12 行上限（超出显示“还有 N 行”）。
- `log_overlay.py`：删「日志正文」+「OCR 扫描」两个控件，改成一个 `Text(wrap="char")` 分块流
  （指纹重建 + 保持滚动位置 + 「隐藏/显示」折叠）；`WINDOW_HEIGHT` 802 → 880，开窗按
  `min(880, 屏幕高-40)` 夹层；`push()` 保留 `_FULL_LINES`/延时进度条/切前台，删旧正文缓存；
  `start()` 接上 OCR 快照提供器，`stop()` 清空 feed。
- `web_ui.py`：`_log()` 在 `_overlay_key` 过滤**之前** `action_feed.push(str(msg), level)`
  （feed 需要完整行，包括被浮窗过滤掉的「动作 3s 未确认…」）。

### 3) 测试

- `tests/test_adapted_description.py`（新）：每种动作描述、卡名回退、`[执行]` 行不再是
  “开始点击”、结果行、`description` 为空回退推荐原文。
- `tests/test_action_feed.py`（新）：归块/置顶/续行/去重/执行段/告警色/阶段块头/忽略行/
  OCR 快照/上限/`render()` 前缀与分隔线/`clear()`。
- `tests/test_ocr_scan_panel.py`：保留 `ocr_panel_lines/status/空状态` 纯函数用例，控件用例改成
  合并面板（单 Text、块渲染、置顶、折叠）。
- `tests/test_overlay_eye_and_colors.py`：正文控件按新布局选择。

### 4) 文档

- `README.md`：浮窗小节换成 ASCII 分块图 + 块规则 + 执行描述示例。
- `SKILL.md`：一句“浮窗是分块流：OCR→执行，新块置顶；完整日志在网页/`ui_log_last.txt`”。
- `AGENT_CODE_MAP.md`：诊断入口补 `action_feed.py` 与 `AdaptedAction.description`。

## Non-Goals

- 不改任何自动化判定/点击/重试/OCR 逻辑；不改 `FSM_action.py` 的门禁与心跳。
- 不改 `src/ocr/**`（OCR 引擎/预处理/证据缓存）。
- 浮窗正文不再显示原始日志行（网页「实时日志」与 `ui_log_last.txt` 保留完整日志）。
- 不做用户可调的窗口高度/块数设置。

## Inputs

- 用户 2026-10-31 原话（见 Goal）与上一轮 R18H/R18I 的浮窗/重试改动。
- 现有 `ocr_panel_lines()`（R18E）作为 OCR 段渲染器复用。

## Outputs

- 单面板分块流浮窗（ASCII 见 README）；执行段写明动作 + 对象 + 目标 + 结果。
- `action_feed.py` 纯逻辑模块（可单测）。

## Interfaces

- Consumes: `web_ui._log()` 的日志行、`log_overlay._OCR/_PHASE`（OCR 快照与阶段）、
  `AdaptedAction`（适配层 DTO）。
- Produces: `action_feed.push/snapshot/render/clear/set_ocr_provider/set_phase`；
  `AdaptedAction.description`、`describe_adapted()`。

## Dependencies And Impact

- Depends on: R18D/R18E 的 OCR 面板数据源与 `ocr_panel_lines()`；R18I 的 `[执行]` 行格式。
- Impacted modules: 浮窗渲染、web_ui 日志分发、适配层 DTO。
- Public API impact: `AdaptedAction` 多一个带默认值的字段；`action_feed` 为新增 legacy 模块。

## Architecture Notes

```text
adapters/legacy/recommendation.py   AdaptedAction.description + describe_adapted()   ← 适配层
application/recommendation_flow.py  只 getattr 读字符串 + print("[执行] …")          ← 应用层（不 import 适配层）
action_feed.py                      root legacy 展示模块，只 import stdlib          ← 外壳
log_overlay.py / web_ui.py          消费 action_feed、复用 ocr_panel_lines()          ← 外壳
```

`src/hslegend` 内部依赖方向不变；`action_feed` 不被分层包 import；`check_architecture.py` 必须 PASS。

## Allowed Files

- `src/hslegend/adapters/legacy/recommendation.py`
- `src/hslegend/application/recommendation_flow.py`
- `action_feed.py`
- `log_overlay.py`
- `web_ui.py`
- `tests/test_adapted_description.py`
- `tests/test_action_feed.py`
- `tests/test_ocr_scan_panel.py`
- `tests/test_overlay_eye_and_colors.py`
- `tests/test_web_logs_wiring.py`
- `tests/test_overlay_dpi_scaling.py`（只改窗口高度断言 802→880 + 夹层）
- `tests/test_overlay_self_check_hint.py`（只改窗口高度断言）
- `tests/test_recommendation_retry.py`（只改「开始点击」旧断言）
- `README.md`
- `SKILL.md`
- `AGENT_CODE_MAP.md`
- `docs/tasks/R18J-overlay-feed-blocks.md`

## Forbidden Files

- `FSM_action.py`
- `config.py`
- `click.py`
- `log_state.py`
- `memory_watch.py`
- `src/ocr/**`
- `src/hslegend/domain/**`
- `src/hslegend/bootstrap/**`
- `src/hslegend/entrypoints/**`
- `src/hslegend/plugins/**`
- `src/hslegend/application/use_cases/**`
- `src/hslegend/adapters/legacy/actions.py`
- `src/hslegend/adapters/legacy/evidence.py`

## Acceptance

```powershell
uv run python -m unittest tests.test_adapted_description tests.test_action_feed tests.test_ocr_scan_panel tests.test_overlay_eye_and_colors -v
uv run python tools/run_headless_tests.py
uv run python tools/run_headless_tests.py --opt-in
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R18J
uv run python -m compileall -q src tests tools action_feed.py log_overlay.py web_ui.py
```

隐藏 Tk 几何复测：窗口高 = min(880, 屏幕高-40)，合并面板可见高度 ≥ 300px，状态行/按钮/延时条不重叠。
手工：重启控制台打一局，确认 ① 每步一块、OCR 在上执行在下；② 执行段写明“号位/卡名/目标 + 结果”；
③ 新块置顶；④ 能滚动回看。

## Risk And Rollback

- Risk: 归块规则靠日志行文本匹配，新增日志格式需要同步规则；浮窗正文不再显示原始日志（排障改看网页/`ui_log_last.txt`）。
- Rollback: 恢复 R18E 的「日志正文 + OCR 面板」两个控件与 `WINDOW_HEIGHT=802`；删除 `action_feed` 接线即可。

## Garbage-Code Check

- In-scope garbage to refactor: 旧浮窗 `_LINES` 正文缓存、独立 OCR 面板控件与刷新分支随合并删除。
- Out-of-scope discoveries to register: 结算界面「开始」按钮 40 轮超时（R18G 登记）仍未修。

## Git Requirement

One commit:

```text
feat(overlay): merge the OCR panel and log into one step feed
```

## Report Paths

- Implementer: `docs/agent/reports/R18J-implementer.md`
- Reviewer: `docs/agent/reviews/R18J-review.md`
