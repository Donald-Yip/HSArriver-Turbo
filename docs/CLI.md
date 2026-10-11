# hsctl —— 命令行控制台

`hsctl.py` 是 `web_ui.py` 本地 HTTP 控制面的**薄客户端**：只做「命令行参数 ↔ HTTP
JSON」的翻译与结果渲染，不含任何自动化逻辑。所以它既能给人用，也能给 AI agent
（WorkBuddy、DSH、Claude Code…）用，作为实时控制脚本的稳定入口。

```
手机 IM ─→ 本机 agent ─┐
你本人 ────────────────┼─→ hsctl ─→ HTTP 127.0.0.1:8765-8785 ─→ web_ui 控制面 ─→ FSM
其它 agent ────────────┘      （薄客户端，零业务逻辑）
```

网页控制台仍是唯一状态源：`hsctl` 改的任何东西，网页上刷新就能看到；反之亦然。

---

## 0. agent 速查（一屏内看完）

```bash
python hsctl.py --json doctor     # ① 每次会话第一条：环境 + 控制台 + 下一步该执行什么
# ② 照抄 next_steps 里的命令（通常 1-2 条）
python hsctl.py --json doctor     # ③ 复验到 verdict=env_ok
python hsctl.py --json status     # ④ 之后按需要探活/干活
```

| `doctor.verdict` | 意思 | 典型的 `next_steps` |
| --- | --- | --- |
| `env_ok` | 环境已保存且合格 | （空；看 `console`） |
| `env_ok_unsaved` | 能用，但**还没保存**到本机 `log/env.json` | `hsctl env --pin "<路径>"` |
| `env_deps_missing` | 找到 3.12 但缺包 | 已定位到该解释器的 pip 命令 |
| `env_no_valid_interpreter` | 本机没有合格解释器 | conda/venv 建环境 → pip → `env --pin` |
| `console_not_running` | 环境 OK，控制台没跑 | `hsctl serve start --via-task` |
| `console_not_admin` | 在跑但没提权（点击不生效） | `serve stop` → `serve start --via-task` |
| `env_wrong_interpreter` | 控制台跑在错的解释器上 | `env --pin` → `serve stop` → `serve start --via-task` |

退出码：`0` 成功 ／ `1` 业务失败（读 `error`）／ `2` 参数错 ／ `3` 连不上控制台 ／
`4` 环境不可用（先跑 `doctor`）。`--json` 下错误也走 **stdout**，便于只读 stdout 的 agent。

跨项目的 agent 行为约定（一条命令一条决策、少废话、Git 纪律）见
[`../AGENTS.md`](../AGENTS.md)。

---

## 1. 快速开始

```powershell
# 前提：脚本已经在跑（网页控制台在 8765-8785 之一）
python hsctl.py status              # 看状态
python hsctl.py log -f              # 实时跟随日志
python hsctl.py start               # 开始对战
python hsctl.py stop --after-game   # 打完本局再停（默认是立即停）
python hsctl.py --json status       # 机器可读
```

控制台没在跑时，可以让 CLI 自己拉起来：

```powershell
python hsctl.py serve start         # 后台拉起，等端口就绪后返回
python hsctl.py serve stop          # 先停自动化，再结束控制台进程
```

> **建议用管理员 PowerShell。** 非管理员时点击会被系统拦掉，现象是「脚本在跑但点了
> 没反应」；`hsctl status` 与 `serve start` 都会检查并告警。

---

## 2. 定位控制台（端口不是固定的）

控制台端口从 `8765` 起，被占用就顺延（到 `8785`）。`hsctl` 按下面的顺序找：

1. `--port` 显式指定
2. 环境变量 `HS_PORT`（同时可用 `HS_HOST` 改地址）
3. **`log\ui_port.txt`** —— `web_ui.py` 绑定成功后写的 `{port, host, url, pid, started_at}`，原子写入
4. 扫 `HS_PORT`(默认 8765) 起的 20 个端口，探测 `/api/status`

端口文件可能过期（进程被硬杀时不会清理），所以用它之前**一定会先探活**：探不到就
退回扫端口，不会打到一个死掉的端口上。

