---
name: hslegend-arriver
description: 用 hsctl 命令行实时控制炉石传说自动化脚本（HSLegendArriver）。当用户要求「看脚本状态 / 要一张实时画面、截图、看看现在屏幕上是什么 / 认出现在在哪个界面 / 一键进入对战（点传统对战、选卡组、开始匹配）/ 切换对战模式（狂野/标准/休闲）/ 从0冷启动（电脑刚开机、炉石和盒子都没开）/ 开始或停止对战 / 改延时或活人感等参数 / 追日志 / 定时 / 校准 / 重启炉石 / 停掉跑在管理员权限下的控制台 / 换机器换 Python 后环境不对、缺依赖、自检全 fail / 让另一个 agent 自己配置并保存本机解释器 / 用手机或 agent 远程指挥脚本」时使用。会话第一条命令固定是 python hsctl.py --json doctor（给出 verdict 与可直接执行的 next_steps；环境改动立即用 hsctl env --pin 保存到本机 log/env.json）。命令是 python hsctl.py <子命令>，输出支持 --json，退出码可判断成败（0 成功 / 1 业务失败 / 2 参数错 / 3 连不上 / 4 环境不可用）；提权只能靠预注册的计划任务，agent 自己无法提权，但停掉提权控制台不需要管理员（serve stop 走 HTTP 自停）。用户要实时画面时直接用 hsctl screenshot 抓图返回，不要多话。界面识别只认文字（认不出就只点画面中心，绝不盲点按钮）。
---

# 用 hsctl 控制炉石自动化脚本

`hsctl.py` 是炉石脚本 `web_ui.py` 控制台的**命令行客户端**（零第三方依赖）。
你（agent）通过它实时控制脚本；网页控制台仍然可用，两者改的是同一份状态。

## 0. 铁律（先读）

1. **先探活，再动手**：任何操作前先 `python hsctl.py --json status`。
   退出码 `3` = 脚本没在跑 → 用 `python hsctl.py serve start` 拉起来（或让用户开）。
   **不只是脚本没跑**（电脑刚重启、炉石/盒子也没开）时，走 §3「从 0 冷启动」。
2. **看退出码，不要猜**：`0` 成功 ／ `1` 业务失败（读 `error`）／ `2` 参数错 ／ `3` 连不上。
3. **别用浏览器/截图去操作系统**：直接调 `hsctl`。它比点网页快、稳、可解析。
4. **不要重启用户的对局**：`hsctl stop` 会打断当前这一局。用户说「打完再停」时
   必须用 `hsctl stop --after-game`。
5. **`restart-hearthstone` 是高风险**：会结束炉石进程并清空它的日志目录。**只有在用户
   明确要求**时才用，并且必须 `--yes`。
6. **不要改网络暴露**：控制台只绑 `127.0.0.1` 且无鉴权。不要建议端口转发或绑 `0.0.0.0`。
7. **点击类操作需要管理员权限，而你无法自己提权。** `status` 里的 `is_admin: false` 意味着
   自动化点击会被系统拦掉（现象：脚本在跑但点了没反应）。**不要**指望 `serve start --elevate`
   能"自动"提权 —— 它会弹 UAC 框，必须有人在电脑前点「是」；无人值守时只会干等超时。
   正确做法见 §8「提权流程」。**也不要**建议关掉 UAC。
8. **用户要「实时画面 / 截图 / 现在屏幕上什么样」时：抓图，把图给他，别多话。**
   直接 `python hsctl.py --json screenshot --kind client --max-width 1280`，读返回的 PNG，
   然后**在回复里直接把这张图展示出来**。不要用文字去描述画面代替图，不要反问"要哪一档"
   （默认 `client` 就对），不要先 `status`/`log` 铺垫，也不要附上一段战况分析。
9. **提交代码前先读 [`docs/GIT.md`](docs/GIT.md)**：一个功能一个提交、不 `git add -A`、
   不擅自 push；`ui_config.json` / 日志 / 截图等本机产物不要入库。
   最多一行说明（档位 + 是否退化为整屏 + 时间）。详见 §7「实时画面」。
   只有图**不能**反映用户想问的东西时（例如他没在跑、图退化成桌面）才追加解释。
   这是铁律 1「先探活」的唯一例外 —— 抓图本身只读且会自查，连不上会返回退出码 3。
9. **只用脚本固有命令，绝不自己造**。所有操作都走 `hsctl` 的现成子命令；
   拿不准有什么命令、参数怎么写时，**当场查**：
   `python hsctl.py --help`（全部命令）、`python hsctl.py <命令> --help`（该命令的参数）、
   `docs/CLI.md`（完整参考）。禁止 agent 自己写轮询循环、sleep 等待、临时脚本去
   模拟固有功能（如"打完这把停"必须用 `stop --after-game`，不要自建状态查询循环；
   串行任务用 `queue run`，不要自己编排）。固有命令覆盖不了的需求，先问用户
   是否要给脚本加功能，而不是绕过它。

## 0.1 最短决策流程（照这个走，别绕）

