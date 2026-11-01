# 2026-11-01 错误记录 v2 设计（Error Diagnostics v2）

> 状态：设计定稿，待排期实施（P1 → P2 → P3）
> 读者：实施者（agent / 人）、复核者、未来的自己
> 参考基线：`0eab08e`（docs(R02F) 之后的工作区状态）

## 0. 一句话

把"出错就截一张图 + 一句笼统中文"改造成**结构化事件 + 全程动作时间线 + 带识别框的证据帧 + 诊断码→代码位置索引**，
产出一个能在人不在场时自动生成、**AI 拿到后 1 分钟内能定位到文件/函数**的运行包。

---

## 1. 背景：为什么现在的记录解决不了问题

### 1.1 真实产物体检（只读统计，2026-11-01）

对 `logs/出错记录_20261029/`、`_20261030/`、`_20261031/` 与 `logs/20261030-打出5号位随从-错误反馈.zip` 的实测：

| 指标 | 实测 | 意味着 |
|---|---|---|
| 出错记录目录数 | 57 个（126 个文件） | 机制在跑，但…… |
| 带截图的记录 | **12 / 57**（PNG 共 31.87 MB，单张 1920×1080 约 2.2 MB） | 45 次出错没有画面 |
| `code` 为空串 | **44 / 57** | "不知道错在哪"占绝大多数 |
| 含异常栈 | **0 / 57** | 无法定位代码 |
| OCR 原文 `raw` 非空 | **0 / 57**（全部 `[]`） | 无法判断"是认错了还是没认出来" |
| 期望 vs 实际 | **0 / 57**（字段根本不存在） | 无法判断"做对了没有" |
| 点击坐标/目标 | 无字段 | 无法判断"点到了哪" |
| 反馈 zip | 8 个条目、**无 manifest/索引** | 日志与截图只能对应到"同一个文件夹"，对不到具体日志行 |
| `log/error_log.txt`、`warn_log.txt` | **0 字节** | 关键日志渠道实际未产出内容 |
| `log/info_log.txt` 格式 | `[23:54:11 INFO] 消息` | 无日期、无模块、无行号 |

单个记录的全部字段（`错误.json`）：`index / time / turn / phase / type / code / retries / instruction / raw / detail / screenshots / folder / capture_skipped`。
**不含**：卡组、运行标识（只在文件夹名前缀）、FSM 状态、OCR 全量文本、决策结果、截图尺寸与抓图元信息。

样本（`20261031_143513-02_对局前_报错_145036`）：

```text
回合 对局前（对局结束）
类型 报错
推荐 打出1号位随从
详情 [14:50:36 WARN] 结算界面超过 40 轮仍未出现「开始」按钮，进入错误处理。
截图 （非回合内，按设置不截图）
```

一句话人话都没有写"当时屏幕上是什么""点了哪""Power.log 有没有事件"。

### 1.2 代码层面的四个根因

| # | 根因 | 证据 |
|---|---|---|
| R1 | **靠正则猜日志文本来决定记不记**：只有命中 `继续重试 / 换牌重试 / 重新点击 / retry:` 等关键词才算 retry，其余归 INFO 直接丢 | `battle_record.py:57`（`_RETRY_MARKERS`）、`battle_record.py:144`（`classify`）、`web_ui.py:134`（`_record_battle`） |
| R2 | **去重与阈值互相抵消**：诊断文本按 code 去重只发一次（`FSM_action.py:1206`），而截图要"同一 code 连续 ≥ 3 次"（`feedback.py:269-284`）→ 重试类几乎永远不达标 | `FSM_action.py:1203-1212` + `feedback.py:278-284` |
| R3 | **阶段白名单丢弃大量现场**：`RECORD_PHASES = ("我方出牌","对手回合","对局结束")`，对局前/换牌直接 `return None`；截图更窄，仅 `CAPTURE_PHASES` | `feedback.py:63-64`、`feedback.py:252-254`、`feedback.py:226-233` |
| R4 | **现成能力从未被用上**：`capture_frame("boxes")` 能画"机器人看到的全部区域框"，出错截图却只调 `capture_frame("client")`，还丢弃返回的 `info`（映射/是否退化成整屏）；`action_feed`（最近 60 块动作流水）、`PendingRecommendationAction`（指令/目标/来源）、`ocr/status.py`（每区域原文+置信）全部没进记录 | `web_ui.py:615-718`（boxes 在 637-667）、`web_ui.py:722-741`、`action_feed.py:35`、`src/ocr/status.py:43` |

补充：日志文件以 `"w"` 模式打开（`print_info.py:22-26`），**进程一重启就清空**——这是 `error_log.txt` 常年 0 字节的直接原因；时间戳只有 `%H:%M:%S`（`print_info.py:48`），跨天无法对齐。

### 1.3 与验收标准的差距