---

## 3. 退出码与 `--json`

| 退出码 | 含义 | agent 该怎么处理 |
| --- | --- | --- |
| `0` | 成功 | 继续 |
| `1` | **业务失败**：控制台返回 `{"ok": false, "error": "..."}` | 读 error 决定重试还是改策略（例如"运行中，请先停止"） |
| `2` | 参数用法错误 | 修正命令 |
| `3` | **连不上控制台** | 先 `hsctl serve start` 或提示用户开脚本 |
| `4` | **环境不可用**（没有合格的 Python 3.12 解释器 / 缺依赖） | 跑 `hsctl --json doctor`，照 `next_steps` 修（见 §5.6） |

`--json` 放在命令前或后都有效（`hsctl --json status` ≡ `hsctl status --json`）。
JSON 模式下：

- 成功：直接输出接口原始 JSON
- 失败：输出 `{"ok": false, "kind": "api"|"transport", "error": "..."}`
- `log -f`：一行一个 JSON 对象（JSONL），便于流式解析

`--json` 下错误也走 **stdout**（不是 stderr），这样只读 stdout 的 agent 也能拿到结构化失败。

全局参数：`--json`、`--host`、`--port`、`--timeout`、`--version`。

---

## 4. 命令参考

### 4.1 观测

| 命令 | 说明 |
| --- | --- |
| `status` | 状态总览：阶段、账号是否匹配、战绩、存活检测、自动投降、上分停止、活人感、延时、自检、最后错误 |
| `status --watch 2` | 每 2 秒刷一行（Ctrl+C 退出） |
| `log [-n 50] [-f] [--interval 1]` | `-n` 先打最近 N 行；`-f` 持续跟随（走 `/api/logs?seq=` 增量接口，不重复） |
| `selfcheck [--refresh]` | 环境自检（Python/依赖/分辨率/管理员） |
| `regions` | 截一张屏并画出所有截图区域框（**只截图，不点击、不移动鼠标**） |
| `screenshot [--kind …]` | **按需抓一帧存成 PNG 并返回路径**（agent 用它"看画面"，见下） |

### 4.1.1 抓图 `screenshot`（给 agent 看画面）

```powershell
python hsctl.py screenshot                       # 默认：炉石客户区
python hsctl.py screenshot --kind full           # 整屏（看盒子/浮窗摆位）
python hsctl.py screenshot --kind roi            # 盒子推荐面板 = OCR 真正看到的那块
python hsctl.py screenshot --kind boxes          # 整屏 + 所有校准区域框
python hsctl.py screenshot --points mode         # 客户区 + 「模式切换」四个点击点（红十字）
python hsctl.py screenshot --max-width 1280      # 4K 屏建议缩一下再喂给模型
python hsctl.py screenshot --open                # 抓完用系统看图打开
python hsctl.py screenshot --watch --interval 2  # 持续抓，每次覆盖同一张（实时看）
python hsctl.py --json screenshot                # 机器可读
```

`--points mode` 只**画图**（不点击、不移动鼠标）：把 `hsctl mode` 实际会点的四个点
（右上角模式按钮 + 狂野/标准/休闲三个盾牌）画成红十字并标上客户区坐标，
用来核对坐标对不对、或者给用户看"脚本会点哪里"。返回里带 `mode_points`
字段（每个点的 `client` 客户区坐标 / `image` 图内像素坐标）。

返回的是**文件路径**（不是 base64），拿到之后直接读那张 PNG 就行：

- `path` —— 本次的时间戳文件，例如 `tmp_screens\client_20261011_010243_517.png`
- `latest` —— **固定路径** `tmp_screens\agent_latest.png`，每次都覆盖，所以可以一直读同一个文件

四档的含义：

| `--kind` | 抓什么 | 用来看 |
| --- | --- | --- |
| `client`（默认） | 炉石**客户区** | 游戏界面 + 叠在上面的盒子面板 —— 排查"为什么没按推荐出牌"就看这个 |
| `full` | 整屏 | 盒子/浮窗摆到哪里去了、有没有别的窗口挡住 |
| `roi` | 盒子推荐面板 ROI | OCR 的**输入**长什么样，识别失败时对比原文 |
| `boxes` | 整屏 + 区域框 | 校准是否对齐（同网页「🎯 校准区域」那张图） |

