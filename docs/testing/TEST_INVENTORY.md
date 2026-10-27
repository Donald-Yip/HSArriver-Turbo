# R02A Test Inventory

> 状态：R02A 盘点完成；R02C1-R02C3 已按批次完成源码串替换，R02C4 已完成 Node/native opt-in 隔离，R02C5 已完成 5 个私有 patch rewrite 与 6 个线程同步 rewrite。其余 `candidate-delete` 仍只是候选，必须先创建并通过替代测试。

## 口径

- 统计对象：`tests/test_*.py` 90 个；辅助 JS 1 个：`tests/frontend_surrender_roundtrip.js`。
- 方法数按 AST 中名称以 `test` 开头的函数统计；类数按含测试方法的 `unittest.TestCase` 类统计。
- 行数为物理行数（包含空行）；`unittest` discovery 计数与 AST 方法数一致。
- 决策：`keep` / `rewrite` / `move-native` / `candidate-delete`。文件级 `keep + move-native N` 表示仅指定方法移动。
- 标志：`S` 源码字符串，`P` 私有 patch（含注入 seam），`N` Node，`E` 真实 OS/子进程/依赖环境，`A` 管理员路径（只 fake/文案），`W` 本地 loopback HTTP，`NET-mock` 外部网络 fake，`D` 危险行为覆盖（fake/临时目录），`D-ACCEPT` 测试接受危险行为，`H` 硬编码本机路径。

## 原始统计

| 指标 | 数值 |
|---|---:|
| Python 测试文件 | 90 |
| 辅助 JS 文件 | 1 |
| Python 测试文件物理行数 | 24401 |
| 辅助 JS 物理行数 | 209 |
| 含测试方法的类 | 337 |
| AST 测试方法 | 1520 |
| unittest discovery | 1520 |
| 私有 patch 覆盖 | 133 个方法 / 32 个文件 |
| 全量运行 | 1520 run / 1 failure / 1 skipped / exit 1 / 134.726s |

## 文件清单

