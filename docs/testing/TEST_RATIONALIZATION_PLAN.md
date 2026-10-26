# R02A Test Rationalization Plan

> 输入：`docs/testing/TEST_INVENTORY.md`。本计划只提出 R02C 动作，不执行删除。所有 `candidate-delete` 必须先在 R02C 创建替代测试并通过，再由 Controller 批准删除。

## 当前基线

- 默认 `unittest discover -s tests -t .`：1520 tests，exit 1，134.726s，1 failure、1 skipped。
- 唯一失败是 Node 前端往返测试的 GBK 解码/输出为空：`tests.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form`。
- 90 个 Python 测试文件、337 个测试类、24401 物理行；1 个辅助 JS 文件，209 行。
- 盘点决策：keep 1471、rewrite 12、move-native 2、candidate-delete 35。
- 35 个 candidate-delete = 源码串 30 + 危险行为验收 2 + 私有表漂移 3。
- 私有 patch 共 133 个方法 / 32 个文件；多数是注入日志/时钟/进程/窗口 seam，不逐条删除，只对 5 个需要真实 seam 的方法 rewrite。

## 目标分层

### 1. Domain unit（默认）

- 纯函数、不可变模型、规则归一化/求值、目标解析、日志分类与序列化。
- 禁止文件、Windows、Tk、Paddle、真实游戏、Node、sleep。
- 代表现有测试：`test_surrender_rules`、`test_card_catalog`、`test_battle_record`、`test_hs_window`、`test_ocr_*` 的纯解析部分。

### 2. Application（默认）

- fake ports、fake clock、取消令牌、重试/超时、动作门控、状态机与恢复路径。
- 保留 `test_battle_entry`、`test_cancel_manual_flow`、`test_draw_extra_delay`、`test_restart_watchdog`、`test_hand_*`、`test_*target*` 等行为覆盖。
- 新增测试不得通过 `inspect.getsource`、读 `.py` 文本或 patch 下划线函数证明接线。

### 3. Adapter contract（默认）

- 固定 frame/ROI/OCR/Power.log fixtures；Win32、鼠标、文件和点击边界全部 fake。
- 保留 `test_display_mapping`、`test_screen_regions`、`test_power_log_hand_entries`、`test_desktop_capture_scaled_roi`、`test_battlenet_play_button` 等。
- 坐标、延时、阈值当前仍属兼容面；R02C 不修改生产常量，只清理测试表达方式。

### 4. Web / CLI contract（默认）

- 允许 Windows 本机 loopback HTTP server 作为进程内集成：`test_hsctl`、`test_web_regions_api`、`test_web_selfcheck_api`、`test_web_hot_control`。
- 禁止外部 URL、真实后台服务、真实游戏和浏览器安装依赖。
- HTML 静态检查只保留用户可见 workflow / form name / 状态 schema 契约，不扩大到实现字符串。

### 5. Fake E2E（默认）

- 一个完整 fake one-game：选模式 → 卡组 → 换牌 → 回合 → 结算 → 停止；所有外部边界用 fake ports。
- 现有零散 flow 测试在 R02C/R03+ 应逐步合并为少量场景测试，不追求测试数量。

### 6. Native / external integration（opt-in）

- `tests/native/` 或显式环境变量门控；默认 discover 不收集。
- 真实进程枚举仅 `NativeProcessTests.test_can_enumerate_processes`、`test_finds_this_python_process` 两个方法；真实 Windows GUI/窗口/鼠标、真实 Hearthstone/Paddle/OCR smoke 也放这里。
- Node 前端测试改为独立 opt-in web integration；固定 `encoding="utf-8"`，移除 `C:\Users\...` 与 `D:\nodejs\...`。

## 推荐默认命令

```powershell
# R02C 完成分层后，默认核心门（示例）
uv run python -m unittest discover -s tests/unit -t .
uv run python -m unittest discover -s tests/contract -t .
uv run python -m unittest discover -s tests/e2e_fake -t .
```

过渡期可先保留单目录 discovery，并用显式模块列表排除 native/opt-in；不要依赖文件名猜测或环境变量静默跳过。

## 先重写，后删除

### R02C-0：建立替代测试