**`client`/`roi` 在截不到客户区时会退化为整屏，并在 `note`/`reason` 里说明原因**，
不会给出一块没有意义的画面还谎称是游戏界面。常见退化原因：

- 炉石未运行 → `读不到炉石窗口（炉石未运行）`
- **炉石最小化** → `炉石窗口不可见或已最小化（0×0）`。注意这是必要的检查：炉石最小化时
  `get_HS_hwnd()` 仍会返回有效句柄，但客户区是 `0×0`、原点被 Windows 挪到 `(-32000,-32000)`。
- 窗口被挪到屏幕外 → `炉石窗口被最小化到屏幕外（(...)）`

全程只读：`ImageGrab` + 与 OCR 同一套客户区几何换算，不点击、不移动鼠标、不改游戏状态。
截图在**控制台进程**里做，所以 CLI 是否提权都不影响。


### 4.1.2 界面识别 `ui`（只认文字）

```powershell
python hsctl.py ui                    # 现在在哪个界面 + 两个匹配分
python hsctl.py ui --frame            # 顺带把这一帧存成 PNG（tmp_screens\ui_frame.png）
python hsctl.py ui --learn            # 按当前框重裁识别模板（皮肤/补丁改了字之后）
python hsctl.py ui --learn traditional  # 只重裁「传统对战」那一个
python hsctl.py --json ui             # 机器可读
```

它回答的是"**脚本现在认为自己在哪个界面**"，这是"先看清楚再动手"的前提：

| `state` | 判据（都在可校准框 ±40px 内找） | 含义 |
| --- | --- | --- |
| `main_menu` | 认出「**传统对战**」四个字 | 在主界面，可以点它进对战 |
| `deck_select` | 认出「**开始**」按钮的图案 | 在选套牌界面（`deck.slots_found` 顺便告诉你 9 格里几格有名牌文字） |
| `unknown` | 两个都没认出来 | 什么都不点；`ui.note` 会说清是模板缺失、框里没字，还是"有模板但没匹配上" |

判定细节（为什么这样不会认错东西）：

- **只认文字**：用灰度 NCC（`TM_CCOEFF_NORMED`，先减均值）—— 平坦的银色背景被减掉、不参与
  打分，打分的只有笔画。实测真值 `1.0000`，反例界面 `0.3105`（选套牌）/`0.1625`（主界面），
  同屏最强的反例是隔壁「酒馆战棋」`0.461`，阈值取 `0.80`。
- **全平/全黑画面给 `0.0000`**（OpenCV 对零方差窗口返回 0）→ 游戏还没画出来时不会误判成主界面。
- **模板是"身份"，框是"到哪儿找"**：所以把框拖到别的按钮上只会**找不到**（不点），
  不会"把酒馆战棋认成传统对战然后点进去"。
- `ui --frame` 存的那张图可以直接读，用来核对"它到底看到了什么"。


### 4.1.3 进入对战 `enter-battle`

```powershell
python hsctl.py enter-battle                    # 拉起炉石 → 点「传统对战」→ 停在选套牌界面
python hsctl.py enter-battle --deck 8           # 顺带自动选第 8 个卡组
python hsctl.py enter-battle --deck 8 --start   # 再点「开始」（等于开始排队）
python hsctl.py enter-battle --no-wait          # 只下发，不等它跑完
python hsctl.py enter-battle --interval 3 --wait-timeout 180   # 等界面时每 3s 看一眼
```

流程（每一步都会写进日志和返回的 `steps`，含坐标，事后能逐条核对有没有乱点）：

```
拉起炉石（没开才拉；只启动，不杀进程、不清日志）
  → 每 3s 认一次主界面；没认出就点一次画面中心 (960,650)（关掉挡住的弹窗/公告）
  → 认出「传统对战」→ 点它
  → 每 3s 等「开始」按钮出现 = 已进选择套牌界面
  → 选卡组（可选，带单格保护）
  → 点「开始」（可选，默认不做）
```

