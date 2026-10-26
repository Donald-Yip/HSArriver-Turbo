# R01 Implementer Report

## 状态

FIX_ROUND_1_COMPLETE（等待复审；按要求未 commit、未 push）

## Base / Head SHA 与分支

- Base SHA: `a0b1c5371cfa93b826050b42a52225b7cece259f`
- Head SHA: `a0b1c5371cfa93b826050b42a52225b7cece259f`（工作区改动尚未提交）
- Branch: `feature/R01-uv-baseline`

## 变更文件

- `pyproject.toml`：13 个直接依赖 + uv OpenCV constraint overrides
- `uv.lock`：重新锁定，三种 OpenCV 发行版均为 4.10.0.84
- `tests/test_uv_environment.py`：新增 manifest 三向 parity、废弃清单/R02 清理断言、OpenCV distribution/runtime 一致性测试
- `requirements.txt`：保留为明确废弃兼容清单，并加入 `R02 cleanup` 注释
- `README.md`：安装/运行命令使用 uv；修复 `python tools/...` 残留
- `SKILL.md`：Python 命令统一使用 uv
- `AGENTS.md`：增加 uv-only 依赖与执行契约
- `docs/CLI.md`：命令与环境说明统一使用 uv
- `docs/FEATURES.md`：入口命令统一使用 uv
- `docs/GIT.md`：测试和发布命令改为 `uv run python tools/...`
- `docs/guide.html`：安装说明改为 uv，导航改为“安装 uv（管理 Python 3.12）”
- `docs/tasks/R01-uv-baseline.md`：修复字面量 `\n`，恢复正常列表
- `docs/agent/reports/R01-implementer.md`：本报告

controller-owned 并发改动 `TOTAL_TASK.md`、`docs/agent/HANDOFF.md`、`docs/agent/TASK_BOARD.md`、`docs/agent/TASK_BOARD.json` 仍出现在工作区；implementer 未编辑这些文件。

## 接口

- 消费：`requirements.txt` 的 13 个直接依赖与精确版本；Python 3.12 约束。
- 生产：`uv sync`、`uv run`、`uv.lock` 锁定环境契约。
- 公共 API：无变更。
- HTML/CLI/HTTP/MCP/config/log 行为：无变更。

## Fix Round 1 解决项

1. **OpenCV 版本不一致**：在 `[tool.uv].constraint-dependencies` 中约束 `opencv-python==4.10.0.84` 与 `opencv-contrib-python==4.10.0.84`；`uv.lock` 已更新，运行时 `cv2.__version__ == 4.10.0`，三个发行版均为 `4.10.0.84`。
2. **活动文档裸 Python**：README 的 `tools/...` 示例和 `docs/GIT.md` 发布命令全部改为 `uv run python ...`；全活动文档检查无裸 Python/pip/Conda 执行示例。
3. **报告命令可复现**：删除 placeholder，改为实际运行的 unittest、OpenCV、文档扫描和 scope 命令，并附 raw results。
4. **Manifest parity**：`tests/test_uv_environment.py` 直接解析 `pyproject.toml` 与废弃 `requirements.txt`，比较两者及已安装直接依赖版本；同时断言 `R02 cleanup` 注释存在。
5. **任务书/导航**：修复 `docs/tasks/R01-uv-baseline.md` 中的字面量 `\n`，修复 guide 导航的旧“装 Python 3.12”。

## 原始验证命令和 exit code

```powershell
uv lock
# Resolved 244 packages in 2ms
# exit 0

uv sync
# Resolved 244 packages in 2ms
# Audited 77 packages in 3ms
# exit 0

uv lock --check
# Resolved 244 packages in 2ms
# exit 0

uv run python -c "import tomllib; data=tomllib.load(open('uv.lock','rb')); wanted=('opencv-python','opencv-python-headless','opencv-contrib-python'); rows=sorted((p['name'],p['version']) for p in data['package'] if p['name'] in wanted); print(rows); assert all(v=='4.10.0.84' for _,v in rows); assert len(rows)==3"
# [('opencv-contrib-python', '4.10.0.84'), ('opencv-python', '4.10.0.84'), ('opencv-python-headless', '4.10.0.84')]
# uv-lock-opencv-check exit=0
```

```powershell
uv run python --version
# Python 3.12.13
# exit 0

uv run python -c "import cv2, numpy, PIL, win32api, paddleocr"
# exit 0

uv run python -m unittest tests.test_uv_environment -v
# test_deprecated_requirements_has_r02_cleanup_note ... ok
# test_manifest_parity_and_installed_direct_versions ... ok
# test_opencv_distributions_and_runtime_agree ... ok
# test_python_is_312 ... ok
# test_required_runtime_modules_import ... ok
# Ran 5 tests in 2.978s
# OK
# exit 0
```