```bash
python hsctl.py --json doctor      # ① 一条命令拿结论（环境 + 控制台 + 下一步）
# ② 直接执行 next_steps 里给的命令（通常 1-2 条，原样照抄即可）
python hsctl.py --json doctor      # ③ 复验（verdict 变成 env_ok）
# ④ 只回报结论：≤3 行（做了什么 / 结果 / 需要用户做什么）
```

`doctor` 的 `verdict` 含义：

| verdict | 含义 | 下一步（`next_steps` 里会给） |
| --- | --- | --- |
| `env_ok` | 环境已保存且合格，控制台状态见 `console` | 无事；直接干活 |
| `env_ok_unsaved` | 环境**能用但还没保存**到本机 `log/env.json` | `hsctl env --pin "<路径>"` 保存（换 agent/换 shell 才不会再找错解释器） |
| `env_deps_missing` | 找到 3.12 但缺包 | 照抄它的 pip 命令（已定位到具体解释器） |
| `env_no_valid_interpreter` | 本机没有合格解释器 | conda/venv 建一个 → pip install → `env --pin` |
| `console_not_running` | 环境 OK，控制台没跑 | `hsctl serve start --via-task` |
| `console_not_admin` | 控制台在跑但没提权（点了没反应） | `serve stop` → `serve start --via-task` |
| `env_wrong_interpreter` | 控制台跑在错的解释器上 | `env --pin` → `serve stop` → `serve start --via-task` |

> **通用 agent 行为约定**（一条命令一条决策、少废话、别自建轮询、Git 纪律）见
> [`AGENTS.md`](AGENTS.md)；本节只讲本项目特有的东西。

## 1. 前置检查（每次会话开头）

**第一条命令永远是 `python hsctl.py --json doctor`**（见 §0.1）：它一次给出环境是否
已保存、控制台在不在跑，以及该执行哪几条命令。下面这张表是它字段的细节参考。

```bash
python hsctl.py --json status
```

关键字段：

| 字段 | 含义 / 该做什么 |
| --- | --- |
| `running` / `phase` | 是否在跑；`phase`: idle / waiting / playing / stopping / finished |
| `is_admin` | `false` → 提醒用户用管理员权限 |
| `name_match.matched` | `false` → 账号不匹配，用 `config set --name "昵称#编号"` 修 |
| `config.name` / `config.log_root` | 两项都不能空，否则 `start` 会被拒 |
| `overlay_running` / `prepared` | 浮窗是否开着、是否已「就绪」 |
| `stop_after_game` | `true` 表示已请求「本局结束后停止」 |
| `games` / `wins` / `win_rate` | 战绩 |
| `selfcheck.ok` / `failed` | 环境自检；`false` 先看 `hsctl selfcheck` 的明细。**若 Python/pip 等依赖"全部同时 fail"** → 大概率是控制台跑在了**错误的解释器**上（版本不符或缺整个环境的包），不是零星缺包，按 §8.1 修 |
| `last_error` | 上次失败原因 |

## 2. 标准编排

```bash
# ① 探活（连不上就拉起来）
python hsctl.py --json status || python hsctl.py serve start

# ② 确保"真的是管理员"才继续（详见 §8）；is_admin 为 false 时不要直接 start
python hsctl.py serve task-status
#   任务在   → python hsctl.py serve start --via-task     （不弹 UAC）
#   任务不在 → python hsctl.py serve install-task          （非管理员会打印一条命令给用户）

# ③ 修配置（只在需要时）
python hsctl.py config set --name "Priscilla#51346" --log-root "D:\Battle.net\Hearthstone\Logs"

# ④ 准备（开浮窗 + 切炉石前台，不开打）
python hsctl.py prepare

# ⑤ 开打
python hsctl.py start

# ⑥ 观察（二选一；都是阻塞的，用超时/后台跑）
python hsctl.py log -f
python hsctl.py status --watch 2

# ⑦ 收工
python hsctl.py stop --after-game     # 打完本局（推荐）
python hsctl.py stop                  # 立即停
```

## 3. 从 0 冷启动（整条链都没起来时）

远程场景（用户在外面、电脑刚重启）就是这条路径。**顺序不能乱，缺一环都会失败**：

| # | 环节 | 谁负责 |
| --- | --- | --- |
| 0 | **你自己（WorkBuddy）在跑** | 你已经在跑才看得到这段；本机**没有**给 WorkBuddy 配开机自启，这是整条链最脆弱的一环 |
| 1 | 炉石盒子 | 项目**没有**启动它的代码，得你/用户打开它 |
| 2 | 脚本控制台（**提权**） | `serve start --via-task`（前提：§8 的 `install-task` 已做过一次） |
| 3 | 战网 → 炉石 | `restart-hearthstone --yes` |
| 4 | 认界面 → 点「传统对战」→ 选卡组 | `enter-battle --deck N`（见 §4；**认不出就只点中心，绝不盲点**） |
| 5 | 就绪（浮窗 + 前台） | `prepare` |
| 6 | 开打 | `start` |