**默认不点「开始」**：那等于开始排队，交给 `hsctl start`（它会先做「上分停止」判定）或者
`--start`。安全性质：**认不出界面就只点画面中心，到超时就退出并说明原因 —— 绝不盲点按钮**。

自动化正在跑时这个命令会**被拒绝**（鼠标归 FSM 管，两边同时点会互相打断）。

### 4.1.4 自动选卡组 `deck`

```powershell
python hsctl.py deck           # 看当前设置
python hsctl.py deck 8         # 每局自动选第 8 个卡组（按当前页 3×3 的第 8 格）
python hsctl.py deck --clear   # 关掉（保持你手选的卡组）
```

写入 `ui_config.json` 的 `battle_entry.deck_index`，**每局开始前重新读盘**，所以改完下一局就生效。

- 默认是**关**的：脚本只按名牌文字判断，认不出「卡牌不足」的卡组，所以不替你选；
  要用时自己指定一个可用格子。
- 只寻址**当前页可见的 9 格**；卡组多到分页时第 10 副起不可寻址。
- 点之前会验"这一格确实像卡组"（名牌上有白字），不像就一格都不点。


### 4.2 对战控制

| 命令 | 说明 |
| --- | --- |
| `start [--keep-score]` | 开始对战。默认战绩清零；`--keep-score` 等价于 `resume` |
| `resume` | 续跑，战绩**继续累计**（浮窗「恢复」的语义） |
| `prepare` | 「开始运行（准备）」：开浮窗 + 把炉石切前台，**不开打** |
| `stop [--after-game]` | 默认立即停；`--after-game` 打完当前这局再停 |

### 4.3 参数（默认走热更新通道）

这一组默认带 `hot`（运行中也能改）。加 `--no-hot` 就退回网页那种「运行中拒绝修改」的保护。

| 命令 | 说明 |
| --- | --- |
| `delays` / `delays set 键=值 [键=值 …]` | 延时；`delays` 会逐条标出**生效时机** |
| `human-like [set …]` | 活人感：`--enabled/--no-enabled`、`--hand-hover/--no-hand-hover`、`--minion-hover/--no-minion-hover`、`--post-delay-min/-max`、`--hover-min/-max`、`--minion-hover-min/-max` |
| `liveness [set …]` | 存活检测：`--enabled/--no-enabled`、`--warn`（秒）、`--stop`（秒） |
| `concede [set …]` | 自动投降：`--enabled/--no-enabled`、`--threshold`（%）、`--rounds` |
| `rank-stop [set …]` | 上分停止：`--enabled/--no-enabled`、`--mode legend\|legend_number`、`--legend-number N` |
| `config [set …]` | 账号与日志目录：`--name`（含 `#编号`）、`--log-root`。带 `hot` 时会立刻注入运行中的日志读取器（`log_state.MY_NAME`），后续昵称判定即用新值 |

延时字段（`_USER_DELAY_KEYS`）与生效时机：

| 字段 | 生效时机 |
| --- | --- |
| `mulligan_ready_delay_seconds` | 立即生效 |
| `mulligan_retry_delay_seconds` | 立即生效 |
| `pre_action_delay_seconds` | 立即生效 |
| `first_turn_per_card_delay_seconds` | 立即生效 |
| `discover_target_delay_seconds` | 立即生效（调用方每次都新建配置、重新读盘） |
| `mulligan_post_ocr_delay_seconds` | 下次「开始对战」生效 |
| `post_action_delay_seconds` | 下次「开始对战」生效 |
| `draw_extra_delay_per_card_seconds` | 下次「开始对战」生效 |
| `ocr_preprocess_scale` | **需重启脚本进程** |

「立即生效」是**就地改写**正在运行的那个 `RecommendationConfig`（它是 frozen dataclass，
所以实现上刻意用 `object.__setattr__` 绕过冻结）—— 这样所有共享同一实例的流程对象
当场就能看到新值。`hsctl delays set` 的回话里会带 `effects` 字段说明每项的实际时机。

### 4.4 浮窗 / 校准 / 定时 / 高风险