目标验收：**人不在场跑完 → 回来把包丢给 AI → AI 1 分钟内说出错在哪个文件/函数、机器人看到了什么、做了什么。**
当前产物对这三问的回答能力分别是：无（无栈/无行号）、弱（一半无图且无框）、无（无动作与坐标）。

---

## 2. 目标与非目标

### 2.1 目标（可测）

1. **可定位**：包内任一事件都能给出 `文件:函数:行号`；异常事件附完整 traceback。
2. **可复盘**：一次运行有一份按时间排序的完整动作/事件流水，能重建"它当时在干什么"。
3. **可判读**：错误帧是全屏 + **识别区域框** + 关键区域裁剪；AI 不需要向用户追问"你说的那个画面是什么"。
4. **人不在场也能自动产出**：运行结束自动生成一个 zip，无需点击。
5. **体积可控**：单包目标 ≤ 30 MB，单帧 ≈ 120–180 KB（1280 宽 JPG）。

### 2.2 非目标（本次明确不做，别偷偷加）

| 不做 | 原因 |
|---|---|
| 机器判定"目标选错/牌打错/策略错" | 没有内部真值；用户明确"不要做期望，这个没办法做"。对错判断交 AI，依据包内证据 |
| 录像式全程落盘（1fps 存盘） | 磁盘/IO 成本高；只在触发时封包 |
| 热键/手动"记一下" | 脚本通常**人不在场**运行，热键无意义 |
| 回合外逐条建错误目录 | 只记"导致自动化停止"的回合外失败（一条汇总） |
| 改变自动化本身的决策逻辑 | 本设计只改"记录什么、怎么记、怎么打包" |

### 2.3 三个已确认假设（本文档据此展开；若推翻需改设计）

- **A1**：「有反应」= 以下任一成立 —— ① Power.log 出现新事件（解析自 `log_op.py`，经 `log_state` 的 revision 递增判断）；② 客户区降采样灰度帧差超过阈值（默认 0.5% 像素变化）；③ OCR 面板文本发生变化。
- **A2**：浮窗「导出本次出错截屏与日志」按钮语义改为「导出本次运行包」，位置与交互不变（`log_overlay.py:2664-2678`、`web_ui.py:2886`）。
- **A3**：无任何事件触发的运行，**timeline 有、证据帧无**。纯"选错目标但程序自认成功"的动作不会留下画面证据——这是"只有多次重试才留"的直接后果，接受。

---

## 3. 架构总览

```text
                    ┌─────────────────────────────┐
  FSM_action ──────►│ diag/events.py  emit()      │  结构化直投（不再正则猜日志）
  recommendation_flow│  EventEnvelope + 计数/阈值  │
  mulligan_flow     └──────────┬──────────────────┘
  web_ui(env 检查)             │
  src/ocr/status               ▼
                    ┌─────────────────────────────┐
                    │ diag/store.py  事件环形表   │  内存，仅本次运行
                    └──────────┬──────────────────┘
                               │
  diag/ring.py ────────────────┤  1fps 帧环 + 动作帧（1280 宽 JPG）
  （独立采集线程）              │
                               ▼
                    ┌─────────────────────────────┐
                    │ diag/package.py  运行包     │  manifest / timeline /
                    │  （运行结束自动生成 zip）    │  frames / summary / env
                    └─────────────────────────────┘
                               │
                    logs/<日期>-<摘要>-运行包.zip
```

新增模块（建议放 `diag/` 包，避免继续膨胀根目录的单文件）：

| 模块 | 职责 | 规模预估 |
|---|---|---|
| `diag/events.py` | `EventEnvelope`、`emit()`、阈值计数、去重/节流 | ~250 行 |
| `diag/ring.py` | 帧环形缓冲、采集线程、JPG 编码、内存预算 | ~220 行 |
| `diag/package.py` | 运行包组装、manifest、原子写、命名 | ~300 行 |
| `diag/codes.py` | 诊断码注册表（code → 文件:函数:行号） | ~80 行 |
| `diag/retention.py` | 保留/清理 | ~80 行 |
| `diag/log_ctx.py` | 调用位置捕获（喂给 `print_info` 与事件） | ~60 行 |

改造现有文件：`print_info.py`、`web_ui.py`（`_record_battle`/`_feedback_capture`/`_feedback_export_and_log`/`capture_frame` 调用侧）、`feedback.py`（旧机制降级为兼容层）、`config.py`（新增 `feedback_v2` 段）、`battle_record.py`（`classify` 仅留兜底）、`log_overlay.py`（按钮文案）、`script_logs.py`（清理口径）。

---

## 4. 事件模型

### 4.1 `EventEnvelope` 字段