```powershell
Start-Process 'D:\炉石传说盒子\HSAng.exe'    # ① 盒子（进程名 HSAng.exe）
python hsctl.py serve start --via-task        # ② 控制台：提权、不弹 UAC
python hsctl.py restart-hearthstone --yes     # ③ 战网启动器 → 炉石
python hsctl.py enter-battle --deck 8         # ④ 认界面：点「传统对战」→ 选第 8 副卡组
python hsctl.py prepare                       # ⑤ 开浮窗 + 切前台（不开打）
python hsctl.py start                         # ⑥ 开打
```

第 ④ 步默认**停在选择套牌界面**（不点「开始」）；要一路打到排队就加 `--start`。
第 ⑤/⑥ 步之间可以先用 `python hsctl.py ui` 确认"它现在认为自己在哪个界面"。

上面两条是本机实测路径；**换机器要重新探测**：

```powershell
# 盒子装在哪（按显示名找）
Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
                 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -match '盒子' } | Select-Object DisplayName, InstallLocation
# 战网/炉石不用探测：restart-hearthstone 会按 log_root 自动推导
```

三条容易踩的坑：

- **`restart-hearthstone` 会先杀炉石进程、再清空它的日志目录。** 冷启动时炉石本来没开，
  无害；但如果炉石**已经在跑**、而用户不是明确要重启，**不要**用它 —— 那种情况用
  `prepare` 就够了（`prepare` 会把炉石切前台；战网开着、炉石没开时它还会点「开始游戏」
  把炉石拉起来）。
- **战网也没开时，`prepare` 只会报「未找到应用战网」**（`click.enter_HS` 的行为），
  这时才必须用 `restart-hearthstone` 去启动战网。
- **`enter-battle` 认不出界面时不会乱点**：它只点画面中心 (960,650)，到超时就退出并说明
  原因（`ui.note` 会说清是模板缺失、框里没字，还是"有模板但没匹配上"）。如果它一直
  超时，按 §7「识别不出来怎么办」查一下再重试 —— 不要改用别的办法去点「传统对战」。
- **首次校准必须有人拖框**，项目**没有任何 API 能写 ROI**（`calibrate` 只是把窗口打开）。
  但 ROI 已经在 `ui_config.json` 里时不用重校 —— 只要盒子的布局没变。

## 4. 命令速查

> 本节只是常用摘录；**全集与参数以 `python hsctl.py --help` 和
> `python hsctl.py <命令> --help` 为准**（argparse 自动生成，永远和代码同步），
> 完整文档见 `docs/CLI.md`。还有个交互模式：`python hsctl.py repl`。