| 命令 | 说明 |
| --- | --- |
| `overlay on\|off\|toggle` | 开/关右上角日志浮窗。`on`/`off` 已经是目标状态时**不会**多发请求 |
| `calibrate [--target 目标]` | 打开屏幕上的校准窗口（和网页按钮是同一个窗口）。`--target main_menu_battle` 可以一打开就停在「传统对战」那个框上；目标名：`recommendation` / `mulligan_confirm` / `win_rate` / `post_game_start` / `rank` / `main_menu_battle` / `deck_grid` |
| `schedule` | 看当前定时任务 |
| `schedule at 22:30 [--end 23:30]` | 开始时间。`HH:MM` 按「今天，已过则明天」；也接受 `2026-05-30T22:30` |
| `schedule at --in 30 [--duration 90]` | 30 分钟后开始，持续 90 分钟 |
| `schedule cancel` | 取消定时任务 |
| `restart-hearthstone [--yes]` | **高风险**：停自动化 → 结束 `Hearthstone.exe` → 清空日志目录 → 重新拉起。非交互环境必须显式 `--yes`；提示时按 EOF/Ctrl+C 一律按「不确认」处理 |

### 4.5 进程托管 `serve`

| 命令 | 说明 |
| --- | --- |
| `serve status` | 控制台是否在运行（含端口文件里的 pid / 启动时间 / 解释器） |
| `serve start [--browser] [--wait 60]` | 用 `--no-browser` 后台拉起 `web_ui.py`，等 `/api/status` 通了才返回；输出写 `log\cli_serve.log`；起不来会回显日志尾部 |
| `serve start --elevate` | **请求提权**启动：弹 UAC 同意框（需要有人在电脑前点「是」） |
| `serve start --via-task` | 通过已注册的「最高权限」计划任务启动：**不弹 UAC**，可无人值守 |
| `serve stop [--force] [--hard]` | **先**请求停止自动化，**再**让控制台**自己退出**（HTTP 自停通道，不需要提权）；自停不可用时才退回结束进程。`--hard` = 跳过自停直接 taskkill；pid 的映像名不是 Python 时拒绝（`--force` 覆盖） |
| `serve restart [--browser] [--elevate\|--via-task]` | stop → start |
| `serve install-task [--force]` | 注册「最高权限」计划任务（**需要管理员跑一次**） |
| `serve task-status` | 看任务是否存在、是否真的勾了最高权限 |
| `serve uninstall-task` | 删除该计划任务 |

`--elevate` 与 `--via-task` 互斥；不指定就是普通的非提权后台启动。

**触发计划任务**：优先 `schtasks /run`；**被本机策略拦掉时自动改用 PowerShell 的
`Start-ScheduledTask`**（有的机器把 `schtasks.exe` 列进了程序黑名单）。
`serve start --via-task` 的回话里会写明这次走的是哪条路。

安全性：**端口上没人监听时，`serve stop` 只清理残留的端口文件，绝不去 kill 它记下的
pid**（那个 pid 可能已被系统复用给别的进程）。

### 4.6 交互模式

```powershell
python hsctl.py            # 裸命令进 REPL（非交互环境会打印帮助）
python hsctl.py repl
```

REPL 里不带 `hsctl` 前缀，`help` 看帮助，`exit` / Ctrl+D 退出。

---

## 5. 提权：让自动化点击真的生效

这是**能不能无人值守**的关键，值得单独说清楚。

### 5.1 为什么必须提权

非管理员权限下，脚本能跑、能读日志，但**鼠标点击会被系统拦掉** —— 现象是「脚本在跑
但点了没反应」。`hsctl status` 会显示 `管理员权限：否` 并给出告警。

### 5.2 为什么不能"自动提权"

**非提权进程无法静默创建提权子进程**，这是 Windows 的安全边界，任何 agent 都越不过：

- 想提权只能通过 `ShellExecute("runas")` **请求** → 弹 UAC 同意框 → **必须有人在电脑前点「是」**。
- 你这台机器的策略是 `ConsentPromptBehaviorAdmin=5`（非 Windows 自带二进制一律弹框），
  而 Python 正是第三方二进制，所以**必然弹框**。