| # | 文件 | 行数 | 方法 | 测试类（方法数） | 层 | 标志 | 决策 | Owner | Follow-up | 目的 / 处置 |
|---:|---|---:|---:|---|---|---|---|---|---|---|
| 1 | `tests/test_adapter_failure_logging.py` | 106 | 2 | FailureLoggingTests(2) | Adapter contract | 无 | keep | controller | - | 失败前先记录推荐/OCR 上下文，保留诊断行为。  |
| 2 | `tests/test_auto_concede_detection.py` | 247 | 13 | MaybeConcedeTests(8), ConcedeDetectionStateTests(3), WinRateReadTests(2) | Application | 私有 patch | keep | controller | - | 自动投降阈值、重试和状态快照。  |
| 3 | `tests/test_battle_entry.py` | 548 | 16 | TextOnlyRecognitionTests(3), WrongPlaceTests(1), CalibrationToleranceTests(1), DeckGeometryTests(1), DeckSlotGuardTests(3), ForegroundDuringWaitTests(1), FlowSafetyTests(4), ToReferenceInterpolationTests(2) | Application / fake E2E | 无 | keep | controller | - | 界面识别、卡组选择与超时安全路径。  |
| 4 | `tests/test_battle_record.py` | 199 | 20 | ClassifyTests(7), BuildBlocksTests(8), RenderTests(3), SessionTests(1), AutoAppenderTests(1) | Domain | 无 | keep | controller | - | 战斗记录分类、聚合与序列化契约。  |
| 5 | `tests/test_battlenet_play_button.py` | 186 | 14 | EnterHsScreenCoordinateTests(4), PointTargetsWindowTests(4), BattlenetPlayPointTests(6) | Adapter contract | 私有 patch | keep | controller | - | 战网按钮定位与窗口命中契约。  |
| 6 | `tests/test_calibrate_tool.py` | 1353 | 85 | TargetTests(7), HitTestTests(12), CardDragTests(8), SaveTests(5), RenderTests(5), ActiveTargetMappingTests(10), WindowFollowTests(7), RoiFrameTests(7), LateClientAreaTests(4), CardButtonTests(7), LayoutScaleTests(2), HintWrapTests(7), OcrPreviewFollowsActiveTargetTests(4) | Adapter / UI | 私有 patch | keep | controller | - | 校准交互、坐标换算与保存回归。  |
| 7 | `tests/test_cancel_manual_flow.py` | 442 | 20 | CancelSwitchTests(2), WaitLoopCancelTests(3), EnterBattleCancelTests(2), DeckClickCancelTests(1), CancelApiTests(3), EnterBattleFastPathTests(1), CtrlQHotkeyTests(4), CancelCliTests(4) | Application | 私有 patch | keep | controller | - | 取消令牌贯穿等待、点击和 CLI。  |
| 8 | `tests/test_card_catalog.py` | 179 | 22 | NormalizeTests(1), ParseClassTests(3), LookupTests(11), SearchTests(5), RealCardsJsonSmokeTests(2) | Domain | 无 | keep | controller | - | 卡牌目录解析与真实 cards.json 只读 smoke。  |
| 9 | `tests/test_card_search_api.py` | 96 | 11 | SearchTests(11) | Web contract | 无 | keep | controller | - | 卡牌搜索 API 输出契约。  |
| 10 | `tests/test_choose_one.py` | 246 | 15 | ChooseOneTests(15) | Application | 无 | keep | controller | - | 抉择/发现与目标选择。  |
| 11 | `tests/test_click_safety_checks.py` | 162 | 6 | ClickSafetyContractTests(6) | Adapter safety | 私有 patch | keep | controller | - | R02C1 已替换危险验收：无窗口/未命中/未知坐标/未验证激活点均零点击，合法前台与后台激活路径仍点击。  |
| 12 | `tests/test_deck_auto_select_disabled.py` | 86 | 3 | ChoosingHeroNoAutoDeckTests(1), DeckSettingApiTests(2) | Web / Application | 私有 patch | keep | controller | - | 禁止旧自动选卡组配置的兼容边界。  |
| 13 | `tests/test_desktop_capture_scaled_roi.py` | 106 | 5 | ScaledRoiFrameTests(5) | Adapter contract | 无 | keep | controller | - | 缩放 ROI 捕获与 client-size 兼容。  |
| 14 | `tests/test_discover_with_target.py` | 288 | 11 | DiscoverWithTargetTests(10), DiscoverTargetModelTests(1) | Application | 无 | keep | controller | - | 发现牌目标选择与旧路径兼容。  |
| 15 | `tests/test_display_mapping.py` | 737 | 67 | IdentityMappingTests(6), MeasuredOffsetTests(7), ProportionalScalingTests(4), BoxRoundingTests(3), CompatibilityTests(4), NoneMappingTests(3), ClampTests(2), DisplayMappingSamplingTests(8), ProbePixelTests(6), UiScaleTests(1), RoiFrameInterpretationTests(9), LiveTrackingTests(5), CaptureRegionTests(4), WindowModeTests(5) | Adapter contract | 私有 patch | keep | controller | - | 显示映射、采样、缩放和捕获区域核心契约。  |
| 16 | `tests/test_dpi_is_not_a_gate.py` | 131 | 6 | DesktopCaptureDpiTests(4), RecommendationValidatorDpiTests(2) | Adapter contract | 无 | keep | controller | - | DPI 只参与换算，不作为运行门槛。  |
| 17 | `tests/test_draw_extra_delay.py` | 341 | 21 | TurnStartDrawTests(5), InTurnDrawTests(9), OpponentAndResetTests(4), FlowIntegrationTests(3) | Application | 无 | keep | controller | - | 抽牌延时状态机和时钟行为。  |
| 18 | `tests/test_draw_extra_delay_settings.py` | 172 | 14 | ConfigTests(3), FlowDefaultTests(2), FsmWiringTests(1), WebSettingsTests(5), WebPageTests(3) | Web / Application | 无 | keep | controller | - | 延时设置配置、FSM 和网页契约。  |
| 19 | `tests/test_env_provision.py` | 394 | 32 | EnvFileTests(5), VerifyPythonTests(6), DiscoverTests(5), ResolveTests(3), EnsureInterpreterTests(6), DoctorVerdictTests(5), TemplateTests(2) | Tooling | 真实 OS/子进程/依赖环境；私有 patch | keep | controller | - | uv/Python 发现、env pin 和 doctor 结论。  |
| 20 | `tests/test_feedback.py` | 494 | 21 | FeedbackTests(11), ReportTests(1), ExportTests(1), ZipNameTests(4), ExportOpenTests(1), ArchiveTests(3) | Tooling / Adapter | 破坏性行为覆盖（fake/临时目录） | keep | controller | - | 反馈打包、导出与临时目录清理。  |
| 21 | `tests/test_four_option_choices.py` | 101 | 7 | FourOptionChoiceTests(7) | Application | 无 | keep | controller | - | 四选一发现卡路径。  |
| 22 | `tests/test_friendly_hand_target_battlecries.py` | 555 | 21 | HandTargetCardDataTests(6), HandTargetRuleTests(3), HandTargetTypeMismatchTests(3), FollowUpHandTargetLogTests(2), FriendlyHandTargetBattlecryTests(7) | Application | 无私有 helper patch | keep (rewritten R02C5) | controller | - | 手牌目标战吼规则；metadata 回退由公共 `Path.open` 失败 seam 覆盖。  |
| 23 | `tests/test_fsm_liveness_stop.py` | 151 | 8 | LivenessStopTests(6), LoopWiringTests(2) | Application | 源码字符串/源码文件内容 | rewrite | R02C | R02C | 存活停止与主循环接线；1 个源码字符串断言改为行为接线测试。  |
| 24 | `tests/test_full_board_magnetic.py` | 127 | 3 | FullBoardMagneticTests(3) | Application | 私有 patch | keep | controller | - | 满场磁力放置与拖拽落点。  |
| 25 | `tests/test_hand_animation_delay.py` | 225 | 5 | HandAnimationDelayTests(5) | Application | 无 | keep | controller | - | 手牌动画延时的 fake clock 测试。  |
| 26 | `tests/test_hand_deck_actions.py` | 42 | 2 | HandDeckActionParserTests(2) | Application | 无 | keep | controller | - | 锻造/交易动作映射。  |
| 27 | `tests/test_hand_or_board_targets.py` | 341 | 19 | HandOrBoardCardDataTests(3), TargetNameParsingTests(3), HandOrBoardAdaptationTests(10), HandOrBoardExecutionTests(3) | Application | 无私有 helper patch | keep (rewritten R02C5) | controller | - | 手牌/场面目标解析；metadata 回退由公共 `Path.open` 失败 seam 覆盖。  |
| 28 | `tests/test_hearthstone_liveness.py` | 221 | 17 | ProcessLivenessTests(7), LogStalenessTests(6), NativeProcessTests(4) | Application / native smoke | 真实 OS/子进程/依赖环境 | keep + move-native 2 | R02C | R02C | 13 个 fake-clock 行为测试保留；2 个真实进程枚举转 opt-in Windows smoke；2 个注入 fake 的 unknown 分支保留 headless。  |
| 29 | `tests/test_hs_restart.py` | 449 | 28 | ResolvePathsTests(5), ClearLogsTests(3), KillHearthstoneTests(4), LaunchHearthstoneTests(10), OverlayDelayDisplayTests(6) | Tooling | 破坏性行为覆盖（fake/临时目录） | keep | controller | - | 重启、清日志、进程终止路径；全部通过 fake/临时目录覆盖。  |
| 30 | `tests/test_hs_window.py` | 66 | 9 | HeightForWidthTests(3), ClampWidthTests(6) | Domain | 无 | keep | controller | - | 窗口宽高纯函数。  |
| 31 | `tests/test_hsctl.py` | 1222 | 88 | CliTests(47), ServeStopSafetyTests(1), TaskHintTests(5), ElevationTests(18), KillPidTests(4), CliDelayAndScreenshotContractTests(5), ParserTests(2), DoctorAndEnvTests(5), HelpCoverageTests(1) | CLI / Adapter contract | 本地 loopback HTTP；管理员路径（仅 fake/文案）；破坏性行为覆盖（fake/临时目录）；私有 patch | keep | controller | - | R02C1 已用 CLI 可观察契约替换 3 个私有表漂移测试；延时字段/效果与 screenshot kind 均由命令收支验证。  |
| 32 | `tests/test_hsctl_surrender.py` | 301 | 24 | ShowTests(4), ToggleTests(4), SetTests(8), CheckTests(3), CardTests(4), ParserTests(1) | CLI / Domain | 无 | keep | controller | - | 投降规则 CLI 参数与输出。  |
| 33 | `tests/test_human_like.py` | 524 | 32 | HumanLikeSettingsTests(4), HumanLikePauseTests(5), ParkMouseTests(2), PostActionPauseHookTests(12), WebHumanLikeTests(9) | Application | 私有 patch | keep | controller | - | 拟人时序、悬停和后动作挂点。  |
| 34 | `tests/test_human_like_minion_hover.py` | 389 | 24 | OpponentMinionHoverPointTests(2), HoverOpponentMinionTests(3), HumanLikeOpponentMinionWatchTests(9), BattleStepWiringTests(2), ConfigDefaultsTests(4), WebPageTests(4) | Application | 无 | keep | controller | - | 对手随从悬停与配置/网页契约。  |
| 35 | `tests/test_human_like_scope.py` | 209 | 11 | RecommendationPauseTests(6), AcceptsActionKindTests(3), MulliganScopeTests(2) | Application | 源码字符串/源码文件内容 | rewrite | R02C | R02C | 拟人挂点作用域；源码切片断言改为 fake flow 接线断言。  |
| 36 | `tests/test_log_overlay_window.py` | 63 | 3 | WindowFingerprintTests(3) | UI contract | 无 | keep | controller | - | 窗口指纹纯函数。  |
| 37 | `tests/test_manual_concede.py` | 106 | 4 | ManualConcedeTests(4) | Application | 无 | keep | controller | - | 手动投降控制器。  |
| 38 | `tests/test_mcp_server.py` | 161 | 2 | McpServerSmokeTests(2) | Integration | 真实 OS/子进程/依赖环境 | keep | controller | - | stdio MCP 子进程冒烟；无 GUI/外部网络。  |
| 39 | `tests/test_mode_click.py` | 149 | 10 | ModePointSourceTests(5), ModeClickOverrideTests(5) | Adapter contract | 无 | keep | controller | - | 模式点击点与配置单一来源。  |
| 40 | `tests/test_mouse_reset_position.py` | 134 | 6 | MouseResetPositionTests(5), PostGameClickPointTests(1) | Adapter contract | 私有 patch | keep | controller | - | 鼠标复位与结算点击点。  |
| 41 | `tests/test_mulligan_ocr_stability.py` | 193 | 11 | JitterToleranceTests(5), StabilityKeyTests(3), MulliganStabilityKeyTests(3) | Application / Adapter | 无 | keep | controller | - | 换牌 OCR 抖动容忍和稳定键。  |
| 42 | `tests/test_mulligan_timing.py` | 149 | 3 | MulliganTimingTests(3) | Application | 无 | keep | controller | - | 换牌时序。  |
| 43 | `tests/test_ocr_confidence_threshold.py` | 111 | 5 | OcrConfidenceThresholdTests(1), ClientUpscaleTests(4) | Adapter contract | 无 | keep | controller | - | OCR 置信度与客户端放大。  |
| 44 | `tests/test_ocr_preprocess_scale.py` | 40 | 3 | PreprocessScaleTests(3) | Adapter contract | 无 | keep | controller | - | OCR 预处理缩放。  |
| 45 | `tests/test_ocr_target_line_variants.py` | 144 | 7 | OcrTargetVariantParserTests(6), OcrTargetVariantAdapterTests(1) | Adapter contract | 无 | keep | controller | - | 目标行 OCR 变体解析。  |
| 46 | `tests/test_overlay_dpi_scaling.py` | 145 | 18 | ComputeUiScaleTests(6), PxTests(5), MinimizedGeometryTests(3), WindowWiringTests(4) | UI contract | 源码字符串/源码文件内容 | rewrite | R02C | R02C | DPI 纯函数保留；3 个 `_run` 源码串断言改为 fake Tk 几何/回调断言。  |
| 47 | `tests/test_overlay_eye_and_colors.py` | 479 | 29 | AccountEyeToggleTests(8), AccountPreferencePersistenceTests(4), LogColorTests(5), ButtonLayoutTests(12) | UI contract | 源码字符串/源码文件内容；破坏性行为覆盖（fake/临时目录）；私有 patch | rewrite | R02C | R02C | 账号/日志颜色行为保留；8 个布局源码串断言改为 widget tree/callback 测试。  |
| 48 | `tests/test_overlay_human_like_status.py` | 328 | 28 | BrandHeaderTests(2), HumanLikeLabelTests(8), ConcedeDetectLabelTests(6), StartWiringTests(2), DelayBarTests(4), WebCallbackTests(6) | UI contract | 源码字符串/源码文件内容；私有 patch | rewrite | R02C | R02C | 状态标签/回调保留；1 个标题源码串断言改为渲染结果断言。  |
| 49 | `tests/test_overlay_restart_button.py` | 264 | 12 | OverlayWiringTests(3), RestartButtonTests(1), RestartWorkerTests(8) | UI contract | 破坏性行为覆盖（fake/临时目录）；私有 patch | keep | controller | - | 重启/停止/清日志确认流；危险动作均为 fake。  |
| 50 | `tests/test_overlay_resume_keeps_score.py` | 156 | 5 | OverlayHaltToggleTests(2), StartAutomationScoreTests(3) | UI contract | 无 | keep | controller | - | 暂停恢复与分数保持。  |
| 51 | `tests/test_overlay_self_check_hint.py` | 135 | 11 | SelfCheckTextTests(5), SelfCheckButtonTests(4), OverlayWiringTests(2) | UI contract | 源码字符串/源码文件内容；私有 patch | rewrite | R02C | R02C | 自检文本/按钮行为保留；2 个源码串断言改为 fake widget tree。  |
| 52 | `tests/test_overlay_tk_lifecycle.py` | 387 | 25 | RunTeardownSourceTests(5), JoinPreviousThreadTests(4), StartLifecycleTests(4), ReopenRaceTests(4), DeferredOpenTests(2), ReportTests(2), IsRunningTests(4) | UI contract | 源码字符串/源码文件内容；私有 patch | rewrite | R02C | R02C | Tk 生命周期行为保留；2 个源码串断言改为 fake thread/wrapper 观测。  |
| 53 | `tests/test_overlay_window_slider.py` | 144 | 17 | SliderWiringTests(6), BindOverlayTests(1), ResizeCallbackTests(4), SliderSyncActionTests(6) | UI contract | 源码字符串/源码文件内容；私有 patch | rewrite | R02C | R02C | 窗口滑条行为保留；2 个源码串断言改为 fake Tk 绑定测试。  |
| 54 | `tests/test_paddle_click_isolation.py` | 44 | 1 | PaddleClickIsolationTests(1) | Adapter contract | 真实 OS/子进程/依赖环境 | keep | controller | - | Paddle 加载时 click 模块隔离；使用临时 fake 模块。  |
| 55 | `tests/test_player_name_match.py` | 211 | 16 | PlayerNameCheckTests(8), FsmWarningTests(4), OverlayAccountRowTests(2), OverlayLivenessRowTests(2) | Application / UI | 无 | keep | controller | - | 玩家名匹配与警告展示。  |
| 56 | `tests/test_power_log_hand_entries.py` | 209 | 11 | PowerLogHandEntryTests(11) | Adapter / Domain | 无 | keep | controller | - | Power.log 手牌实体解析。  |
| 57 | `tests/test_rank_stop.py` | 1039 | 45 | ParseRankTextTests(1), BlankRegionTests(1), ReadRankRegionTests(4), PureRankTextTests(1), RankConditionTests(1), RankActionForTests(3), CheckRankStopTests(8), PostGameStartButtonTests(3), QuittingBattleTests(6), KeepScoreTests(5), MulliganWaitTests(3), ChoosingHeroRankGateTests(5), WebRankStopApiTests(4) | Application / UI | 私有 patch | keep | controller | - | 段位停止、结算和网页状态。  |
| 58 | `tests/test_region_overlay_button.py` | 380 | 27 | ToggleStateTests(5), HintTests(4), PaintLayerTests(4), AutoMappingTests(4), OverlayButtonTests(5), CalibrationSessionTests(5) | UI contract | 源码字符串/源码文件内容；破坏性行为覆盖（fake/临时目录）；私有 patch | rewrite | R02C | R02C | 校准按钮行为保留；1 个源码串断言改为工厂/回调 spy。  |
| 59 | `tests/test_release_tool.py` | 92 | 10 | ReleaseDateTests(2), VersionFileTests(2), ChangelogTests(4), WriteLikeTests(2) | Tooling | 无 | keep | controller | - | 版本/日期/changelog 发布工具。  |
| 60 | `tests/test_repeated_recommendation.py` | 107 | 1 | RepeatedRecommendationTests(1) | Application | 无 | keep | controller | - | 重复推荐回归路径。  |
| 61 | `tests/test_restart_watchdog.py` | 295 | 15 | WatchdogGateTests(6), WatchdogRateLimitTests(3), WatchdogSettlingTests(2), WatchdogResetTests(2), WatchdogStateTests(2) | Application | 无 | keep | controller | - | 重启看门狗门控与限速。  |
| 62 | `tests/test_roi_config_frame.py` | 216 | 15 | RoiFrameDetectionTests(5), UserBoxInterpretationTests(8), RecommendationConfigIntegrationTests(2) | Adapter contract | 无 | keep | controller | - | ROI 坐标系解释与兼容。  |
| 63 | `tests/test_screen_regions.py` | 507 | 40 | RegionRegistryTests(8), CalibrationTargetTests(13), RegionPreviewTests(15), RegionBoxDrawingTests(4) | Adapter / UI contract | 无 | keep | controller | - | 截图区域、校准目标与预览契约；坐标属于当前兼容面。  |
| 64 | `tests/test_script_logs.py` | 309 | 18 | FormatSizeTests(3), NamingTests(2), ScanTests(5), ClearTests(4), OpenLocationTests(3), IsFeedbackTests(1) | Tooling | 破坏性行为覆盖（fake/临时目录） | keep | controller | - | 日志扫描/清理/打开目录；删除仅限临时目录。  |
| 65 | `tests/test_select_target_after_choice.py` | 282 | 17 | ParserTests(5), AdapterAndClickTests(7), FriendlyChoiceTargetFallbackTests(5) | Application | 无 | keep | controller | - | 选择后目标点击和回退。  |
| 66 | `tests/test_selfcheck.py` | 285 | 27 | PythonVersionTests(5), DependencyManifestTests(5), DependencyItemTests(5), RunChecksTests(6), WindowedClientSizeTests(3), RealEnvironmentTests(3) | Tooling / environment | 真实 OS/子进程/依赖环境；管理员路径（仅 fake/文案） | keep | controller | - | 自检逻辑与真实依赖/环境 smoke；admin 仅为报告字段，不要求提权。  |
| 67 | `tests/test_selfcheck_collapse.py` | 68 | 9 | SelfcheckCollapsePageTests(7), ScriptHelpersTests(2) | UI / web contract | 无 | keep | controller | - | 自检折叠页面与脚本 helper。  |
| 68 | `tests/test_starship_launch.py` | 350 | 23 | StarshipCardDataTests(4), StarshipParserTests(6), StarshipAdapterTests(6), StarshipExecutionTests(3), StarshipLaunchClickTests(4) | Application | 私有 patch | keep | controller | - | 星舰/发射目标规则。  |
| 69 | `tests/test_start_latency.py` | 256 | 15 | NoFixedSleepTests(3), StartMilestoneTests(2), RankConfirmGateTests(3), StartingFlagTests(3), OverlayStartingFeedbackTests(4) | Application / UI | 源码字符串/源码文件内容；私有 patch | rewrite | R02C | R02C | 启动时延行为保留；5 个源码串断言改为 fake clock/flow/widget 断言。  |
| 70 | `tests/test_surrender_frontend.py` | 49 | 1 | FrontendRoundTripTests(1) | Web integration | Node；真实 OS/子进程/依赖环境；硬编码本机路径 | rewrite | R02C | R02C | Node 假 DOM 往返有价值，但硬编码本机路径且 GBK 解码失败；改为 opt-in 明确 UTF-8 的浏览器/Node 契约测试。  |
| 71 | `tests/test_surrender_integration.py` | 267 | 12 | OpponentTurnTests(3), DirectionTests(2), GuardTests(4), LegacyConcedeTextTests(2), StateApiTests(1) | Application | 私有 patch | keep | controller | - | 投降规则与 FSM/日志集成。  |
| 72 | `tests/test_surrender_rules.py` | 731 | 57 | NormalizeTests(11), CompileTests(3), EvaluateTests(6), DirectionTests(4), LogicTests(5), EngineTests(14), NestedNormalizeTests(7), NestedEvalTests(4), NestedStatusTests(3) | Domain | 无 | keep | controller | - | 投降规则归一化、求值和引擎。  |
| 73 | `tests/test_targeted_battlecries.py` | 118 | 6 | TargetedBattlecryTests(6) | Application | 无 | keep | controller | - | 指向性战吼。  |
| 74 | `tests/test_targeted_locations.py` | 354 | 12 | TargetedLocationTests(8), HandTargetLocationTests(4) | Application | 无 | keep | controller | - | 地标/手牌目标位置。  |
| 75 | `tests/test_timeline_choice.py` | 365 | 18 | TimelineParserTests(7), TimelineAdapterTests(2), TimelineControllerTests(4), TimelineFlowTests(5) | Application | 无 | keep | controller | - | 时间线选择解析/执行。  |
| 76 | `tests/test_timeline_restore.py` | 89 | 4 | RestoredTimelineTests(4) | Application | 无 | keep | controller | - | 时间线恢复。  |
| 77 | `tests/test_update_check.py` | 260 | 15 | ParseVersionTests(3), FetchLatestTests(6), StatusTests(3), RefreshTests(2), PersistenceTests(1) | Tooling / Adapter | 外部网络用 fake | keep | controller | - | 更新检查逻辑；网络全部由 fake opener 替代。  |
| 78 | `tests/test_uv_environment.py` | 98 | 5 | UvEnvironmentSmokeTest(5) | Tooling / environment | 真实 OS/子进程/依赖环境 | keep | controller | - | uv manifest、安装版本和运行时 import 契约。  |
| 79 | `tests/test_validator_client_size.py` | 103 | 5 | ClientSizeFreshnessTests(4), WindowedValidateTests(1) | Application | 私有 patch | keep | controller | - | 客户区大小校验。  |
| 80 | `tests/test_version.py` | 134 | 12 | VersionValueTests(4), StatusApiTests(2), SurfaceTests(6) | Domain / UI / CLI | 源码字符串/源码文件内容 | rewrite | R02C | R02C | 版本值/API 保留；2 个源码文件内容断言改为公开表面测试。  |
| 81 | `tests/test_web_admin_banner.py` | 70 | 7 | AdminWarningBannerTests(7) | UI / CLI | 源码字符串/源码文件内容；管理员路径（仅 fake/文案） | rewrite | R02C | R02C | 管理员横幅文本保留；1 个 `main()` 源码串断言改为捕获输出测试。  |
| 82 | `tests/test_web_hot_control.py` | 473 | 43 | HotChannelTests(19), ScreenshotApiTests(14), PortExclusivityTests(3), PortFileTests(3), RestartHearthstoneApiTests(4) | Web contract | 本地 loopback HTTP；破坏性行为覆盖（fake/临时目录）；私有 patch | keep | controller | - | 热更新、截图、端口独占与重启 API；重启由 fake 覆盖。  |
| 83 | `tests/test_web_liveness_api.py` | 210 | 17 | SaveLivenessTests(5), LivenessStateTests(5), OverlayCallbackTests(4), WebPageTests(3) | Web contract | 私有 patch | keep | controller | - | 存活配置/状态与网页。  |
| 84 | `tests/test_web_logs_wiring.py` | 437 | 32 | LogsRowTests(8), SnapshotTests(4), OverlayLogActionsTests(6), OverlayKeyTests(2), LogTrimTests(4), OverlayWiringTests(8) | Web / UI contract | 源码字符串/源码文件内容；破坏性行为覆盖（fake/临时目录）；私有 patch | rewrite | R02C | R02C | 日志行、确认与回调行为保留；1 个 `_run` 源码串断言改为 fake widget/callback。  |
| 85 | `tests/test_web_prepare_flow.py` | 371 | 22 | PrepareTests(6), OverlayStateSyncTests(6), OverlayPageTests(4), OverlayVerificationTests(5), StartAfterPrepareTests(1) | Web / UI contract | 私有 patch | keep | controller | - | prepare 流程与浮窗同步。  |
| 86 | `tests/test_web_regions_api.py` | 138 | 8 | RegionsApiTests(5), RegionsPageTests(3) | Web contract | 本地 loopback HTTP；私有 patch | keep | controller | - | 本地 loopback 区域预览 API。  |
| 87 | `tests/test_web_screenshot_points.py` | 204 | 11 | ModePointMarkerTests(3), ScreenshotPointsApiTests(3), ServeStopApiTests(4), PortFileTests(1) | Web / UI contract | 私有 patch | keep | controller | - | 截图点与停止 API。  |
| 88 | `tests/test_web_selfcheck_api.py` | 243 | 16 | SelfcheckCacheTests(6), SelfcheckLoggingTests(2), SelfcheckApiTests(4), SelfcheckPageTests(4) | Web contract | 本地 loopback HTTP | keep (rewritten 3 R02C5) | controller | - | 自检 API/cache/status 契约保留；3 个 runner patch 改为公开 `selfcheck.run_checks` fake seam。  |
| 89 | `tests/test_web_ui_surrender.py` | 215 | 16 | SaveTests(6), ConfirmTests(2), EmptyConfigTests(2), NestedSaveTests(3), WebPageTests(3) | Web contract | 无 | keep | controller | - | 投降配置保存/确认/嵌套结构。  |
| 90 | `tests/test_window_tracker.py` | 257 | 16 | TrackerTests(12), ClockConsistencyTests(2), ModuleSingletonTests(2) | Adapter contract | 无实时 sleep 轮询 | keep (rewritten 6 R02C5) | controller | - | 16 个确定性测试；异步 hook 生命周期和事件测试由 Condition/Event 同步。  |
| 91 | `tests/test_ci_gates.py` | 33 | 1 | RequiredOptInResultTests(1) | Tooling / CI contract | 无 | keep (added R02C5) | controller | - | required opt-in skip 必须转成 failure；默认 headless runner 自动收集。  |