```bash
# 自诊 / 环境（**会话第一条命令就是 doctor**）
python hsctl.py --version               # 版本号＝发布日期（v2026.10.24），回报里带上便于对账
python hsctl.py --json doctor           # 环境 + 控制台 + 下一步（verdict / next_steps）
python hsctl.py env                     # 当前 / 已保存的解释器
python hsctl.py env --discover          # 列出本机候选解释器 + 各自缺什么（不写盘）
python hsctl.py env pin "<path|auto>"   # 校验并保存到本机 log/env.json

# 观测
python hsctl.py status [--watch 秒]
python hsctl.py log [-n 50] [-f] [--interval 秒]
python hsctl.py selfcheck [--refresh]
python hsctl.py regions                 # 截屏画区域框，只截图不点击
python hsctl.py screenshot [--kind client|full|roi|boxes] [--max-width 1280]
                                        # 抓一帧存成 PNG，返回路径 → 你直接读那张图"看画面"
python hsctl.py screenshot --points mode  # 同上，并把模式切换的四个点击点画成红十字（只截图）
python hsctl.py ui                      # 现在在哪个界面（主界面/选择套牌）+ 匹配分
python hsctl.py ui --frame              # 顺带存一帧图并打印路径（核对它看到了什么）
python hsctl.py ui --learn              # 站到对应界面时重裁识别模板（皮肤/补丁改了字之后）

# 进入对战（认界面 → 点「传统对战」→ 选卡组 → 可选点「开始」）
python hsctl.py enter-battle --deck 8   # 默认停在选择套牌界面（不点「开始」）
python hsctl.py enter-battle --deck 8 --start   # 连「开始」一起点（等于开始排队）
python hsctl.py deck-click 8            # 快速选卡组：直接点第 8 格（秒级，不走状态机）
                                        # 前提：当前已在选牌界面、自动化未运行；那一格不像卡组就不点
python hsctl.py deck --clear            # 清掉旧的「自动选卡组」设置（自动选卡组已停用）

# 切换对战模式（手动、同步约 2-3 秒：点右上角开模式菜单 → 点对应模式）
python hsctl.py mode wild               # wild | standard | casual（或 狂野/标准/休闲）
                                        # 前提：停在选择套牌界面、自动化未运行（详见下文）

# 待做命令队列：一次提交多条，控制台后台按顺序执行（断线不影响，可查可取消）
python hsctl.py queue run "stop mode=after_game" "wait-stopped" "mode wild" "deck-click 1" "start"
python hsctl.py queue status            # 看每步进度（pending/running/ok/failed/skipped）
python hsctl.py queue cancel            # 取消（当前步跑完后停止）
#   支持的动作：stop [mode=after_game] / wait-stopped [timeout=秒] / deck 序号 /
#   deck-click 序号 / mode wild|standard|casual / prepare / start [keep_score=1] / delay 秒
#   某一步失败即停，剩余 skipped；提交时整体校验语法，写错一条都不收

# 对战
python hsctl.py start [--keep-score]
python hsctl.py resume                  # 续跑，战绩累加（不清零）
python hsctl.py prepare
python hsctl.py stop [--after-game]

# 参数（默认走热更新通道，运行中也能改；--no-hot 退回网页那种保护）
python hsctl.py delays                           # 看延时 + 每条生效时机
python hsctl.py delays set pre_action_delay_seconds=5
python hsctl.py human-like set --enabled --post-delay-min 0.5 --post-delay-max 2.5
python hsctl.py liveness set --enabled --warn 120 --stop 300
python hsctl.py watchdog on                      # 中断重启：日志停滞 5 分钟就重启炉石并重开
python hsctl.py watchdog set --stale 300 --max-restarts 3 --window 600
python hsctl.py concede set --enabled --threshold 12 --rounds 4
# 卡组识别投降（只判对方）：认出对面是哪套卡组就投；规则是「特征牌 + 与/或/非」树
python hsctl.py surrender                        # 看配置（条件译成中文）
python hsctl.py surrender on|off                 # 总开关（保留卡组）
python hsctl.py surrender check                  # 这一局对方职业 + 每个条件是否满足
python hsctl.py surrender set --file rules.json  # 存规则；当场会命中时要加 --yes
python hsctl.py card 神秘访客                     # 查卡名（本地，不联网）
python hsctl.py rank-stop set --enabled --mode legend_number --legend-number 500 --action stop
# 控分：名次数值 < 500（名次比目标更靠前）就开局投降，压到 >= 500 再正常打
python hsctl.py rank-stop set --enabled --mode legend_number --legend-number 500 --action keep
python hsctl.py config set --name "昵称#编号"

# 浮窗 / 校准 / 定时
python hsctl.py overlay on|off|toggle
python hsctl.py calibrate
python hsctl.py calibrate --target main_menu_battle   # 校准窗口直接停在「传统对战」框上
python hsctl.py schedule at 22:30 --end 23:30
python hsctl.py schedule at --in 30 --duration 90
python hsctl.py schedule cancel

# 高风险（必须用户明确要求）
python hsctl.py restart-hearthstone --yes

# 进程托管
python hsctl.py serve status
python hsctl.py serve start [--browser]          # 普通（非提权）后台启动
python hsctl.py serve start --elevate            # 弹 UAC 提权（需人在电脑前，慎用）
python hsctl.py serve start --via-task           # 走「最高权限」计划任务（不弹 UAC）
python hsctl.py serve install-task               # 注册该任务（需管理员跑一次）
python hsctl.py serve task-status                # 任务在不在、勾没勾最高权限
python hsctl.py serve uninstall-task             # 删掉该任务
python hsctl.py serve stop                       # 自停通道（不需要提权）；失败才退 taskkill
python hsctl.py serve stop --hard                # 跳过自停，直接结束进程（提权时需管理员 shell）
```

所有命令都接受 `--json`（放命令前或后都行）、`--port`、`--host`、`--timeout`。

### 4.1 窗口尺寸口径（自检会提示）

- **炉石窗口设成 1920×1080 即可：窗口化就行，不必全屏**（选项 → 图形 → 分辨率），改完打开校准窗口拖一次保存。别用「最大化窗口」（客户区不是 1920×1080）。
- 「🧪 环境自检」里的 `client-windowed` 项：窗口化且尺寸 ≠ 1920×1080 时是 ⚠️（`required=False`，
  **不拦运行**）；全屏时该项根本不出现。判定用窗口样式 `WS_CAPTION`（有标题栏=窗口化），
  不看尺寸——最大化/换屏都不会误判。
- 用户抱怨「点击偏、法术指向一直重试」时，先问是不是窗口化且尺寸不是 1920×1080，
  再查校准。

### 4.2 切换对战模式（`hsctl mode`）注意

- **前提**：画面停在「选择套牌」界面（右上角模式按钮可见）、自动化未运行。
  自动化 / 「进入对战」/ 命令队列任一在跑都会被拒绝（退出码 1，读 `error` 即知原因）。
- **命令不校验当前界面**：站错界面调用可能误点别的 UI。执行完按 §7 惯例
  主动补抓一张截图（新文件名）给用户核对模式是否切成功。
