# R02D Test Cleanup Plan

> 输入：`docs/testing/TEST_PORTFOLIO_AUDIT.md`。本计划只描述 R02E 及后续 cleanup；当前 R02D 未删除、移动或重命名任何测试。每个批次必须单独 commit、单独 review、单独回滚。

## 1. 硬性规则

1. 先建立 replacement/equivalent gate，并在旧测试仍存在时跑绿，再删/搬旧测试。
2. 每批只动该批列出的 `file::Class.method`；若 AST 身份变化立即停止。
3. 批次提交前记录：命令、exit code、run/pass/fail/error/skip、count delta。
4. 默认 headless 必须 0 failure、0 error；不允许新增 skip。
5. opt-in 门的 required skip 必须失败：任何 Node/native/tooling 缺环境应报错并给出设置方式，不静默跳过。
6. 不删除 fail-closed、歧义、取消、deadline、safe-click、architecture purity 和真实用户/API 契约。
7. 不做生产代码重构；若 replacement 需要生产 seam，停止并另立任务。
8. 回滚单位就是单个 batch commit；不混入其他 cleanup。

## 2. 基线与总目标

当前基线（2026-10-27，base `75dc30899377d5b8b56a97e842200b22c7128c3d`）：

- 全树 AST：108 文件 / 29,240 行 / 366 类 / 1,691 方法。
- 默认 runner：1,525 run / 1,524 pass / 0 fail / 0 error / 1 skip。
- 普通 discover：1,688 run / 1,687 pass / 0 fail / 0 error / 1 skip。
- 默认 runner 与 discover 的差异：分层 `tests/domain`、`tests/application`、`tests/adapters`、`tests/plugins` 共 163 方法不在默认 runner 中；因此分层测试删除只影响 discover，不影响 default。
- 显式 opt-in：3 run / 3 pass / 0 fail / 0 error / 0 skip。
- 清理候选：40 个方法 = A 类 MUST-REMOVE 4 + M 类 MERGE 8 + R 类 REWRITE 14 + O 类 MOVE-OPT-IN 14。

目标不是压低测试数，而是：去掉私有表/源码实现断言和非确定性默认环境依赖，同时保持行为门可追溯。

## 3. 批次总览

| 批次 | 主题 | 候选 ID | 方法数 | 独立 commit 判据 |
|---|---|---:|---:|---|
| B1 | duplicate table / private list drift | A01, A02, R06, R07 | 4 | 公开 CLI/Web/config roundtrip 门先绿 |
| B2 | DTO duplicate boundary assertion | M01 | 1 | confidence/max_turns 断言已并入目标方法 |
| B3 | web implementation strings | M02-M05, R02, A04, R10-R15 | 12 | opt-in JS DOM 行为门先绿 |
| B4 | overlay lifecycle private patch | M06-M08, R01, A03 | 5 | 公共 thread/Tk cleanup 场景先绿 |
| B5 | private seam / mock-call-only | R08, R09 | 2 | 公共文件/状态行为门先绿 |
| B6 | wall-clock timeout assertions | R04, R05 | 2 | Event-based timeout 门先绿且无 sleep 上限 |
| B7 | native/window/MCP gate extraction | O01, O02, O03, O13 | 4 | opt-in native gate 全绿，默认 suite 不再 skip |
| B8 | tooling/environment gate extraction | O04-O11, O14 | 9 | tooling opt-in gate 全绿，manifest 静态门保留 |
| B9 | repository gitignore check extraction | O12 | 1 | CI/tooling 检查可执行 |

合计 40 个方法，互不重复。批次之间除明确列出的替换文件外无共同源文件，可独立回滚；B3 的新 JS harness 只被 B3 使用，B4 的 overlay fake 只被 B4 使用。

### 3.1 按推荐顺序的精确数量账本