- 顺带纠正一个常见误解：WorkBuddy 等 agent 的「完全访问权限」是**它自己的确认层**，
  不是 Windows 管理员令牌 —— 它同样受这条边界约束。

所以"手机远程 → agent 启动提权脚本"这条路，**不能**靠 UAC 走通。

### 5.3 可行方案

| 方案 | 无人值守 | 说明 |
| --- | --- | --- |
| `serve start --elevate` | ❌ | 弹 UAC，需要有人在电脑前点；适合你正好在电脑前 |
| **`serve install-task` + `serve start --via-task`** | ✅ | 唯一可靠的无人值守方案（见下） |
| 让 agent 自己以管理员身份启动 | ⚠️ | 它拉起的子进程都会继承提权，但 agent 自动更新/重启后可能掉回非提权，而你人在外面时无法补救 |
| 关闭 UAC（`EnableLUA=0`） | ✅ | ❌ 全局降低安全性、需重启，不建议 |

### 5.4 计划任务方案（推荐）

一次性操作，之后永久生效：

```powershell
# ① 用**管理员** PowerShell 跑一次（只有这一步需要管理员）
hsctl serve install-task

# ② 之后在普通窗口、或让 agent 直接跑（不会弹 UAC）
hsctl serve start --via-task
hsctl serve task-status      # 随时确认任务在、且勾了最高权限
hsctl serve uninstall-task   # 不要了就删掉
```

原理：任务计划程序服务以 SYSTEM 身份按 `/rl HIGHEST` 创建进程，**不经过 `ShellExecute`
提权路径，所以没有 UAC 弹窗**。之后任何非提权进程（包括 WorkBuddy 这类 agent）都能
`schtasks /run` 触发它。

`install-task` 做的事：

1. 生成启动器 `log\hs_serve_elevated.cmd`（内容就是 `cd` 到项目根 + `hsctl serve start --wait 120`）。
   绕一层启动器是因为 `schtasks /tr` 的值本身要用引号包住，内联 `cmd /c ... && ...`
   那种带引号的命令用户粘到 cmd 里会被内层引号截断。
2. 执行 `schtasks /create /tn "HSLegendArriver_serve" /tr "<启动器>" /sc ONCE /st 00:00 /rl HIGHEST /f`。

两个设计要点：

- **任务动作是幂等的。** 它跑的是 `hsctl serve start`（先探活，已在运行就什么都不做），
  不是直接跑 `web_ui.py`。所以就算 ONCE 触发器的时间已经过去、任务被意外触发，也
  不会平白多起第二个控制台（第二实例会绑到 8766，两个控制台抢同一份配置）。
- **非管理员时不会替你弹 UAC**，而是把上面那条 `schtasks` 命令原样打印出来，让你在
  管理员窗口里粘一次（退出码 1）。

### 5.5 提权控制台怎么停

提权起来的控制台，**非提权进程结束不了它**（Windows 不允许低权限进程结束高权限
进程，`os.kill` 和 `taskkill` 都会被拒）。所以控制台开了一条**自停通道**：

```powershell
hsctl serve stop            # 默认：先停自动化，再让控制台自己退出（不需要提权）
hsctl serve stop --hard     # 跳过自停，直接 taskkill（提权起的那种要管理员 shell）
```

自停通道的原理：`web_ui` 启动时随机生成一个 token 写进 `log/ui_port.txt`，
`hsctl serve stop` 用这个 token POST `/api/serve/stop`；控制台校验通过后**自己**
停自动化 → 清端口文件 → 退出进程。发起者不需要任何特权，agent / 手机 / 普通 shell 都能用。
token 只在本机端口文件里（`log/` 不入库），且接口只回 `127.0.0.1`，网络上打进来停不掉。

自停不可用时（老版本控制台、端口文件没有 token、进程卡死），才退回结束进程；
那时如果报 `Access denied`，就用**管理员** PowerShell 跑 `hsctl serve stop --hard`。

---

### 5.6 换机器 / 换 Python：解释器自配置（`doctor` + `env`）

**运行环境因机器而异**，所以本仓库**不把任何解释器路径写进代码或默认值**：
代码只负责"找、验、存、用"，具体路径由每台机器自己保存。