- **点击坐标（2026-10 实机截图实测，1920×1080 客户区基准）**：
  模式按钮 `(1405, 55)`；狂野盾牌 `(617, 380)`、标准盾牌 `(955, 375)`、
  休闲盾牌 `(1290, 380)`。三个点统一在同一水平带里（三个盾牌的亮面都覆盖
  y 330–430；往上会碰到等级六边形、往下中间会碰到「标准模式」蓝幅）。
- **核对坐标不用真的点**：`python hsctl.py --json screenshot --points mode`
  会把四个点画成红十字标在图上（只截图、不点击、不移动鼠标）—— 想让用户确认
  "到底点在哪儿"就用它，把图发过去即可。
- 点击位置不准时**不用改代码**：`ui_config.json` 顶层 `mode_click` 段可覆盖坐标
  （`menu_open` / `wild` / `standard` / `casual`，值为 `[x, y]`，1920×1080 客户区坐标），
  改完立即生效。也可以 `hsctl calibrate` 在校准窗口里拖这四个框（框中心=点击点），
  按 S 保存 —— **ROI 不是热开关**：保存后要在下次「开始对战」时读取一次才生效
  （`fsm.init()` 重建 `RecommendationConfig`），不用重启脚本进程。
- 典型场景：上传说后换模式继续——
  `python hsctl.py queue run "stop mode=after_game" "wait-stopped" "mode wild" "deck-click 2" "start"`。

### 4.3 卡组识别投降规则（`surrender`）

**只判对方**：对方打出 / 出现过 / 开局生效的卡才算数，自己的牌永远不算。
命中任意一套 → 立刻认输（不需要等自己回合）。

```json
{"enabled": true, "decks": [{
  "id": "priest_alizana", "name": "牧师 · 阿扎莉娜 + 神秘访客", "class": "牧师",
  "when": {"all": [
    {"type": "start_of_game", "card": "裂魂者阿扎莉娜"},
    {"type": "played", "card": "神秘访客", "within": {"max": 4}}
  ]}}]}
```

- 节点：`all`（与）/ `any`（或）/ `not`（非），**可任意层嵌套**（`A 且（B 或 C）` =
  `{"all":[A, {"any":[B, C]}]}`；深度 ≤8、每套 ≤64 条）；条件类型：`played` /
  `revealed`（别名 `seen`）/ `start_of_game` / `played_or_revealed` / `in_zone` / `turn`
- 其它字段：`card`（中文名或 id，数组=任一）、`card_type`、`side`（默认 `opponent`）、
  `at_least/at_most`（可同时写，表示 N~M 张）、`within`（`{"min":1,"max":4}`）；
  `class` 可写可不写
- 卡名写中文即可，错字会自动纠正；校验失败**一律不写盘**（保留上一份能用的）
- 运行中热保存：若这份规则**当场就能命中**会返回 `needs_confirm`，CLI 加 `--yes` 才存
- 调规则时先 `python hsctl.py surrender check`：它列出对方职业 + 条件树（`且/或/非`
  缩进，✓/× 按真实表达式算）；网页状态栏同源，另每套卡组可单独收起

## 5. 改参数前必须知道的生效时机

对局中改延时，**不是全部都立刻生效**：

| 生效时机 | 字段 |
| --- | --- |
| 立即生效 | `mulligan_ready_delay_seconds`、`mulligan_retry_delay_seconds`、`pre_action_delay_seconds`、`first_turn_per_card_delay_seconds`、`discover_target_delay_seconds` |
| 下次「开始对战」生效 | `mulligan_post_ocr_delay_seconds`、`post_action_delay_seconds`、`draw_extra_delay_per_card_seconds` |
| **需重启脚本进程** | `ocr_preprocess_scale` |

`hsctl delays set` 的回话里有 `effects` 字段直接告诉你每项的实际时机 —— **以此为准**，
不要凭本表猜。活人感 / 存活检测 / 中断重启 / 自动投降 / 段位目标是每次读盘，改完立即生效。

**自动选卡组已停用**：自动化不再每局按 `hsctl deck 8` 点卡组（会覆盖手选）。
选卡组只用 `deck-click N`（自动化未运行时立即点）或 `enter-battle --deck N`（进对战时选）。

## 6. 常见拒绝与处理

| 回话 | 处理 |
| --- | --- |
| `自动化运行中，请先停止后再修改…` | 该命令没带 `hot`。参数类命令默认带；若你显式加了 `--no-hot`，去掉它 |
| `自动化已经在运行中。` | 别重复 `start`；先看 `status` |
| `当前没有正在运行的自动化。` | `stop` 没意义；先 `start` |
| `请先填写用户 ID / 炉石日志目录` | `config set` 补上 |
| `日志目录不存在：…` | 路径写错了；用 `--log-root` 改对 |
| `必须介于 a–b` | 数值越界，按提示范围改 |
| `没找到正在运行的控制台` | 退出码 3：`serve start`，或让用户启动脚本 |
| `重启炉石会结束炉石进程…请显式确认` | 这需要用户明确同意，再加 `--yes` |
| `注册「最高权限」计划任务需要管理员权限` | 正常：你无法提权。把打印出来的那条 `schtasks` 命令**原样交给用户**，请他在管理员窗口跑一次 |
| `计划任务还没注册` | 同上；或者用户不想用，就请他手动用管理员 PowerShell 启动脚本 |
| `提权被拒绝`（退出码 5） | 用户在 UAC 框上点了「否」，或没人点导致超时。别重试 —— 改走计划任务 |
| `结束 pid … 失败：… Access denied` | 控制台是提权起的，非提权进程杀不掉。先 `hsctl stop`（走 HTTP，一定有效），再请用户用管理员 shell 跑 `serve stop` |
| `进入对战未完成：等待主界面（传统对战）超时` | 见下面「6.1 识别不出来怎么办」 |