```json
{
  "seq": 12,
  "at": "2026-11-01T14:50:36.123+08:00",
  "mono": 84231.77,
  "kind": "action_unconfirmed",
  "code": "action_unconfirmed",
  "severity": "warn",
  "phase": "我方出牌",
  "turn": 6,
  "revision": 4213,
  "source": {
    "file": "FSM_action.py",
    "func": "play_recommendation",
    "line": 2711,
    "code_id": "FSM_action.py:2711:play_recommendation"
  },
  "context": {
    "instruction": "打出6号位法术",
    "target": {"kind": "minion", "slot": 6, "rect": [1200, 540, 1340, 680]},
    "click": {"x": 1268, "y": 610, "button": "left"},
    "plan": "spell_face",
    "ocr": [{"tag": "hand_6", "text": "火球术", "confidence": 0.93}]
  },
  "observe": {
    "power_events": [],
    "frame_diff_ratio": 0.0004,
    "ocr_changed": false
  },
  "evidence": {"frames": ["frames/t+0s.jpg", "frames/t+10s.jpg"], "crops": ["frames/crop_hand_6.jpg"]},
  "traceback": null
}
```

必填：`seq/at/mono/kind/code/source`。其余按事件类型可选。
`source` 由 `emit()` 用 `sys._getframe(1)` 自动填，调用方**不手写**。

### 4.2 事件清单与阈值

| kind | 触发条件 | 默认阈值 | 封包 | emit 位置（现状代码） |
|---|---|---|---|---|
| `action_sent` | 每次发出点击/出牌前登记意图（指令、目标、坐标、依据） | 每次 | 否（只进 timeline） | `recommendation_flow.py` 的 `PendingRecommendationAction` 生成处；`click.py` 点击执行处 |
| `action_unconfirmed` | 动作后 10s 内 A1 三条都不成立 | 10s | 是 | 现有 `recommendation_flow.py:412` 附近「动作 3s 未确认，重新执行」；`FSM_action.py:2162` 「尚未检测到「结束回合」」 |
| `retry_exhausted` | 同 `code` 连续达到阈值 | 3 次 | 是 | `FSM_action.py:1203-1212`（现在按 code 去重只发一行，需改成"发文本 + 计数事件"两件事） |
| `stage_stuck` | 同 phase + 同 turn，且 revision 不变，持续超时 | 60s | 是 | `FSM_action.py` 主循环（有 `last_automation_diagnostic` 与 revision 的现成取值点） |
| `ocr_empty_or_low_conf` | 同 tag 连续读空或置信 < 阈值 | 5 次 / 0.55 | 是 | `src/ocr/status.py:43` `record()`（已有每区域原文+置信累积） |
| `env_failure` | 客户区不可见 / 被最小化到屏幕外 / DPI 变化 / 掉线界面 | 单次 | 是 | `web_ui.py:_hs_client_capture_ready`（已有判定与原因文案） |
| `run_stopped` | 自动化停止（正常/异常退出） | 每次运行 1 条 | 是（汇总） | `web_ui.py` 自动化收尾（现 `auto_export_on_stop` 分支附近） |

**回合外规则**（Q18 定稿）：`env_failure` 与 `run_stopped` 在回合外也记，但只写**一条汇总**，不逐条建小节；其余事件仅在回合内（`我方出牌 / 对手回合 / 对局结束`）。

### 4.3 计数、去重、节流

- 计数按 `(kind, code)` 分别累计；`mark_success` 等价语义：动作确认成功 / 画面推进 → 清零对应计数。
- 去重只作用于**写入 timeline 的重复行**（同类同码 1s 内合并），**不作用于计数与触发**。这一条修掉 R2。
- 节流：`min_interval_seconds` 默认 2s，只限制"封包"，不限制 timeline 记录。
- 单包最多 `max_events_per_package`（默认 20）个封包事件，超出按 severity 与时间取样，并在 manifest 记 `events_dropped`。

### 4.4 emit 点登记规范

```python
from diag import events

# 推荐写法：不手写位置，emit 自己取调用栈
events.emit("action_unconfirmed",
            phase=phase, turn=turn, revision=rev,
            context={"instruction": ins, "target": tgt, "click": pt})
```

硬性约束：

1. **禁止**在 emit 里做截图/IO（必须只入内存，采集由 `diag/ring.py` 线程负责）。
2. **禁止**让 emit 抛异常影响主流程：内部全 `try/except`，失败打 `sys_print` 一行并继续。
3. `code` 必须是**稳定字符串**（英文+下划线），禁止把诊断文本当 code（现状 `detail = code` 就是 R1 的坑）。
4. 同一次失败只 emit 一次；需要"每步都知道"的用 `action_sent`（只进 timeline）。

---

## 5. 证据采集（环形缓冲）

### 5.1 数据结构与预算