| 步骤 | default 累计 | discover 累计 | CI opt-in 累计 | native-live 累计 | 净变化说明 |
|---|---:|---:|---:|---:|---|
| baseline | 1,525 | 1,688 | 3 | 0 | 分层 163 方法属于 discover、不在 default |
| B1 | 1,523 | 1,686 | 3 | 0 | 删除 2 个 top-level 方法 |
| B2 | 1,523 | 1,685 | 3 | 0 | 合并 1 个 application 方法，只减少 discover |
| B5 | 1,523 | 1,685 | 3 | 0 | 原位改写，不改变方法数 |
| B9 | 1,522 | 1,684 | 4 | 0 | O12 从 top-level 移入 CI opt-in |
| B8 | 1,514 | 1,676 | 13 | 0 | 9 个环境方法移入 CI opt-in，新增 1 个静态默认方法 |
| B7 | 1,511 | 1,673 | 16 | 1 | 3 个移入 CI opt-in，O01 移入 native-live；新增 1 个静态默认方法 |
| B6 | 1,511 | 1,673 | 16 | 1 | 2 个 adapter 方法原位改写 |
| B4 | 1,507 | 1,669 | 16 | 1 | 5 个 top-level 方法合并为 1 个默认场景 |
| B3 | 1,495 | 1,657 | 19 | 1 | 12 个 top-level 方法由 3 个 CI opt-in JS 方法取代 |

最终目标：default **1,495**，discover **1,657**，CI opt-in **19**，native-live **1**，全树 **1,677** 个真实 TestCase 方法。任何 `run` 数与账本不一致都必须解释，或回滚该 batch。

## 4. 批次明细

### B1 duplicate table / private list drift

**范围**

- MUST-REMOVE `tests/test_web_hot_control.py::HotChannelTests.test_delay_effect_scope_table`
- MUST-REMOVE `tests/test_draw_extra_delay_settings.py::ConfigTests.test_key_is_part_of_the_user_delay_whitelist`
- REWRITE `tests/test_screen_regions.py::CalibrationTargetTests.test_interface_recognition_boxes_match_battle_entry`
- REWRITE `tests/test_roi_config_frame.py::RecommendationConfigIntegrationTests.test_the_rank_box_falls_back_to_the_code_default`

**替换 / 等效门**

- 保留并运行公开 `delays show`/`delays set`、Web `api_save_delays`、`_current_delays` 契约。
- 对 `test_interface_recognition_boxes_match_battle_entry`：删除三组跨模块常量相等断言，保留 min-size；把 screen_regions target 到 `battle_entry.identify` 的黑盒识别断言并入现有 R06 方法，使用固定 frame，不新增方法。
- 对 rank box：保留默认坐标，增加保存后加载 roundtrip；不再查 `_USER_BOX_KEYS`。

**Risk**