## 辅助 JS

| 文件 | 行数 | 调用者 | 决策 | Owner | Follow-up | 原因 |
|---|---:|---|---|---|---|---|
| `tests/frontend_surrender_roundtrip.js` | 209 | `tests/test_surrender_frontend.py` | rewrite | R02C | R02C | 假 DOM 往返有价值，但依赖 Node 与本机搜索路径；Python 调用未固定 UTF-8，当前唯一全量失败。应与调用者一起改为 opt-in、固定 UTF-8，或用浏览器/JS 测试运行器替代。 |

## 方法级非 keep 覆盖

除下表外，每个文件中的其余测试方法均按文件级 `keep` 处理；类名后的数字已给出完整分组计数。

### R02C1 Batch 1 删除记录

| 原测试 | 删除原因 | 替代测试 | 状态 |
|---|---|---|---|
| `test_click_safety_checks.py::ClickSafetyCheckRemovalTests.test_clicks_when_hearthstone_window_is_not_found` | 把无窗口仍点击锁成正确行为 | `ClickSafetyContractTests.test_unknown_hearthstone_window_is_not_clicked` | 已删除 |
| `test_click_safety_checks.py::ClickSafetyCheckRemovalTests.test_background_window_activates_and_clicks_without_hit_testing` | 把未命中仍点击锁成正确行为 | `ClickSafetyContractTests.test_unverified_hearthstone_point_is_not_clicked`、`test_background_window_without_a_verified_activation_point_is_not_clicked`、`test_verified_background_window_activates_then_clicks_target` | 已删除 |
| `test_hsctl.py::TableDriftTests.test_delay_keys_match_config_whitelist` | 比较 CLI 与 config 私有表 | `CliDelayAndScreenshotContractTests.test_delays_show_exposes_every_supported_key_and_effect`、`test_delays_set_accepts_every_supported_key` | 已删除 |
| `test_hsctl.py::TableDriftTests.test_delay_effect_table_matches_web_ui` | 比较 CLI 与 web_ui 私有表 | `CliDelayAndScreenshotContractTests.test_delays_show_exposes_every_supported_key_and_effect` | 已删除 |
| `test_hsctl.py::TableDriftTests.test_screenshot_kinds_match_web_ui` | 比较 CLI 与 web_ui 私有表 | `CliDelayAndScreenshotContractTests.test_screenshot_accepts_every_public_kind`、`test_screenshot_rejects_an_unknown_kind_before_posting` | 已删除 |