```python
# diag/ring.py
@dataclass
class Frame:
    mono: float          # 单调时钟
    at: str              # ISO 本地时间
    jpeg: bytes          # 已编码，1280 宽，q=80
    kind: str            # "tick" | "action" | "trigger"
    meta: dict           # mapping 快照、是否退化成整屏、帧差相对上一帧

_RING: deque[Frame]      # maxlen = buffer_seconds * fps
```

预算（默认参数）：60 s × 1 fps = 60 帧 × ≈150 KB ≈ **9 MB**；加动作帧与触发帧，硬上限 `15 MB`，超限丢最旧（丢弃优先于阻塞）。

### 5.2 采集线程

- 独立 daemon 线程 `diag-capture`，周期 `1/fps`；**绝不在自动化线程里抓图/编码**（现状是 `feedback` 的 worker 线程，可复用其模式，但队列策略改为"最新帧覆盖"而非 FIFO 累积）。
- 抓图只调 `web_ui.capture_frame("client")`（客户区，含 `info.mapping`/`fallback`）；`boxes`（整屏 + 区域框，`screen_regions.build_region_preview`）**只在触发时抓一张**，避免整屏+base64 的每秒开销。
- 编码：缩放到 `frame_max_width`（1280）→ `JPEG quality 80`；实测预算 ≤ 20 ms/帧。
- 帧差：降到 320 宽灰度后与上一帧比对，`changed_pixels / total`；写入 `Frame.meta["diff"]`，供 A1 判据②使用。

### 5.3 触发时取帧（封包）

设触发事件时间 `t0`（用 `mono`）：

1. 取 `[t0-pre_seconds, t0]` 内 tick 帧，等距抽 3 张；
2. 抓 1 张 `boxes`（整屏 + 区域框），命名 `frames/boxes_t0.jpg`；
3. 从 `t0` 起等 `post_seconds`（默认 10s）再取 1 张确认帧 `frames/t+10s.jpg`（这正是 `action_unconfirmed` 的判据时刻）；
4. 按事件 `context.target.rect` 从原始（未缩放）帧裁剪关键区域，命名 `frames/crop_<tag>.jpg`；
5. 全部降采样为 1280 宽 JPG 后入包；`max_frames_per_package=24` 兜底。

> 待实施时验证：`screen_regions.draw_region_boxes()` 能否直接叠加在**客户区帧**上（需要 client mapping）。若不能，则保持"boxes 只出整屏一张"的降级方案，并在此文档追加说明。

### 5.4 隐私与脱敏

- 默认只存**客户区**帧；整屏帧（`boxes`）**仅在 `env_failure` 或触发点**出现，且 `summary.txt` 用一行说明"含整屏截图，可能包含桌面内容"。
- 不采集用户名/路径以外的环境信息（沿用现有 `环境.txt` 字段）。

---

## 6. 包格式

### 6.1 目录树（一次运行一个 zip，运行结束时生成）

```text
logs/20261101-145036-打出6号位法术-运行包.zip
├── manifest.json          # 机读总索引（含 code_index）
├── summary.txt            # 人读一页纸（≤ 60 行）
├── timeline.jsonl         # 单一时间排序事件流（取代旧 对战日志.txt+jsonl 两份）
├── env.txt                # 环境（沿用现有 _build_env_text + 新增采集参数）
├── events/
│   └── 0001/
│       ├── event.json     # 单个封包事件的完整 EventEnvelope
│       └── context.txt    # 该事件前后 5 条 timeline 的可读摘录
└── frames/
    ├── t-10s_1.jpg  t-10s_2.jpg  t-10s_3.jpg
    ├── t0.jpg  t+10s.jpg
    ├── boxes_t0.jpg
    └── crop_hand_6.jpg
```

命名：`<紧凑日期>-<首次异常推荐前 24 字|未知推荐>-运行包.zip`；重名追加 `-2`。沿用 `feedback.MAX_RECO_NAME_LEN=24` 与 `UNKNOWN_RECOMMENDATION` 常量（`feedback.py:56-60`）。

### 6.2 `manifest.json`

```json
{
  "schema": "hslegend.feedback/2",
  "created_at": "2026-11-01T15:12:03+08:00",
  "session": "20261101_145036",
  "app": {"name": "HSLegendArriver", "version": "2026.10.27"},
  "env": {"os": "Windows-11-10.0.26200", "python": "3.12.13", "dpi": 144,
          "client": "1920x1080", "deck": "拼装", "admin": true},
  "config": {"frame_fps": 1.0, "frame_max_width": 1280, "jpeg_quality": 80,
             "buffer_seconds": 60, "pre_seconds": 10, "post_seconds": 10,
             "action_unconfirmed_seconds": 10, "stage_stuck_seconds": 60},
  "run": {"started_at": "...", "stopped_at": "...", "games": 1, "turns": 9,
          "stop_reason": "user_stop", "auto_export": true},
  "stats": {"timeline_lines": 812, "frames": 18, "bytes": 4718592,
            "events_dropped": 0},
  "events": [
    {"seq": 12, "kind": "action_unconfirmed", "code": "action_unconfirmed",
     "at": "...", "phase": "我方出牌", "turn": 6,
     "source": {"file": "FSM_action.py", "func": "play_recommendation", "line": 2711},
     "dir": "events/0001", "frames": ["frames/t0.jpg", "frames/t+10s.jpg"]}
  ],
  "code_index": {
    "action_unconfirmed": [{"file": "FSM_action.py", "func": "play_recommendation", "line": 2711}],
    "retry:recommendation_not_stable": [{"file": "FSM_action.py", "func": "run_player_turn_step", "line": 2711}],
    "ocr_empty_or_low_conf": [{"file": "src/ocr/status.py", "func": "record", "line": 43}]
  },
  "missing": ["无 traceback（非异常路径）"]
}
```