低到中。公开字段/坐标契约已经存在，但 `screen_regions` 到 `battle_entry` 的默认框一致性必须由行为替代，而不是只删表比较。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_hsctl.CliDelayAndScreenshotContractTests tests.test_draw_extra_delay_settings.WebSettingsTests tests.test_click_safety_checks.ClickSafetyContractTests tests.test_screen_regions.CalibrationTargetTests tests.test_roi_config_frame.RecommendationConfigIntegrationTests
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
uv run python -m unittest discover -s tests -t .
```

B1 的公开等效门为 31 个方法，R06/R07 在现有方法内改写，所以改写前后都应是 31/31 通过。A01 所在 `HotChannelTests` 从 19 改为 18，A02 所在 `ConfigTests` 从 3 改为 2；B1 分组总数从 53 改为 51。相对 baseline：default `1,525 -> 1,523`，discover `1,688 -> 1,686`，opt-in 保持 `3`。

**Rollback**

revert B1 commit；若新识别黑盒测试已加到 helper，确保只回滚该 batch 的 helper/branch，不触碰其他批次。

### B2 DTO duplicate boundary assertion

**范围**

- MERGE `tests/application/test_dto.py::DtoContractTests.test_invalid_confidence_and_status_are_rejected`

**替换 / 等效门**

- confidence `1.1`、`-0.1` 断言并入 `test_observations_are_immutable_and_serializable`。
- `OneGameCommand(max_turns=0)` 断言并入 `test_max_turns_requires_an_exact_positive_int`。
- 不删除 `test_all_dtos_are_frozen_slots_dataclasses`、serialization、nested finite、max_turns exact-int contract。

**Risk**

低。只是重复边界表达，不降低 fail-closed 面。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.application.test_dto
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

Expected：`tests.application.test_dto` 从 7 个方法变为 6 个，全部通过；default 仍为 `1,523`，discover 精确 `1,686 -> 1,685`，opt-in 保持 `3`。

**Rollback**

revert B2 commit；原断言可原样恢复。

### B3 web implementation strings

**范围**

- MERGE `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_toggle_switches_text_and_visibility`
- MERGE `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_choice_is_remembered_locally`
- MERGE `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_defaults_to_expanded`
- MERGE `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_failure_auto_expands_without_overwriting_the_preference`
- REWRITE `tests/test_selfcheck_collapse.py::ScriptHelpersTests.test_collapse_helpers_are_initialised_on_load`
- MUST-REMOVE `tests/test_selfcheck_collapse.py::ScriptHelpersTests.test_methods_use_the_set_helper`
- REWRITE `tests/test_draw_extra_delay_settings.py::WebPageTests.test_page_sends_and_renders_the_value`
- REWRITE `tests/test_human_like_minion_hover.py::WebPageTests.test_page_sends_and_renders_the_switches`
- REWRITE `tests/test_web_prepare_flow.py::OverlayPageTests.test_overlay_button_label_follows_status`
- REWRITE `tests/test_web_prepare_flow.py::OverlayPageTests.test_toggle_refreshes_the_status_immediately`
- REWRITE `tests/test_web_prepare_flow.py::OverlayPageTests.test_start_button_still_depends_on_prepared`
- REWRITE `tests/test_version.py::SurfaceTests.test_web_renders_version_from_status`

**替换 / 等效门**

1. 新建 `tests/native/test_web_ui_behaviors.py`，固定 3 个 opt-in Node DOM 行为方法：
   - selfcheck collapse 默认值、切换、记忆、失败展开；
   - settings controls 的提交、状态回填和非法值；
   - overlay/version 状态文案、按钮可用性和占位符。
2. 保留默认层的用户可见 HTML 结构契约：id、中文文案、form/status schema；不保留函数体表达式。
3. 新 JS 门不得依赖机器路径；Node 缺失必须 failure，不静默 skip。

**Risk**

中。需真实执行页面 script；不得用 HTML 快照或函数名断言替代。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in
```

Expected：默认移除 12 个旧方法、增加 0；opt-in 新增 3 个行为方法。最终累计 default `1,507 -> 1,495`，discover `1,669 -> 1,657`，opt-in `17 -> 20`。

**Rollback**

revert B3 commit，并移除该 batch 新增的 opt-in JS 行为模块；不得保留半迁移测试。

### B4 overlay lifecycle private patch

**范围**

- MERGE `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_wraps_run_in_a_finally_block`
- MERGE `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_collects_before_releasing_started`
- MERGE `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_wrapper_collects_after_run_returns`
- REWRITE `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_start_records_wrapper_as_thread_target`
- MUST-REMOVE `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_gc_is_imported`

**替换 / 等效门**

- 在 `tests/test_overlay_tk_lifecycle.py` 新增 1 个公共生命周期场景方法，通过 `log_overlay.start()`、注入 thread factory 和 fake Tk 覆盖异常/正常退出、`_STARTED` 释放和 reopen。
- 不 patch `_run` 后只 drill 私有顺序，也不断言 `gc` 是否 import。
- 保留 `JoinPreviousThreadTests`、`ReopenRaceTests`、`DeferredOpenTests` 等用户回归。

**Risk**

中高。Tk wrong-thread cleanup 是真实用户回归；替代门必须覆盖异常路径。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_overlay_tk_lifecycle
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

Expected：默认移除 5 个旧方法、增加 1 个场景方法。最终累计 default `1,511 -> 1,507`，discover `1,673 -> 1,669`，opt-in 保持 `17`。

**Rollback**

revert B4 commit；恢复原 method identities，不做跨批次 cherry-pick。

### B5 private seam / mock-call-only

**范围**

- REWRITE `tests/test_web_logs_wiring.py::LogTrimTests.test_small_runtime_log_is_left_alone`
- REWRITE `tests/test_web_prepare_flow.py::OverlayVerificationTests.test_verification_skips_unknown_statuses`

**替换 / 等效门**

