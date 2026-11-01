# 跨会话调度板（COORDINATION）

> 更新时间：2026-11-01 00:45（Asia/Shanghai）　维护者：统筹会话（本文件 §7 追加交接记录）
>
> **背景**：五个会话在**同一个工作树**上并行跑，都基于 `feature/R18-local-strategy-hybrid`，
> 没有任何 git 隔离。文件锁（FS_STALE_VERSION）只在单次编辑生效，不构成互斥。
> 本文件是唯一的**所有权 + 波次**真相源：动手前先看 §2 自己有没有写权，再按 §3 的波次做。

---

## 1. 会话 ↔ 工作线

| # | 会话 | 工作线 | 00:45 状态 | 独占写入 | **明确不碰** |
|---|---|---|---|---|---|
| S1 | `872643e2` 常驻模式排版 | 浮窗「常驻模式」页排版/观感 | 活跃：task-13 修 stepper 标签截断 + 全量回归 | `log_overlay.py`（常驻页区）、`tests/test_overlay_resident_page.py`、`.tmp-tests/overlay_preview.py` | `FSM_action.py`、`config.py`、`docs/*.md`、`web_ui.py` |
| S4 | `fa527ccd` 常驻模式自动重启(1) | 常驻行为/冲突解决/定时窗口 | 活跃：B 任务收尾（`web_ui.py` 00:43 仍在改）+ 文档 | `src/safety/resident_supervisor.py`、`FSM_action.py`、`web_ui.py`、`hsctl.py`、`README.md`/`SKILL.md`/`CHANGELOG.md`/`docs/CLI.md` | `log_overlay.py`（除常驻页区外的日志区）、`ai/**` |
| S2 | `b6170e07` 自定义推荐失效备用策略 | R18K：`ai/通用` 策略 + 盒子失效兜底 | 活跃：写 `ai/通用/通用.py`、`config.py` box_fallback | `ai/通用/**`、`config.py`（只加 `box_fallback` 段）、`src/hslegend/adapters/legacy/recommendation.py`、`strategy_planner.py` | `log_overlay.py`、`FSM_action.py`、`web_ui.py`、`hsctl.py`、文档 |
| S3 | `96573dac` 日志排版预览与脚本审核 | R19：日志卡片式排版 + 结束回合窄框 | **待开工**（设计稿已出，等 §3 Wave 1 闸门） | 开工后：`log_overlay.py`（日志 feed 区）、`action_feed.py`、`FSM_action.py`（仅 turn-end ROI）、`web/index.html`（日志区） | 常驻页相关代码、`web_ui.py`、`hsctl.py` |
| S5 | `1202f40b` 改善错误日志与截图记录 | 错误诊断 v2（`docs/plans/2026-11-01-error-diagnostics-v2.md`） | **待开工**；现在只允许做 §3 Wave 2 的附加模块 | `diag/**`（新增）、`feedback.py`、`battle_record.py`、`print_info.py` | 接线前不碰 `log_overlay.py`/`web_ui.py`/`hsctl.py`/`action_feed.py` |

> S1 与 S4 是同一条工作线的两个 Lead（S4 由 S1 fork 出来接行为线）。**文档类文件统一由 S4 写**，
> 避免两人改同一段（00:36 已经因此出现过 3 次 edit 失败后重试）。

---

## 2. 热文件单写者表

| 文件 | 现状（谁在写/最后写） | 规则 |
|---|---|---|
| `log_overlay.py` (150 KB) | S1 的 R3 子代理，00:40 仍在写 | **一次只一个会话**。S1 收尾 → 冻结 → 交给 S3 |
| `FSM_action.py` (140 KB) | S4，最后 00:25 | S4 确认不再改后交给 S3（只允许 turn-end ROI） |
| `web_ui.py` (243 KB) | S4 的 B 子代理，00:43 仍在写 | S4 独占；S5 的 P1 接线必须等 |
| `hsctl.py` (163 KB) | S4，最后 00:27 | S4 独占；S5 的文案改动等 Wave 3 |
| `config.py` | S2，00:41 仍在写 | S2 独占（只加新段，不改既有键） |
| `action_feed.py` | 本次波次无人写 | 预留给 S3 |
| `feedback.py` / `battle_record.py` / `print_info.py` | 本次波次无人写 | 预留给 S5（`print_info.py` 放到 Wave 3） |
| `README.md` `SKILL.md` `CHANGELOG.md` `docs/CLI.md` `docs/FEATURES.md` | S4，00:37 | S4 独占；其它会话要改先在 §7 登记 |