**`code_index` 是"1 分钟定位"的核心**：AI 读到 `source.file:line` 即可直接跳文件；即使事件在运行后代码已漂移，也能用 `func` 名再定位。

### 6.3 `timeline.jsonl` 行格式

```json
{"seq": 1, "t": "2026-11-01T14:50:36.100+08:00", "type": "phase", "phase": "我方出牌", "turn": 6}
{"seq": 2, "t": "...", "type": "power", "event": "PLAY", "entity": 123, "card": "火球术", "target": 456}
{"seq": 3, "t": "...", "type": "ocr", "tag": "hand_6", "text": "火球术", "confidence": 0.93}
{"seq": 4, "t": "...", "type": "action_sent", "instruction": "打出6号位法术", "target": {...}, "click": {"x":1268,"y":610}}
{"seq": 5, "t": "...", "type": "diag", "kind": "action_unconfirmed", "code": "action_unconfirmed", "seq_event": 12}
```

与现有 `battle_record` 的关系：`timeline.jsonl` **取代** zip 内的 `对战日志_*.txt` 与 `对战日志_*.jsonl`（现在两份内容高度重复，见 `feedback.py:711-735`）；
旧格式文件仍可独立生成（`battle_record.write_jsonl` 不动），只是不再塞进 v2 包。

### 6.4 生命周期与原子写

1. 运行开始时 `diag.session.begin()`：清空事件/帧环，返回 `session stamp`。
2. 运行结束时（正常停止 / 异常退出兜底）**自动**生成包：先写临时文件 `.tmp-<name>`，成功后 `os.replace` 重命名 → 避免半截 zip。
3. 导出失败**绝不**影响主流程：`except` 兜底，返回 `{"ok": false, "error": ...}`（沿用 `export_bundle` 的约定，`feedback.py:689-701`）。
4. 自动导出默认开启（`auto_export_on_stop: true`），且**不弹窗、不打开资源管理器**（只有手动导出才 `open_folder=True`）。

### 6.5 与旧格式兼容

- 旧 `*错误反馈.zip`、`出错记录_<日期>/` 目录：**只读兼容**，不删除，清理规则同时认两种。
- `feedback.py` 保留 `note_event()` 作为兼容入口：若 `feedback_v2.enabled=false`，行为完全回到现状（便于 A/B 对比与回退）。

---

## 7. 诊断码 → 代码位置索引

### 7.1 机制：emit 自动注册

```python
# diag/codes.py
_REGISTRY: dict[str, dict] = {}

def record(code: str, frame) -> dict:
    entry = _REGISTRY.setdefault(code, {"code": code, "sites": []})
    site = {
        "file": os.path.basename(frame.f_code.co_filename),
        "func": frame.f_code.co_name,
        "line": frame.f_lineno,
    }
    if site not in entry["sites"]:
        entry["sites"].append(site)          # 同码多发射点全部保留
    entry["last_at"] = time.time()
    return entry
```

- `emit()` 内部第一件事就是 `codes.record(code, sys._getframe(1))`，**零维护、不漂移**。
- 包导出时 `codes.dump()` → `manifest.code_index`。
- 额外校验（P2 可选）：`tools/check_diag_codes.py` 扫源码里的 `emit("...")` 字面量，要求每个 code 在单测中至少出现一次（防"定义了从不触发"）。

### 7.2 为什么不用静态扫描/手写映射

- 手写映射会过时（现状 `_DIAGNOSTIC_LABELS` 手工表已有 11 项，与实际发射点无强关联，`FSM_action.py:1550`）。
- 静态扫描能离线生成，但遇到 `code=f"retry:{x}"` 这类拼接码会漏；运行时注册天然覆盖拼接值。

---

## 8. 日志改造

### 8.1 目标格式

```text
[2026-11-01 14:50:36.123 WARN] feedback.py:311:note_event 结算界面超过 40 轮仍未出现「开始」按钮
```

规则：