| 文件 / 类 | 方法 | 决策 | 依据 |
|---|---|---|---|
| `test_click_safety_checks.py::ClickSafetyCheckRemovalTests` | `test_clicks_when_hearthstone_window_is_not_found`, `test_background_window_activates_and_clicks_without_hit_testing` | deleted (R02C1) | 两个危险验收已由 5 个 fail-closed/合法路径测试替代；见上方 R02C1 删除记录。 |
| `test_friendly_hand_target_battlecries.py::HandTargetCardDataTests` | `test_unreadable_card_data_falls_back_to_the_legacy_cards` | rewritten (R02C5) | 行为保留；公共 `Path.open` 失败后模块重载，验证 legacy cards 回退，不 patch `_metadata_*`。 |
| `test_hand_or_board_targets.py::HandOrBoardCardDataTests` | `test_unreadable_card_data_falls_back_to_the_legacy_card` | rewritten (R02C5) | 行为保留；同一公共 `Path.open` 失败 seam 验证 ambiguous-card legacy 回退。 |
| `test_fsm_liveness_stop.py::LoopWiringTests` | `test_main_loop_and_battling_call_the_check` | replaced (R02C3) | 已由 `test_main_loop_and_battling_call_the_check_once_before_stopping` 通过 fake loop 计数 `check_hearthstone_liveness` 的出口调用替代。 |
| `test_human_like_scope.py::MulliganScopeTests` | `test_fsm_does_not_wire_the_human_like_hook_into_mulligan` | replaced (R02C3) | 已由 `test_fsm_wires_the_human_like_hook_only_into_recommendation_flow` 通过 fake flow 构造参数替代：MulliganFlow 无挂点，RecommendationFlow 有挂点。 |
| `test_hsctl.py::TableDriftTests` | `test_delay_keys_match_config_whitelist`, `test_delay_effect_table_matches_web_ui`, `test_screenshot_kinds_match_web_ui` | deleted (R02C1) | 私有表互相比较已删除；由 5 个 CLI 可观察输出/参数契约测试替代。 |
| `test_hearthstone_liveness.py::NativeProcessTests` | `test_can_enumerate_processes`, `test_finds_this_python_process` | moved (R02C4) | 两个真实 ctypes 进程枚举方法已迁到 `tests/native/test_process_enumeration.py`；`test_returns_bool_for_unknown_process` 与 `test_unreadable_process_list_is_unknown` 注入 fake，保留默认 headless。 |
| `test_overlay_dpi_scaling.py::WindowWiringTests` | `test_run_computes_the_scale_and_pins_tk_font_scaling`, `test_run_sizes_the_window_through_px`, `test_detail_wrap_uses_the_scaled_window_width` | replaced (R02C2) | 已由 `test_window_run_applies_dpi_scale_to_tk_and_geometry`、`test_window_and_minimized_bar_use_scaled_geometry`、`test_status_detail_wrap_uses_scaled_window_width` 通过 fake Tk scaling/geometry/wraplength 替代。 |
| `test_overlay_eye_and_colors.py::AccountEyeToggleTests/LogColorTests/ButtonLayoutTests` | `test_eye_is_rendered_and_clickable`, `test_renderer_uses_the_tag_function_and_configures_every_tag`, `test_destructive_buttons_guard_themselves`, `test_button_labels_have_no_tofu_glyphs`, `test_window_metrics_and_grid_helpers`, `test_status_rows_put_name_and_value_next_to_each_other`, `test_status_rows_wrap_instead_of_being_clipped`, `test_overlay_can_be_minimized_to_a_title_bar` | replaced (R02C2) | 8 个 fake widget/event/callback 测试替代：眼睛点击、日志标签、危险确认、纯文本按钮、grid 契约、状态行 pack/wrap、最小化/展开。 |
| `test_overlay_human_like_status.py::BrandHeaderTests` | `test_brand_name_has_no_subtitle` | replaced (R02C2) | 已由 `test_rendered_brand_header_has_no_subtitle_widget` 通过 fake Label 树断言品牌文本且无副标题。 |
| `test_overlay_self_check_hint.py::SelfCheckButtonTests` | `test_run_builds_the_button_and_the_info_dialog`, `test_start_accepts_the_version_callback` | replaced (R02C2) | 已由 `test_self_check_button_opens_info_dialog_with_version_payload` 与 `test_start_installs_version_callback_for_self_check` 替代。 |
| `test_overlay_tk_lifecycle.py::RunTeardownSourceTests` | `test_frame_must_be_released_before_collecting`, `test_start_threads_the_wrapper_not_run` | replaced (R02C2) | 已由 `test_wrapper_collects_after_run_returns` 与 `test_start_records_wrapper_as_thread_target` 替代；同文件其余源码断言也改为 wrapper 行为。 |
| `test_overlay_window_slider.py::SliderWiringTests` | `test_run_builds_a_custom_canvas_slider`, `test_root_drag_ignores_events_from_the_slider` | replaced (R02C2) | 已由 `test_slider_canvas_events_update_value_and_apply_on_release` 与 `test_root_drag_ignores_slider_events` 替代。 |
| `test_region_overlay_button.py::OverlayButtonTests` | `test_calibrate_button_exists_and_is_short` | replaced (R02C2) | 已由 `test_calibrate_button_dispatches_callback` 通过 fake Button 调用与校准回调 spy 替代。 |
| `test_start_latency.py::NoFixedSleepTests/OverlayStartingFeedbackTests` | `test_main_menu_action_does_not_wait_three_seconds`, `test_choosing_hero_action_does_not_wait_two_seconds`, `test_both_entry_states_log_a_milestone`, `test_overlay_renders_starting_text`, `test_start_accepts_the_is_starting_callback` | replaced (R02C3) | 已由 fake clock 睡眠记录、milestone logger spy、fake widget 的「启动中…」按钮状态和 `start(is_starting=...)` 回调接线测试替代。 |
| `test_window_tracker.py::TrackerTests` | `test_ensure_started_is_idempotent`, `test_stop_stops_recording`, `test_rehook_when_handle_changes`, `test_wait_ready_sees_the_hook_after_startup`, `test_wait_ready_gives_up_when_the_hook_fails`, `test_wait_ready_never_blocks_when_nothing_is_starting` | rewritten (R02C5) | 用 Condition 同步 hook attach/unhook/attempt，用 Event 验证非阻塞返回；事件测试先 `wait_ready()` 再 fire。 |
| `test_surrender_frontend.py::FrontendRoundTripTests` | `test_nested_config_survives_the_form` | moved (R02C4) | 已迁到 `tests/native/test_surrender_frontend.py`；Node 前端往返契约保留，子进程固定 `encoding="utf-8"`，支持 `HSL_NODE` 或自动发现且无机器路径。 |
| `test_version.py::SurfaceTests` | `test_overlay_draws_version_label`, `test_cli_version_flag_uses_module` | replaced (R02C3) | 已由 `test_overlay_renders_version_text_next_to_brand` 与 `test_cli_version_flag_prints_module_label` 通过 fake Label 树和公开 `hsctl.py --version` 子进程替代。 |
| `test_web_admin_banner.py::AdminWarningBannerTests` | `test_main_prints_the_banner_only_for_non_admins` | replaced (R02C3) | 已由 `test_main_outputs_the_banner_only_for_non_admins` 通过 patch admin bool 并捕获 `main()` stdout 替代。 |
| `test_web_logs_wiring.py::OverlayWiringTests` | `test_run_uses_the_log_row_and_confirms_before_clearing` | replaced (R02C3) | 已由 `test_run_renders_log_row_and_clears_only_after_confirmation` 通过 fake widget 树、日志回调和确认取消/确认两路径替代。 |
| `test_web_selfcheck_api.py::SelfcheckCacheTests/SelfcheckApiTests` | `test_result_is_cached_until_forced`, `test_status_carries_the_summary_after_a_run`, `test_api_selfcheck_wraps_the_result` | rewritten (R02C5) | `FakeSelfcheckRunner` 注入公开 `web_ui.selfcheck.run_checks`；保留 cache identity、force rerun、status summary 与 API wrapper。 |