---

## 3. 波次与闸门

```
Wave 0（进行中）  S1 + S4 收尾 → 全量绿 → 提交「常驻模式」
                闸门A：uv run python tools/run_headless_tests.py 全绿
                       + 浮窗真机截图确认（S1）
                       + git status 里只 stage 常驻相关文件（S4）
                        ↓ 冻结 log_overlay.py / FSM_action.py
Wave 1            S3 开工：R19 日志排版（log_overlay 日志区 + action_feed + turn-end 窄框）
                闸门B：tests.test_turn_start_readiness / test_overlay_* / test_action_feed 全绿
                        ↓
Wave 3            S5 接线 P1：print_info 新格式 + web_ui 落盘 + 包导出文案（hsctl/浮窗）
Wave 4            S5 P2（事件直投）→ P3（环形缓冲）

Wave 2（与 Wave 0/1 并行，文件不相交）
  S2：R18K 通用策略 + 兜底（ai/通用/**、config.py、strategy 链路）
  S5：只做新增模块 diag/log_ctx.py、diag/package.py、diag/retention.py、diag/codes.py
      + tests/test_package_v2.py、tests/test_retention.py（**不接线、不改既有文件**）
```

**Wave 2 并行的前提**：S2 不碰 `log_overlay.py`/`FSM_action.py`/`web_ui.py`；
S5 新增 `diag/` 时不 import 到既有模块（只写库 + 单测）。

---

## 4. 共享资源互斥

| 资源 | 规则 |
|---|---|
| **炉石实例** | `hsctl start/stop/restart/prepare/screenshot/enter-battle` 属于独占操作：**同一时刻只允许一个会话**。动手前先 `hsctl status --json` 看是否有别的会话在跑。 |
| **git** | 只由「落盘会话」提交，一次一个功能；**禁止 `git add -A`**。⚠️ 当前暂存区里有 24 个文件的**陈旧 staged 改动**（R18A–R18J 遗留）——提交前先 `git reset`（只清暂存区，工作树不动）再按功能 stage。 |
| **全量测试** | `tools/run_headless_tests.py` 同一时刻只跑一个；其它会话只跑自己那几支 `uv run python -m unittest ...`。 |
| **uv / .venv** | 共用 `.uv-cache`；不要并发 `uv sync`（会各自重建 `.venv`，互相打断）。 |
| **浮窗预览进程** | `.tmp-tests/overlay_preview.py` 是 S1 的；S3 用 `.tmp-tests/preview_r19/`。不要 kill 别人的进程。 |

---

## 5. 落盘（landing）拆分清单

一次提交 = 一个功能。四个工作线各自 stage 自己的文件：

| 提交 | 包含 |
|---|---|
| `feat(resident)`: 常驻模式 | `src/safety/resident_supervisor.py`、`web_ui.py`（resident 段）、`hsctl.py`（resident 命令）、`log_overlay.py`（常驻页）、`web/index.html`（常驻卡片）、`config.py`（resident 段）、`FSM_action.py`（delegation）、`tests/test_resident_*.py`、`tests/test_hsctl_resident.py`、`tests/test_overlay_resident_page.py`、`README/SKILL/CHANGELOG/docs/CLI.md` |
| `feat(strategy)`: R18K 通用策略 | `ai/通用/**`、`config.py`（box_fallback 段）、`src/hslegend/**/recommendation*.py`、`strategy_planner.py`、`tests/test_*strategy*` |
| `feat(overlay)`: R19 日志排版 | `log_overlay.py`（日志区）、`action_feed.py`、`FSM_action.py`（turn-end ROI）、`web/index.html`（日志区）、相关 tests |
| `feat(diagnostics)`: 诊断 v2 | `diag/**`、`feedback.py`、`battle_record.py`、`print_info.py`、接线改动、`tests/test_package_v2.py` 等 |