- 前缀保留方括号结构，**在级别前加日期、级别后加 `文件:行号:函数`**；旧解析器只要按 `[...]` 取整段就不受影响。
- 文件名不带目录（便于包内引用与跨机对齐），行号用调用处 `frame.f_lineno`。
- 级别不再靠"文本里有没有 ERROR]"猜（现状 `web_ui.py:1008-1011`），改为显式传参。

### 8.2 兼容性要求（实施前必须 grep 确认）

| 位置 | 影响 | 处理 |
|---|---|---|
| `action_feed.py:214` `_strip_time_prefix` | 去掉 `[HH:MM:SS 标签]` 前缀 | 扩展为同时吃新格式；加回归测试 |
| `web_ui.py:356-388` `_log` 自带落盘格式 | 两套格式并存 | 统一走 `print_info`；`_log` 只保留内存环并调 `print_info` |
| `web_ui.py:1008-1011` 级别嗅探 | 依赖文本 | 改为显式 `level` 参数 |
| `tests/test_web_logs_wiring.py`、`test_script_logs.py`、`test_feedback.py` | 断言格式/行为 | 同步更新；旧断言保留为兼容用例 |

### 8.3 traceback 捕获

- `emit(..., exc=sys.exc_info())` 或 `emit_exc()`：`traceback.format_exc()` 全文入 `EventEnvelope.traceback`，并在 `summary.txt` 打前 5 行。
- 非异常路径（未确认/卡阶段）没有栈——由 `source` 位置承担定位职责，这是"没有期望也要能定位"的替代方案。

### 8.4 性能与线程

- `sys._getframe(1)` 约几百纳秒，只在 `emit`/日志封装处调用一次，可忽略。
- 文件写入继续由 `print_info` 直接 `write`（现状就是同步写），不引入 logging 框架，避免大改；若后续出现写盘抖动再议。

---

## 9. 配置：`feedback_v2` 段

写在 `ui_config.json`，由 `config.py` 新增 `feedback_v2_settings()` / `save_feedback_v2_setting()`（复用 `feedback_settings` 的校验模式，`config.py:730-794`）。

| 字段 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `enabled` | bool | `true` | — | v2 总开关；关闭时回退旧机制 |
| `frame_fps` | float | `1.0` | 0.2–5.0 | 基础帧率 |
| `buffer_seconds` | int | `60` | 10–300 | 环形缓冲时长 |
| `frame_max_width` | int | `1280` | 640–1920 | 降采样宽度 |
| `jpeg_quality` | int | `80` | 50–95 | JPG 质量 |
| `pre_seconds` / `post_seconds` | int | `10` / `10` | 0–60 | 触发取帧窗口 |
| `action_unconfirmed_seconds` | int | `10` | 3–60 | 动作未确认判定窗 |
| `frame_diff_ratio` | float | `0.005` | 0–0.2 | A1 判据②阈值 |
| `stage_stuck_seconds` | int | `60` | 20–300 | 阶段卡住阈值 |
| `ocr_low_conf` | float | `0.55` | 0–1 | OCR 低置信阈值 |
| `ocr_empty_streak` | int | `5` | 2–20 | 连续读空/低置信次数 |
| `retry_exhausted_streak` | int | `3` | 2–10 | 同码连续重试阈值 |
| `min_interval_seconds` | float | `2.0` | 0–60 | 封包最小间隔 |
| `max_events_per_package` | int | `20` | 1–50 | 单包封包事件上限 |
| `max_frames_per_package` | int | `24` | 4–60 | 单包帧数上限 |
| `auto_export_on_stop` | bool | `true` | — | 停止时自动生成运行包（不弹窗） |
| `keep_days` | int | `7` | 1–90 | 保留天数 |
| `keep_packages` | int | `30` | 1–200 | 保留包数 |

注意：`auto_export_on_stop` 在旧 `feedback` 段默认 `false`（`config.py:723`），v2 段默认 `true`——这是**行为变更**，需在 CHANGELOG 与 `docs/FEATURES.md` 明写。

---

## 10. 保留与清理

- 保留最近 **7 天** 且最多 **30 个** 运行包；超出删除最旧的，**永远保留最新一个包**。
- 清理只认命名模式：`*运行包.zip`、`*错误反馈.zip`、`出错记录_<YYYYMMDD>/`（与 `script_logs` 现有清理口径合并，避免误删用户文件）。
- 清理时机：每次运行结束生成新包之后（幂等），不在自动化进行中删。
- 记录被删时写一行 `sys_print`，并在下一次 `manifest.missing` 里体现"历史包已清理"。

---

## 11. 三阶段实施计划

> 每阶段独立可验、可回退。P1 不改变判定逻辑，只改"记录的可读/可定位"与"包的结构"。

### P1 — 取证与包（不改判定）

**范围**