### R02C4 opt-in 迁移记录

| 原测试 | 新测试 | 说明 |
|---|---|---|
| `tests.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form` | `tests.native.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form` | 假 DOM 往返、嵌套条件、折叠状态与渲染断言全部保留；Node 输出固定 UTF-8 解码。 |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_can_enumerate_processes` | `tests.native.test_process_enumeration.NativeProcessTests.test_can_enumerate_processes` | 真实 ctypes 进程枚举保持 opt-in。 |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_finds_this_python_process` | `tests.native.test_process_enumeration.NativeProcessTests.test_finds_this_python_process` | 真实 ctypes 进程枚举保持 opt-in。 |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_returns_bool_for_unknown_process` | 原位置保留 | 注入 fake 的 headless 行为未移动。 |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_unreadable_process_list_is_unknown` | 原位置保留 | 注入 fake 的 headless 行为未移动。 |

### R02C5 rewrite 记录

| 文件 / 方法 | before | after |
|---|---|---|
| `test_friendly_hand_target_battlecries.py::test_unreadable_card_data_falls_back_to_the_legacy_cards` | patch `_metadata_hand_target_card_ids` | 公共 `Path.open` 失败 + 重载清缓存 + legacy assertions |
| `test_hand_or_board_targets.py::test_unreadable_card_data_falls_back_to_the_legacy_card` | patch `_metadata_hand_or_board_card_ids` | 公共 `Path.open` 失败 + legacy ambiguous-card assertions |
| `test_web_selfcheck_api.py` 的 cache/status/API 3 方法 | patch `web_ui.selfcheck.run_checks` + `_log_selfcheck` | `FakeSelfcheckRunner` 注入公开 `selfcheck.run_checks` |
| `test_window_tracker.py` 6 个异步测试 | `time.time()` + `time.sleep(0.01)` 轮询 | Condition/Event 同步；先 ready 再 fire |
| `tools/run_headless_tests.py` opt-in | 逐模块 `unittest`，skip 仍成功 | `--opt-in` 将 required skip 转为 failure |