1. 修复 `test_surrender_frontend` 的 UTF-8 与机器路径问题。
2. 新增 fail-closed 点击测试：无窗口、未命中、未知坐标、目标失效时不点击。
3. 新增 fake Tk/widget factory 测试，覆盖浮窗布局、按钮文案、回调、DPR/geometry/wraplength。
4. 新增 fake flow/loop 接线测试，覆盖存活检查、拟人挂点、启动 milestone、版本面和日志确认。
5. 新增 CLI 可见契约测试，覆盖延时字段、生效提示和 screenshot kind，不比较私有表。
6. 重写 5 个私有 patch 测试，改为注入 metadata reader / selfcheck runner。
7. 将 2 个真实进程枚举测试迁入 opt-in native 模块；同类的 unknown/fake 分支继续留在 headless。
8. 将 `test_window_tracker.py` 6 个实时轮询线程同步测试改为 Event/Condition 或注入同步器。

## Candidate-Delete 台账

| 批次 | 文件 / 方法 | 数量 | 替代或非行为理由 |
|---|---|---:|---|
| B1 危险验收 | `test_click_safety_checks`: `test_clicks_when_hearthstone_window_is_not_found`, `test_background_window_activates_and_clicks_without_hit_testing` | 2 | 替代为 fail-closed：window=0 / point 未命中 / 坐标未知时 dispatch 不被调用。旧断言不是可保留行为。 |
| B1 表漂移 | `test_hsctl.TableDriftTests`: `test_delay_keys_match_config_whitelist`, `test_delay_effect_table_matches_web_ui`, `test_screenshot_kinds_match_web_ui` | 3 | 替代为 CLI 黑盒参数化：`delays show` 的字段/提示、`delays set` 的接受/拒绝、`screenshot` 的 kind/max-width 请求。 |
| B2 浮窗源码串 | `test_overlay_dpi_scaling.WindowWiringTests`: 3 个源码串方法 | 3 | fake Tk root：断言 scaling 设置、窗口 geometry 由 `px()` 计算、detail wraplength 使用缩放宽度。 |
| B2 浮窗源码串 | `test_overlay_eye_and_colors`: 8 个源码串方法 | 8 | fake widget tree/callback spy：眼睛绑定、日志 tag 渲染、危险按钮确认、按钮标签、状态行布局/换行、最小化标题栏。 |
| B2 浮窗源码串 | `test_overlay_human_like_status::test_brand_name_has_no_subtitle` | 1 | 渲染后的品牌标题只有标题、没有副标题；由 widget text 断言。 |
| B2 浮窗源码串 | `test_overlay_self_check_hint`: 2 个源码串/签名方法 | 2 | fake widget factory 断言按钮/弹窗/version callback 的实际接线。 |
| B2 浮窗源码串 | `test_overlay_tk_lifecycle`: 2 个源码串方法 | 2 | fake wrapper/thread 记录：先释放 frame 再 collect；start target 为 wrapper。 |
| B2 浮窗源码串 | `test_overlay_window_slider`: 2 个源码串方法 | 2 | fake Tk binding：Canvas slider、拖动事件 `return "break"`、root drag 忽略 slider 事件。 |
| B2 浮窗源码串 | `test_region_overlay_button::test_calibrate_button_exists_and_is_short` | 1 | 注入 button factory/place spy，断言“校准”按钮与回调；已有 panel state 测试保留。 |
| B3 流程源码串 | `test_fsm_liveness_stop::test_main_loop_and_battling_call_the_check` | 1 | fake loop 计数 calls；不读取源码。 |
| B3 流程源码串 | `test_human_like_scope::test_fsm_does_not_wire_the_human_like_hook_into_mulligan` | 1 | fake flow 构造参数断言 mulligan 无 post-action、对局推荐有。 |
| B3 流程源码串 | `test_start_latency`: 5 个源码串方法 | 5 | fake clock 证明不推进 2/3 秒；logger spy 证明 milestone；fake callback/widget 证明 starting 状态。 |
| B3 界面源码串 | `test_web_admin_banner::test_main_prints_the_banner_only_for_non_admins` | 1 | patch admin bool + capture stdout 调用 `main()`。 |
| B3 界面源码串 | `test_web_logs_wiring::test_run_uses_the_log_row_and_confirms_before_clearing` | 1 | fake widget factory、日志回调与确认取消/确认路径。 |
| B3 版本源码串 | `test_version.SurfaceTests`: `test_overlay_draws_version_label`, `test_cli_version_flag_uses_module` | 2 | 公开 CLI `--version` 与浮窗 version callback/标签行为；版本值/API 测试保留。 |

合计：35 个 candidate-delete。每个方法都在对应文件/分组中列出，并已有替代测试方向；没有“无替代直接删除”的条目。

## 非删除重写