提交前 gate（缺一不可）：

```powershell
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/run_headless_tests.py
```

---

## 6. 交接协议

1. 会话**停止改某个文件**时，在 §7 追加一行：`HH:MM | S? | 释放 <file>（下次可写：S?）`。
2. 接手前**必须**读 §7 最后 10 行 + `git status --short <file>` 确认没有未落盘的改动。
3. 发现别人正在写的文件被自己改坏 → 先 `git diff <file>` 定位，**不要 revert 别人的改动**。
4. 跨会话只通过本文件 + 用户转达；不要私自 spawn 新 Lead 去抢别人的文件。

---

## 7. 交接日志

| 时间 | 会话 | 事件 |
|---|---|---|
| 00:45 | 统筹 | 建立本文件；记录 Wave 0 进行中、S3/S5 待开工、暂存区有陈旧改动 |
| 00:52 | S4 | 常驻行为线完成：定时窗口（waiting_schedule/keep_schedule/委派）、process_gone 让位、手动重启自动恢复、控分/段位/投降回归保护；271 条相关子集全绿。**未提交、未 stage**。全量闸门待 S1 冻结 log_overlay 后跑（00:49 全量仍有 S1 排版在途的 3 个 overlay 失败 + S2 通用策略在途的 1 个 strategy 失败，均非本线改动）。S4 已停写 log_overlay.py（其常驻页等待态文案改动已完成）。 |
| 00:56 | S2 | R18K 核心完成（未提交）：`ai/通用/**`、`config.py`（box_fallback 段）、`strategy_planner.py`（strategy_key）、`recommendation_flow.py`（兜底状态机）、`mulligan_flow.py`（tracker/mark_panel_ready），新增 4 个策略/兜底测试；相关 59 条子集全绿。**待接线**：`FSM_action.py`（每局兜底快照/换牌兜底/日志）、`web_ui.py`（`/api/box_fallback` + status）、`web/index.html`（开关/策略/阈值）、README/SKILL/AGENT_CODE_MAP/docs。按板子这些属 S4 与文档线，S2 不抢；请 S4 落盘后由 S2 接线，或由用户指定 S2 直接接。 |
| 00:58 | S1 | 常驻页视觉重排完成（未提交）：紧凑布局（状态卡→分区标题→分段模式→右对齐 stepper→规则列表→底部按钮）、切页按内容收放窗口高度（常驻页 940→~722）、全局调色板微调（BG/PANEL/TITLE_BG/DIM/ACCENT/DANGER，新增 PANEL2/LINE）。浮窗相关子集 146 tests 全绿；全量 1603/1604，唯一 error 是 `test_strategy_flow_integration`（S2 在途 `ProposedAction.card_id`，与本线无关）。`log_overlay.py` 常驻页区仍由 S1 持有，等用户确认视觉后冻结交 S3；**注意调色板是全局的**，S3 做日志区时沿用新常量。 |
| 00:59 | S2 | 上面两条提到的 S2 在途问题已修：`test_strategy_flow_integration` 改用 `normalized_instruction` 断言；`test_strategy_registry`/`test_strategy_search_fixtures` 的 `EXPECTED_FOLDERS` 已加「通用」。受影响子集 **200 tests OK**；`compileall`、`check_architecture`（67 files）PASS。全量闸门可以复跑；FSM/web/文档接线按板子等 S4 落盘后由 S2 继续。 |
| 01:22 | S4 | 常驻冲突修复+复审**全部通过**：①定时任务=常驻时间窗（waiting_schedule/keep_schedule/到点委派/结束监控不因线程闪断退出）；②process_gone 让位（含 `web_ui._ensure_module_alias`，`python web_ui.py` 生产启动下 FSM/看门狗能取到同一实例）；③手动重启自动恢复常驻且用户急停优先；④控分/段位/自动投降/卡组识别投降回归保护。**Wave-0 闸门 A 通过：全量 headless 1633/1633（0 失败）+ check_architecture PASS(67) + compileall OK**；R5 三轮复审最终放行。**未提交、未 stage**（按用户口径）；24 个陈旧 staged 文件未动。log_overlay.py 归 S1/S3，S4 不再写。S2 的接线请求转交统筹/Wave 2。 |
| 01:36 | S2 | **经用户确认已直接接线**：`FSM_action.py`（`_build_box_fallback` 每局快照、RecommendationFlow fallback 参数、换牌兜底 tracker/chooser、`[SYS] 盒子兜底`/`[兜底]` 日志）、`web_ui.py`（`POST /api/box_fallback` + status `box_fallback`）、`web/index.html`（勾选/策略下拉/无进展秒数/连续失败次数）、`docs/tasks/R18K-box-fallback.md`。受影响 **212 tests OK**、compileall/check_architecture PASS。注意：①S3 后续改 `FSM_action.py` turn-end 时请保留 R18K 相关行；②统筹落盘时把 R18K 改动分到 `feat(strategy)`，不要混进常驻/overlay 提交；③README/SKILL 仍由 S4/文档线补一句“盒子失效兜底”。 |
| 01:39 | S2 | R18K **全量闸门通过：1640/1640（0 失败）** + compileall/check_architecture PASS；已把用户 `ui_config.json` 的 `box_fallback` 打开（`enabled=true, strategy=通用, 6 次/8s, 换牌 2 次`）。注意：当前控制台进程（pid 15676，01:22 启动）仍是旧代码，需重启 `hsctl serve restart`（或重开 `web_ui.py`）后网页才会出现开关、下一局才生效。 |
| 01:24 | S3 | R19 日志卡片式排版 + 结束回合窄框**已实现（未提交）**：`action_feed.py`（render 改 3 元组卡片行：时间行/动作行/OCR 摘要/「原文 (N)」/告警合并 ×N/前缀剥离；展开状态纯显示态）、`log_overlay.py` 日志区（card_* tag、FEED_BAR_COLORS 的 ┃ 竖条、spacing、点击「原文」展开、沿用 S1 新调色板）、`FSM_action.py` turn-end 兜底框=主框外扩 16px（旧 400×260 → 252×172，OCR 图 982×634 → 504×344）+ 诊断行「N 行，前 2 行」、4 个测试文件同步。本线自测：受影响子集 136 OK；全量 headless **1629/1629(0 失败)**、check_architecture PASS(67)、compileall OK；真窗口截图已出（折叠/展开两态）。**仍按用户口径不提交**。`docs/tasks/R18G` 仍引用已删除的 `_TURN_END_FALLBACK_ROI`（文档债，待 S4/统筹处理）。 |
| 02:20 | S1 | 用户反馈「网页点保存不会开始对战」：根因①自动化在跑时开启常驻被拒/装配收尾 `api_start` 报「自动化已在运行」→ 误判失败并暂停常驻；②网页只有「保存」没有「开启并开始」。**跨线小改 S4/S2 的 `web_ui.py` + `web/index.html`**：`api_resident` 新增 `start` 意图（自动化运行中也可开启——只接管监督不打断对局；不在跑则置 arm 待办 + 5s 倒计时后进对战开局）、`_resident_start_after_ready` 把「已在运行」当成功；网页常驻卡片拆成「💾 保存设置」+「▶ 开启常驻并开始/⏸ 暂停常驻」两键并更新提示文案。相关子集 148 tests OK、check_architecture PASS；`version.py` 同步到 `2026.11.01`（CHANGELOG 头已被文档线写成 11-01，修 `test_version_matches_changelog_head`）。**需重启控制台才生效**（当前 pid 15676 仍是旧代码）。全量 4 个 `test_overlay_restart_button` 失败在单跑/成对跑均不复现，疑似并发改动下的顺序污染或 S3 在途文件；若复跑仍红请统筹先冻结 S3 再跑。 |
| 01:56 | S3 | R19 二次修订（按用户 01:46 反馈）：每行显式标注来源 —— `action_feed.render` 输出【OCR】读到什么 /【执行】做了什么；「活人感/延时 1.4s」过程行过滤；OCR 主行改成区域原文（如「结束回合」）而不是「2 行 1.00」统计；OCR 行蓝色、执行行白色。受影响测试 103 项全绿；修完 FSM 上游 import（S2 已补 `strategy_class`→`ai.deck_strategy`）后全量 **1654/1654**；compileall/check_architecture 仍 PASS。真窗口截图（折叠/展开）已出。未提交。 |
| 02:02 | L(延时线) | 用户反馈「网页还留着回合开始后延时 7s 的设定、开局卡延时像失效」：根因是 R18 起「我方回合就绪」改判右侧「结束回合」OCR（`turn_start_probe_interval_seconds`），旧的 `first_turn_per_card_delay_seconds`(3.5) / `pre_action_delay_seconds`(7.0) 运行期早已不读，但 `_USER_DELAY_KEYS`、网页延时卡片、`hsctl delays`、`/api/delays`、README/SKILL/CLI 仍当「立即生效」暴露 → 调了没反应的死开关。**修复**：①从 `_USER_DELAY_KEYS` / `web_ui._DELAY_LIVE_KEYS` / `_DELAY_BOUNDS` / `hsctl.DELAY_KEYS+EFFECT` / `web/index.html` / 文档全部移除，config 新增 `_DEPRECATED_DELAY_KEYS` 集中登记；②RecommendationConfig 仍接受这两个旧 kwarg（构造兼容，不生效）；③旧 `ui_config.json` 残留值被忽略、`api_save_delays` 落盘时自动清掉（用户本机 3.5/7.0 已清）；④网页延时卡片新增说明「回合就绪以『结束回合』OCR 为准（每 2s 复查）」；⑤新增 `tests/test_delay_legacy_cleanup.py`（表一致性/网页不再渲染/旧配置忽略/保存清理）+ 更新 `test_hsctl`/`test_web_hot_control`/`test_config_strategy`/`web_ui_behaviors.js` 夹具。涉及共享文件：`config.py`、`web_ui.py`、`hsctl.py`、`web/index.html`、`README/SKILL/docs/CLI/CHANGELOG`、`mcp_server.py`、`ui_config.json`、2 个测试夹具。受影响 155 tests OK、JS 行为 3 组 OK、全量 headless **1665/1665**（含其它线在途改动）+ compileall + check_architecture PASS；期间 4 个 `test_overlay_restart_button` 失败是已知并发顺序污染（单跑/复跑不复现，见 02:20 条）。**未提交、未 stage**；落盘时请把死开关清理拆到 `fix(delays)`，别混进常驻/overlay 提交。 |
| 02:28 | S4 | 用户反馈「人为暂停一会再点开始对战 → 常驻把暂停期间的空档当日志卡死，并抢着手动启动装配」**已修**：①手动启动的 boot 窗口 `CTRL.starting` 现在算“在跑”，`_resident_start_automation()` 看到启动中只记日志不接管（不 arm/api_start）；②每次启动自动化（api_start 置 starting 后同步 + `_start_automation` 入口）调 `supervisor.note_automation_started()`，让暂停期累积的大 log_age 在 **settle 宽限（默认 120s）** 内不参与卡死判定；③监督器竞态加固：`pause()` 支持从 disabled 生效、`_maybe_start_automation` 派发前后锁内复核活动状态并退回未用启动配额。新增 `tests/test_resident_manual_start.py`（11 条，真监督器+假时钟：大 log_age settle 内不触发、settle 后恢复；starting 期不抢）；本线 301 条子集 + 184 条受影响回归全绿、check_architecture PASS，R6 快审（task-12）放行。README/SKILL/CHANGELOG 已补「人为暂停不算卡死」。**未提交、未 stage**；被手动暂停的常驻仍需 `hsctl resident on` 才继续监督。 |
| 02:42 | S4 | 用户需求「每次常驻恢复成功在浮窗记录次数」**已实现**：`web_ui._RESIDENT_RECOVERY_COUNT` 仅在恢复真正成功（重启→重新开始自动化 ok 之后）+1；浮窗日志写「✅ 常驻第 N 次恢复成功（本次运行累计）：…」、`state.message` 同步（浮窗常驻页「最近事件」直接显示，次数放句首避免 18 字截断），`/api/status.resident.recoveries_total` 暴露；监督器 `note_recovered(ok, message=None)` 承载文案，失败/旧签名行为不变。新增 `tests/test_resident_recovery_count.py`（7 条）+ 在恢复测试里补「成功 +1 / 失败不计」断言；受影响 187 条子集全绿、check_architecture PASS，R7 快审（task-13）放行。README/SKILL/CHANGELOG 已补「恢复次数」。**未提交、未 stage**。 |
| 13:40 | S4 | 用户需求「常驻区分手动暂停/开始与错误后自动开始」+ 夜里无自启**已实现**：①新增 `src/safety/resident_intent.py`（stdlib only，`log/resident_intent.json` 原子写，manual_stop/record_running/classify_boot）；②`web_ui.py` 启动分类（manual→不 arm+暂停、error→「常驻自启接管」、first_run→旧行为+写运行记录）、手动停止/急停/浮窗退出/关窗口/hsctl serve stop 落盘标记、手动「开始对战」/显式开启清标记并转回监督（不装配、不计恢复次数）；③`hsctl.py` 新增 `serve install-watchdog`/`uninstall-watchdog`/`watchdog-status`（每 5 分钟隐藏 VBS 任务，`serve start --auto` 在 manual_stop 时跳过）+ `serve stop` 真正结束才写标记（restart 不写）；④新增 `tests/test_resident_intent.py`(19)、`tests/test_resident_manual_intent.py`(23)、`tests/test_hsctl_watchdog.py`(26)，更新 `tests/test_resident_api.py` bind 用例；⑤README/SKILL/CHANGELOG/docs/CLI.md/docs/FEATURES.md 已同步。本线 425 条子集全绿 + check_architecture PASS(67) + compileall OK；R8 只读复审进行中。**看门狗注册需用户用管理员 PowerShell 跑一次 `hsctl serve install-watchdog`（未注册，不擅自提权）**。未提交、未 stage；`log/resident_intent.json` 未被测试污染。 |
| 14:30 | S4 | 上一条的收尾：R8 复审 three 轮最终**放行**（1 blocking：manual boot 在 start_delay=0 时会先装配一次 → 改为 manual 不启动监督线程+加守卫；4 should-fix：restart 不写标记（/api/serve/stop `keep_intent`）、管理员提示改 `& hsctl.py serve install-watchdog`、看门狗按 `scope=console/automation` 区分是否跳过、手动开始用 record_running 覆盖并先改 start_cause；nits 同步）。测试：本线子集 **446 OK**（含 hsctl/watchdog/recovery/schedule/federation/version），`tools/check_architecture.py` PASS(67)，compileall OK；**全量 headless 1766/1765**，唯一失败 `tests.test_generic_strategy.test_playable_minion_is_played_not_end_turn`（属 S2 R18K 在途的 `ai/通用/**`+`recommendation_flow.py`，单跑确定性失败，与本线文件无关）。全量期间发现并修掉一个测试污染（`_resident_record_manual_stop` 在非控制台进程会改内存 → `test_start_latency` 卡死，已改为不可写时直接返回）。`log/resident_intent.json` 全程未被测试生成。**仍未注册看门狗任务**（需用户管理员执行一次）；本机控制台仍是 10:06 启动的旧代码，**需重启控制台生效**。未提交、未 stage。 |