1. `print_info.py`：新日志格式（日期 + `文件:行号:函数`），级别显式传参；同步更新 `action_feed._strip_time_prefix`、`web_ui._log` 落盘路径与级别嗅探。
2. `diag/log_ctx.py`：`caller_site()` 工具（供打印与 emit 复用）。
3. `web_ui._feedback_capture` 升级：`capture_frame("boxes")` 触发帧 + 1280 宽 JPG + 裁剪 + 保留 `info`（映射/退化原因）写入记录。
4. `diag/package.py` + `diag/retention.py`：v2 包结构（manifest/summary/timeline/env/frames）；运行结束自动生成；原子写；保留清理。
5. `diag/codes.py` 骨架 + `emit()` 占位（先只记录、不判定）。
6. `config.py`：`feedback_v2` 段与读写。
7. 浮窗按钮文案与 `hsctl feedback export` 文案改为「本次运行包」（`log_overlay.py:2664-2678`；`hsctl.py` 的「导出/查看本次运行的错误反馈包」命令，约 1894–1930 行）。

**不包含**：环形缓冲、任何事件判定（仍走旧 `note_event` 触发点）。

**改动文件**：`print_info.py`、`action_feed.py`、`web_ui.py`、`feedback.py`、`config.py`、`log_overlay.py`、`hsctl.py`、新增 `diag/*`、`tests/test_log_format_compat.py`、`tests/test_package_v2.py`、`tests/test_retention.py`，并适配 `tests/test_feedback.py`、`tests/test_web_logs_wiring.py`。

**验收**

```powershell
uv sync
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python -m unittest tests.test_log_format_compat tests.test_package_v2 tests.test_retention -v
uv run python -m unittest discover -s tests -t .
```

人工验收：跑一次（不必对局，`hsctl` 起停即可）→ 确认 `logs/` 下有 v2 运行包 → 解压检查 `manifest.json`/`timeline.jsonl`/`frames/` 齐全、`code_index` 非空、日志行含日期与 `文件:行号`。

### P2 — 事件直投

**范围**

1. `diag/events.py`：`EventEnvelope`、`emit()`、按 `(kind, code)` 计数、阈值、去重/节流、`mark_success`。
2. 接入 7 类事件（第 4.2 节）：`action_sent` / `action_unconfirmed` / `retry_exhausted` / `stage_stuck` / `ocr_empty_or_low_conf` / `env_failure` / `run_stopped`。
3. 消费端切换：`web_ui._record_battle` 不再"正则分类决定记不记"，改为"日志照写 + emit 决定记录"；`battle_record.classify` 降级为兼容兜底（`feedback_v2.enabled=false` 时仍用）。
4. `code_index` 全量落地（emit 自动注册）+ `tools/check_diag_codes.py`。
5. 回合内/回合外规则（`env_failure`/`run_stopped` 汇总一条）。

**验收**：故障注入单测（模拟点击无效、阶段不推进、OCR 连续读空、窗口消失），断言"恰好产生预期事件、含正确 source 位置、不产生额外事件"；`uv run python -m unittest discover -s tests -t .`。

### P3 — 环形缓冲与未确认判定

**范围**

1. `diag/ring.py`：1fps 采集线程、1280 JPG、60s 环、15 MB 上限、帧差计算、`boxes` 触发帧。
2. `action_unconfirmed` 判定（10s + A1 三判据）；`stage_stuck` 判定（60s + revision 不变）。
3. 封包取帧（前 10s 抽 3 张 + t0 + t+10s）与 `max_frames_per_package` 兜底。
4. 影子期开关：只记录不告警（v2 无告警通道，天然满足）。

**验收**：故障注入（点击无效 10s）→ 包内出现 `action_unconfirmed` 且帧数、时间戳、`observe` 字段符合预期；性能检查：抓图线程 CPU < 3%、内存增量 < 20 MB；`uv run python -m unittest discover -s tests -t .`。

---

## 12. 影子期验收流程（人不在场）

1. 实施完成后，正常跑 **2–3 局**（可刻意制造一次卡顿：例如出牌前手动把炉石窗口最小化 5 秒）。
2. 停止自动化 → 包自动落在 `logs/`。
3. 回来把包路径发我（AI）。
4. 我输出一份复盘报告，固定结构：
   - `manifest` 摘要（环境、参数、事件数、体积）
   - **可疑决策清单**：逐条给 `时间 → 指令 → Power.log 实际 → 我的判断（是否异常）→ 依据帧`
   - **证据缺口清单**：哪些情况我想看但包里没有（阈值太松 / 帧没抓到 / 字段缺失）
5. 按"缺口清单"调 `feedback_v2` 阈值，再跑一轮；两轮后判定是否收口。

**通过标准**：任取包内一个事件，AI 能在 1 分钟内说出 `文件:函数:行号` + 当时画面内容 + 当时指令。

