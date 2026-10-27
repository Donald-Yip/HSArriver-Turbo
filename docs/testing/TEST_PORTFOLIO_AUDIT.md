# R02D Post-R08 Test Portfolio Audit

> 状态：审计完成，未修改任何测试或生产代码。统计快照：分支 `feature/R02D-test-portfolio`，base commit `75dc30899377d5b8b56a97e842200b22c7128c3d`，日期 2026-10-27。

## 1. 口径与结论

- 统计范围：`tests/**/test_*.py`，包括 `tests/domain`、`tests/application`、`tests/adapters`、`tests/plugins`、`tests/native` 和顶层测试。
- AST 方法口径：`unittest.TestCase` 类内名称以 `test` 开头的函数；当前顶层测试全部是类方法，没有模块级测试函数。
- 当前总计：**108 个 Python 测试文件、29,240 物理行、366 个含测试方法的类、1,691 个 AST 测试方法**。
- 默认 headless runner 只收集 90 个顶层文件：**1,525 方法**；R08 分层目录另有 **18 文件 / 166 方法**，其中 native 为 2 文件 / 3 方法。
- 当前残留高风险候选中，**40 个精确方法**进入清理台账：A 类 MUST-REMOVE 4、M 类 MERGE 8、R 类 REWRITE 14、O 类 MOVE-OPT-IN 14。其余 1,651 个方法默认 KEEP；文件级混合状态见第 5 节。
- 未发现 `inspect.getsource()`。仍存在的源码/页面字符串断言已逐类区分：函数体 JS 字符串属于 cleanup；用户可见的 HTML id、中文文案、API route、form schema 保留为 Web 契约。

## 2. 验收证据

### 2.1 AST 统计

命令：从仓库根把 AST 统计脚本通过 stdin 传给 `uv run python -`。

结果：`exit 0`；`files=108`、`physical_lines=29240`、`classes_with_tests=366`、`ast_tests=1691`、`top_level_files=90`、`top_level_methods=1525`、`layered_files=18`、`layered_methods=166`。

### 2.2 默认 headless 门

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

结果：`exit 0`；`run=1525`、`passed=1524`、`failures=0`、`errors=0`、`skipped=1`、耗时 `134.530s`。唯一 skip 是 `tests/test_display_mapping.py::DisplayMappingSamplingTests.test_live_window_lookup`（本机无运行中的炉石窗口）。

### 2.3 可发现的分层 suite

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest discover -s tests -t .
```

结果：`exit 0`；`Ran 1688 tests in 139.822s`、`OK (skipped=1)`。`tests/native` 没有 `__init__.py`，因此不被普通 discover 递归；其 3 个方法由显式 opt-in runner 验证。

### 2.4 显式 opt-in suite

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in
```

结果：`exit 0`；`run=3`、`passed=3`、`failures=0`、`errors=0`、`skipped=0`。