| 文件 | 是否入库 | 内容 |
| --- | --- | --- |
| [`env.example.json`](../env.example.json) | ✅ 进仓库 | 只有占位符与说明的模板（**不含任何本机路径**） |
| `log/env.json` | ❌ 被 `log/` 忽略 | 本机实际生效的那一份：`interpreter` / `pinned_at` / `source` / `last_ok` |

```powershell
python hsctl.py --json doctor      # 一条命令：环境 + 控制台 + verdict + next_steps
python hsctl.py env                # 当前解释器 / 已保存的解释器 / 来源
python hsctl.py env --discover     # 列出本机候选解释器并逐个校验（不写盘）
python hsctl.py env --pin "<python.exe 路径>"   # 校验并**保存**到 log/env.json
python hsctl.py env --pin auto     # 自动选第一个合格候选
```

- **校验**：必须 Python **3.12.x**（3.13+ 装不上 OCR 轮子）且
  `win32gui/cv2/numpy/PIL/paddleocr/paddle/psutil/keyboard/pynput/requests/click`
  全部齐全。校验只 `find_spec`（**不 import**），所以不会把 paddle 拉起来。
- **解析顺序**：`HS_PYTHON` 环境变量 → `log/env.json` → 当前解释器（校验通过才用）
  → 候选探测（`CONDA_PREFIX` / `VIRTUAL_ENV` / 常见 conda 位置 / 仓库内 venv /
  `where python`）。
- **透明代理**：任何 agent 用任何 python 跑 `hsctl`，只要当前解释器不合格而本机有合格的，
  `hsctl` 就 `os.execv` 重入过去（并顺手保存），退出码/输出原样传递。
  `HSCTL_NO_PROXY=1` 关掉它；`HSCTL_REEXEC=1` 防死循环。一个都不合格 → 退出码 **4**。
- **计划任务跟着走**：`serve install-task` 生成的启动器以 `log/env.json` 为准，
  不会再出现"计划任务被硬编码成生成它时那个错误 Python"的老坑（原 §8.1 那套
  手改 `log/hs_serve_elevated.cmd` 的应急流程已经不需要了）。

**多个候选都合格**时 `doctor` 会把它们都列出来，**问你一次**要固定哪个，
然后 `hsctl env --pin "<路径>"` 保存 —— 保存完再 `doctor` 复验到 `env_ok`。

---

## 6. 给 agent 的典型编排

```
0. hsctl --json doctor             → 拿到 verdict + next_steps；先照它把环境/控制台修好
                                     （env_ok_unsaved 就先 hsctl env --pin 保存本机解释器）
1. hsctl --json status             → 连不上（退出码 3）就先 hsctl serve start
2. 看 is_admin                     → false 时：hsctl serve task-status
                                     · 任务在 → hsctl serve start --via-task（不弹 UAC）
                                     · 任务不在 → hsctl serve install-task
                                       （非管理员会打印一条让你/用户粘的命令）
3. 检查 name_match.matched        → false 就用 hsctl config set --name 纠正
4. hsctl prepare                  → 确认浮窗/前台就绪，再 hsctl start
5. hsctl log -f                   → 跟随日志观察；或 hsctl --json status --watch 2
6. 需要时热改：hsctl delays set … / hsctl liveness set …
7. 收工：hsctl stop --after-game（打完本局）或 hsctl stop
```

配套的 agent 技能说明见 [`../SKILL.md`](../SKILL.md)；跨项目的 agent 行为约定见
[`../AGENTS.md`](../AGENTS.md)。

---

## 7. 安全边界

- **只绑本机。** 控制台监听 `127.0.0.1`（[`config.py`](../config.py) 的 `HS_HOST`），**没有鉴权**。
  远程控制请交给 WorkBuddy 这类**在本机运行**的 agent，**不要**做端口转发或绑到 `0.0.0.0`。
- **管理员权限。** 点击类操作需要提权；请走 §5 的计划任务方案，不要关 UAC。
- **计划任务本身是一个提权入口。** `install-task` 注册的任务会被任何能跑 `schtasks /run`
  的本机进程触发（这正是它有用的原因）。不用时请 `hsctl serve uninstall-task` 删掉；
  也不要把任务名透露给不该提权的人。