## 发现的主要垃圾测试类型

1. **源码字符串测试 30 个方法 / 13 个文件**：`inspect.getsource` 28 个，直接读取 `log_overlay.py`/`hsctl.py` 2 个。它们复制实现文本，重构必误报。
2. **危险行为验收 2 个方法**：`test_click_safety_checks` 曾明确断言无窗口/未命中仍点击；R02C1 已替换为 fail-closed。
3. **重复表漂移 3 个方法**：`test_hsctl.TableDriftTests` 曾直接比较私有表；R02C1 已改为外部可观察 CLI 契约。
4. **私有 patch 133 个方法 / 32 个文件**：多数是注入日志、时钟、进程/窗口等 seam，暂时可保留；其中 2 个 hand-target metadata patch 和 3 个 web selfcheck runner patch 已在 R02C5 重写。
5. **真实/外部运行依赖**：Node 1 个测试，真实进程枚举 2 个方法，MCP 子进程 2 个，环境/依赖 smoke 8 个；大多可保留在独立层，Node 与进程枚举不应进默认核心门。
6. **本机路径与编码**：`test_surrender_frontend.py` 曾硬编码用户目录与特定盘符的 Node 路径，且 `subprocess.run(text=True)` 使用 GBK，导致当时唯一失败；R02C4 已移除具体路径。
7. **实时轮询/线程同步**：`test_window_tracker.py` 6 个方法曾用 `time.time()` + `time.sleep(0.01)` 等后台 hook，最长 2s；R02C5 已改为 Condition/Event 同步。
8. **仓库内临时目录**：`test_hsctl.py`、`test_web_hot_control.py`、`test_web_screenshot_points.py` 使用 `.tmp-tests`，并行/中断时可能污染工作区；不是删测试理由，但 R02C 应改为独立临时目录或 fixture 清理。

