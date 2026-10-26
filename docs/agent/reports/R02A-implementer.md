# R02A Implementer Report

## 状态

APPROVED_FOR_COMMIT（独立复审通过；按要求未 commit、未 push）。

## Base / Head SHA 与分支

- Base SHA: `e925d9e4bf5626c3277ce10ed2db843d20a1fa0e`
- Head SHA: `e925d9e4bf5626c3277ce10ed2db843d20a1fa0e`（工作区改动尚未提交）
- Branch: `feature/R02A-test-inventory`

## 变更文件

- `docs/testing/TEST_INVENTORY.md`：90 个 `test_*.py` + 1 个辅助 JS 的完整盘点、统计、分类、标志、决策与替代方向。
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`：headless 分层、35 个 candidate-delete 台账、rewrite/move-native 计划、删除批次与门禁。
- `docs/agent/reports/R02A-implementer.md`：本报告。

未修改 `tests/**`、生产代码或 CI；未删除任何测试；未 commit/push。

## Fix Round 1

- `NativeProcessTests` 决策收窄：仅 2 个真实 ctypes 枚举测试 `move-native`；2 个 fake unknown 分支保持 `keep`。
- `test_window_tracker.py` 增加 6 个实时轮询/线程同步方法为 `rewrite`，要求 Event/Condition/注入同步器替代 wall-clock + sleep。
- Inventory 每个文件行新增 `Owner` 与 `Follow-up`；keep 为 `controller / -`，非 keep 为 `R02C / R02C`。
- 决策总数修正为 keep 1471 + rewrite 12 + move-native 2 + candidate-delete 35 = 1520。

## 接口

- 消费：`docs/agent/TEST_STRATEGY.md`、R02 任务书、当前 `tests/` 树、`unittest` discovery。
- 生产：R02B/R02C 可审阅的 behavior-to-test mapping 与删除候选台账。
- 公共 API/HTTP/CLI/MCP/config/log 行为：无变更。
- 新依赖：无。

## 盘点摘要

| 指标 | 结果 |
|---|---:|
| Python 测试文件 | 90 |
| 辅助 JS 文件 | 1（`tests/frontend_surrender_roundtrip.js`，209 行） |
| Python 测试文件物理行数 | 24401 |
| 含测试方法的类 | 337 |
| 私有 patch 覆盖 | 133 个方法 / 32 个文件 |
| AST 测试方法 | 1520 |
| `unittest` discovery | 1520 |
| keep | 1471 |
| rewrite | 12（5 个私有 patch + 1 个 Node 前端测试 + 6 个 `test_window_tracker` 实时轮询/线程同步测试） |
| move-native | 2（仅真实进程枚举） |
| candidate-delete | 35（源码串 30 + 危险验收 2 + 重复表漂移 3） |

## 原始验证命令和结果

```powershell
uv run python -m unittest discover -s tests -t .
# Ran 1520 tests in 134.726s
# FAILED (failures=1, skipped=1)
# exit 1
```

唯一失败：

```text
FAIL: test_nested_config_survives_the_form (tests.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form)
UnicodeDecodeError: 'gbk' codec can't decode byte 0x94 in position 8: illegal multibyte sequence
AssertionError: 'OK: A �� (B �� C)' not found in ''
```

该失败已记录为 R02C rewrite：Node 子进程必须固定 `encoding="utf-8"`，并移除 `C:\Users\DonaldYip\...` / `D:\nodejs\node.exe` 本机路径。全量测试仍有 1520 个 case，说明 discovery 数量未因盘点变化。

```powershell
# 检查 inventory 表行、唯一性、AST 方法数和 discovery 数一致
# rows=90 unique=90 owner_followup=90 ast_methods=1520 expected_files=90
# liveness_decision=keep + move-native 2
# window_tracker_decision=keep + rewrite 6
# decision_totals: keep=1471 rewrite=12 move-native=2 candidate-delete=35 total=1520
# inventory-check: ok
# exit 0

git diff --check -- docs/testing/TEST_INVENTORY.md docs/testing/TEST_RATIONALIZATION_PLAN.md docs/agent/reports/R02A-implementer.md
# diff-check: ok
# exit 0

git diff --name-only -- tests src .github
# forbidden-scope-check: no changes under tests/src/.github
# exit 0
```

## Scope Check

- 仅修改 R02A allowed files：`docs/testing/**` 与 `docs/agent/reports/R02A-implementer.md`。
- 未修改任何 `tests/` 文件。
- 未修改生产代码、CI、坐标、延时、阈值或点击序列。
- 未 commit、未 push。
- controller-owned 工作区改动保持原样，implementer 未编辑。

## 主要发现

- 30 个源码字符串/源码内容断言集中在 13 个文件，重构时会误报；应改为 fake widget/flow/clock 或公开 CLI/API 行为。
- `test_click_safety_checks` 的 2 个方法把“找不到窗口/未命中仍点击”锁成正确行为，是 fail-open 测试，必须优先替换。
- `test_hsctl.TableDriftTests` 的 3 个方法只比较私有表，应用 CLI 黑盒输出替代。
- 私有 patch 共 133 个方法 / 32 个文件，多数用于 fake 日志/时钟/进程/窗口 seam；其中 5 个（2 个 hand-target metadata、3 个 web selfcheck cache/runner）明确需要 rewrite。
- 唯一 Node 测试同时含本机绝对路径和 GBK 解码失败，当前挡红默认 suite。
- 真实进程枚举仅 2 个方法（`test_can_enumerate_processes`、`test_finds_this_python_process`）应移动到 opt-in native；`test_returns_bool_for_unknown_process` 与 `test_unreadable_process_list_is_unknown` 注入 fake，必须保留 headless。
- `test_window_tracker.py` 6 个方法用 `time.time()` + `time.sleep(0.01)` 实时轮询后台 hook（最长 2s），应改为 Event/Condition/注入同步器的确定性测试。

## 风险

- 盘点为文件/类分组 + 非 keep 方法清单；R02C 删除时必须逐条对照台账，避免把行为替代误当纯垃圾清理。
- 浮窗源码串测试包含用户可见文案/布局行为，替代测试不能只做源码快照或过宽的 UI 冒烟。
- 本地 loopback HTTP 测试虽然不访问外部网络，仍占用端口；并行 CI 需确认端口隔离与 cleanup。
- `test_surrender_frontend` 的 Node 修复应在 R02C 完成，否则默认 suite 持续为红。

## 请求 Review Focus

1. 核对 inventory 是否为 90 个测试文件各出现一次、每行都有 Owner/Follow-up，方法总数是否仍为 1520。
2. 复核 35 个 candidate-delete 是否 each 有替代或明确非行为理由，并为每个非 keep 文件保留 R02C follow-up。
3. 复核 30 个源码串文件的分类与 5 个私有 patch 的重写方向。
4. 确认 `move-native` 只用于 2 个真实进程枚举方法；另外 2 个 fake unknown 分支与 13 个 fake-clock 测试保持默认 headless。
5. 确认 `test_window_tracker` 的 6 个实时轮询测试已纳入 rewrite，且 Batch 1-5 计数与 inventory 一致。