### 6.1 识别不出来怎么办（`ui` / `enter-battle` 超时）

脚本"看界面"**只认文字**：在可校准框 ±40px 内找「传统对战」四个字 / 「开始」按钮的图案。
认不出来时它**只会点画面中心**，到超时就退出 —— 这是有意的（宁可不动，也不乱点）。
排查顺序：

```bash
python hsctl.py ui --frame     # 它会告诉你 state / 两个匹配分，并把这一帧存成 PNG
#   → 直接读那张 PNG：它到底看到了什么？是不是还停在加载页/公告弹窗/选人界面？
```

| `ui` 里的线索 | 含义 / 你该做什么 |
| --- | --- |
| `state=unknown`，`score=—（template_missing）` | 模板文件没了。站到主界面跑 `python hsctl.py ui --learn` 重裁 |
| `state=unknown`，`score=—（template_flat）` | 那个框里**没有字**（一片平坦）：先在校准窗口把框拖到文字上，再 `ui --learn` |
| `state=unknown`，`score=0.4~0.7`（`low_score`） | 有模板但没对上：多半是**框偏了**（拖回来）或**皮肤/补丁把字改了样子**（`ui --learn` 重裁） |
| `state=main_menu` 但 `enter-battle` 仍不动 | 看它是不是卡在拉起炉石那一步；`hsctl log -n 50` 看后台线程写的日志 |
| `state=deck_select`，但 `slots_found < 6` | 「开始」按钮在、卡组格却没认全 —— 可能是**结算界面**（脚本不会去点卡组格） |

要重新对齐框：`python hsctl.py calibrate --target main_menu_battle`（直接停在那个框上）。
校准窗口里拖这个框时，**顶部提示条会实时显示匹配分**（`框内文字匹配 1.00（阈值 0.80），对准了`），
用的就是脚本运行时同一个判定函数 —— 显示 1.00 就是真的对上了，按 **S** 保存即可。

> 用户报「按完开始鼠标好久不动」时，先 `hsctl log -n 50` 找 `[SYS] 进入对战 +x.xs：…`：
> 停在「初始状态=…」= 界面识别/前台问题；停在「段位检测完成」= 段位 OCR 慢；
> 这几行完全没有 = 自动化线程没起来（看 ERROR）。

## 7. 与用户沟通的要点

- 报告状态时给出：阶段、是否在跑、战绩、账号是否匹配、有没有告警（`liveness.alert`、`last_error`）。
- 用户说「随便调快一点」这类模糊需求时，优先改**立即生效**的字段
  （`pre_action_delay_seconds`、`post_action_delay_seconds` 要下次开局才生效，别选它）。
- 不确定端口/连不上时，用 `python hsctl.py --json serve status` 看端口文件来源。
- 用户想「远程/手机指挥」时：说明控制台只绑本机、交给本机 agent 跑 `hsctl` 即可，
  **不需要**也不应该把端口暴露到网络。

### 实时画面（用户要图 → 抓图、返图、不多话）

用户说「给我看看屏幕 / 现在什么情况，来张图 / 截个图 / 实时画面 / 打到哪了（附张图）」时，
**标准动作只有三步，不要再加第四步**：

```bash
# ① 抓（默认 client 就对，不用问）
python hsctl.py --json screenshot --kind client --max-width 1280
# ② 读返回里的 path（或固定路径 latest）那张 PNG
# ③ 在回复里直接把图展示出来，最多配一行说明
```

规矩：

- **要返图，不是要报告。** 不要把画面"翻译"成文字（"当前处于换牌阶段，手上有…"）——
  用户要的就是那张图本身。文字描述再多也不能替代图。
- **不要问档位**：没特别说就用 `client`。只有用户明确说要看整屏/盒子面板/校准框时才换
  `full`/`roi`/`boxes`。
- **不要铺垫**：不要先 `status`/`log`/`selfcheck` 再抓图，不要先解释"我这就去抓"。
  抓完直接给图。
- **一行说明封顶**，只写必要事实：档位、是否退化为整屏、时间。用户没问就别给战况解读。
- 用户连要多次画面时，反复跑同一条命令即可（每次都覆盖 `latest`）；**不要**改用
  `--watch` 阻塞在那里 —— 用户要的是"这一刻"。