- **高风险命令**只有 `restart-hearthstone`（会杀炉石进程 + 清空它的日志目录），必须显式确认。
- `stop` / `serve stop` 都会先尝试优雅停自动化，尽量不在出牌动作中途硬杀。

---

## 8. 实现说明（为什么是 argparse 而不是 Click）

`CLI-Anything` 那类工具生成的是 Click 命令行。但本仓库**根目录有 `click.py`**
（炉石的鼠标点击模块），在仓库内 `import click` 会解析到它而不是 PyPI 的 click：

```powershell
> conda run -n HTL python -c "import click; print(click.__file__)"
C:\...\HearthstoneLegendArriver-main\click.py     # ← 不是 site-packages
```

所以任何基于 Click 的 CLI 在这里都会直接失败。`hsctl.py` 因此**只用标准库**
（`argparse` + `urllib`），Python 3.9+ 都能跑，也不需要新增依赖。
解释器探测/校验那一块同样只用标准库（`subprocess` + `importlib.util.find_spec`），
所以**在错的解释器上也能先跑出诊断结论**，再把自己换到对的解释器上。

`hsctl` 借用的只是那套 CLI 规范：子命令分组、`--json` 机器可读输出、`--help`
自描述、裸命令进 REPL。

---

## 9. 故障排查

| 现象 | 原因 / 处理 |
| --- | --- |
| `✗ 没找到正在运行的控制台（已探测 …）` | 脚本没跑：`python web_ui.py`（管理员）或 `hsctl serve start` |
| 退出码 1 且提示「自动化运行中，请先停止」 | 那是网页保护性守卫。给该命令加 `hot` 通道即可（参数类命令默认已带） |
| `hsctl status` 显示账号「未读到玩家名」 | 还没打过一局，日志里还没有玩家名 |
| 改了延时但没感觉 | 看 `hsctl delays` 标的生效时机：`下次「开始对战」生效` / `需重启脚本进程` |
| `serve stop` 说 pid 不是 Python 进程 | 端口文件过期或 pid 被复用；确认后加 `--force` |
| `serve stop` 报「结束 pid 失败 / Access denied」 | 正常情况下不该再出现（`serve stop` 走 HTTP 自停，不需要提权）。真出现时说明自停没成功：用**管理员** shell 跑 `hsctl serve stop --hard`，或直接关掉那个控制台窗口 |
| `serve start --elevate` 说「提权被拒绝」 | UAC 框上点了「否」或超时取消（返回码 5）。无人值守请改用 `--via-task` |
| `serve start --via-task` 说「计划任务还没注册」 | 先用管理员跑一次 `hsctl serve install-task` |
| `serve task-status` 显示「最高权限：否」 | 任务没勾最高权限，起来仍是普通权限：`uninstall-task` 后重新 `install-task` |
| 订了任务但自动化还是点不动 | 确认 `hsctl status` 里的 `is_admin` 是 `true`；不是就说明启动没走任务 |
| 自检里依赖「全部同时 fail」 | 控制台跑在错的解释器上：`hsctl --json doctor` → 按 `next_steps`（`env --pin` → `serve stop` → `serve start --via-task`），见 §5.6 |
| `hsctl` 直接退出码 4 | 本机没有合格的 Python 3.12 环境：`hsctl env --discover` 看候选与缺什么，再按 `doctor` 的 bootstrap 步骤建环境 |
| 换了机器/换了 Python 后所有命令都不对 | 这是**预期**：解释器是每台机器各自保存的。`hsctl --json doctor` → `hsctl env --pin "<路径>"` → 复验，见 §5.6 |
| JSON 解析失败 | 端口上跑的可能不是本脚本；`hsctl --json serve status` 看端口文件来源 |

排查实机问题时可先看这几个文件：`ui_log_last.txt`（运行日志）、`log\ui_port.txt`
（端口）、`log\cli_serve.log`（`serve start` 的输出）、`log\hs_serve_elevated.cmd`
（计划任务的启动器）。
