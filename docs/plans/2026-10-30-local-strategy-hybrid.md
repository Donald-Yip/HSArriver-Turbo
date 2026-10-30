# 2026-10-30 本地策略 + AI 盒子混合玩法（R18）

> 任务书见 [`docs/tasks/R18-local-strategy-hybrid.md`](../tasks/R18-local-strategy-hybrid.md)。
> 来源：`Joint0.1.zip`（旧基线 + `ai/` + `search/` + 标注台）。
> 目标：把本地卡组策略作为**可选决策源**接进当前 R17 分层仓库，默认仍是盒子AI。

## 1. 目标

- 网页「基础配置 → 策略选择」：盒子AI（默认）/ 任务牧 / 动物园术 / 强化学习动物园术。
- 本地策略：`search.Search` 在 `StrategyState` 上搜索整回合最优动作，交给现有
  适配器/控制器执行；换牌由卡组策略 `choose()` 决定。
- 联合模式：盒子首步 + 本地补齐整回合，与本地最优同口径评分；本地高 20% 才用本地；
  每局结束写一行 `ai.txt`。
- 复用 R17 的 pending 确认修复；搜索可被 Ctrl+Q 中断；有 `max_search` 与
  `search_time_limit_seconds` 预算。

## 2. 集成架构

```
FSM_action.py（组合根）
  └─ RecommendationFlow(box_ai=uses_box_ai(), planner=LegacyStrategyPlanner())
        ├─ box_ai=True : 现有 OCR 路径（逐字保留）
        ├─ box_ai=False: planner.plan(state, cost) → adapt/validate/execute
        └─ joint       : planner.joint_plan(state, local_actions, local_score, box_action)
application 只依赖 StrategyPlannerPort；adapters/legacy 才 import search/ai。
```

## 3. 范围

**In**：`ai/`（三套卡组 + card_rules + sim_cards + registry）、`search/`、
config 策略/joint/max_search、`StrategyPlannerPort` + legacy 规划适配器、
flow 本地/联合分支、`MulliganFlow.run_local`、FSM 接线、web UI、测试/文档。

**Out**：标注台（`src/annotation/**`、`web/annotation.html`）、`ml/` RL 值网络、
`get_action.py`、`card/` 空壳、`脏牧/龟法` 新卡组（后续按 `ai/新建卡组说明.txt` 加）。

## 4. 关键兼容性决策

1. `ActionKind.USE_MINION_ABILITY` 独立枚举，适配器路由到现有 `_adapt_use_ability`；
   能力下标优先用 `choice_card_id` 反查 `rules/card_rules.json` 的 `ability_panel`。
2. `ProposedAction` 增加带默认值的 `cost/choice_card_id/entity_id/target_entity_id`，
   盒子路径不填，序列化兼容。
3. 预备/交易：`_adapt_trade_card` 按指令/卡面文本选 `tempt_mana_spent`，否则
   `hand_card_left`，避免“牌回手牌”pending 死等。
4. 本地/盒子共用 pending 3s 超时重试；本地 `_verify_result(check_recommendation=False)`。
5. 搜索传 `stopped` 与时间预算；超时返回当前最优，Ctrl+Q 秒退。
6. 联合模式用 `search/serialize.py` 的最小序列化；统计只走内存。
7. 默认 `strategy=hsbox`、`joint=false`、`max_search=1500`。

## 5. 工作流与写作用域

- Lead：任务书/计划/领域模型/config/`search` 适配/规划适配器/flow/FSM/集成/门禁/真机。
- W1：`tests/test_strategy_registry.py`、`tests/test_strategy_search_fixtures.py`、
  `tests/fixtures/strategy/**`。
- W2：`web_ui.py`、`web/index.html`、`tests/test_config_strategy.py`、
  `tests/test_web_strategy_api.py`。
- W3：`src/flow/mulligan_flow.py`、`tests/test_mulligan_local.py`。

## 6. 验收

```powershell
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_card_rules.py --check
uv run python tools/check_task_scope.py --task R18
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
uv run python tools/run_headless_tests.py --opt-in
```

- 默认盒子：现有 1602 项 headless 测试不变、全过。
- 本地卡组：日志 `[推荐] ... 最佳评分`，不依赖推荐面板 OCR；本地留牌生效。
- 联合模式：`[联合]` 行 + `ai.txt` 每局一行。
- 黄金样例：动物园术 turn4（2 费、手牌 6 号 TIME_025）首步 = `play_card TIME_025`。
- Ctrl+Q 搜索中 <1s 退出；热搜索决策 p95 < 2s（首次冷启动 ~2s，墙钟预算默认 5s）。

## 7. 回滚

`strategy` 默认 `hsbox`；删除 `ui_config.json` 的 `strategy` 键即回默认；本地栈未选中时
完全不参与运行；R18 提交可整体 revert，无数据/结构迁移。