## Discovery 原始结果

```powershell
uv run python -m unittest discover -s tests -t .
# Ran 1520 tests in 134.726s
# FAILED (failures=1, skipped=1)
# exit 1
```

唯一失败：`tests.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form`。原始异常包含：
```text
UnicodeDecodeError: 'gbk' codec can't decode byte 0x94 in position 8: illegal multibyte sequence
```

## 汇总

- `keep`：1471 个方法（含可按文件分组保留的测试）。
- `rewrite`：12 个方法（5 个私有 patch + 1 个 Node 前端测试 + 6 个 `test_window_tracker` 实时轮询/线程同步测试）；R02C4/R02C5 已完成对应 rewrite。
- `move-native`：2 个方法（仅真实进程枚举）。
- `candidate-delete`：35 个方法（源码串 30 + 危险验收 2 + 重复表漂移 3）。
- 合计：1471 + 12 + 2 + 35 = 1520。

## R02E Cleanup Execution Log

### B1 duplicate table / private list drift

- Status: implemented, pending independent review and commit.
- Removed `tests/test_web_hot_control.py::HotChannelTests.test_delay_effect_scope_table`.
- Removed `tests/test_draw_extra_delay_settings.py::ConfigTests.test_key_is_part_of_the_user_delay_whitelist`.
- Rewrote R06 in place as a fixed-frame `screen_regions -> battle_entry.identify` behavior check.
- Rewrote R07 in place as a default-coordinate plus save/load roundtrip check.
- Public equivalent gate: 31/31 pass; `HotChannelTests` 19 -> 18; `ConfigTests` 3 -> 2.