- log trim：创建真实临时 runtime log，调用 `_log`，断言文件内容/大小和 replace 可观察结果；不只查 `replace.assert_not_called()`。
- overlay verification：断言 `CTRL.prepared`、overlay status 和外部 observable 结果；不把 `threading.Thread` 未调用当唯一结论。
- R03 `test_starship_launch...test_unreadable_card_data_falls_back_to_the_legacy_card` 已从候选移除：它 patch 的是公开 `starship_card_ids` seam，原审计的“私有 patch”证据不成立。

**Risk**

低到中。两项保留原有回归，只替换观察面。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_web_logs_wiring.LogTrimTests tests.test_web_prepare_flow.OverlayVerificationTests
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

Expected：两个方法原位改写，默认 `1,522 -> 1,522`，opt-in `3 -> 3`。

**Rollback**

revert B5 commit；两个原测试可分别恢复，互不依赖。

### B6 wall-clock timeout assertions

**范围**

- REWRITE `tests/adapters/test_external_adapters.py::LegacyLobbyStateAdapterTests.test_slow_recognizer_returns_within_deadline_budget`
- REWRITE `tests/adapters/test_external_adapters.py::LegacyActionDispatchTests.test_slow_handler_returns_within_deadline_budget`

**替换 / 等效门**

- 用 `threading.Event`/barrier 阻塞 handler，断言 `TimeoutError` 在 deadline 被传播。
- cleanup 在 finally 中 set event，并 join worker；不使用 `time.sleep(0.3)`，不保留 `monotonic < 0.15` 这种负载相关断言。
- 若生产实现没有可注入 clock，使用真实 bounded timeout 可以保留，但必须有确定释放和 cleanup；必要时拆为 opt-in integration，并另立生产 seam 任务。

**Risk**

中。要证明 adapter deadline 仍生效，不能只测没有 hang。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.adapters.test_external_adapters.LegacyLobbyStateAdapterTests tests.adapters.test_external_adapters.LegacyActionDispatchTests
$env:PYTHONUTF8='1'; uv run python -m unittest discover -s tests -t .
```

Expected：两模块全绿；重复 3 次无不稳定失败；无遗留 worker thread；两个 adapter 方法原位改写，default 保持 `1,511`，discover 保持 `1,673`，opt-in 保持 `17`。

**Rollback**

revert B6 commit；不要用放宽 timeout 的方式“修复”不稳定。

### B7 native/window/MCP gate extraction

**范围**

- O01 `tests/test_display_mapping.py::DisplayMappingSamplingTests.test_live_window_lookup`
- O02 `tests/test_mcp_server.py::McpServerSmokeTests.test_lists_the_curated_tools`
- O03 `tests/test_mcp_server.py::McpServerSmokeTests.test_doctor_tool_runs_hsctl`
- O13 `tests/test_web_hot_control.py::PortExclusivityTests.test_plain_httpserver_would_have_shared_it`

**替换 / 等效门**

- 把 O01 移到 native live-window smoke；默认保留固定 reader/cache mapping 测试。
- 把 O02/O03 移到 native MCP stdio integration；在 `tests/test_mcp_server.py` 增加 1 个静态 tool-name/schema 默认方法，不启动子进程。
- 把 O13 移到 Windows native/CI gate；默认保留项目 `_ControlServer` 的端口独占测试。
- 更新 `tools/run_headless_tests.py::OPT_IN_MODULES`，required skip 继续 failure。

**Risk**

中。外部协议和 Windows 端口语义会离开默认门，必须保留显式 opt-in CI job。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in
uv run python -m unittest discover -s tests -t .
```

Expected：默认移除 4 个旧方法、增加 1 个静态 schema 方法，累计 default `1,514 -> 1,511`、discover `1,676 -> 1,673`；CI opt-in 增加 3 个方法，累计 `13 -> 16`；native-live 增加 O01，累计 `0 -> 1`。required skip=failure。

**Rollback**

revert B7 commit，恢复原模块和 opt-in manifest；不要留下重复模块。

### B8 tooling/environment gate extraction

**范围**