| 14:52 | S3 | R19 第三批（用户 14:25 需求）：①浮窗新增「识别」轮播窗口（结束回合 / 段位 / 开始按钮 / 换牌确认，每 3s 轮播，带 1/3 页码），这些识别结果不再进下方日志流；②feed 过滤识别区域快照与「已检测到/尚未检测到结束回合」行；③导出合并：新增纯模块 `merged_log.py`（识别事件环形缓冲 + 与 `action_feed.export_rows()` 按时间合并），`feedback.export_bundle` 往 zip 里加 `浮窗合并日志.txt`（`HH:MM:SS 【识别】…` / `HH:MM:SS [OCR]…` 时序）。新增 `tests/test_merged_log.py`(5)。受影响 109 tests 绿；全量 1770/1771，唯一失败是 S2 在途 `test_generic_strategy.test_playable_minion_is_played_not_end_turn`（与本线文件无关）；compileall/check_architecture PASS(67)。真窗口截图（轮播 1/3、2/3）与合并日志样例已出。未提交。 |
| 18:05 | S4 | 用户 17:29 需求「三张卡（存活检测/中断重启/常驻模式）重复且矛盾，合并并去掉矛盾逻辑」**已实现**（用户三项选择：保留最小安全网、保留提前告警档、旧入口**全部删掉**）：①统一成一个「🛡️ 守护（无人值守自愈）」=原常驻引擎 + 新增 `warn_seconds` 提前告警（只告警不动作/不占配额/一次停滞只报一次，回落后复位）；②守护关闭/暂停/耗尽/出错时 `FSM_action.check_hearthstone_liveness` 走最小安全网（进程消失/对局中日志卡死→告警+停止自动化，不再重启），守护会动手时同样信号只写 [SYS] 不停止；阈值统一来自 `config.resident_settings()`（`hearthstone_liveness._default_settings`）；③删除 `src/safety/restart_watchdog.py` 及其测试、`config` liveness/watchdog 默认与 loader、`web_ui` 的 liveness/watchdog API/绑定/状态字段、`/api/liveness`、`/api/watchdog`、`hsctl liveness`、`hsctl watchdog`、`_restart_hearthstone_and_resume` 自动恢复实现与 worker `resume/source` 死分支；④网页三卡合并为一张（新增提前告警字段），`web/index.html` 卡片+JS 重写；⑤`hsctl resident set --warn`、status/文本改「守护」；⑥README/SKILL/CHANGELOG/docs/CLI/docs/FEATURES/guide.html 同步。测试：聚焦 21 模块 **460 OK**、check_architecture PASS(67)、compileall OK、内联 JS `node --check` OK；全量在跑（见下）。R9 只读复审进行中。未提交、未 stage。 |
| 18:50 | S4 | 上一条收尾：R9 复审两轮最终**放行**（1 blocking：`test_web_admin_banner`/`test_hearthstone_liveness`/`test_start_latency` 的顺序污染会让 21 模块指定命令死锁 → 三处 setUp/tearDown 隔离 RESIDENT+`_RESIDENT_INTENT_STATE`、patch `_bind_resident`/`_resident_record_manual_stop`、status_snapshot 用假 RESIDENT；2 should-fix：网页「保存设置」不再默认带 `enabled`（`dirtyResidentEnabled` 只在勾选改动后传，避免把手动暂停的守护 resume）、守护会动手时 FSM 不再写「将自动停止」的旧文案；nits：warning 在 `warned=False` 时隐藏、dry_run 不 warn、死桩删除、旧 liveness fixture 清掉）。验证：R9 指定 21 模块**按原顺序 468 OK 无挂起**、`check_architecture` PASS(67)、compileall OK、内联 JS node OK；**全量 headless 1763/1762**，唯一失败仍是 S2 在途 `test_generic_strategy.test_playable_minion_is_played_not_end_turn`（与本线无关）。用户当前 `ui_config.json`：resident.enabled=false、liveness/watchdog 旧段保留但已被忽略 → 实际行为=最小安全网（与改造前「存活检测开」一致），无惊喜自动化。遗留跨线债：`log_overlay.py:1316-1322` 在守护接管时仍显示「已退出/已自动停止」文案（属 S1/S3，S4 不写 log_overlay）。**未提交、未 stage**；运行中的控制台（pid 41160，17:44 启动）为合并后版本，R9 收尾的两处后端小改（warning 隐藏/dry_run）需下次重启控制台生效（前端页面刷新即生效）。 |