```powershell
uv run python -c "import cv2, importlib.metadata as m; print(f'cv2.__version__={cv2.__version__}'); [print(f'{x}=={m.version(x)}') for x in ('opencv-python','opencv-python-headless','opencv-contrib-python')]"
# cv2.__version__=4.10.0
# opencv-python==4.10.0.84
# opencv-python-headless==4.10.0.84
# opencv-contrib-python==4.10.0.84
# opencv-runtime-check exit=0
```

```powershell
uv run python -m unittest tests.test_uv_environment.UvEnvironmentSmokeTest.test_manifest_parity_and_installed_direct_versions tests.test_uv_environment.UvEnvironmentSmokeTest.test_opencv_distributions_and_runtime_agree -v
# test_manifest_parity_and_installed_direct_versions ... ok
# test_opencv_distributions_and_runtime_agree ... ok
# Ran 2 tests in 0.180s
# OK
# parity-and-opencv-tests exit=0
```

```powershell
uv run python -m unittest tests.test_selfcheck.DependencyManifestTests -v
# Ran 5 tests in 0.001s
# OK
# legacy-dependency-manifest-tests exit=0
```

```powershell
$files = @((Resolve-Path README.md).Path,(Resolve-Path SKILL.md).Path,(Resolve-Path AGENTS.md).Path) + (Get-ChildItem docs -Recurse -File | Where-Object { $_.Extension -in '.md','.html' -and $_.FullName -notlike '*\docs\agent\reports\*' } | ForEach-Object { $_.FullName })
$hits = Select-String -Path $files -Pattern '(?<!uv run )python (?:tools/|[A-Za-z0-9_.-]+\.py|--json|--version|-m\b|-c\b)|\bpip install\b|\bconda (?:create|activate|install)\b'
if ($hits) { $hits; exit 1 }
'active-doc-uv-check: no bare python/pip/conda execution examples'
# active-doc-uv-check: no bare python/pip/conda execution examples
# exit=0
```

```powershell
$forbidden = @('.github','FSM_action.py','web_ui.py','hsctl.py','battle_entry.py','click.py','config.py','calibrate_roi.py','screen_regions.py','display_mapping.py')
$changed = git diff --name-only HEAD
$violations = $changed | Where-Object { $p = $_; $forbidden | Where-Object { $p -eq $_ -or $p -like "$_\*" } }
if ($violations) { $violations; exit 1 }
'forbidden-runtime-check: no production or CI files changed'
# forbidden-runtime-check: no production or CI files changed
# exit=0
```

```powershell
git diff --check -- README.md SKILL.md AGENTS.md docs/CLI.md docs/FEATURES.md docs/GIT.md docs/guide.html docs/tasks/R01-uv-baseline.md requirements.txt
# authored-doc-diff-check exit=0
```

```powershell
uv run python -m unittest discover -s tests -t .
# Ran 1520 tests in 133.239s
# FAILED (failures=1, skipped=1)
# full unittest exit=1
```

全量测试唯一原始失败（R01 范围外）：

```text
FAIL: test_nested_config_survives_the_form (tests.test_surrender_frontend.FrontendRoundTripTests)
AssertionError: 'OK: A �� (B �� C)' not found in ''
```

同一测试过程还有：

```text
UnicodeDecodeError: 'gbk' codec can't decode byte 0x94 in position 8: illegal multibyte sequence
```

```powershell
uv run hslegend-onegame --help
# error: Failed to spawn: `hslegend-onegame`
# Caused by: program not found
# exit 2
```

该入口不在 R01 任务验收内，当前仓库尚无对应实现；未改运行时代码。

## Scope Check

- 仅修改 R01 允许的环境、测试和文档文件。
- 未修改 `.github`、CI、运行时代码、坐标、延时、阈值或点击逻辑。
- 未 commit、未 push。
- `requirements.txt` 保留为 deprecated-only 兼容文件，并明确标记 R02 清理。
- `uv.lock` 与 `pyproject.toml` 一致，`uv lock --check` exit 0。

## 风险

- 全量测试仍有 `test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form` 的 Windows/Node 输出编码失败，位于 R01 文件范围外。
- `requirements.txt` 的兼容 pin 与 `pyproject.toml` 暂时重复；已由测试锁定 parity，R02 应迁移 self-check 后删除。
- PaddleOCR 传递依赖仍会解析额外 OpenCV 发行版，但已由 uv constraint 全部锁定到 4.10.0.84，运行时确认一致。

## Review Focus

- 核对 `pyproject.toml` 的 uv constraints 是否覆盖三个 OpenCV 发行版，且 `uv.lock` 无 5.x 残留。
- 核对 smoke test 是否同时验证 manifest parity、已安装直接依赖和 `cv2.__version__`。
- 复核活动文档扫描命令无漏网裸 `python tools/...`。
- 确认废弃 `requirements.txt` + R02 cleanup 注释可接受。
- 复核全量测试唯一失败与 R01 无关的证据。