### B2 DTO duplicate boundary assertion

- Status: implemented, pending independent review and commit.
- Merged M01 confidence assertions into `test_observations_are_immutable_and_serializable`.
- Reused the existing `test_max_turns_requires_an_exact_positive_int` assertion for `max_turns=0`.
- Removed the duplicate `test_invalid_confidence_and_status_are_rejected`.
- `tests.application.test_dto`: 7 -> 6 methods, all pass; default remains 1,523, discover target 1,685.

### B5 private seam / mock-call-only

- Status: implemented, pending independent review and commit.
- R08 now uses a temporary runtime log and asserts preserved content, appended line, bounded size, and absence of a temp file.
- R09 now asserts observable control state instead of `Thread.assert_not_called()`.
- Method count and both suite counts remain unchanged.

### B6 wall-clock timeout assertions

- Status: implemented and independently approved.
- R04/R05 now gate blocking callbacks with `threading.Event`.
- Timeout is asserted by `TimeoutError`; after release they locate the actual deadline worker and join it.
- Removed `time.sleep` upper-bound timing assertions.
- Method counts remain default 1,523, discover 1,685, opt-in 3.

## R02E Execution Summary

- Stage 1 B1/B2/B5: independently APPROVED.
- Stage 2A B6: independently APPROVED.
- Stage 2B B9/B8/B7: implemented, pending independent review.
- Current boundaries: default 1,511; discover 1,673; CI opt-in 16; native-live 1.
- B9 established non-package `tests/opt_in` and `tests/native_live` directories and import-free manifests.
- B8 moved installed-environment checks to CI opt-in while retaining static manifest parity in default.
- B7 moved live-window mapping to native-live and MCP/Windows semantics to required CI opt-in.