- O04-O08 `tests/test_uv_environment.py::UvEnvironmentSmokeTest` 五个方法
- O09-O11 `tests/test_selfcheck.py::RealEnvironmentTests` 三个方法
- O14 `tests/test_selfcheck.py::PythonVersionTests.test_running_interpreter_is_the_recommended_one`

**替换 / 等效门**

- 新 `tests/tooling/test_environment.py`（或等价）承接已安装版本、解释器、runtime imports、真实 selfcheck。
- 在 `tests/test_uv_environment.py` 增加 1 个纯静态 manifest parity 默认方法，不 import 重依赖。
- CI/tooling job 必须显式运行新模块；缺依赖/解释器时 failure，不静默 skip。
- 不删除 `RunChecksTests`、`DependencyManifestTests`、`DependencyItemTests` 等 fake/静态行为门。

**Risk**

中。环境门离开默认 suite 后可能漏掉安装漂移；必须把 tooling job 加到 CI 或 release checklist。

**Verification**

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_uv_environment tests.test_selfcheck.RealEnvironmentTests
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in
```

Expected：默认移除 9 个旧方法、增加 1 个静态 manifest 方法，累计 default `1,522 -> 1,514`、discover `1,684 -> 1,676`；tooling opt-in 增加 9 个环境方法，累计 `4 -> 13`。

**Rollback**

revert B8 commit；恢复顶层模块和 import/version 断言。

### B9 repository gitignore check extraction

**范围**

- O12 `tests/test_env_provision.py::TemplateTests.test_template_is_not_ignored_by_git`

**替换 / 等效门**

- 移到 CI/tooling repository hygiene script 或 tooling test；默认保留 `TemplateTests.test_template_has_no_machine_specific_path`。
- CI 命令直接执行 `git check-ignore --quiet env.example.json`，结果必须为 non-zero（not ignored）；显式记录 exit code。

**Risk**

低。只影响 repo hygiene 检查的位置，不影响运行时行为。

**Verification**

```powershell
git check-ignore --quiet env.example.json; Write-Output "check-ignore-exit=$LASTEXITCODE"
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_env_provision
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

Expected：`git check-ignore` 为 1（not ignored），模板路径检查通过；默认移除 1 个方法、增加 0，累计 default `1,523 -> 1,522`、discover `1,685 -> 1,684`；opt-in 增加 1 个 repository-hygiene 方法，累计 `3 -> 4`。

**Rollback**

revert B9 commit；不要把 `.gitignore` 变化混入该批次。

## 5. 每批 review checklist

- [ ] Diff 只含本批列的 method identities 和 replacement tests。
- [ ] 旧测试在 replacement 通过前仍存在。
- [ ] 新测试断言可观察行为/API/文件状态，不做源码拼写断言。
- [ ] 没有 patch 私有 helper 只断言调用顺序。
- [ ] 没有新增 `time.sleep`/wall-clock 上限。
- [ ] 没有新增默认环境依赖、Node、真实窗口、MCP 子进程或 skip。
- [ ] 记录命令、exit code、run/pass/fail/error/skip 与精确 count delta。
- [ ] `git status --short` 无临时产物。
- [ ] rollback 可由单个 commit 完成。

## 6. 推荐执行顺序

1. B1、B2、B5（低风险、已有公开门）。
2. B9、B8、B7（先建立 tooling/native gate，再减默认环境负担）。
3. B6（确定性 timeout）。
4. B4（Tk 生命周期，需要 careful review）。
5. B3（12 个 Web 实现串迁移到 3 个 opt-in JS DOM 行为门，最后做）。

## 7. 当前不确定项 / 需要 controller 确认

- B3 是否允许 opt-in Node DOM 行为门；若不允许，M02-M05/R02/A04/R10-R15 应保持 KEEP/needs confirmation，不能仅凭源码字符串删除。
- B4 若无法在不改生产代码的前提下建立公共 lifecycle 门，M06-M08/R01 应保持 KEEP；不得用私有 patch 顺序替代。
- B7/B8 需要 controller 确认 CI 是否已有 native/tooling job；没有 job 时先加 job，再迁移测试。
- B1 的 `screen_regions` 黑盒替代需要 controller 同意新增固定 frame fixture；没有 fixture 时 R06 保持 KEEP。
