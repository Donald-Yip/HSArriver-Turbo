# R18F Memory Growth

## Task ID

R18F

## Goal

修掉长跑进程的一处**无界内存增长**，并让内存趋势在日志里可见：用户反馈
「脚本运行久了占用的内存越来越多」。已确认 `StableRecommendationReader._cache`
以 `frame.exact_hash` 为 key 缓存 `OcrEvidence` 且插入后永不清理；reader 是
`FSM_action` 里的模块级单例，整个进程生命周期只建一次 → 每个新面板帧约 +4.2KB
（实测 2 万条 = 80MB），一小时上千帧长期累积。改为有界 LRU（只为「同一次读取里
同一帧不用重复推理」服务，不需要跨局保留），并新增每局结束的 RSS 观测 + 超阈值
回收 OCR 引擎的钩子（集成由 Lead 在 `FSM_action` 接）。

## Scope

- `src/ocr/stable_reader.py`
  - 新增模块常量 `MAX_CACHE_ENTRIES = 64`。
  - `_cache` 改为 `collections.OrderedDict`；`__init__` 新增
    `max_cache_entries=None`（None = 用 `MAX_CACHE_ENTRIES`）。
  - 命中时 `move_to_end`；写入新证据后超限 `popitem(last=False)` 淘汰最久未用。
  - 新增 `clear_cache()`。
  - **缓存命中/未命中的返回值语义、`_last_rejected` 行为保持不变**。
- `src/ocr/paddle_adapter.py`
  - 新增 `reload()`：`self.engine = None`，下次 `recognize()` 走 `load()` 重建；
    docstring 说明用途是「跨局回收 OCR 引擎占用的内存」，注入引擎（测试）同样适用。
- `memory_watch.py`（新增根模块，legacy 侧，纯 stdlib + 可选 psutil）
  - `rss_mb()`：当前进程 RSS（MB，float）；psutil 不可用/读取失败返回 None。
  - `baseline_mb()` / `peak_mb()` / `reset()`：第一次观测记基线、记录最大 RSS、
    清空状态（测试用）。
  - `note_game_end(on_recycle=None, rss_reader=rss_mb)`：每局结束打一行
    `[SYS] 内存 RSS 612 MB（较基线 +72 MB / 峰值 640 MB）`；RSS ≥
    `MEMORY_RECYCLE_MB`(900) 先调 `on_recycle()` 再打
    `[SYS] 内存回收完成：RSS 612 → 480 MB`；回收后 RSS ≥ `MEMORY_WARN_MB`(1400)
    用 `warn_print` 建议重启控制台；所有异常不外溢。
- 测试：`tests/test_memory_watch.py`、`tests/test_ocr_evidence_cache.py`。

## Non-Goals

- 不改 OCR 引擎/模型/预处理/ROI 坐标，不改推荐解析与点击逻辑。
- 不改 `FSM_action.py` 的集成钩子（由 Lead 在任务收尾时接线）。
- 不改 `log_overlay.py` / `web_ui.py` / `src/ocr/status.py`（属于其它未提交任务）。
- 不做进程级 memray/tracemalloc 剖析，不在日志里输出对象级统计。
- 不引入新第三方依赖（psutil 已是项目既有可选依赖）。

## Inputs

- 用户 2026-10-31 反馈：「脚本运行久了占用的内存越来越多」。
- 已排除项（实测）：PaddleOCR 引擎本身（300 次真实中文文本识别 RSS 稳定 541MB）、
  Tk 日志重绘（200 次重建 RSS 不变）、`log_state`（每局 flush 重建）、
  `battle_record`（deque maxlen）、feedback（有上限）。
- 已确认项：`StableRecommendationReader._cache` 每帧 +4.2KB，2 万条约 80MB，
  模块级单例整个进程不重建（`FSM_action.recommendation_reader` /
  `mulligan_reader`）。
- 基分支 `feature/R18-local-strategy-hybrid` @ `81c2736`。

## Outputs

- 有界帧缓存（默认 64 条）+ `clear_cache()`；`PaddleOcrAdapter.reload()`；
  每局一行 `[SYS] 内存 RSS …` 与回收/告警日志；`memory_watch` 观测接口。

## Interfaces

- Consumes: `OcrEvidence`（`type(evidence)(...)` 重建，沿用现有语义）、
  `print_info.info_print` / `warn_print`。
- Produces: `StableRecommendationReader(max_cache_entries=…)`、`clear_cache()`；
  `PaddleOcrAdapter.reload()`；`memory_watch.rss_mb/baseline_mb/peak_mb/reset/
  note_game_end`。

## Dependencies And Impact

- Depends on: 现有 `src/ocr` reader/adapter 与 legacy `print_info`。
- Impacted modules: OCR 读取路径（缓存语义不变，仅容量有界）与每局收尾日志。
- Public API impact: 构造参数与新增方法均为可选项，旧调用不受影响。

## Allowed Files

- `src/ocr/stable_reader.py`
- `src/ocr/paddle_adapter.py`
- `memory_watch.py`
- `tests/test_memory_watch.py`
- `tests/test_ocr_evidence_cache.py`
- `docs/tasks/R18F-memory-growth.md`
- `README.md`（Lead 集成时补「长跑内存」小节）
- `SKILL.md`（Lead 集成时补排障口径）
- `AGENT_CODE_MAP.md`（Lead 集成时补诊断入口）

## Forbidden Files

- `FSM_action.py`
- `log_overlay.py`
- `web_ui.py`
- `src/ocr/status.py`
- `src/hslegend/**`

## Acceptance

```powershell
uv run python -m unittest tests.test_memory_watch tests.test_ocr_evidence_cache -v
uv run python tools/run_headless_tests.py
uv run python tools/check_architecture.py
uv run python -m compileall -q src tests tools
```

## Risk And Rollback

- Risk: 64 条缓存上限理论上会让「同一帧在两次读取之间被重复推理」，但缓存命中
  只用于同一次 `read`/`read_frame` 内的重复帧，跨读取命中本来就很少；即便未命中
  也只是多一次推理，不影响正确性。`note_game_end` 会额外做一次 psutil RSS 采样
  （微秒级），回调异常已兜住。
- Rollback: `_cache` 恢复普通 dict 并删除 LRU/上限逻辑（`MAX_CACHE_ENTRIES`、
  `move_to_end`、`popitem`）即可；`memory_watch.py` 与 `reload()` 无其它调用方时
  可直接删除。

## Garbage-Code Check

- In-scope garbage to refactor: 无（缓存插入抽成 `_remember`，消掉 `read` /
  `read_frame` 两处重复写入逻辑）。
- Out-of-scope discoveries to register: `log_state` / `battle_record` / feedback
  已确认有界或每局重建；若后续仍见缓慢上涨，再开任务做对象级剖析。

## Git Requirement

One commit:

```text
fix(memory): bound the OCR evidence cache and report RSS per game
```

## Report Paths

- Implementer: `docs/agent/reports/R18F-implementer.md`
- Reviewer: `docs/agent/reviews/R18F-review.md`