- **例外，只有两种才需要多说几句**：① 返回 `note`/`reason` 说「已退化为整屏截图」
  （炉石没运行/最小化）→ 说明图里是桌面、不是游戏界面，并提议 `prepare` 或拉起脚本；
  ② 退出码 3（控制台没跑）→ 说明拿不到画面，并按 §2 拉起控制台。这两种之外，
  即使画面看起来很糟（连败、误操作、投降），也**照图返回**，别擅自加评论。

### 自己排查用：看画面（判断"为什么没按推荐出牌"最有效）

```bash
python hsctl.py --json screenshot --kind client --max-width 1280
# 拿到返回里的 path（或固定路径 latest），然后**直接读那张 PNG**
```

- `--kind client`（默认）= 炉石客户区，**含叠在上面的盒子面板** —— 这就是脚本操作的画面；
- `--kind roi` = 盒子推荐面板，即 **OCR 的输入**：识别失败时对比日志里的 OCR 原文最直接；
- `--kind full` = 整屏，用来看盒子/浮窗有没有被别的窗口挡住；
- `--kind boxes` = 带校准区域框，用来判断校准是否还对齐。

要点：

- 抓图是**只读**的：不点击、不移动鼠标、不改游戏状态。随便抓，不会干扰对局。
- 4K 屏务必加 `--max-width 1280` 左右再读，否则图太大。
- 返回里的 `latest` 是**固定路径**（`tmp_screens\agent_latest.png`），每次都覆盖 ——
  反复看画面时可以一直读同一个文件。
- 如果 `note` 里说「已退化为整屏截图」，说明炉石**没运行或已最小化**（客户区 0×0）。
  这时看到的是桌面，**不要**把它当成游戏界面来判断对局状态；先让炉石可见（或 `prepare`）。
- 想看连续画面用 `--watch --interval 2`，它每次覆盖同一张图。
- **给用户看图要用带时间戳的文件名，不要用 `latest`**。`latest` 路径固定，
  聊天客户端经常按路径缓存，同名文件第二次投递时用户看到的还是**上一张旧图**
  （表现为"怎么还是之前那个界面"）。每次抓完，把返回里 `path` 那个
  `client_YYYYMMDD_HHMMSS_xxx.png` 投递给用户即可，文件名唯一就永不串图。
- **用户要求"选卡组 / 重启炉石 / 进入对战"等有画面变化的操作后，主动补一张图**：
  操作完成 → 立刻抓一张（新文件名）→ 投递给用户核对结果，不用等他问
  "到底选上没有"。这属于"操作回执"，不算多话。
- **读进你自己的上下文 ≠ 用户看到了**。要让用户看到画面，必须把 PNG 文件
  作为附件/文件卡片投递给用户（例如 agent 的文件展示工具），只在回复里说
  "图在这里"而不投递文件，用户是看不到的。
- **不要用 `sleep` 等炉石启动**。用户普遍反感干等；`enter-battle`、
  `restart-hearthstone`、`screenshot` 这些命令**自带等待/自查**：
  enter-battle 会自己等主界面并超时退出，restart-hearthstone 是异步的
  （返回后进度看日志），screenshot 抓不到会自己退化并说明原因。
  直接连续调命令，让命令自己等；如果炉石还在启动中就再抓一张即可，
  绝不要插 `sleep 30` 这类轮询。

## 8. 提权流程（is_admin=false 时必须走）

**事实：你无法自己提权。** 非提权进程不能静默创建提权子进程，这是 Windows 的安全边界。
唯一的技术途径是弹 UAC 同意框，而那**必须有人在电脑前点**。用户的 agent（WorkBuddy 等）
所谓"完全访问权限"是它自己的确认层，**不是** Windows 管理员令牌，同样越不过这条边界。

所以按这个顺序处理：

```bash
python hsctl.py --json status          # 看 is_admin
python hsctl.py serve task-status      # 看有没有现成的「最高权限」计划任务
```

| 情况 | 你该做什么 |
| --- | --- |
| `is_admin: true` | 没事，继续正常编排 |
| 任务存在（`highest: true`） | `python hsctl.py serve start --via-task` —— 不弹 UAC，可直接无人值守启动 |
| 任务不存在 | `python hsctl.py serve install-task`：非管理员时它会**打印一条完整的 `schtasks` 命令**，把这条命令原样交给用户，请他在**管理员** PowerShell 里执行一次。之后回到上一条 |
| 用户不接受装任务，且人在电脑前 | 才用 `python hsctl.py serve start --elevate`，并明确告诉用户"屏幕上会弹 UAC，请点「是」" |

**为什么计划任务能免 UAC**：任务计划程序服务以 SYSTEM 身份按"最高权限"标志创建进程，
不经过 `ShellExecute` 提权路径，所以没有 UAC 弹窗。之后任何非提权进程都能
触发它（`hsctl` 会先试 `schtasks /run`，**被策略拦掉时自动改走 PowerShell 的
`Start-ScheduledTask`** —— 有的机器把 `schtasks.exe` 列进了黑名单，不必你操心）。

注意：