### 2.5 替代门抽样

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_hsctl.CliDelayAndScreenshotContractTests tests.test_draw_extra_delay_settings.WebSettingsTests tests.test_click_safety_checks.ClickSafetyContractTests
```

结果：`exit 0`；`Ran 16 tests in 2.682s`、`OK`。这证明本审计引用的公开 CLI/Web 延时契约与 fail-closed 点击契约当前真实通过。

## 3. R02A 计数对账

| 指标 | R02A 旧值 | 当前顶层 | 当前全部 | 对账 |
|---|---:|---:|---:|---|
| Python 文件 | 90 | 90 | 108 | 顶层仍是 90；R08 增加 18 个分层文件（其中 native 2） |
| 物理行 | 24,401 | 25,027 | 29,240 | 顶层 +626；R08 分层 +4,213 |
| 含测试类 | 337 | 337 | 366 | 顶层不变；分层 +29 |
| AST 方法 | 1,520 | 1,525 | 1,691 | 顶层 +5；分层 +166 |

顶层 +5 的已知来源：R02C1 替代/删除净增 6（新增 11、删除 5），`test_ci_gates` 增加 1，Node 前端测试迁出 -1，真实进程枚举迁出 -2，`test_window_tracker` 确定性重写净增 1；合计 `6 + 1 - 1 - 2 + 1 = 5`。R08 分层新增 `domain/application/adapters/plugins` 16 文件 / 163 方法，加 native 2 文件 / 3 方法，合计 +18 文件 / 166 方法。旧 inventory 的 90/1,520 口径已不能描述当前全树。

## 4. 分类口径

| 分类 | 本审计处理 |
|---|---|
| 用户可见行为 | KEEP；HTML workflow 只保留 id、文案、route、表单/状态 schema。 |
| 显式 port/API/data contract | KEEP；包括 frozen/slots、序列化、port conformance、CLI/HTTP/JSON schema。 |
| 回归保护 | KEEP；真实用户回归、fail-closed、取消、歧义、超时、窗口生命周期均保留。 |
| 架构不变量 | KEEP；`tests/domain/test_purity.py`、`tests/application/test_purity.py` 的 AST import/IO 检查明确保留。 |
| 集成/native gate | 已 opt-in 的 KEEP；默认 runner 中依赖真实进程、Node、MCP 子进程、真实窗口或本地环境的方法 MOVE-OPT-IN。 |
| duplicate / implementation-coupled | 只列精确候选；私有表顺序、函数体 JS 字符串、私有 patch 顺序优先 merge/rewrite。 |
| over-defensive validation | 只处理明确重复断言；DTO 的 frozen、序列化、finite、confidence、max_turns 契约保留。 |
| nondeterministic / environment-bound | 墙钟耗时和真实环境依赖转 rewrite 或 move-opt-in；没有以“会 skip”为理由直接删除。 |

## 5. 完整测试文件清单

| # | 文件 | 行数 | 方法 | 类数 | 含测试类（方法数） | 层 | 决策 |
|---:|---|---:|---:|---:|---|---|---|
| 1 | `tests/adapters/test_data_adapters.py` | 559 | 22 | 5 | JsonConfigRepositoryTests(9), JsonCardCatalogTests(5), JsonlRunJournalTests(4), FileArtifactStoreTests(3), DataAdapterPortConformanceTests(1) | Adapter contract | KEEP |
| 2 | `tests/adapters/test_external_adapters.py` | 595 | 33 | 6 | HsangRecommendationAdapterTests(6), PowerLogGameStateAdapterTests(10), AdapterDeadlineTests(1), LegacyLobbyStateAdapterTests(6), LegacyActionDispatchTests(9), ExternalAdapterPortConformanceTests(1) | Adapter contract | KEEP + 2 REWRITE |
| 3 | `tests/application/test_dto.py` | 149 | 7 | 1 | DtoContractTests(7) | Application | KEEP + 1 MERGE |
| 4 | `tests/application/test_enter_battle.py` | 514 | 16 | 1 | EnterBattleUseCaseTests(16) | Application | KEEP |
| 5 | `tests/application/test_planning.py` | 793 | 23 | 1 | PlanningExecutionTests(23) | Application | KEEP |
| 6 | `tests/application/test_ports.py` | 129 | 3 | 1 | PortContractTests(3) | Application | KEEP |
| 7 | `tests/application/test_purity.py` | 119 | 2 | 1 | ApplicationPurityTests(2) | Application | KEEP |
| 8 | `tests/application/test_runtime.py` | 242 | 10 | 3 | StopTokenTests(4), ActionGateTests(4), RunContextTests(2) | Application | KEEP |
| 9 | `tests/domain/test_models.py` | 161 | 8 | 1 | ModelContractTests(8) | Domain | KEEP |
| 10 | `tests/domain/test_postconditions.py` | 139 | 8 | 1 | PostconditionEvaluatorTests(8) | Domain | KEEP |
| 11 | `tests/domain/test_purity.py` | 77 | 2 | 1 | DomainPurityTests(2) | Domain | KEEP |
| 12 | `tests/domain/test_results.py` | 69 | 3 | 1 | ResultContractTests(3) | Domain | KEEP |
| 13 | `tests/domain/test_targets.py` | 78 | 6 | 1 | TargetResolverTests(6) | Domain | KEEP |
| 14 | `tests/domain/test_validation.py` | 238 | 11 | 1 | PlanRuleValidatorTests(11) | Domain | KEEP |
| 15 | `tests/native/test_process_enumeration.py` | 37 | 2 | 1 | NativeProcessTests(2) | Native opt-in | KEEP (explicit opt-in) |
| 16 | `tests/native/test_surrender_frontend.py` | 86 | 1 | 1 | FrontendRoundTripTests(1) | Native opt-in | KEEP (explicit opt-in) |
| 17 | `tests/plugins/test_default_plugins.py` | 132 | 3 | 1 | DefaultPluginTests(3) | Plugin contract | KEEP |
| 18 | `tests/plugins/test_registry.py` | 96 | 6 | 1 | PluginRegistryTests(6) | Plugin contract | KEEP |
| 19 | `tests/test_adapter_failure_logging.py` | 106 | 2 | 1 | FailureLoggingTests(2) | Application/fake E2E | KEEP |
| 20 | `tests/test_auto_concede_detection.py` | 247 | 13 | 3 | MaybeConcedeTests(8), ConcedeDetectionStateTests(3), WinRateReadTests(2) | Application/fake E2E | KEEP |
| 21 | `tests/test_battle_entry.py` | 548 | 16 | 8 | TextOnlyRecognitionTests(3), WrongPlaceTests(1), CalibrationToleranceTests(1), DeckGeometryTests(1), DeckSlotGuardTests(3), ForegroundDuringWaitTests(1), FlowSafetyTests(4), ToReferenceInterpolationTests(2) | Application/fake E2E | KEEP |
| 22 | `tests/test_battle_record.py` | 199 | 20 | 5 | ClassifyTests(7), BuildBlocksTests(8), RenderTests(3), SessionTests(1), AutoAppenderTests(1) | Domain/parser | KEEP |
| 23 | `tests/test_battlenet_play_button.py` | 192 | 14 | 3 | EnterHsScreenCoordinateTests(4), PointTargetsWindowTests(4), BattlenetPlayPointTests(6) | Platform adapter | KEEP |
| 24 | `tests/test_calibrate_tool.py` | 1353 | 85 | 13 | TargetTests(7), HitTestTests(12), CardDragTests(8), SaveTests(5), RenderTests(5), ActiveTargetMappingTests(10), WindowFollowTests(7), RoiFrameTests(7), LateClientAreaTests(4), CardButtonTests(7), LayoutScaleTests(2), HintWrapTests(7), OcrPreviewFollowsActiveTargetTests(4) | Platform adapter | KEEP |
| 25 | `tests/test_cancel_manual_flow.py` | 442 | 20 | 8 | CancelSwitchTests(2), WaitLoopCancelTests(3), EnterBattleCancelTests(2), DeckClickCancelTests(1), CancelApiTests(3), EnterBattleFastPathTests(1), CtrlQHotkeyTests(4), CancelCliTests(4) | Application/fake E2E | KEEP |
| 26 | `tests/test_card_catalog.py` | 179 | 22 | 5 | NormalizeTests(1), ParseClassTests(3), LookupTests(11), SearchTests(5), RealCardsJsonSmokeTests(2) | Domain/parser | KEEP |
| 27 | `tests/test_card_search_api.py` | 96 | 11 | 1 | SearchTests(11) | Web/CLI contract | KEEP |
| 28 | `tests/test_choose_one.py` | 246 | 15 | 1 | ChooseOneTests(15) | Domain/parser | KEEP |
| 29 | `tests/test_ci_gates.py` | 33 | 1 | 1 | RequiredOptInResultTests(1) | Tooling/integration | KEEP |
| 30 | `tests/test_click_safety_checks.py` | 162 | 6 | 1 | ClickSafetyContractTests(6) | Application/fake E2E | KEEP |
| 31 | `tests/test_deck_auto_select_disabled.py` | 86 | 3 | 2 | ChoosingHeroNoAutoDeckTests(1), DeckSettingApiTests(2) | Web/CLI contract | KEEP |
| 32 | `tests/test_desktop_capture_scaled_roi.py` | 106 | 5 | 1 | ScaledRoiFrameTests(5) | Platform adapter | KEEP |
| 33 | `tests/test_discover_with_target.py` | 288 | 11 | 2 | DiscoverWithTargetTests(10), DiscoverTargetModelTests(1) | Domain/parser | KEEP |
| 34 | `tests/test_display_mapping.py` | 737 | 67 | 14 | IdentityMappingTests(6), MeasuredOffsetTests(7), ProportionalScalingTests(4), BoxRoundingTests(3), CompatibilityTests(4), NoneMappingTests(3), ClampTests(2), DisplayMappingSamplingTests(8), ProbePixelTests(6), UiScaleTests(1), RoiFrameInterpretationTests(9), LiveTrackingTests(5), CaptureRegionTests(4), WindowModeTests(5) | Platform adapter | KEEP + 1 MOVE-OPT-IN |
| 35 | `tests/test_dpi_is_not_a_gate.py` | 131 | 6 | 2 | DesktopCaptureDpiTests(4), RecommendationValidatorDpiTests(2) | Platform adapter | KEEP |
| 36 | `tests/test_draw_extra_delay.py` | 341 | 21 | 4 | TurnStartDrawTests(5), InTurnDrawTests(9), OpponentAndResetTests(4), FlowIntegrationTests(3) | Domain/parser | KEEP |
| 37 | `tests/test_draw_extra_delay_settings.py` | 172 | 14 | 5 | ConfigTests(3), FlowDefaultTests(2), FsmWiringTests(1), WebSettingsTests(5), WebPageTests(3) | Application/fake E2E | KEEP + 1 MUST-REMOVE + 1 REWRITE |
| 38 | `tests/test_env_provision.py` | 394 | 32 | 7 | EnvFileTests(5), VerifyPythonTests(6), DiscoverTests(5), ResolveTests(3), EnsureInterpreterTests(6), DoctorVerdictTests(5), TemplateTests(2) | Tooling/integration | KEEP + 1 MOVE-OPT-IN |
| 39 | `tests/test_feedback.py` | 494 | 21 | 6 | FeedbackTests(11), ReportTests(1), ExportTests(1), ZipNameTests(4), ExportOpenTests(1), ArchiveTests(3) | Application/fake E2E | KEEP |
| 40 | `tests/test_four_option_choices.py` | 101 | 7 | 1 | FourOptionChoiceTests(7) | Domain/parser | KEEP |
| 41 | `tests/test_friendly_hand_target_battlecries.py` | 560 | 21 | 5 | HandTargetCardDataTests(6), HandTargetRuleTests(3), HandTargetTypeMismatchTests(3), FollowUpHandTargetLogTests(2), FriendlyHandTargetBattlecryTests(7) | Domain/parser | KEEP |
| 42 | `tests/test_fsm_liveness_stop.py` | 176 | 8 | 2 | LivenessStopTests(6), LoopWiringTests(2) | Application/fake E2E | KEEP |
| 43 | `tests/test_full_board_magnetic.py` | 127 | 3 | 1 | FullBoardMagneticTests(3) | Domain/parser | KEEP |
| 44 | `tests/test_hand_animation_delay.py` | 225 | 5 | 1 | HandAnimationDelayTests(5) | Domain/parser | KEEP |
| 45 | `tests/test_hand_deck_actions.py` | 42 | 2 | 1 | HandDeckActionParserTests(2) | Domain/parser | KEEP |
| 46 | `tests/test_hand_or_board_targets.py` | 347 | 19 | 4 | HandOrBoardCardDataTests(3), TargetNameParsingTests(3), HandOrBoardAdaptationTests(10), HandOrBoardExecutionTests(3) | Domain/parser | KEEP |
| 47 | `tests/test_hearthstone_liveness.py` | 205 | 15 | 3 | ProcessLivenessTests(7), LogStalenessTests(6), NativeProcessTests(2) | Domain/parser | KEEP |
| 48 | `tests/test_hs_restart.py` | 449 | 28 | 5 | ResolvePathsTests(5), ClearLogsTests(3), KillHearthstoneTests(4), LaunchHearthstoneTests(10), OverlayDelayDisplayTests(6) | Application/fake E2E | KEEP |
| 49 | `tests/test_hs_window.py` | 66 | 9 | 2 | HeightForWidthTests(3), ClampWidthTests(6) | Platform adapter | KEEP |
| 50 | `tests/test_hsctl.py` | 1222 | 88 | 9 | CliTests(47), ServeStopSafetyTests(1), TaskHintTests(5), ElevationTests(18), KillPidTests(4), CliDelayAndScreenshotContractTests(5), ParserTests(2), DoctorAndEnvTests(5), HelpCoverageTests(1) | Web/CLI contract | KEEP |
| 51 | `tests/test_hsctl_surrender.py` | 301 | 24 | 6 | ShowTests(4), ToggleTests(4), SetTests(8), CheckTests(3), CardTests(4), ParserTests(1) | Web/CLI contract | KEEP |
| 52 | `tests/test_human_like.py` | 524 | 32 | 5 | HumanLikeSettingsTests(4), HumanLikePauseTests(5), ParkMouseTests(2), PostActionPauseHookTests(12), WebHumanLikeTests(9) | UI contract | KEEP |
| 53 | `tests/test_human_like_minion_hover.py` | 389 | 24 | 6 | OpponentMinionHoverPointTests(2), HoverOpponentMinionTests(3), HumanLikeOpponentMinionWatchTests(9), BattleStepWiringTests(2), ConfigDefaultsTests(4), WebPageTests(4) | UI contract | KEEP + 1 REWRITE |
| 54 | `tests/test_human_like_scope.py` | 256 | 11 | 3 | RecommendationPauseTests(6), AcceptsActionKindTests(3), MulliganScopeTests(2) | UI contract | KEEP |
| 55 | `tests/test_log_overlay_window.py` | 63 | 3 | 1 | WindowFingerprintTests(3) | UI contract | KEEP |
| 56 | `tests/test_manual_concede.py` | 106 | 4 | 1 | ManualConcedeTests(4) | Domain/parser | KEEP |
| 57 | `tests/test_mcp_server.py` | 161 | 2 | 1 | McpServerSmokeTests(2) | Tooling/integration | MOVE-OPT-IN (2/2) |
| 58 | `tests/test_mode_click.py` | 149 | 10 | 2 | ModePointSourceTests(5), ModeClickOverrideTests(5) | Platform adapter | KEEP |
| 59 | `tests/test_mouse_reset_position.py` | 134 | 6 | 2 | MouseResetPositionTests(5), PostGameClickPointTests(1) | Platform adapter | KEEP |
| 60 | `tests/test_mulligan_ocr_stability.py` | 193 | 11 | 3 | JitterToleranceTests(5), StabilityKeyTests(3), MulliganStabilityKeyTests(3) | Domain/parser | KEEP |
| 61 | `tests/test_mulligan_timing.py` | 149 | 3 | 1 | MulliganTimingTests(3) | Domain/parser | KEEP |
| 62 | `tests/test_ocr_confidence_threshold.py` | 111 | 5 | 2 | OcrConfidenceThresholdTests(1), ClientUpscaleTests(4) | Domain/parser | KEEP |
| 63 | `tests/test_ocr_preprocess_scale.py` | 40 | 3 | 1 | PreprocessScaleTests(3) | Domain/parser | KEEP |
| 64 | `tests/test_ocr_target_line_variants.py` | 144 | 7 | 2 | OcrTargetVariantParserTests(6), OcrTargetVariantAdapterTests(1) | Domain/parser | KEEP |
| 65 | `tests/test_overlay_dpi_scaling.py` | 190 | 18 | 4 | ComputeUiScaleTests(6), PxTests(5), MinimizedGeometryTests(3), WindowWiringTests(4) | UI contract | KEEP |
| 66 | `tests/test_overlay_eye_and_colors.py` | 488 | 29 | 4 | AccountEyeToggleTests(8), AccountPreferencePersistenceTests(4), LogColorTests(5), ButtonLayoutTests(12) | UI contract | KEEP |
| 67 | `tests/test_overlay_human_like_status.py` | 330 | 28 | 6 | BrandHeaderTests(2), HumanLikeLabelTests(8), ConcedeDetectLabelTests(6), StartWiringTests(2), DelayBarTests(4), WebCallbackTests(6) | UI contract | KEEP |
| 68 | `tests/test_overlay_restart_button.py` | 264 | 12 | 3 | OverlayWiringTests(3), RestartButtonTests(1), RestartWorkerTests(8) | UI contract | KEEP |
| 69 | `tests/test_overlay_resume_keeps_score.py` | 156 | 5 | 2 | OverlayHaltToggleTests(2), StartAutomationScoreTests(3) | UI contract | KEEP |
| 70 | `tests/test_overlay_self_check_hint.py` | 182 | 11 | 3 | SelfCheckTextTests(5), SelfCheckButtonTests(4), OverlayWiringTests(2) | UI contract | KEEP |
| 71 | `tests/test_overlay_tk_lifecycle.py` | 430 | 25 | 7 | RunTeardownSourceTests(5), JoinPreviousThreadTests(4), StartLifecycleTests(4), ReopenRaceTests(4), DeferredOpenTests(2), ReportTests(2), IsRunningTests(4) | UI contract | KEEP + 3 MERGE + 1 REWRITE + 1 MUST-REMOVE |
| 72 | `tests/test_overlay_window_slider.py` | 180 | 17 | 4 | SliderWiringTests(6), BindOverlayTests(1), ResizeCallbackTests(4), SliderSyncActionTests(6) | UI contract | KEEP |
| 73 | `tests/test_paddle_click_isolation.py` | 44 | 1 | 1 | PaddleClickIsolationTests(1) | Platform adapter | KEEP |
| 74 | `tests/test_player_name_match.py` | 211 | 16 | 4 | PlayerNameCheckTests(8), FsmWarningTests(4), OverlayAccountRowTests(2), OverlayLivenessRowTests(2) | Domain/parser | KEEP |
| 75 | `tests/test_power_log_hand_entries.py` | 209 | 11 | 1 | PowerLogHandEntryTests(11) | Platform adapter | KEEP |
| 76 | `tests/test_rank_stop.py` | 1039 | 45 | 13 | ParseRankTextTests(1), BlankRegionTests(1), ReadRankRegionTests(4), PureRankTextTests(1), RankConditionTests(1), RankActionForTests(3), CheckRankStopTests(8), PostGameStartButtonTests(3), QuittingBattleTests(6), KeepScoreTests(5), MulliganWaitTests(3), ChoosingHeroRankGateTests(5), WebRankStopApiTests(4) | Domain/parser | KEEP |
| 77 | `tests/test_region_overlay_button.py` | 395 | 27 | 6 | ToggleStateTests(5), HintTests(4), PaintLayerTests(4), AutoMappingTests(4), OverlayButtonTests(5), CalibrationSessionTests(5) | UI contract | KEEP |
| 78 | `tests/test_release_tool.py` | 92 | 10 | 4 | ReleaseDateTests(2), VersionFileTests(2), ChangelogTests(4), WriteLikeTests(2) | Tooling/integration | KEEP |
| 79 | `tests/test_repeated_recommendation.py` | 107 | 1 | 1 | RepeatedRecommendationTests(1) | Domain/parser | KEEP |
| 80 | `tests/test_restart_watchdog.py` | 295 | 15 | 5 | WatchdogGateTests(6), WatchdogRateLimitTests(3), WatchdogSettlingTests(2), WatchdogResetTests(2), WatchdogStateTests(2) | Application/fake E2E | KEEP |
| 81 | `tests/test_roi_config_frame.py` | 216 | 15 | 3 | RoiFrameDetectionTests(5), UserBoxInterpretationTests(8), RecommendationConfigIntegrationTests(2) | Application/fake E2E | KEEP + 1 REWRITE |
| 82 | `tests/test_screen_regions.py` | 507 | 40 | 4 | RegionRegistryTests(8), CalibrationTargetTests(13), RegionPreviewTests(15), RegionBoxDrawingTests(4) | Platform adapter | KEEP + 1 REWRITE |
| 83 | `tests/test_script_logs.py` | 309 | 18 | 6 | FormatSizeTests(3), NamingTests(2), ScanTests(5), ClearTests(4), OpenLocationTests(3), IsFeedbackTests(1) | Tooling/integration | KEEP |
| 84 | `tests/test_select_target_after_choice.py` | 282 | 17 | 3 | ParserTests(5), AdapterAndClickTests(7), FriendlyChoiceTargetFallbackTests(5) | Application/fake E2E | KEEP |
| 85 | `tests/test_selfcheck.py` | 285 | 27 | 6 | PythonVersionTests(5), DependencyManifestTests(5), DependencyItemTests(5), RunChecksTests(6), WindowedClientSizeTests(3), RealEnvironmentTests(3) | Tooling/integration | KEEP + 4 MOVE-OPT-IN |
| 86 | `tests/test_selfcheck_collapse.py` | 68 | 9 | 2 | SelfcheckCollapsePageTests(7), ScriptHelpersTests(2) | Web/CLI contract | KEEP + 4 MERGE + 1 REWRITE + 1 MUST-REMOVE |
| 87 | `tests/test_starship_launch.py` | 350 | 23 | 5 | StarshipCardDataTests(4), StarshipParserTests(6), StarshipAdapterTests(6), StarshipExecutionTests(3), StarshipLaunchClickTests(4) | Domain/parser | KEEP |
| 88 | `tests/test_start_latency.py` | 359 | 15 | 5 | NoFixedSleepTests(3), StartMilestoneTests(2), RankConfirmGateTests(3), StartingFlagTests(3), OverlayStartingFeedbackTests(4) | Application/fake E2E | KEEP |
| 89 | `tests/test_surrender_integration.py` | 267 | 12 | 5 | OpponentTurnTests(3), DirectionTests(2), GuardTests(4), LegacyConcedeTextTests(2), StateApiTests(1) | Application/fake E2E | KEEP |
| 90 | `tests/test_surrender_rules.py` | 731 | 57 | 9 | NormalizeTests(11), CompileTests(3), EvaluateTests(6), DirectionTests(4), LogicTests(5), EngineTests(14), NestedNormalizeTests(7), NestedEvalTests(4), NestedStatusTests(3) | Domain/parser | KEEP |
| 91 | `tests/test_targeted_battlecries.py` | 118 | 6 | 1 | TargetedBattlecryTests(6) | Domain/parser | KEEP |
| 92 | `tests/test_targeted_locations.py` | 354 | 12 | 2 | TargetedLocationTests(8), HandTargetLocationTests(4) | Domain/parser | KEEP |
| 93 | `tests/test_timeline_choice.py` | 365 | 18 | 4 | TimelineParserTests(7), TimelineAdapterTests(2), TimelineControllerTests(4), TimelineFlowTests(5) | Domain/parser | KEEP |
| 94 | `tests/test_timeline_restore.py` | 89 | 4 | 1 | RestoredTimelineTests(4) | Domain/parser | KEEP |
| 95 | `tests/test_update_check.py` | 260 | 15 | 5 | ParseVersionTests(3), FetchLatestTests(6), StatusTests(3), RefreshTests(2), PersistenceTests(1) | Tooling/integration | KEEP |
| 96 | `tests/test_uv_environment.py` | 98 | 5 | 1 | UvEnvironmentSmokeTest(5) | Tooling/integration | MOVE-OPT-IN (5/5) |
| 97 | `tests/test_validator_client_size.py` | 103 | 5 | 2 | ClientSizeFreshnessTests(4), WindowedValidateTests(1) | Application/fake E2E | KEEP |
| 98 | `tests/test_version.py` | 148 | 12 | 3 | VersionValueTests(4), StatusApiTests(2), SurfaceTests(6) | Web/CLI contract | KEEP + 1 REWRITE |
| 99 | `tests/test_web_admin_banner.py` | 103 | 7 | 1 | AdminWarningBannerTests(7) | Web/CLI contract | KEEP |
| 100 | `tests/test_web_hot_control.py` | 473 | 43 | 5 | HotChannelTests(19), ScreenshotApiTests(14), PortExclusivityTests(3), PortFileTests(3), RestartHearthstoneApiTests(4) | Web/CLI contract | KEEP + 1 MUST-REMOVE + 1 MOVE-OPT-IN |
| 101 | `tests/test_web_liveness_api.py` | 210 | 17 | 4 | SaveLivenessTests(5), LivenessStateTests(5), OverlayCallbackTests(4), WebPageTests(3) | Web/CLI contract | KEEP |
| 102 | `tests/test_web_logs_wiring.py` | 454 | 32 | 6 | LogsRowTests(8), SnapshotTests(4), OverlayLogActionsTests(6), OverlayKeyTests(2), LogTrimTests(4), OverlayWiringTests(8) | Web/CLI contract | KEEP + 1 REWRITE |
| 103 | `tests/test_web_prepare_flow.py` | 371 | 22 | 5 | PrepareTests(6), OverlayStateSyncTests(6), OverlayPageTests(4), OverlayVerificationTests(5), StartAfterPrepareTests(1) | Web/CLI contract | KEEP + 4 REWRITE |
| 104 | `tests/test_web_regions_api.py` | 138 | 8 | 2 | RegionsApiTests(5), RegionsPageTests(3) | Web/CLI contract | KEEP |
| 105 | `tests/test_web_screenshot_points.py` | 204 | 11 | 4 | ModePointMarkerTests(3), ScreenshotPointsApiTests(3), ServeStopApiTests(4), PortFileTests(1) | Web/CLI contract | KEEP |
| 106 | `tests/test_web_selfcheck_api.py` | 244 | 16 | 4 | SelfcheckCacheTests(6), SelfcheckLoggingTests(2), SelfcheckApiTests(4), SelfcheckPageTests(4) | Web/CLI contract | KEEP |
| 107 | `tests/test_web_ui_surrender.py` | 215 | 16 | 5 | SaveTests(6), ConfirmTests(2), EmptyConfigTests(2), NestedSaveTests(3), WebPageTests(3) | Web/CLI contract | KEEP |
| 108 | `tests/test_window_tracker.py` | 302 | 17 | 3 | TrackerTests(13), ClockConsistencyTests(2), ModuleSingletonTests(2) | Platform adapter | KEEP |

完整性：上表 108 行与 AST 的 108 个 `tests/**/test_*.py` 一一对应；每个文件只出现一次。

## 6. 清除候选总表

### 6.1 MUST-REMOVE（4）

| ID | 精确身份 | 根因证据 | 保留的行为 / 等效门 | 风险 |
|---|---|---|---|---|
| A01 | `tests/test_web_hot_control.py::HotChannelTests.test_delay_effect_scope_table` | 调私有 `web_ui._delay_effect_scope` 并遍历私有 `_USER_DELAY_KEYS`；属于重复表分类。 | `tests/test_hsctl.py::CliDelayAndScreenshotContractTests.test_delays_show_exposes_every_supported_key_and_effect`、`test_delays_set_accepts_every_supported_key` 以及 `WebSettingsTests.test_status_exposes_the_draw_delay` 已证明公开字段/生效范围。 | 低；若公开 CLI/Web 契约漏字段，16-test 抽样与默认 suite 会失败。 |
| A02 | `tests/test_draw_extra_delay_settings.py::ConfigTests.test_key_is_part_of_the_user_delay_whitelist` | 断言私有 `_USER_DELAY_KEYS` 的顺序，外部没有该顺序契约。 | 同 A01 的 `delays show`/`api_save_delays` 公开门；`ConfigTests.test_default_is_one_second_per_card` 保留默认值行为。 | 低。 |
| A03 | `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_gc_is_imported` | 只证明模块导入了 `gc`，没有可观察行为。 | M06-M08 的线程收尾替代门保留真实 Tk 清理回归；`IsRunningTests` 保留状态行为。 | 低；属于非行为断言。 |
| A04 | `tests/test_selfcheck_collapse.py::ScriptHelpersTests.test_methods_use_the_set_helper` | 统计 HTML 中 `style.display` 的实现字符串次数，测试实现写法。 | B3 的新 JS DOM 行为门证明收起/展开、记忆、失败展开；`test_toggle_button_and_body_exist` 等 HTML 契约测试保留。 | 中；替代门落地前不得删除。 |

### 6.2 MERGE（8）

| ID | 精确身份 | 根因证据 | 合并目标 / 等效门 | 风险 |
|---|---|---|---|---|
| M01 | `tests/application/test_dto.py::DtoContractTests.test_invalid_confidence_and_status_are_rejected` | `max_turns=0` 与 `test_max_turns_requires_an_exact_positive_int` 重复；confidence 无效值可并入 observation 契约。 | 把 confidence 断言并入 `test_observations_are_immutable_and_serializable`，把 max_turns 归入 `test_max_turns_requires_an_exact_positive_int`。 | 低；保留同一 finite/range fail-closed。 |
| M02 | `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_toggle_switches_text_and_visibility` | 断言 JS 函数体文本 `body.style...`、`btn.textContent...`。 | 新 fake-DOM/opt-in JS 测试：点击切换 display 和文案。 | 中；需要 JS harness；落地前 KEEP。 |
| M03 | `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_choice_is_remembered_locally` | 断言 `localStorage.setItem/getItem` 的实现字符串。 | 同一 JS 测试：reload 后保持 folded state，并验证存储 key 行为而非源码。 | 中。 |
| M04 | `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_defaults_to_expanded` | 断言 `setSelfcheckCollapsed(saved === "1", false)` 源码。 | 同一 JS 测试：无存储值默认展开；非法存储值默认展开。 | 中。 |
| M05 | `tests/test_selfcheck_collapse.py::SelfcheckCollapsePageTests.test_failure_auto_expands_without_overwriting_the_preference` | 断言 `if (!res.ok) ...` 源码。 | 同一 JS 测试：失败响应展开但 remembered preference 不变。 | 中。 |
| M06 | `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_wraps_run_in_a_finally_block` | patch 私有 `_run`/`gc.collect` 并验证调用顺序。 | 新公共 `start()` + fake thread/Tk 失败测试：线程退出后 `STARTED` 释放、无 stale window。 | 中高；Tk 清理回归必须保留。 |
| M07 | `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_collects_before_releasing_started` | 同上，私有 patch 顺序。 | 同 M06；用注入的 thread target 和 observable state 证明顺序。 | 中高。 |
| M08 | `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_wrapper_collects_after_run_returns` | 同上。 | 同 M06；异常/正常两条路径只保留一个生命周期场景测试。 | 中高。 |

### 6.3 REWRITE（14）

| ID | 精确身份 | 根因证据 | 替换方式 / 等效门 | 风险 |
|---|---|---|---|---|
| R01 | `tests/test_overlay_tk_lifecycle.py::RunTeardownSourceTests.test_start_records_wrapper_as_thread_target` | 只断言私有 wrapper 被设为 thread target。 | 通过 fake thread 的 `start()` 执行 target，观察 cleanup/state；合并到 M06-M08 门。 | 中。 |
| R02 | `tests/test_selfcheck_collapse.py::ScriptHelpersTests.test_collapse_helpers_are_initialised_on_load` | 断言 `initSelfcheckCollapse()` 函数体/调用字符串。 | JS DOM 行为门验证页面 load 后默认状态和控件可用。 | 中。 |
| R04 | `tests/adapters/test_external_adapters.py::LegacyLobbyStateAdapterTests.test_slow_recognizer_returns_within_deadline_budget` | `time.sleep(0.3)` + wall-clock `<0.15`，负载下抖动。 | 事件阻塞 handler + `TimeoutError` + cleanup release；避免墙钟上限。 | 中；必须证明 deadline 传播。 |
| R05 | `tests/adapters/test_external_adapters.py::LegacyActionDispatchTests.test_slow_handler_returns_within_deadline_budget` | 同上。 | 同 R04；阻断 handler 后断言 timeout，再用 Event 释放并 join。 | 中。 |
| R06 | `tests/test_screen_regions.py::CalibrationTargetTests.test_interface_recognition_boxes_match_battle_entry` | 直接比较两个模块常量，属于 duplicate table drift；后半段 min-size 行为可保留。 | 删除三组常量相等断言，保留 min-size；在现有 R06 方法内并入 screen_regions target→`battle_entry.identify` 的固定 frame 黑盒断言，由 `TextOnlyRecognitionTests` 和 `WrongPlaceTests` 保留识别行为覆盖。 | 中。 |
| R07 | `tests/test_roi_config_frame.py::RecommendationConfigIntegrationTests.test_the_rank_box_falls_back_to_the_code_default` | 行为断言后额外查私有 `config._USER_BOX_KEYS`。 | 保留默认 box 与实际保存/加载 roundtrip，删除私有 key membership。 | 低。 |
| R08 | `tests/test_web_logs_wiring.py::LogTrimTests.test_small_runtime_log_is_left_alone` | 只用 `replace.assert_not_called()` 证明行为。 | 使用真实临时小文件，断言内容/大小不变且不需要 replace；保留 throttling 行为。 | 低。 |
| R09 | `tests/test_web_prepare_flow.py::OverlayVerificationTests.test_verification_skips_unknown_statuses` | 只断言 `threading.Thread` 未调用。 | 断言 `CTRL.prepared`、overlay 状态和无 thread 的 observable 结果。 | 低。 |
| R10 | `tests/test_draw_extra_delay_settings.py::WebPageTests.test_page_sends_and_renders_the_value` | 对 `$("inpDrawDelay").value` 的正反向 JS 赋值做精确源码串断言。 | 在 opt-in JS DOM 行为门中验证输入字段提交值、状态回填和非法值拒绝；HTML id/中文文案契约保留。 | 中；替代 JS 门落地前不得删除。 |
| R11 | `tests/test_human_like_minion_hover.py::WebPageTests.test_page_sends_and_renders_the_switches` | 对两个 hover switch 的精确 JS 布尔赋值/回填源码做断言。 | 同一 opt-in JS DOM 门验证两个开关提交与状态回填，并保留 switch id/标签契约测试。 | 中。 |
| R12 | `tests/test_web_prepare_flow.py::OverlayPageTests.test_overlay_button_label_follows_status` | 对按钮 `textContent` 的精确 JS 表达式做断言。 | opt-in JS DOM 门分别渲染 running/stopped 状态，断言按钮可见文案与可用性。 | 中。 |
| R13 | `tests/test_web_prepare_flow.py::OverlayPageTests.test_toggle_refreshes_the_status_immediately` | 切片脚本后断言内部函数名 `refreshStatus()`。 | opt-in JS DOM 门触发 toggle，断言状态请求发生且 UI 更新，不断言函数名。 | 中。 |
| R14 | `tests/test_web_prepare_flow.py::OverlayPageTests.test_start_button_still_depends_on_prepared` | 对 prepared 三元表达式做精确源码串断言。 | opt-in JS DOM 门分别渲染 prepared/not-prepared 状态，断言开始按钮文案和可用性。 | 中。 |
| R15 | `tests/test_version.py::SurfaceTests.test_web_renders_version_from_status` | 只断言 HTML 源码包含 `s.version`。 | opt-in JS DOM 门用有/无 version 的状态渲染，断言版本 chip 文本及占位符。 | 中。 |

### 6.4 MOVE-OPT-IN（14）

| ID | 精确身份 | 根因证据 | 目标门 / 等价保护 | 风险 |
|---|---|---|---|---|
| O01 | `tests/test_display_mapping.py::DisplayMappingSamplingTests.test_live_window_lookup` | 默认 suite 当前为它 skip；依赖运行中的炉石窗口。 | 移到 `tests/native/` 的 Windows live-window smoke；固定矩形的 mapping 测试仍默认跑。 | 中；真实窗口几何只由 opt-in 覆盖。 |
| O02 | `tests/test_mcp_server.py::McpServerSmokeTests.test_lists_the_curated_tools` | 真实 `mcp_server.py` 子进程、stdio、最长 90s。 | 移到 native/integration opt-in；默认保留静态 tool schema/CLI 契约或新增进程内 schema 检查。 | 中。 |
| O03 | `tests/test_mcp_server.py::McpServerSmokeTests.test_doctor_tool_runs_hsctl` | 真实子进程并可等 180s。 | 同 O02；opt-in required skip=failure。 | 中。 |
| O04 | `tests/test_uv_environment.py::UvEnvironmentSmokeTest.test_python_is_312` | 依赖当前解释器环境。 | `tests/tooling` opt-in/CI env gate；保留静态 pyproject 检查。 | 低。 |
| O05 | `tests/test_uv_environment.py::UvEnvironmentSmokeTest.test_manifest_parity_and_installed_direct_versions` | 同时检查 manifest 与已安装 distribution，环境绑定。 | 拆成默认静态 manifest parity + tooling opt-in installed-version gate。 | 中。 |
| O06 | `tests/test_uv_environment.py::UvEnvironmentSmokeTest.test_deprecated_requirements_has_r02_cleanup_note` | 文件文本契约，但属于 tooling 文档门。 | tooling gate；该文本不是运行时行为。 | 低。 |
| O07 | `tests/test_uv_environment.py::UvEnvironmentSmokeTest.test_opencv_distributions_and_runtime_agree` | 读取已安装 OpenCV distributions 并 import cv2。 | tooling/CI env gate，required skip=failure。 | 中。 |
| O08 | `tests/test_uv_environment.py::UvEnvironmentSmokeTest.test_required_runtime_modules_import` | import PaddleOCR/Win32 等环境依赖。 | tooling/CI env gate；不删除依赖验证。 | 中。 |
| O09 | `tests/test_selfcheck.py::RealEnvironmentTests.test_python_312_is_reported_ok` | 真跑 `selfcheck.run_checks()`，依赖机器。 | tooling env gate；`RunChecksTests` 的 fake 行为继续默认跑。 | 低。 |
| O10 | `tests/test_selfcheck.py::RealEnvironmentTests.test_all_declared_dependencies_are_importable` | 同上。 | tooling env gate。 | 低。 |
| O11 | `tests/test_selfcheck.py::RealEnvironmentTests.test_environment_items_are_present` | 同上。 | tooling env gate；保留 item keys 的 fake 测试。 | 低。 |
| O12 | `tests/test_env_provision.py::TemplateTests.test_template_is_not_ignored_by_git` | 启动 `git check-ignore`，依赖 repo/worktree 状态。 | CI/tooling 检查；保留 `test_template_has_no_machine_specific_path`。 | 低。 |
| O13 | `tests/test_web_hot_control.py::PortExclusivityTests.test_plain_httpserver_would_have_shared_it` | 只在 Windows 语义成立并 skip 非 Windows。 | Windows native/CI gate；`PortExclusivityTests` 的项目 server 端口测试继续默认跑。 | 中。 |
| O14 | `tests/test_selfcheck.py::PythonVersionTests.test_running_interpreter_is_the_recommended_one` | 直接检查当前解释器 `python_item()`，与 O04/O09 同类，默认 suite 绑定机器环境。 | 移到 tooling/CI environment gate；解释器规则的注入式单测继续默认跑。 | 低。 |

## 7. 不该被误删的保留项

- `tests/domain/test_purity.py`、`tests/application/test_purity.py`：架构 AST 检查，明确 KEEP。
- `tests/domain/test_validation.py`、`tests/domain/test_postconditions.py`、`tests/application/test_planning.py`、`tests/application/test_enter_battle.py`：fail-closed、歧义、取消、deadline、journal contract，KEEP。
- `tests/adapters/test_external_adapters.py` 中真实 handler 错误、unknown action、typed timeout 传播均 KEEP；只重写两个墙钟耗时断言。
- `tests/test_click_safety_checks.py`：无窗口/未命中/未知坐标 fail-closed 与合法路径，KEEP。
- 所有 Web/CLI 用户可见的 id、中文文案、route、JSON schema 断言：KEEP；不因“出现字符串”误判为源码串。
- 其余 DTO/model 不删除：frozen/slots、finite、confidence、max_turns 都是当前 API 与 serialization fail-closed 契约；只合并 M01 的重复边界断言。

## 8. 不确定项

- R02A inventory 与当前行的行号/行数已变化；本报告只以当前 AST 与当前文件为事实源。
- 若 R09 之前生产代码发生变化，candidate identity 必须在 cleanup commit 前重新跑 AST 身份校验。
- `test_card_catalog.RealCardsJsonSmokeTests` 会读仓库 `cards.json`，但它是只读静态 fixture，不是真实游戏/网络环境，因此 KEEP。
- `tests/native` 已 opt-in，不列为删除或再次搬迁候选；只在 `--opt-in` 门保留。
