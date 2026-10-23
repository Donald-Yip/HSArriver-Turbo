# Git 工作流（本项目）

> 目标：提交历史能一眼看懂「每个功能改了什么、为什么改」，并且**不把本机配置、
> 日志、截图、卡牌数据以外的大文件混进仓库**。
> 通用 agent 规则见根目录 `AGENTS.md` §8；这里是本项目的具体做法。

---

## 1. 仓库与分支

```powershell
git status --short
git branch --show-current      # 正常是 main
git remote -v                  # 当前配置：mine=Donald-Yip/HSArriver-Turbo，origin=magicstrangelzb/HearthstoneLegendArriver
```

- 默认在本地 `main` 提交；**不要擅自 push**、不要 `git push --force`、不要改写远端历史。
- 需要推送/合并/开分支时先问用户。
- 当前分支有未提交改动时，先确认哪些属于本次功能，**不要 `git add -A`**。

---

## 2. 一条铁律：一个功能一个提交

- 一次提交只做**一个功能/一个修复**（可以包含它的代码 + 测试 + 文档）。
- 不要把不相关的格式化、其它 bug 修复、临时调试混进同一个提交。
- 跨多个功能的公共文件（如 `FSM_action.py`、`README.md`）按功能**分次落盘**：
  做完一个功能 → 跑测试 → 只 stage 这个功能的文件/hunk → 提交 → 再做下一个。
- 提交信息用祈使句，格式：

  ```text
  <type>(<scope>): <为什么>
  ```

  常用 type：`fix` / `feat` / `perf` / `ui` / `docs` / `test` / `chore`。
  scope 用中文或模块名，例如 `fix(控分): 在换牌阶段结束后再认输`。
  正文可写「改了什么、怎么验证的」，但标题先说**为什么**。

---

## 3. 提交前检查

```powershell
git status --short
git diff --stat
git diff --cached --stat        # stage 后确认只包含本次功能
```

- 先跑对应测试（项目测试用 `unittest` 可直接跑，装了 pytest 也可以）：

  ```powershell
  # 本机解释器以 log/env.json 的 interpreter 为准，例如：
  D:\miniconda3\envs\HTL\python.exe -m unittest -q tests.test_xxx
  ```

- 改动涉及界面/校准/OCR 区域时，额外跑一次离屏渲染或 `python hsctl.py --json doctor`。
- 改动涉及启动/重启/点击时，确认没有把真实鼠标/键盘操作带进单测（测试必须可注入桩）。
- 改动涉及 `version.py` / `CHANGELOG.md` 时，两者顶部日期必须一致（`tests/test_version.py` 会检查）。

---

## 4. 哪些能提交、哪些不能

**不要提交（本机/产物/临时文件）**

- `ui_config.json`、`log/env.json`、`log/`、`logs/`、`tmp_screens/`、`*.log`、`*.txt`（`requirements.txt` 除外）
- 截图/调试图：`out.jpg`、`snapshot_live.jpg`、`tmp*/`、`*.png`（项目正式文档图除外）
- `__pycache__/`、`.pytest_cache/`、`pytest-cache-files-*/`、`.tmp-tests/`
- 本机计划任务、绝对路径、账号/令牌

**可以提交**

- 源码、测试、文档（README / docs / SKILL / AGENTS）
- `cards.json`：核心卡牌数据，**允许入库**；但它当前可能只是本机更新，提交前确认改动
  是否属于本次功能。若只是数据更新，单独提交：`chore(data): 更新 cards.json`。
- `assets/` 下的 UI 模板、正式文档图片。

> 提交前一定看 `git status --short`：`cards.json` 经常处于「已修改」状态，
> 不属于本次功能就**不要 stage**。

---

## 5. 版本号与 CHANGELOG

- `version.py` 的 `VERSION` 是唯一版本源，格式 `YYYY.MM.DD`，版本号 = 发布日期。
- 只有**发版**才改 `version.py`，同时把 `CHANGELOG.md` 顶部加一行同日期记录；
  `tests/test_version.py` 会检查两者一致。
- 普通 bugfix / 文档改动不要顺手改版本号，避免「每天都是新版本」。

---

## 6. 常用操作

```powershell
# 只 stage 本次文件（不要 git add -A）
git add FSM_action.py tests/test_rank_stop.py docs/CLI.md

# 只看某个文件的 staged 改动
git diff --cached -- FSM_action.py

# 撤销 stage（不改工作区）
git restore --staged FSM_action.py

# 撤销工作区某个文件的改动（危险：会丢未保存修改，先确认）
git restore FSM_action.py

# 最近几条提交
git log --oneline -10

# 修正刚提交漏掉的文件（仅限尚未推送的自己的提交）
git add get_screen.py
git commit --amend --no-edit
```

> `git reset --hard`、`git clean -fdx` 会清掉用户本机配置/未跟踪文件，**不要用**。

---

## 7. 本次工作的提交顺序（示例）

按功能拆分提交，每个提交先测试再落库：

1. `fix(卡组选择): 停止每局自动点卡组，避免覆盖手选`
2. `fix(控分): 在换牌阶段结束后再认输`
3. `fix(反馈): 出错截图只保留回合内`
4. `perf(OCR): 换牌推荐与回合推荐使用独立截图区域`
5. `fix(重启): 战网拉起炉石后持续把游戏窗口置前`
6. `ui(校准): 加宽目标条并重排标签与尺寸列`
7. `docs(git): 增加项目 Git 工作流说明`

每个提交都应满足：`git status` 干净（除本机忽略文件）、对应测试通过、没有 push。
