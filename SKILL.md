---
name: hslegend-arriver
description: 用 hsctl 命令行实时控制炉石传说自动化脚本（HSLegendArriver）。当用户要求「看脚本状态 / 要一张实时画面、截图、看看现在屏幕上是什么 / 认出现在在哪个界面 / 一键进入对战（点传统对战、选卡组、开始匹配）/ 从0冷启动（电脑刚开机、炉石和盒子都没开）/ 开始或停止对战 / 改延时或活人感等参数 / 追日志 / 定时 / 校准 / 重启炉石 / 用手机或 agent 远程指挥脚本」时使用。命令是 python hsctl.py <子命令>，输出支持 --json，退出码可判断成败；提权只能靠预注册的计划任务，agent 自己无法提权。用户要实时画面时直接用 hsctl screenshot 抓图返回，不要多话。界面识别只认文字（认不出就只点画面中心，绝不盲点按钮）。
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
   最多一行说明（档位 + 是否退化为整屏 + 时间）。详见 §7「实时画面」。
   只有图**不能**反映用户想问的东西时（例如他没在跑、图退化成桌面）才追加解释。
   这是铁律 1「先探活」的唯一例外 —— 抓图本身只读且会自查，连不上会返回退出码 3。

## 1. 前置检查（每次会话开头）

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

```bash
# 观测
python hsctl.py status [--watch 秒]
python hsctl.py log [-n 50] [-f] [--interval 秒]
python hsctl.py selfcheck [--refresh]
python hsctl.py regions                 # 截屏画区域框，只截图不点击
python hsctl.py screenshot [--kind client|full|roi|boxes] [--max-width 1280]
                                        # 抓一帧存成 PNG，返回路径 → 你直接读那张图"看画面"
python hsctl.py ui                      # 现在在哪个界面（主界面/选择套牌）+ 匹配分
python hsctl.py ui --frame              # 顺带存一帧图并打印路径（核对它看到了什么）
python hsctl.py ui --learn              # 站到对应界面时重裁识别模板（皮肤/补丁改了字之后）

# 进入对战（认界面 → 点「传统对战」→ 选卡组 → 可选点「开始」）
python hsctl.py enter-battle --deck 8   # 默认停在选择套牌界面（不点「开始」）
python hsctl.py enter-battle --deck 8 --start   # 连「开始」一起点（等于开始排队）
python hsctl.py deck-click 8            # 快速选卡组：直接点第 8 格（秒级，不走状态机）
                                        # 前提：当前已在选牌界面；那一格不像卡组就不点
python hsctl.py deck                    # 看自动选择的卡组序号
python hsctl.py deck 8                  # 每局自动选第 8 副；hsctl deck --clear 关掉

# 待做命令队列：一次提交多条，控制台后台按顺序执行（断线不影响，可查可取消）
python hsctl.py queue run "stop mode=after_game" "wait-stopped" "deck 1" "start"
python hsctl.py queue status            # 看每步进度（pending/running/ok/failed/skipped）
python hsctl.py queue cancel            # 取消（当前步跑完后停止）
#   支持的动作：stop [mode=after_game] / wait-stopped [timeout=秒] / deck 序号 /
#   deck-click 序号 / prepare / start [keep_score=1] / delay 秒
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
python hsctl.py concede set --enabled --threshold 12 --rounds 4
python hsctl.py rank-stop set --enabled --mode legend_number --legend-number 500
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
python hsctl.py serve stop                       # 先停自动化再结束进程
```

所有命令都接受 `--json`（放命令前或后都行）、`--port`、`--host`、`--timeout`。

## 5. 改参数前必须知道的生效时机

对局中改延时，**不是全部都立刻生效**：

| 生效时机 | 字段 |
| --- | --- |
| 立即生效 | `mulligan_ready_delay_seconds`、`mulligan_retry_delay_seconds`、`pre_action_delay_seconds`、`first_turn_per_card_delay_seconds`、`discover_target_delay_seconds` |
| 下次「开始对战」生效 | `mulligan_post_ocr_delay_seconds`、`post_action_delay_seconds`、`draw_extra_delay_per_card_seconds` |
| **需重启脚本进程** | `ocr_preprocess_scale` |