- 注册任务**必须**管理员权限，你做不到 —— 只能把命令交给用户。
- 用完后如需清理：`python hsctl.py serve uninstall-task`。这个任务等于一个提权入口
  （任何能触发它的本机进程都算提权入口），所以不要到处宣传任务名。
- **不要**建议用户关闭 UAC。
- **停掉提权控制台不需要管理员**：`python hsctl.py serve stop` 会走控制台的
  **HTTP 自停通道**（token 从 `log/ui_port.txt` 读，只有本机能发），控制台自己收尾退出。
  失败时它才退回 taskkill，那时才需要 `--hard` + 管理员 shell。

### 8.1 控制台跑在错误的 Python 上（自检"全部依赖 fail"）

**症状**：`status` 里 `selfcheck.failed` 等于全部依赖数（pywin32/cv2/numpy/PIL/paddle…
一整排 `ModuleNotFoundError`），且 `selfcheck.executable` 指向的解释器版本不对
（本项目要求 3.12，3.13+ 装不上 OCR 的轮子）。

**先做这一件事**（大概率一条命令就够）：

```bash
python hsctl.py --json doctor        # 看 verdict / candidates / next_steps
python hsctl.py env --discover       # 列出本机所有候选解释器 + 各自缺什么
python hsctl.py env --pin "<选中的 python.exe 路径>"   # 校验并**保存**到本机 log/env.json
python hsctl.py serve stop           # 走自停通道（不需要提权）
python hsctl.py serve start --via-task
python hsctl.py --json doctor        # 复验：env_ok + console.is_admin=true
```

**为什么这样就够**：

- `log/env.json` 是**本机专属**配置（`log/` 不入库），保存一次之后：
  * `hsctl` 用**任何** python 启动都会自动重入到它（见下），agent 不必再找环境；
  * `serve install-task` 生成的启动器也以它为准 —— 修掉了"计划任务把生成它时的
    那个错误 Python 硬编码进去"这个老坑。
  * 仓库里只有占位符模板 [`env.example.json`](env.example.json)，不含任何机器路径。
- `hsctl` 入口会校验当前解释器（3.12 + 依赖齐全，只 `find_spec` 不 import）；
  不合格但本机有合格的就 `os.execv` 重入过去并顺手保存，一个都不合格才退出码 **4**
  并打印"怎么修"（`HSCTL_NO_PROXY=1` 可关闭这个代理，`HSCTL_REEXEC=1` 防死循环）。

**换机器 / 换 agent 的完整清单**（`doctor` 会把这几条写成 `next_steps`）：

1. `python hsctl.py --json doctor`
2. 按它给的 `hsctl env --pin "<路径>"` 保存本机解释器（多个候选都合格时**问用户一次**选哪个）
3. 再 `doctor` 复验到 `env_ok`
4. `serve start --via-task`（任务没注册就先 `serve install-task`，需要管理员跑一次）

**只有这一种情况还需要旧办法**：`log/env.json` 里固定的解释器被卸载/改名，
且本机确实没有别的合格环境 —— 那就按 `doctor` 的 `env_no_valid_interpreter` 分支
建环境（conda / venv）再 `env --pin`。

## 9. MCP（把 hsctl 当 MCP 工具用）

如果客户端支持 MCP，可以不起命令行、直接调工具 —— `mcp_server.py` 把 hsctl 的
常用子命令包成了 MCP tools（stdio）。**它只是 hsctl 的薄封装**，语义、退出码、
`--json` 输出与命令行完全一致；`doctor` / `status` / `screenshot` 等工具返回的
就是 hsctl 的 JSON。

客户端配置（以 `D:\miniconda3\envs\HTL\python.exe` 为例）：

```json
{
  "mcpServers": {
    "hsctl": {
      "command": "D:\\miniconda3\\envs\\HTL\\python.exe",
      "args": ["<仓库路径>\\mcp_server.py"]
    }
  }
}
```

- `command` 的解释器必须装了 `mcp`（见 `requirements.txt`）；跑命令时 hsctl 会
  自己再切到本机固定的 HTL 环境。
- 工具：`doctor` `selfcheck` `status` `log` `ui` `screenshot` `start` `stop`
  `resume` `prepare` `cancel` `enter_battle` `mode` `deck` `calibrate`
  `restart_hearthstone`（需 `confirm=true`）`config_show/set` `delays_show/set`，
  外加万能兜底 `hsctl(argv=[...])`（任意子命令，自动加 `--json`）。
- `screenshot` 直接把 PNG 作为图片内容返回，不用再读文件。
- 没装 MCP 的客户端照常用命令行；两者行为一致，不用二选一。

## 10. 参考

- **通用 agent 行为约定（跨项目）**：[`AGENTS.md`](AGENTS.md)
- 完整命令参考：`docs/CLI.md`
- 解释器配置模板：[`env.example.json`](env.example.json)（实际生效的是本机 `log/env.json`）
- 脚本本身的用法/安装：`README.md`
- 控制台实现（HTTP 接口定义）：`web_ui.py` 的 `Handler.do_GET` / `do_POST`