---

## 13. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| 1fps 抓图拖慢自动化 | 掉帧/卡顿 | 独立线程 + 最新帧覆盖 + 编码上限；P3 提供 `frame_fps=0.2` 降级 |
| 包体积失控 | 磁盘 | 7 天/30 包 + 单包帧数上限 + 1280 JPG |
| 旧测试大面积红 | 回归 | P1 分批改，`tests/test_feedback.py`（19 KB）保留旧用例并新增 v2 用例 |
| 日志格式变更打断解析 | 浮窗/命令行显示异常 | 第 8.2 节兼容清单 + 兼容回归测试 |
| 整屏截图含隐私 | 误分享 | 默认客户区；`env_failure` 才整屏，`summary.txt` 明示 |
| 事件误报刷屏 | 包噪声大 | `min_interval_seconds` + 单包事件上限 + 影子期调阈值 |
| 诊断码漂移 | 索引失效 | 自动注册 + `check_diag_codes.py` 静态校验 |

**回退**：`feedback_v2.enabled=false` → 完全回到现有 `feedback.note_event` 行为；新增模块不被调用即无影响。P1/P2/P3 各自独立提交，可单独 revert。

---

## 14. 遗留缺口（诚实清单）

1. **"选错目标但程序自认成功"不会有帧证据**（A3）——只有 timeline。要补需要"每次动作都留帧"，与"只有多次重试才留"冲突，本次不做。
2. **策略对错无内部真值**——`expected` 字段不存在，AI 只能依据「指令 + Power.log + 带框帧」推断，可能判错；报告里必须标注置信度。
3. **`raw` OCR 目前 57/57 为空**——P2 需要确认 `instruction/raw` 的实际取值链路（`web_ui._record_battle` 的 ctx 维护点），否则新包也会继承空字段。
4. **回合外失败只留汇总**——换牌阶段的偶发卡顿可能被"只记回合内"漏掉。
5. `log_op`/`log_state` 的 revision 语义需在 P3 前复核（`stage_stuck` 判据依赖它）。

---

## 15. 附录

### A. 代码锚点表（行号以 2026-11-01 工作区为准，实施前 grep 复核）

| 锚点 | 位置 | 与本文关系 |
|---|---|---|
| 旧记录入口 | `feedback.py:236` `note_event` | P2 降级为兼容层 |
| 阶段白名单 | `feedback.py:63-64` | R3，P2 按 4.2 修订 |
| 阈值截图 | `feedback.py:278-284` | R2，P2 由 `diag/events` 取代 |
| 存档写入 | `feedback.py:315-337` `_write_archive` | P1 改为 v2 包 |
| 截图调度 | `feedback.py:340-365` `_maybe_capture` | P3 由 ring 取代 |
| 导出 | `feedback.py:678-787` `export_bundle` | P1 重写为 `diag/package.py` |
| 配置段 | `config.py:709-794` | P1 新增 `feedback_v2` |
| 抓图 | `web_ui.py:615-718` `capture_frame`（boxes 在 637-667） | P1 复用 |
| 出错截图 | `web_ui.py:722-741` `_feedback_capture` | P1 升级（降采样/框/裁剪/保留 info） |
| 日志转发 | `web_ui.py:134` `_record_battle`、`1000-1014` `_TeeStream` | P2 切换消费端 |
| 日志写盘 | `print_info.py:22-26`（`"w"` 覆盖）、`48`（`%H:%M:%S`）、`50-92` | P1 改造 |
| 重试去重 | `FSM_action.py:1203-1212` | R2，P2 改为"文本 + 计数事件" |
| 诊断标签表 | `FSM_action.py:1550` `_DIAGNOSTIC_LABELS` | P2 与 code_index 对齐 |
| 存活旁证 | `FSM_action.py:1263-1270` `_liveness_detail` | P2 拆出结构化字段 |
| 正则分类 | `battle_record.py:57,144` | P2 降级兜底 |
| 动作流水 | `action_feed.py:35,127` | P1/P2 供 timeline |
| 区域框 | `screen_regions.py:553,695` | P1/P3 证据帧 |
| OCR 状态 | `src/ocr/status.py:43` | P2 低置信事件源 |
| 清理 | `script_logs.clear()`（`script_logs.py` 约 82-93 起） | P1 合并口径 |

### B. 术语表

| 术语 | 含义 |
|---|---|
| 运行包 | 一次自动化运行对应的一份 zip（v2 唯一产物） |
| 封包事件 | 触发证据落盘的事件（`action_sent` 除外） |
| A1 | 「有反应」的三判据（Power.log / 帧差 / OCR 文本） |
| code_index | 诊断码 → 代码位置（文件:函数:行号），emit 时自动注册 |
| 影子期 | 只落盘不告警、按缺口迭代阈值的验证阶段 |