`hsctl delays set` 的回话里有 `effects` 字段直接告诉你每项的实际时机 —— **以此为准**，
不要凭本表猜。活人感 / 存活检测 / 自动投降 / 上分停止是每次读盘，改完立即生效。

**卡组序号**（`hsctl deck 8`）也是每次开局前重读 —— 改完**下一局**生效，不用重启脚本。

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
`schtasks /run` 触发它。

注意：

- 注册任务**必须**管理员权限，你做不到 —— 只能把命令交给用户。
- 用完后如需清理：`python hsctl.py serve uninstall-task`。这个任务等于一个提权入口
  （任何能跑 `schtasks /run` 的本机进程都能触发），所以不要到处宣传任务名。
- **不要**建议用户关闭 UAC。

### 8.1 控制台跑在错误的 Python 上（自检"全部依赖 fail"）

**症状**：`status` 里 `selfcheck.failed` 等于全部依赖数（pywin32/cv2/numpy/PIL/paddle…
一整排 `ModuleNotFoundError`），且 `selfcheck.executable` 指向的解释器版本不对
（本项目要求 3.12，3.13+ 装不上 OCR 的轮子）。

**根因（通用，任何机器都可能踩）**：`serve install-task` 生成的计划任务 cmd
（`log\hs_serve_elevated.cmd`）把"当时跑 install-task 的那个 python"**硬编码**了进去。
如果那次是别的工具自带的 Python（版本不符、没装依赖），之后每次 `serve start --via-task`
都会用错解释器。

**修法（不需要重装任何包，也不需要用户手动提权）**：

1. 先确认机器上存在一个**正确的环境**：找一个 Python 3.12 的环境，
   逐个 import 依赖验证（`win32gui/cv2/numpy/PIL/paddleocr/paddle/psutil/keyboard/pynput/requests/click`）。
   常见位置：conda 的项目 env、venv。都验证通过再往下走。
2. **直接编辑** `log\hs_serve_elevated.cmd`，把里面那行 python 绝对路径换成
   正确环境的 python.exe（这是个普通文本文件，agent 可以直接改；
   `install-task` 只是用"当前 python"重新生成它，效果等价）。
3. **旧控制台要先停**：它是提权起的，非提权进程 `serve stop` 杀不掉（Access denied）。
   通用技巧——**借计划任务自己来停**：
   - 把 cmd 临时改成 `"<正确python>" hsctl.py serve stop`；
   - 触发任务（见下面的 shell 注意事项），等几秒，确认 `status` 连不上（退出码 3）；
   - 把 cmd 改回正常的 `serve start --wait 120`，再触发一次任务 → 控制台以正确解释器提权重启。
   **血泪教训**：①用 `.bak` 备份/恢复轮换 cmd 极易把 stop 版残留成"当前版"，
   下次重启时控制台被悄悄降级成非提权（表现为 admin 变 false）——每次改完
   **必须 cat 核对文件内容**，确认最后一行是 `serve start --wait 120`；
   ②shell 的 `printf` 会吃掉 `\U`/`\e` 转义写坏 cmd 路径，改这个文件一律用
   编辑工具整文件写入，不要用 shell 字符串拼接；
   ③如果自动化正卡在"对局结算"收尾，`serve stop` 会等它退完，看似没反应 ——
   端口迟迟不释放时改用提权 `taskkill /F /PID <监听8765的pid>`（netstat -ano 查）。
4. 重启后立刻 `status` 核对：`selfcheck.ok: true` 且 `is_admin: true`，再继续 §2 编排。

**shell 注意事项（通用）**：在 Git Bash / POSIX shell 里，`schtasks /run /tn X`
的 `/run` 会被当成路径改写成 `D:/.../run` 导致报错 —— 要写成 `schtasks //run //tn X`
（双斜杠防改写）。PowerShell/cmd 里则用单斜杠。触发成功会打印一条乱码或本地语言的
"成功"消息，以退出码为准。

**预防**：换机器、换 Python、重装环境后，第一次 `serve start --via-task` 之前，
先跑一次 `status` 看 `selfcheck`，别直接开打。

## 9. 参考

- 完整命令参考：`docs/CLI.md`
- 脚本本身的用法/安装：`README.md`
- 控制台实现（HTTP 接口定义）：`web_ui.py` 的 `Handler.do_GET` / `do_POST`
