# R18K 盒子推荐失效兜底（通用本地策略）

## Task ID

R18K

## Goal

把 MetaStone / Silverfish / Jev-hearthstone 三个公开实现里**可通用**的评分思路，
按本仓库现有本地策略的写法落成 `ai/通用/`；并在盒子 AI 推荐失效时用本地通用
策略接管一步，盒子恢复后自动切回：

- 回合内：连续 `fail_streak` 次读不到可执行推荐，或连续 `stall_seconds` 秒没有
  成功执行动作 → 用 `ai/通用` 搜索并执行一步。
- 换牌：盒子换牌 OCR 连续失败 `mulligan_fail_streak` 次 → 用 `ai/通用` 的
  `choose()` 提交换牌，避免卡在换牌界面。
- 网页「基础配置」新增开关/策略/阈值；默认关闭，保存后下一局生效。

## Scope

- 新增 `ai/通用/通用.py`（`Tongyong`：`choose` / `rankStaticState` /
  `rankActions` / `rankState`，`RANKS_FROM_SEQUENCE_START=True`）与 `通用.txt`。
- `config.py`：`DEFAULT_BOX_FALLBACK`、`normalize_box_fallback`、
  `box_fallback_settings`、`save_box_fallback_setting`。
- `src/hslegend/adapters/legacy/strategy_planner.py`：`strategy_key` 显式策略 +
  `use_strategy` 上下文；策略不可用抛 `fallback_strategy_unavailable`。
- `src/hslegend/application/recommendation_flow.py`：`fallback_*` 参数、兜底状态机
  `_maybe_box_fallback`、`_run_local_step(planner=...)`、pending 动作在盒子 OCR
  失败时交给本地确认。
- `src/flow/mulligan_flow.py`：`MulliganFallbackTracker`、`mark_panel_ready()`。
- `FSM_action.py`：每局构建 `_box_fallback`、传兜底参数、换牌兜底、
  `[SYS] 盒子兜底` / `[兜底]` 日志。
- `web_ui.py`：`POST /api/box_fallback`，status 新增 `box_fallback`。
- `web/index.html`：开关 / 兜底策略下拉 / 无进展秒数 / 连续失败次数，保存链接入。
- 新增测试：`test_generic_strategy`、`test_box_fallback_flow`、
  `test_box_fallback_fsm`、`test_config_box_fallback`、`test_mulligan_fallback`；
  更新 `test_strategy_registry` / `test_strategy_search_fixtures` /
  `test_strategy_flow_integration` / `test_web_strategy_api`。

## Non-Goals

- 不改现有三套卡组策略（任务牧/动物园术/强化学习动物园术）的评分与行为。
- 不搬 Silverfish 卡牌专属表、不实现完整卡牌效果引擎、不猜对手手牌/牌库顺序。
- 不新写 beam/MCTS；复用现有 `Search`。
- 不改盒子 OCR/ROI/解析/点击坐标/延时默认值；不改 `manual_controller.py`、
  `click.py`。
- 默认不开启兜底（`DEFAULT_BOX_FALLBACK["enabled"]=False`）。

## Allowed Files

- `ai/通用/**`
- `config.py`
- `FSM_action.py`
- `src/flow/mulligan_flow.py`
- `src/hslegend/application/recommendation_flow.py`
- `src/hslegend/adapters/legacy/strategy_planner.py`
- `web_ui.py`
- `web/index.html`
- `tests/**`
- `docs/tasks/R18K-*.md`

## Forbidden Files

- `cards.json`
- `pyproject.toml`
- `uv.lock`
- `manual_controller.py`
- `click.py`
- `src/ocr/**`
- `src/hslegend/domain/**`
- `src/hslegend/bootstrap/**`
- `src/hslegend/entrypoints/**`
- 现有 `ai/任务牧/**`、`ai/动物园术/**`、`ai/强化学习动物园术/**`

## Acceptance

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run python -m compileall -q src tests tools ai
uv run python tools/check_architecture.py
uv run python -m unittest tests.test_generic_strategy tests.test_box_fallback_flow tests.test_box_fallback_fsm tests.test_config_box_fallback tests.test_mulligan_fallback tests.test_strategy_registry tests.test_strategy_search_fixtures tests.test_strategy_flow_integration tests.test_web_strategy_api -v
```

- 通用策略可被注册表发现，`rankStaticState` 对身材/关键字/威胁/低血/斩杀有反应，
  `rankActions` 只惩罚溢出伤害，`choose` 换掉高费且至少留一张。
- 兜底：连续失败/超时触发、盒子恢复切回、pending/对手回合/停止不触发、
  本地连续失败 3 次后本回合放弃。
- `FSM_action.initialize_recommendation_automation()` 在开启时构建快照并把
  `fallback_*` 传给 `RecommendationFlow`。
- 网页 `/api/box_fallback` 可往返保存，status 回显；运行中默认拒绝，`hot` 放行。

## Risk And Rollback

- 风险：默认 8s/6 次阈值在盒子极慢时可能短暂接管；通用策略比卡组策略弱；
  `RANKS_FROM_SEQUENCE_START` 每节点重放动作序列会增加搜索开销（受
  `search_time_limit` / `max_search` 约束）。
- 回滚：网页取消勾选并保存；删除 `ai/通用/` 即运行时禁用兜底；代码级把
  `RecommendationFlow` 的 fallback 参数保持默认（不传）即可恢复原盒子路径。

## Git Requirement

One commit（与常驻/日志排版等并行工作线分开提交）：

```text
feat(strategy): add generic deck strategy and box-recommendation fallback
```