| 文件 / 方法 | 当前问题 | R02C 动作 |
|---|---|---|
| `test_friendly_hand_target_battlecries::test_unreadable_card_data_falls_back_to_the_legacy_cards` | patch `_metadata_hand_target_card_ids` | 注入 metadata reader fake，保留回退行为。 |
| `test_hand_or_board_targets::test_unreadable_card_data_falls_back_to_the_legacy_card` | patch `_metadata_hand_or_board_card_ids` | 同上。 |
| `test_web_selfcheck_api` 3 个方法 | patch `web_ui` 私有 cache/runner | 注入 fake selfcheck runner，保留 cache/API/状态行为。 |
| `test_surrender_frontend::test_nested_config_survives_the_form` + helper JS | Node、本机绝对路径、GBK 解码 | 固定 UTF-8，删除机器路径，移到 opt-in web integration。 |
| `test_hearthstone_liveness.NativeProcessTests.test_can_enumerate_processes`, `test_finds_this_python_process` | 真实 ctypes 进程枚举 | 移到 opt-in Windows native smoke；同文件 13 个 fake 行为测试及另外 2 个注入 fake 的 NativeProcess 测试留在默认层。 |
| `test_window_tracker.TrackerTests` 6 个方法 | `time.time()` + `time.sleep(0.01)` 轮询后台 hook，最长 2s | 改用 Event/Condition 或注入同步器，禁止实时轮询和 wall-clock 等待。 |

## 删除批次

### Batch 1：安全与重复表（5）

- 先合并 fail-closed 点击测试。
- 先合并 CLI 黑盒契约测试。
- 删除 2 个危险验收 + 3 个 `TableDriftTests`。
- 门槛：默认 suite 0 failures；新的 fail-closed/CLI 契约测试全绿。

### Batch 2：浮窗源码串（19）

- 覆盖 `test_overlay_dpi_scaling`、`test_overlay_eye_and_colors`、`test_overlay_human_like_status`、`test_overlay_self_check_hint`、`test_overlay_tk_lifecycle`、`test_overlay_window_slider`、`test_region_overlay_button`。
- 一次只替换一个文件组，删除前运行新旧测试对照。
- 门槛：fake widget tree/callback 测试证明文案、布局、回调和生命周期。

### Batch 3：非浮窗源码串（11）

- 覆盖 `test_fsm_liveness_stop`、`test_human_like_scope`、`test_start_latency`、`test_web_admin_banner`、`test_web_logs_wiring`、`test_version`。
- 门槛：loop/flow/clock/stdout/version 公开行为均由替代测试覆盖。

### Batch 4：opt-in 迁移（2 move-native + 1 Node rewrite）

- 只移动 2 个真实进程枚举方法；2 个注入 fake 的 unknown 分支继续默认 headless。前端 Node 测试改为显式 opt-in web integration 并修 UTF-8。
- 默认平台测试不得因缺失 Node/Windows/游戏而 skip 或失败。

### Batch 5：确定性 rewrite（11）

- 替换 hand-target metadata 与 web selfcheck 私有 patch（5 个方法）。
- 把 `test_window_tracker.py` 的 6 个实时轮询测试改为 Event/Condition/注入同步器。
- 这些是 rewrite，不是 candidate-delete；完成前不删除对应文件。

## Headless 门禁与验收

1. 默认 suite 0 failures、0 errors，不能靠 skip 掩盖环境缺失。
2. 默认 suite 不要求真实 Hearthstone、GUI、鼠标、管理员、OCR 模型下载、外部网络或 Node；测试不得用 wall-clock/sleep 轮询线程。
3. 允许的 opt-in：真实进程/GUI/游戏、真实 OCR/Paddle、Node 前端、外部网络 mock smoke。
4. 每个 candidate-delete 的替代测试在删除前必须单独通过；记录命令、exit code、pass/fail/skip。
5. 删除后运行全量 class/模块组合回归，再运行默认门；检查 collection 数变化只能来自已批准删除。
6. 任何替代测试无法稳定 headless 化时，保留原行为测试并降级为 opt-in，不强行删除。

## 风险

- 浮窗源码串测试虽然反模式，但其中文案/布局是用户可见行为；替代测试不能只做快照，要断言触发条件和回调。
- `TableDriftTests` 删除后可能失去 CLI-Web 字段一致性保护；必须用可见契约覆盖全部字段。
- 只移动 2 个真实进程枚举后，默认 CI 仍保留 2 个 fake unknown 分支；真实 ctypes 分支需要 opt-in Windows job 或 release smoke。
- Node 前端测试目前是全量唯一失败；修复编码不等于降低契约，必须保留嵌套配置往返断言。
- 删除批次必须与 R02A 台账一一对应；若出现未登记删除，立即停止并回滚该批次。
