"""Priority-queue search over simple minion attacks and minion plays.

本文件是搜索模块的唯一对外入口，内外两套数据结构在此分界：

    * 对外（执行层，如 src/flow/recommendation_flow.py）：
      Search.search(StrategyState, cost) -> (list[ProposedAction], score)
      参数与返回值仍是执行层的旧协议，调用方一行都不用改。
    * 对内（搜索过程）：aiSearch / getActions / doAction / rankState 全部只吃
      精简的 AiState / AiAction，搜索逻辑不再背 StrategyState / ProposedAction
      那些搜索用不到的字段（OCR 置信度、帧号、可选目标表、坟场……）。

也就是：StrategyState / ProposedAction 只出现在进出口两个地方——
AiState.from_strategy_state()（进）和 Search._to_proposed()（出），中间过程干净。
"""

import heapq
import re
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import count
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from ai.card_rules import (
    apply_board_ability, apply_deathrattle, apply_deterministic_spell,
    apply_hand_cost_effects, auto_board_ability, board_ability_options,
    board_ability_specs, buffs_board_only, card_mana_cost, can_minion_attack,
    coin_mana_gain, drawn_card_mana_reserve, must_be_first_action,
    summon_battlecry_copy, summon_deathrattle_minions, surviving_minions)
from ai.deck_strategy import deck_strategy
from src.game_state.choose_one import (
    choose_one_option_card_ids, is_choose_one_card)
from src.game_state.recommendation_adapter import ordered_board
from src.recommendation_models import ActionKind, ProposedAction, SlotRef
from strategy import StrategyState

from .ai_action import AiAction, AiSlot
from .ai_state import AiState

try:
    # 星舰识别依赖本地 cards.json；缺失时退回单卡硬编码判断（见 starship.py）。
    from src.game_state.starship import is_starship_card
except ImportError:  # pragma: no cover - 只有裁剪掉该模块时才会走到
    is_starship_card = None

# 节点"还没评过分"的哨兵：与旧版 search.py 的 UNRANKED 同值，定义收敛到 AiState。
UNRANKED = AiState.UNRANKED
# 搜索预算（评分次数上限）。2026-10-25 从 500 提到 1000：实测一个高分分支
# 能独占几百次预算，把「首步不涨分的铺垫动作」整条饿死（见 aiSearch 注释）。
MAX_SEARCH = 1500


def _apply_max_search() -> None:
    """把 Web 上「最大搜索深度」的设置同步到 MAX_SEARCH（每次搜索读一次）。

    ui_config.json 里的 max_search 由网页输入框写入；读不到/非法时保持默认。
    ml/arena 直接调 Search().aiSearch 自行设预算，不走这里，互不影响。
    """
    global MAX_SEARCH
    try:
        from config import max_search_setting
        MAX_SEARCH = max_search_setting()
    except Exception:
        pass


# “本回合可斩杀”分支的优先级地板（用户 2026-10-28）：斩杀分只会在走完整条
# 打脸序列后才结算，中途节点的分很低（如先花 5 费打直伤），会被正常优先级
# 排到最后、被 MAX_SEARCH 饿死。策略提供 lethal_ceiling 钩子（还能打出的
# 最大脸伤）时，凡“够斩杀”的节点直接顶到这个优先级，永远先展开。
LETHAL_POTENTIAL_PRIORITY = 900.0
# 斩杀线内部的优先级：深度优先（更深的节点先出堆，一条道走到底），同深度
# 再按常规分排序（谁分高谁先展开），避免斩杀分支之间因入队先后而任意排序。
LETHAL_SCORE_SCALE = 1000.0


def _lethal_priority(depth: int, score: float) -> float:
    capped = min(max(float(score or 0.0), 0.0), 99.0)
    return LETHAL_POTENTIAL_PRIORITY + depth + capped / LETHAL_SCORE_SCALE


# 解场动作的入堆优先级加分（用户 2026-10-29）：解场后场面静态分常常先跌
# （自己的随从掉血、甚至换掉），纯按分数排序会被铺场/抽牌分支挤到队尾，
# 走出“解场 → 收头”这条线时预算已经耗尽（10:0 的「撞复生怪 → 污亵」）。
# 这里按“实际打掉敌方场面多少”算一个加分，只抬高展开顺序，不进动作分。
CLEAR_DAMAGE_WEIGHT = 2.0        # 每 1 点打在敌方随从身上的伤害
CLEAR_TAUNT_BONUS = 20.0         # 解掉/打残一个嘲讽
CLEAR_HIGH_ATTACK_THRESHOLD = 4  # 高攻门槛：攻 >= 4 的敌方随从
CLEAR_HIGH_ATTACK_BONUS = 15.0   # 解掉一个高攻随从
CLEAR_HIGH_ATTACK_STEP = 3.0     # 攻每高出 4 点再加的分
CLEAR_PRIORITY_CAP = 55.0        # 单个动作的封顶，防止一张 AOE 顶到斩杀线附近


def _enemy_minion_changes(before_state, after_state) -> list[tuple[float, bool, int]]:
    """这次动作打在敌方随从身上的明细：[(掉血量, 是否嘲讽, 攻击力)]。

    用 entity_id 对齐动作前后的同一只随从：动作后已离场的按原剩余生命算，
    复生复活（如 2/5 变 2/1）只算 4 点，不会被误判成“没解场”。
    只统计掉过血的随从，打脸不计。
    """
    before = []
    for minion in getattr(before_state, "oppo_minions", ()) or ():
        key = getattr(minion, "entity_id", None) or id(minion)
        before.append((key, max(0, getattr(minion, "health", 0) or 0),
                       bool(getattr(minion, "taunt", 0)),
                       max(0, getattr(minion, "attack", 0) or 0)))
    if not before:
        return []
    remaining = {}
    for minion in getattr(after_state, "oppo_minions", ()) or ():
        key = getattr(minion, "entity_id", None) or id(minion)
        remaining[key] = max(0, getattr(minion, "health", 0) or 0)
    changes = []
    for key, health, taunt, attack in before:
        lost = health - remaining.get(key, 0)
        if lost > 0:
            changes.append((float(lost), taunt, attack))
    return changes


def _clear_priority(before_state, after_state) -> float:
    """解场动作的入堆优先级加分（只影响展开顺序，不进最终得分）。"""
    boost = 0.0
    for lost, taunt, attack in _enemy_minion_changes(before_state, after_state):
        boost += lost * CLEAR_DAMAGE_WEIGHT
        if taunt:
            boost += CLEAR_TAUNT_BONUS
        if attack >= CLEAR_HIGH_ATTACK_THRESHOLD:
            boost += (CLEAR_HIGH_ATTACK_BONUS
                      + (attack - CLEAR_HIGH_ATTACK_THRESHOLD)
                      * CLEAR_HIGH_ATTACK_STEP)
    return min(boost, CLEAR_PRIORITY_CAP)


# 相同场面的去重开关（用户 2026-10-26 要求）：不同动作顺序走到同一个可见
# 场面时，只保留优先级最高的一条继续展开。关掉时行为与旧版完全一致，
# 便于对比搜索节点数。
DEDUPE_STATES = True

# 敌方回合模拟开关（用户 2026-10-26 要求）：只对“真正结束回合”的完整计划，
# 模拟敌方随从一轮最佳攻击；关闭时完全退回旧评分。
SIMULATE_ENEMY = True
# 敌方枚举的攻击步数上限，防止极复杂场面把预算吃光（攻击者×目标 的指数枚举）。
ENEMY_SEARCH_LIMIT = 100
# 我方英雄被斩杀时该分支的最终分（绝对值，覆盖静态分与动作分）。
ENEMY_LETHAL_SCORE = -1000.0

# 验证器专用：在当前请求上下文里临时指定卡组策略。ContextVar 按线程/上下文
# 隔离，不会影响同一进程里正在打牌的自动化线程。
_STRATEGY_OVERRIDE: ContextVar = ContextVar(
    "hs_ai_strategy_override", default=None)


def current_strategy():
    """当前搜索应使用的卡组策略（默认读网页配置，验证器可临时指定）。"""
    override = _STRATEGY_OVERRIDE.get()
    return override if override is not None else deck_strategy()


@contextmanager
def use_strategy(strategy):
    """本上下文内让搜索/评分使用指定策略实例（退出时自动还原）。"""
    token = _STRATEGY_OVERRIDE.set(strategy)
    try:
        yield strategy
    finally:
        _STRATEGY_OVERRIDE.reset(token)


def _value_context():
    """搜索静态分的学习值开关（强化学习，见 ml/docs/RL_DESIGN.md §12.3）。

    三道闸门，任何一道没开都完全退回旧行为：
      1. 没设环境变量 `HSAI_VALUE_CKPT` → `active()` 直接给空上下文；
      2. 当前卡组模块里没有 `use_static_evaluator`（纯规则卡组，如 ai/动物园术）
         → 解析结果为 None，同样是空上下文；
      3. `HSAI_VALUE_LAMBDA=1` → 原样返回规则分。

    放在函数里延迟导入：`ml.*` 依赖 `search.*`（自博弈策略建在 doAction 上），
    模块级反着 import 会形成循环。导入失败一律当"未启用"——一个可选的强化
    学习功能不能把主流程带崩。
    """
    try:
        from ml.runtime.value_hook import active

        return active()
    except Exception:                          # noqa: BLE001
        from contextlib import nullcontext

        return nullcontext()


def rankState(state: AiState, actions: list[AiAction]) -> float:
    #关键函数，评估一个场面的得分，分数越高越好
    #目前版本较简单，未来要基于不同卡组，实现不同的rankState
    #参数里增加actions，是因为有些时候相同的场面，动作顺序是有讲究的，比如敌方有个防死的奥秘，那攻击的顺序应该优先打到正数最低的血量
    #可以通过actions的顺序，赋分体现顺序差异
    """Evaluate both boards, heroes, and hands using the documented weights."""
    # 交给"当前卡组策略"评分（网页「基础配置 → 策略选择」决定用哪一个）：
    #   ai/<卡组文件夹>/<不含下划线的>.py（文件夹名即下拉框选项名）
    # 每个卡组一个文件夹，核心 py 只实现 choose / rankState，搜索流程一行都不用改。
    # 传进去的是 AiState / AiAction，策略只读它们与 StrategyState 同名的字段
    # （my_minions / my_hero / my_total_mana / action.action / action.card.name …）。
    strategy = current_strategy()
    if strategy is None:
        # 选的是"盒子AI"（本地搜索本不该被调用），或者策略文件缺失/配置里名字
        # 写错：这里不猜分数，返回 0，让搜索退化成 END_TURN 兜底，而不是把异常
        # 抛到主循环里。
        return 0.0
    return strategy.rankState(state, actions)


def _search_lethal_action(state, action) -> bool:
    """当前策略是否判定“这一个动作自己就能斩杀”。

    策略在评分里不再给单卡斩杀额外加分；搜索一旦发现这种动作，就直接采用
    这条线并结束整个搜索，不再和其它分支比分数（用户 2026-10-25 要求：
    搜到能斩杀就退出整个搜索）。只认“这一下直接打死”，不认还差一步才凑够
    的组合斩杀——那种情况人类会先把伤害打出去，把收尾牌留在最后
    （2026-10-25 125102 t012-s0/s1 先打脸、s2 才污亵收尾）。
    策略没有提供 lethal_action 时不启用（其它卡组行为不变）。
    """
    strategy = current_strategy()
    checker = getattr(strategy, "lethal_action", None)
    if not callable(checker):
        return False
    return bool(checker(state, action))


def _slot(kind, owner=None, index=None) -> AiSlot:
    """构造搜索用的位置引用（语义与 src.recommendation_models.SlotRef 一致）。"""
    return AiSlot(kind, owner, index)


def _minion_from_slot(state, side, index):
    """把 1 基场面号位换成随从实体。

    那一格是地标、号位越界、或 index 为空（英雄攻击没有场面号位）时返回 None。
    旧实现直接做 `board[index - 1]`，英雄攻击会因为 index=None 抛 TypeError 把
    整个搜索打断，这里统一收敛成 None，再由调用方决定怎么处理。
    """
    try:
        entry = ordered_board(state, side)[index - 1]
    except (IndexError, TypeError, ValueError):
        return None
    if entry.kind != "minion":
        return None
    return entry.entity


def _entity_for_slot(state, slot):
    if slot is None:
        return None
    if slot.kind == "hero":
        return getattr(state, "my_hero" if slot.owner == "friendly"
                       else "oppo_hero", None)
    if slot.kind == "hero_power":
        return getattr(state, "my_hero_power", None)
    if slot.kind == "hand":
        try:
            return state.my_hand_cards[slot.index - 1]
        except (IndexError, TypeError, AttributeError):
            return None
    if slot.kind == "board_slot":
        try:
            return ordered_board(state, slot.owner)[slot.index - 1].entity
        except (IndexError, TypeError, ValueError, AttributeError):
            return None
    return None


def action_identity_key(state, action):
    """Stable same-turn identity: action kind plus exact source/target entities."""
    source = _entity_for_slot(state, action.source)
    target = _entity_for_slot(state, action.target)
    source_id = getattr(source, "entity_id", None) or action.entity_id
    target_id = getattr(target, "entity_id", None) or action.target_entity_id
    source_key = (str(source_id) if source_id is not None else
                  _slot_identity(action.source))
    target_key = (str(target_id) if target_id is not None else
                  _slot_identity(action.target))
    # 同一实体的多个技能/抉择选项：source+target 可能完全相同（例如古神本体
    # 的愤怒与仆从），必须带上 choice_card_id 才能区分，否则一个失败会连带
    # 拉黑另一个。与 recommendation_flow._action_identity 保持同一格式。
    return (action.action.value, source_key, target_key,
            getattr(action, "choice_card_id", None) or "")


def _slot_identity(slot):
    if slot is None:
        return ""
    return f"{slot.kind}:{slot.owner or ''}:{slot.index or ''}"


def _attack_source_key(action):
    """同一只随从的多个攻击方向归为一组：键是攻击者的场面号位。

    用户 2026-10-25 要求：同一只战场随从进攻不同敌人时，只保留得分最高的
    那个方向继续搜索，其余方向不再入队，避免同一只随从的目标选择把搜索
    预算摊成多份。英雄攻击（source.kind == "hero"）没有场面号位，不参与
    归组，仍按原样各自入队。
    """
    source = action.source
    if source is None or source.kind != "board_slot":
        return None
    return (source.owner or "", source.index)


def _first_only_violation(action, actions) -> bool:
    """剪枝：只准当首步的牌（咒怨之墓）和英雄技能，出现在序列中间就跳过。

    用户 2026-10-30：只为压缩搜索规模，不改任何序列的得分——咒怨之墓换来的
    临时牌必须当回合先出，英雄技能早用晚用都只是补资源；放到序列中间既不会
    让最终分更高，又会让每条分支再复制一遍。
    """
    if not actions:
        return False
    if action.action == ActionKind.USE_HERO_POWER:
        return True
    return must_be_first_action(action.card_id)


def _face_attack_out_of_order(state, action) -> bool:
    """剪枝：打脸顺序必须是攻击力从小到大，同攻时场面从左到右。

    先低攻后高攻打脸是为了防刀盾、变羊这类“先撞谁”结算的奥秘（用户
    2026-10-30）。这里按“当前必须轮到最小的那只出手”判断：场上还有攻击力
    更小（或同攻但号位更左）且能打脸的友方随从时，本次打脸就不合法。

    采用局部判断（只看“现在轮到谁”），而不是事后校验整条序列：后者要先把
    高攻先打脸的前缀展开完才发现走不通，白白占掉搜索预算。
    """
    target = action.target
    if action.action != ActionKind.ATTACK or target is None:
        return False
    if target.kind != "hero" or (target.owner or "enemy") != "enemy":
        return False
    source = action.source
    if source is None or source.kind != "board_slot":
        return False
    current_attack = max(0, int(getattr(action.card, "attack", 0) or 0))
    current_index = int(source.index or 0)
    for slot, entry in enumerate(ordered_board(state, "friendly"), 1):
        if entry.kind != "minion" or slot == current_index:
            continue
        minion = entry.entity
        if not getattr(minion, "can_attack_minion", False):
            continue
        if not can_minion_attack(state, minion):
            continue
        if not getattr(minion, "can_beat_face", False):
            continue
        attack = max(0, int(getattr(minion, "attack", 0) or 0))
        if attack < current_attack:
            return True
        if attack == current_attack and slot < current_index:
            return True
    return False


def _buff_before_minion_violation(state, action, actions) -> bool:
    """剪枝：场上还有随从空位时，先打续连熵能、再上随从。

    熵能（以及同类的“只增益在场随从”的牌）打出去只加给已经在场的随从，空位
    还在时把随从放到它前面打出永远不差，后面再上的随从白丢这份加成。只压缩
    搜索规模，不改任何序列的得分（用户 2026-10-30）。
    """
    if not actions:
        return False
    if action.action != ActionKind.PLAY_CARD:
        return False
    if str(action.card_type or "") != "MINION":
        return False
    if not any(buffs_board_only(earlier.card_id) for earlier in actions):
        return False
    # 满场时本来就没法再上随从；还有空位才存在“先上随从再打熵能”的顺序。
    return int(getattr(state, "my_board_slot_num", 0) or 0) < 7


def _attack_before_buff_violation(state, action, actions) -> bool:
    """剪枝：场上还有随从空位时，先随从进攻、再打续连熵能。

    熵能给的是在场随从的 +1/+1，先进攻再打它，已经打过的那只白丢这份加成（打
    脸少伤害、换怪少血）。这一条和“熵能后再上随从”一起，把空位还在时的顺序
    收敛成「先上随从 → 熵能 → 再进攻」。只压缩搜索规模，不改序列得分
    （用户 2026-10-30）。
    """
    if action.action != ActionKind.PLAY_CARD or not buffs_board_only(action.card_id):
        return False
    # 满场时没有“先上随从”的余地，不适用。
    if int(getattr(state, "my_board_slot_num", 0) or 0) >= 7:
        return False
    return any(earlier.action == ActionKind.ATTACK
               and earlier.source is not None
               and earlier.source.kind == "board_slot"
               for earlier in actions)


def _pruned(state, action, actions) -> bool:
    """这些动作不评估、不入堆：不改任何一条序列的得分，只是不让它进入候选。"""
    return (_first_only_violation(action, actions)
            or _face_attack_out_of_order(state, action)
            or _buff_before_minion_violation(state, action, actions)
            or _attack_before_buff_violation(state, action, actions))


def _heal_hero(hero, amount: int) -> None:
    """给英雄回血：扣减 damage，最低到 0（即封顶最大生命+护甲）。

    吸血按“实际造成的伤害”结算；圣盾吸收时伤害为 0，由调用方传 0。
    """
    if hero is None or amount <= 0:
        return
    hero.damage = max(0, getattr(hero, "damage", 0) - amount)


# ---------------------------------------------------------------- 敌方回合模拟
def _enemy_attack_steps(state) -> list:
    """敌方下回合能出手的攻击者；风怒随从占两个攻击位。

    忽略 exhausted：敌方回合开始时会刷新。冻结、休眠、无法攻击的随从不能动。
    """
    steps = []
    for minion in getattr(state, "oppo_minions", ()) or ():
        if (getattr(minion, "attack", 0) or 0) <= 0:
            continue
        if (getattr(minion, "frozen", 0) or getattr(minion, "dormant", 0)
                or getattr(minion, "cant_attack", 0)):
            continue
        steps.append(minion)
        if getattr(minion, "windfury", 0):
            steps.append(minion)
    steps.sort(key=lambda item: -(getattr(item, "attack", 0) or 0))
    return steps


def _enemy_targets(state) -> tuple[list, bool]:
    """敌方可以攻击的我方目标：返回（随从列表，是否可打英雄）。"""
    taunts, others = [], []
    for minion in getattr(state, "my_minions", ()) or ():
        if getattr(minion, "health", 1) <= 0:
            continue
        if (getattr(minion, "stealth", 0) or getattr(minion, "dormant", 0)
                or getattr(minion, "untouchable", 0)
                or getattr(minion, "immune", 0)):
            continue
        (taunts if getattr(minion, "taunt", 0) else others).append(minion)
    if taunts:
        return taunts, False
    return others, True


def _restore_journal(journal) -> None:
    for entity, attr, old_value in reversed(journal):
        setattr(entity, attr, old_value)


def _drop_dead_with_reborn(state, journal) -> None:
    """抹掉死亡随从，复生的先以 1 血回场（就地改场面，改动写进撤销日志）。

    敌方回合枚举靠 journal 回溯，复生会改 damage/reborn/exhausted 三个字段，
    不记日志就会把“复活过一次”的状态带进下一条枚举分支。
    """
    for minion in state.my_minions + state.oppo_minions:
        if getattr(minion, "health", 1) <= 0:
            for attr in ("damage", "reborn", "exhausted"):
                journal.append((minion, attr, getattr(minion, attr, None)))
    # 亡语召唤（暮光龙卵 -> 雏龙）同样要进敌方回合枚举的模拟场面：我方
    # 龙卵被换掉后场上会多一条雏龙，敌方后续攻击可以打它，静态分也得算上。
    # 只会改 minions 列表，而调用方已经在 journal 里备份了双方列表；亡语的
    # 回血不在这里结算（英雄血量改动没有日志，回溯会串味）。
    # 召唤会往列表里追加雏龙，必须先拷一份再遍历，否则会遍历到新召唤物。
    for minion in list(state.my_minions):
        if getattr(minion, "health", 1) <= 0:
            summon_deathrattle_minions(state, minion, "friendly")
    for minion in list(state.oppo_minions):
        if getattr(minion, "health", 1) <= 0:
            summon_deathrattle_minions(state, minion, "enemy")
    state.my_minions = surviving_minions(state.my_minions)
    state.oppo_minions = surviving_minions(state.oppo_minions)


def _apply_enemy_attack(state, attacker, defender):
    """就地结算一次敌方攻击，返回撤销日志（不复制场面，回溯时还原）。

    结算口径与 doAction 的攻击分支一致：圣盾吸收、剧毒必杀、吸血回血，
    死亡随从立刻移出场面。返回的日志按逆序还原即可完全撤销。
    """
    journal = []

    def remember(entity, attr):
        if entity is not None:
            journal.append((entity, attr, getattr(entity, attr, None)))

    if defender is None:
        hero = getattr(state, "my_hero", None)
        if hero is None:
            return journal
        remember(hero, "damage")
        hero.damage += getattr(attacker, "attack", 0) or 0
        if getattr(attacker, "life_steal", 0):
            enemy_hero = getattr(state, "oppo_hero", None)
            remember(enemy_hero, "damage")
            _heal_hero(enemy_hero, getattr(attacker, "attack", 0) or 0)
        return journal

    remember(attacker, "damage")
    remember(attacker, "divine_shield")
    remember(defender, "damage")
    remember(defender, "divine_shield")
    attack = getattr(attacker, "attack", 0) or 0
    dealt = 0
    if getattr(defender, "divine_shield", 0):
        defender.divine_shield = 0
    else:
        defender.damage += attack
        dealt = attack
    taken = 0
    if getattr(attacker, "divine_shield", 0):
        attacker.divine_shield = 0
    else:
        taken = getattr(defender, "attack", 0) or 0
        attacker.damage += taken
    if dealt and getattr(attacker, "poisonous", 0):
        defender.damage = getattr(defender, "max_health", defender.damage)
    if taken and getattr(defender, "poisonous", 0):
        attacker.damage = getattr(attacker, "max_health", attacker.damage)
    if dealt and getattr(attacker, "life_steal", 0):
        enemy_hero = getattr(state, "oppo_hero", None)
        remember(enemy_hero, "damage")
        _heal_hero(enemy_hero, dealt)
    if taken and getattr(defender, "life_steal", 0):
        my_hero = getattr(state, "my_hero", None)
        remember(my_hero, "damage")
        _heal_hero(my_hero, taken)
    journal.append((state, "my_minions", list(state.my_minions)))
    journal.append((state, "oppo_minions", list(state.oppo_minions)))
    _drop_dead_with_reborn(state, journal)
    return journal


def _enemy_face_plan_score(state, static_scorer) -> float:
    """敌方“能打脸就打脸”的最伤方案：嘲讽挡路时先清嘲讽，其余全打英雄。

    与 _enemy_best_board_score 的枚举互补：枚举按“把我方静态分压到最低”
    选交换，分支又受 100 步预算限制、打英雄排在每个攻击者的最后，复杂场面
    往往一次都走不到全打脸（用户 2026-10-30 ann-20261025_211156-t010-s0：
    敌方 5 点攻全部打头、4 只身体全留在场上，我方静态分 57→44，旧口径算成
    0）。这里按固定顺序走一遍“清嘲讽 → 全打英雄”，作为额外候选。
    嘲讽优先解攻击力最高的那只（威胁最大），同攻击力先打血量低的。
    """
    attackers = _enemy_attack_steps(state)
    baseline = static_scorer(state)
    if not attackers:
        return baseline
    journal = []
    lethal = False
    for attacker in attackers:
        if attacker not in state.oppo_minions:
            # 风怒的第一击已经把它换死，第二击自然不存在。
            continue
        targets, can_hit_hero = _enemy_targets(state)
        if can_hit_hero:
            # 没有嘲讽挡路：这一击直接打英雄。
            target = None
        elif targets:
            # 有嘲讽：先清嘲讽，优先攻击力最高的那只。
            target = min(
                targets,
                key=lambda minion: (
                    -(getattr(minion, "attack", 0) or 0),
                    getattr(minion, "health", 0) or 0))
        else:
            continue
        journal += _apply_enemy_attack(state, attacker, target)
        my_hero = getattr(state, "my_hero", None)
        if my_hero is not None and getattr(my_hero, "health", 1) <= 0:
            lethal = True
            break
    score = ENEMY_LETHAL_SCORE if lethal else static_scorer(state)
    _restore_journal(journal)
    return score


def _enemy_best_board_score(state, static_scorer) -> float:
    """敌方一轮攻击后能压到的“我方最低场面分”。

    精确枚举每个攻击者的目标（风怒两次），用就地施加 + 日志回溯代替深拷贝；
    枚举步数受 ENEMY_SEARCH_LIMIT 限制。出现斩杀立即返回 ENEMY_LETHAL_SCORE。
    敌方也可以选择不再攻击，因此当前场面分始终是候选下界。
    另外补一条“清完嘲讽全打英雄”的固定方案（见 _enemy_face_plan_score），
    避免打脸分支被枚举预算饿死、敌方行动分恒为 0。
    """
    steps = _enemy_attack_steps(state)
    baseline = static_scorer(state)
    if not steps:
        return baseline
    face_score = _enemy_face_plan_score(state, static_scorer)
    if face_score <= ENEMY_LETHAL_SCORE:
        return ENEMY_LETHAL_SCORE
    budget = [ENEMY_SEARCH_LIMIT]
    best = [min(baseline, face_score)]
    lethal = [False]

    def dfs(index):
        if lethal[0]:
            return
        if index >= len(steps) or budget[0] <= 0:
            score = static_scorer(state)
            if score < best[0]:
                best[0] = score
            return
        attacker = steps[index]
        if attacker not in state.oppo_minions:
            # 风怒的第一击已经把它换死，第二击自然不存在。
            dfs(index + 1)
            return
        targets, can_hit_hero = _enemy_targets(state)
        for target in list(targets) + ([None] if can_hit_hero else []):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            journal = _apply_enemy_attack(state, attacker, target)
            my_hero = getattr(state, "my_hero", None)
            if my_hero is not None and getattr(my_hero, "health", 1) <= 0:
                lethal[0] = True
                _restore_journal(journal)
                return
            dfs(index + 1)
            _restore_journal(journal)
            if lethal[0]:
                return
        # 敌方也可以收手：当前场面本身就是一个候选结果。
        score = static_scorer(state)
        if score < best[0]:
            best[0] = score

    dfs(0)
    return ENEMY_LETHAL_SCORE if lethal[0] else best[0]


def _enemy_turn_adjustment(state, base_score: float) -> float:
    """把“这条计划走完后敌方一轮最佳攻击”折算成分数增量（标注台序列评分用）。

    state 必须是计划走完后的场面。与 aiSearch 同一口径：每条候选计划都按
    “做完这些动作就结束回合”来评估，因此不再区分之后是否还有可用动作。
    敌方能斩杀我方时返回足够把总分压到 ENEMY_LETHAL_SCORE 的负数增量。
    """
    if not SIMULATE_ENEMY:
        return 0.0
    scorer = getattr(current_strategy(), "rankStaticState", None)
    if not callable(scorer):
        return 0.0
    after = _enemy_best_board_score(state, scorer)
    if after <= ENEMY_LETHAL_SCORE:
        return ENEMY_LETHAL_SCORE - base_score
    return after - scorer(state)


def doAction(state: AiState, action: AiAction) -> AiState:
    """Return a simulated copy after an action.

    除了 `_simulate_action` 的场面模拟，还要同步手牌费用：喉魔异教徒的光环
    （随从入场 / 阵亡都按差额增减，双方都算）与腐蚀者扎卡兹的战吼减费。
    手牌费用不变的话，搜索会以为光环下 1 费的仪式还能付、或者漏掉扎卡兹
    降费后才付得起的那张牌。
    """
    played_card = None
    if action.action == ActionKind.PLAY_CARD and action.source is not None:
        hand_index = action.source.index - 1
        if 0 <= hand_index < len(state.my_hand_cards):
            played_card = state.my_hand_cards[hand_index]
    result = _simulate_action(state, action)
    apply_hand_cost_effects(state, result, played_card)
    return result


def _simulate_action(state: AiState, action: AiAction) -> AiState:
    """Return a simulated copy after a minion attack or minion play.

    模拟永远发生在 copy() 出来的新场面上，绝不改传进来的 state，因此搜索过程中
    真实战局（以及执行层手里那份 StrategyState）不会被污染。

    注意：目前只精确模拟 ATTACK 与 PLAY_CARD。其它动作（Trade / 英雄技能 /
    地标 / 星舰 / 发现）仍会作为候选动作参与评分，但模拟时场面不变——与旧版
    行为一致，后续需要在下面补上对应的场面模拟。
    """
    result = state.copy()
    result.rank = UNRANKED

    # ---------------------------------------------------------------- 攻击
    if action.action == ActionKind.ATTACK:
        if action.source is None or action.target is None:
            return result
        # 随从攻击：source 是己方场面号位。
        # TODO：英雄攻击（source.kind == "hero"）目前没有模拟——英雄没有场面
        # 号位，_minion_from_slot 会返回 None，于是原样返回未改动的场面。
        attacker = _minion_from_slot(result, "friendly", action.source.index)
        if attacker is None:
            return result
        attack = getattr(attacker, "attack", 0)
        # 风怒随从本回合能出手两次（2026-10-30 190729 t016-s2：场上栉龙 3 攻、
        # 敌方英雄 4 血，风怒两击就是斩杀，旧实现第一击后一律 exhausted=1，
        # 搜索永远走不出这条线）。按本回合已出手次数判断是否还能再动。
        attacks_done = int(getattr(attacker, "attacks_this_turn", 0) or 0) + 1
        attacker.attacks_this_turn = attacks_done
        attacks_allowed = 2 if getattr(attacker, "windfury", 0) else 1
        attacker.exhausted = 0 if attacks_done < attacks_allowed else 1
        if hasattr(attacker, "attackable_by_rush"):
            attacker.attackable_by_rush = 0
        if action.target.kind == "hero":
            if result.oppo_hero is not None:
                result.oppo_hero.damage += attack
                # 吸血打脸：按实际伤害回血，满血时治疗溢出被自然浪费。
                if getattr(attacker, "life_steal", 0):
                    _heal_hero(result.my_hero, attack)
            return result

        defender = _minion_from_slot(result, "enemy", action.target.index)
        if defender is None:
            return result
        dealt = 0
        if getattr(defender, "divine_shield", 0):
            defender.divine_shield = 0
        else:
            defender.damage += attack
            dealt = attack
        taken = 0
        if getattr(attacker, "divine_shield", 0):
            attacker.divine_shield = 0
        else:
            taken = getattr(defender, "attack", 0)
            attacker.damage += taken
        # 吸血：攻击方按打到防守方的伤害回血；防守方按反击伤害回它的英雄。
        # 必须放在死亡过滤前——随从这一撞后死掉，已造成的伤害仍然算数。
        if dealt and getattr(attacker, "life_steal", 0):
            _heal_hero(result.my_hero, dealt)
        if taken and getattr(defender, "life_steal", 0):
            _heal_hero(result.oppo_hero, taken)
        # 我方随从被换死时先通知卡组上下文（任务牧：关键随从死亡标记），
        # 再把血量 <= 0 的从场面过滤掉。过滤是"抹掉"，通知必须放在前面。
        dead_friendly = [minion for minion in result.my_minions
                         if getattr(minion, "health", 1) <= 0]
        dead_enemy = [minion for minion in result.oppo_minions
                      if getattr(minion, "health", 1) <= 0]
        if result.deck_context is not None:
            for dead_minion in dead_friendly:
                result.deck_context.on_minion_died(dead_minion)
        # 亡语：确定部分（预言师给英雄回 6 血）必须结算进模拟场面，否则
        # 「杀预言师再打脸」会被误判成斩杀（2026-10-24 205200 t009-s0）。
        for dead_minion in dead_friendly:
            apply_deathrattle(result, dead_minion, "friendly")
        for dead_minion in dead_enemy:
            apply_deathrattle(result, dead_minion, "enemy")
        # 复生：撞死的复生随从以 1 血回场（再死才是真死），必须先结算亡语
        # 再复活。不模拟这层会让“撞一下就当它没了”高估解场收益
        # （2026-10-28 204524 t010-s0：以为撞掉晶石魔像就清了嘲讽）。
        result.my_minions = surviving_minions(result.my_minions)
        result.oppo_minions = surviving_minions(result.oppo_minions)
        return result

    # ---------------------------------------------------------------- 出牌
    if action.action == ActionKind.PLAY_CARD:
        if action.source is None:
            return result
        hand_index = action.source.index - 1
        if not 0 <= hand_index < len(result.my_hand_cards):
            return result
        minion = result.my_hand_cards.pop(hand_index)
        minion.zone = "PLAY"
        minion.zone_pos = max(
            (getattr(entity, "zone_pos", 0)
             for entity in result.my_minions + result.my_locations),
            default=0) + 1
        if hasattr(minion, "exhausted"):
            # 冲锋当回合就能攻击；突袭当回合只能攻击随从（attackable_by_rush），
            # 不能打脸。旧实现一律 exhausted=1，导致搜索里 TIME_029 这类突袭
            # 随从永远无法在本回合行动，评估不出「幼龙+撕裂」的爆发线。
            minion.exhausted = 0 if getattr(minion, "charge", 0) else 1
        if (getattr(minion, "rush", 0)
                and not getattr(minion, "charge", 0)
                and hasattr(minion, "attackable_by_rush")):
            minion.attackable_by_rush = 1
        # 只把真正的随从加入 my_minions、地标加入 my_locations；法术/武器/
        # 英雄牌的效果没有建模，但绝不能当成站场随从——否则法术会凭空给场面
        # 加分、还会挤占 7 格上限，导致搜索推荐"空放法术"或后续随从上不了场。
        cardtype = getattr(minion, "cardtype", None)
        if cardtype == "MINION":
            result.my_minions.append(minion)
            result.my_minions.sort(key=lambda entity: entity.zone_pos)
            # 战吼召唤本体复制（灾毁迅疾幼龙）：点亮时场面要多出一只 5/5 突袭，
            # 不模拟这层终局静态分就少算一只随从（2026-10-30 204524 t010-s0）。
            summon_battlecry_copy(
                result, getattr(minion, "card_id", None) or action.card_id,
                played_card=minion)
        elif cardtype == "LOCATION":
            result.my_locations.append(minion)
            result.my_locations.sort(key=lambda entity: entity.zone_pos)
        # 出牌后通知卡组上下文（任务牧：按法术学派累计任务进度）。必须放在战吼
        # 召唤之后：灾毁会消耗牌库里的撕裂，点亮判断要先看到打牌前的存量。
        if result.deck_context is not None:
            result.deck_context.on_card_played(minion)
        # 确定性法术伤害（AOE / 单体）：结算进模拟场面，由终局静态分体现；
        # 评分不再为同一份伤害加动作分。随机伤害不在此列，仍由评分补期望值。
        # 具体牌面规则全部在 ai.card_rules。
        target = None
        if action.target is not None:
            target = _minion_from_slot(
                result, action.target.owner or "enemy", action.target.index)
        apply_deterministic_spell(
            result, getattr(minion, "card_id", None) or action.card_id,
            played_card=minion, target_minion=target)
        # 打出古神时客户端会立刻弹出技能面板并自动选中一张（无需点本体）。
        # 搜索必须把这次「免费的第 1 次激活」算进本回合次数，否则会以为还能
        # 用 2 次，点出第 3 次而被游戏拒绝。选哪张与执行层共用同一套规则
        # （ai.card_rules.auto_board_ability），两边不会打架。
        if board_ability_specs(result, minion):
            # 执行层是在**打出前**的局面上算下标，搜索必须同口径：把刚落场的
            # 本体从计数里去掉，否则两边会选中不同技能，真实对局里第二次激活
            # 就可能点到已点亮的技能（adapter 直接拒绝，重试三次后被拉黑）。
            pre_view = SimpleNamespace(
                my_minion_num=max(0, int(result.my_minion_num) - 1))
            auto_ability = auto_board_ability(
                pre_view, getattr(minion, "card_id", None))
            apply_board_ability(result, minion, auto_ability,
                                check_requirements=False)
        result.my_used_mana += card_mana_cost(action)
        # 抽牌/发现类牌（咒怨之墓、栉龙）：模拟里看不到抽到的那张牌，按“本
        # 回合会花 3 费把它打出去”预留法力，后续动作只按剩下的法力规划
        # （对应 +10 分出牌分见 ai/动物园术/dwys.py，用户 2026-10-30）。
        reserve = drawn_card_mana_reserve(
            getattr(minion, "card_id", None) or action.card_id)
        if reserve:
            # 剩余法力不够 3 点时按“把剩下的全花掉”封顶，别让可用法力变成
            # 负数（my_last_mana 有 max(0, ...) 兜底，但负数别流进模拟）。
            result.my_used_mana = min(
                result.my_used_mana + reserve,
                result.my_total_mana + result.my_temp_mana)
        # 幸运币：本回合多 1 点法力。不模拟它，搜索会把「硬币 → 原本付不起
        # 的牌」这条线整条剪掉（见 ai.card_rules.coin_mana_gain）。
        result.my_temp_mana += coin_mana_gain(
            getattr(minion, "card_id", None) or action.card_id)
    elif action.action == ActionKind.USE_MINION_ABILITY:
        # 站场随从技能：不进手牌、不花法力，只是点本体开面板再点一张技能卡。
        if action.source is None:
            return result
        body = _minion_from_slot(result, "friendly", action.source.index)
        if body is None:
            return result
        target = None
        if action.target is not None:
            target = _minion_from_slot(
                result, action.target.owner or "enemy", action.target.index)
        apply_board_ability(result, body, getattr(action, "choice_card_id", None),
                            target)
        return result
    elif action.action == ActionKind.USE_HERO_POWER:
        # 英雄技能同样要花法力（生命分流 2 费）。旧实现不扣费，搜索把
        # 「分流 + 大牌」当成免费过牌，排出一堆实际付不起的连招
        # （2026-10-25 160228 t004/t006/t010）。
        result.my_used_mana += card_mana_cost(action)
        # 一回合只能用一次英雄技能（用户 2026-10-29）：把模拟副本里的技能
        # 标成已用，getActions 就不再生成第二个分流。真对局里这个标记由
        # Power.log 的 EXHAUSTED 给出，这里只补搜索内部的重复执行。
        power = getattr(result, "my_hero_power", None)
        if power is not None and hasattr(power, "exhausted"):
            power.exhausted = 1
    elif action.action == ActionKind.CHOOSE_DISCOVER:
        # 发现只选择一个选项。旧模拟保留了选择面板，搜索会在同一状态里再
        # 选一次；第二次虽得分更高，返回路径的第一步仍是之前的低分选项。
        result.discover_choice_count = None
        result.general_choice_cards = {}
    elif action.action == ActionKind.TRADE_CARD and action.instruction.startswith("预备"):
        if action.source is None:
            return result
        hand_index = action.source.index - 1
        if not 0 <= hand_index < len(result.my_hand_cards):
            return result
        card = result.my_hand_cards[hand_index]
        remaining = max(0, result.my_last_mana)
        result.my_used_mana += remaining
        try:
            card.current_cost = max(0, int(card.current_cost) - remaining - 1)
        except (TypeError, ValueError, AttributeError):
            pass
        entity_id = getattr(card, "entity_id", None)
        if entity_id is not None:
            result.prepared_entity_ids.add(str(entity_id))
    return result


def _targeting_flag(entity):
    """该牌指定目标时要检查的"不可被指定"标记；不检查时返回 None。

    扰魔（CANT_BE_TARGETED_BY_SPELLS）只挡法术，英雄技能另有
    CANT_BE_TARGETED_BY_HERO_POWERS；随从的战吼不是法术，不受扰魔限制。
    """
    cardtype = getattr(entity, "cardtype", None)
    if cardtype == "SPELL":
        return "not_targeted_by_spell"
    if cardtype == "HERO_POWER":
        return "not_targeted_by_power"
    return None


# 任何来源都不能指定的状态：免疫、不可触碰、休眠。
_UNIVERSAL_TARGET_BLOCKERS = ("immune", "untouchable", "dormant")


def _target_blocked(source, slot, target) -> bool:
    """这个目标号位能不能被 source 指定（True = 不能指定）。

    免疫 / 不可触碰 / 休眠对任何来源都不可指定；潜行只挡敌方来源
    （我方仍可给自己的潜行随从贴 buff）；扰魔只挡法术、英雄技能另算。
    2026-10-25 20:23 冰川裂片实战：敌方 EDR_840t2 蛇魔之种处于
    DORMANT + UNTOUCHABLE，日志判 REQ_ENEMY_TARGET_NOT_IMMUNE，
    搜索仍把它当合法目标，点下去被游戏拒绝，牌一直留在手里。
    """
    if target is None:
        return True
    for flag in _UNIVERSAL_TARGET_BLOCKERS:
        if getattr(target, flag, 0):
            return True
    if slot.owner == "enemy" and getattr(target, "stealth", 0):
        return True
    flag = _targeting_flag(source)
    return flag is not None and bool(getattr(target, flag, 0))


def _targetable_slots(entity, state, slots):
    """过滤掉目标实体当前不可被这张牌指定的号位。

    敌方扰魔随从（或扰魔状态的英雄）不能成为法术目标；免疫、不可触碰、
    休眠、敌方潜行同样点不出去。提前剔除后搜索就不会生成一个执行层点不
    出去、还会被误判为高分的候选动作。
    """
    if state is None:
        return tuple(slots)
    return tuple(
        slot for slot in slots
        if not _target_blocked(entity, slot, _entity_for_slot(state, slot)))


def _possible_targets(entity, state=None) -> tuple[AiSlot, ...]:
    """返回手牌/英雄技能的候选目标号位（搜索用 AiSlot，号位 1 基）。

    优先读实体自带的可选目标表（SlotRef 或同类对象，目前一般没有）；没有时
    按卡面文本推断（_inferred_targets）——执行层选完牌还要点一下目标，卡面
    注明「有目标」的牌必须把每个合法目标各生成一个候选动作，否则执行那一步
    点不出目标、整手牌卡死。
    目标合法性（用户确认）：伤害可打脸（敌方英雄+敌方随从），恢复可奶英雄
    （己方英雄+己方随从）；buff 类（真言术：盾/愈合/幽魂不散/捉鬼专家）
    只指己方随从。
    """
    targets = getattr(entity, "possible_targets", None)
    if targets:
        slots = []
        for target in targets:
            if isinstance(target, AiSlot):
                slots.append(target)
            elif isinstance(target, SlotRef):
                slots.append(_slot(target.kind, target.owner, target.index))
        return _targetable_slots(entity, state, slots)
    if state is None:
        return ()
    # 卡组策略可以按 card_id 精确接管目标（快牧的沉默/消灭/恢复/伤害都走这里，
    # 比通用卡面文本推断更准；返回 None = 不接管，继续走下面的通用推断）。
    strategy = current_strategy()
    policy = getattr(strategy, "possible_targets", None)
    if callable(policy):
        slots = policy(entity, state)
        if slots is not None:
            return _targetable_slots(entity, state, slots)
    return _targetable_slots(entity, state, _inferred_targets(entity, state))


def _requires_target(entity) -> bool:
    """按卡面文本判断这张牌是否需要指定目标。

    随从只认「战吼：」后的文本（预言师亡语的恢复、索利托斯战吼「一个本随从」
    都不会被误判）；排除「随机/所有」的群体效果（不死徒刑/噬灵疫病/麦迪文的
    胜利/暗言术：毁/雷斯的战吼）。已对任务牧全套卡牌核对无误判。
    """
    card_id = getattr(entity, "card_id", None)
    strategy = current_strategy()
    policy = getattr(strategy, "requires_target", None)
    if callable(policy):
        result = policy(entity)
        if result is not None:
            return bool(result)
    text = _card_text(card_id)
    if not text:
        return False
    if getattr(entity, "cardtype", None) == "MINION":
        head = text.find("战吼")
        if head < 0:
            return False
        text = text[head:]
    if "恢复" in text and "生命值" in text:
        return True
    if "一个角色" in text or "任意角色" in text:
        return True
    if ("造成" in text and "点伤害" in text
            and "随机" not in text and "所有" not in text):
        return True
    # 单体消灭/沉默：只点一个随从（排除「所有」「随机」的群体/随机效果）。
    if ("消灭" in text and "随从" in text
            and "所有" not in text and "随机" not in text):
        return True
    if "沉默" in text and "随从" in text and "随机" not in text:
        return True
    if "随机" not in text and ("友方随从" in text or "一个随从" in text):
        return True
    return False


def _inferred_targets(entity, state) -> tuple[AiSlot, ...]:
    """按卡面文本推断候选目标：每个合法目标一个号位（1 基，与攻击/执行一致）。"""
    if not _requires_target(entity):
        return ()
    text = _card_text(getattr(entity, "card_id", None))
    if getattr(entity, "cardtype", None) == "MINION":
        # _requires_target 为真时战吼一定存在。
        text = text[text.find("战吼"):]
    try:
        friendly = ordered_board(state, "friendly")
        enemy = ordered_board(state, "enemy")
    except (TypeError, ValueError):
        return ()

    def minion_slots(side, entries, predicate=None):
        return [_slot("board_slot", side, position)
                for position, entry in enumerate(entries, 1)
                if entry.kind == "minion"
                and (predicate is None or predicate(entry.entity))]

    # 消灭优先于恢复判断：「消灭一个随从，为你的英雄恢复3点生命值」是单解，
    # 不能因为先匹配到「恢复」就把目标判成己方随从。
    if ("消灭" in text and "随从" in text
            and "所有" not in text and "随机" not in text):
        match = re.search(
            r"攻击力(小于或等于|大于或等于|小于|大于)(\d+)", text)
        predicate = None
        if match:
            operator, number = match.group(1), int(match.group(2))
            if operator == "小于或等于":
                predicate = lambda m: getattr(m, "attack", 0) <= number
            elif operator == "大于或等于":
                predicate = lambda m: getattr(m, "attack", 0) >= number
            elif operator == "小于":
                predicate = lambda m: getattr(m, "attack", 0) < number
            else:
                predicate = lambda m: getattr(m, "attack", 0) > number
        return tuple(minion_slots("enemy", enemy, predicate))
    if "恢复" in text and "生命值" in text:
        if "一个随从" in text or "友方随从" in text:
            return tuple(minion_slots("friendly", friendly))
        return (*minion_slots("friendly", friendly), _slot("hero", "friendly"))
    if "沉默" in text and "随从" in text and "随机" not in text:
        return tuple(minion_slots("enemy", enemy))
    if ("造成" in text and "点伤害" in text
            and "随机" not in text and "所有" not in text):
        # 「对一个随从」只能点随从（双方都合法）；「对一个敌方随从」只点敌方；
        # 「对一个角色」才是随从+英雄。
        if "对一个敌方随从" in text:
            return tuple(minion_slots("enemy", enemy))
        if "对一个随从" in text or (
                "一个随从" in text
                and "敌人" not in text and "敌方" not in text):
            return (*minion_slots("enemy", enemy),
                    *minion_slots("friendly", friendly))
        if "一个角色" in text or "任意角色" in text:
            return (*minion_slots("enemy", enemy),
                    *minion_slots("friendly", friendly),
                    _slot("hero", "enemy"), _slot("hero", "friendly"))
        return (*minion_slots("enemy", enemy), _slot("hero", "enemy"))
    return tuple(minion_slots("friendly", friendly))


def _card_text(card_id) -> str:
    """card_id -> 当前卡组策略人工属性；其它卡牌回退到通用卡牌库。

    预备在日志实体上没有像 tradeable 那样的专属字段，只能靠 cards.json 的
    卡面文本认。src.game_state.choose_one.card_text 是现成的通用查询
    （lru_cache 缓存整张卡表，失败一律返回空串，不会把搜索打断）。
    """
    strategy = current_strategy()
    text_provider = getattr(strategy, "card_text", None)
    deck_text = text_provider(card_id) if callable(text_provider) else ""
    if deck_text:
        return deck_text
    try:
        from src.game_state.choose_one import card_text
        return card_text(card_id) or ""
    except Exception:  # pragma: no cover - 卡表缺失时退化为"不生成预备动作"
        return ""


def getActions(state: AiState) -> list[AiAction]:
    """根据当前 AiState 生成所有候选动作（AIAction 版 getActions）。

        1. 随从攻击随从 / 随从攻击英雄
        2. 英雄攻击随从 / 英雄攻击英雄
        3~7. 打出手牌：随从 / 法术 / 武器 / 地标 / 英雄牌
        8. 交易（Trade）
        9. 使用英雄技能
        10. 使用场上地标
        11. 发射星舰
        12. 发现 / 选择
        13. 一个动作都没有 -> 结束回合
    与 doAction() 的分工：这里只负责"列出所有可做的动作"，具体模拟见 doAction()。
    目前只有 ATTACK / PLAY_CARD 有精确模拟，其余动作先作为候选参与评分。
    """
    actions: list[AiAction] = []

    # 发现界面是阻塞式选择：先选一张，选择完成后再重新搜索本回合动作。
    # 具体候选在下面的 add_choose_one_actions 之后生成（有的选项选完还要
    # 再点一个目标，需要先拿到卡表按卡面判断）。

    try:
        friendly_board = ordered_board(state, "friendly")
        enemy_board = ordered_board(state, "enemy")
    except (TypeError, ValueError):
        # 场面号位未知（zone_pos 缺失或重复）：给不出可执行动作，返回空表，
        # 由 aiSearch 的兜底逻辑结束回合。
        return actions

    available_mana = max(0, getattr(state, "my_last_mana", 0) or 0)

    enemy_minions = [entry for entry in enemy_board
                     if entry.kind == "minion"]
    enemy_taunts = [
        entry for entry in enemy_minions
        if getattr(entry.entity, "taunt", 0)
        and getattr(entry.entity, "can_be_attacked", True)
    ]

    def board_slot_number(board, target_entry):
        """把 BoardEntry 转成 1 基场面号位。"""
        for slot, entry in enumerate(board, 1):
            if entry is target_entry:
                return slot
        return None

    def card_cost(card):
        try:
            return int(getattr(card, "current_cost", 0))
        except (TypeError, ValueError, AttributeError):
            return -1

    def card_label(card):
        """卡牌中文名，拼进指令里（本地搜索的日志才知道到底出的是哪张牌）。

        优先问卡组策略（新卡不在旧 cards.json 里时也能立刻拿到中文名，避免
        在搜索路径里触发整表重下载）；策略认不出再回退实体的 name 属性。
        """
        card_id = getattr(card, "card_id", None)
        name = ""
        provider = getattr(current_strategy(), "card_name", None)
        if callable(provider):
            try:
                name = provider(card_id) or ""
            except Exception:
                name = ""
        if not name:
            name = getattr(card, "name", "") or ""
        return f"（{name}）" if name else ""

    def card_item_by_id(card_id):
        """cards.json -> 完整卡牌数据（抉择选项名/类型用）；查不到返回空 dict。"""
        try:
            from json_op import query_json_item
            return query_json_item(card_id) or {}
        except Exception:
            return {}

    def add_play_card(hand_slot, card, target=None, instruction=None,
                      choice_name=None, choice_card_id=None,
                      destination=None):
        """生成一个 PLAY_CARD 候选（source 是手牌号位）。

        destination 是随从/地标的落点（1 基空隙号），None = 执行层默认最右。
        卡组策略可用 play_destination(state, card) 指定落点。
        """
        actions.append(AiAction(
            action=ActionKind.PLAY_CARD,
            instruction=instruction or f"打出手牌{hand_slot}号卡牌{card_label(card)}",
            source=_slot("hand", "friendly", hand_slot),
            target=target,
            destination=destination,
            cost=card_cost(card),
            card_type=getattr(card, "cardtype", None),
            card_id=getattr(card, "card_id", None),
            entity_id=getattr(card, "entity_id", None),
            choice_name=choice_name,
            choice_card_id=choice_card_id,
            # card 让卡组策略直接 action.card.name 认关键牌（嗜血/鱼人领军…）
            card=card,
        ))

    def add_choose_one_actions(hand_slot, card):
        """抉择牌：为每个选项各生成一个候选（带 choice_name / choice_card_id）。

        执行层（recommendation_adapter._with_choose_one）靠 choice_name 点选项；
        策略（rankActions）靠 choice_card_id 按 card id 认选项。选项自己是否
        需要目标、有哪些合法目标，同样走 _possible_targets / _requires_target，
        卡组策略可以按 card_id 精确接管（快牧的「操纵荆棘」只点≤3攻随从）。
        """
        card_id = getattr(card, "card_id", None)
        if not is_choose_one_card(card_id):
            return False
        option_ids = choose_one_option_card_ids(card_id)
        if not option_ids:
            return False
        added = False
        for option_id in sorted(option_ids):
            item = card_item_by_id(option_id)
            if not item:
                continue
            option_name = item.get("name") or ""
            option_type = item.get("type") or item.get("cardtype")
            pseudo = SimpleNamespace(card_id=option_id, cardtype=option_type)
            targets = _possible_targets(pseudo, state)
            if targets:
                for target in targets:
                    add_play_card(
                        hand_slot, card, target=target,
                        instruction=(f"打出手牌{hand_slot}号法术"
                                     f"{card_label(card)}选择{option_name}"
                                     "并指定目标"),
                        choice_name=option_name,
                        choice_card_id=option_id)
                    added = True
            elif not _requires_target(pseudo):
                add_play_card(
                    hand_slot, card,
                    instruction=(f"打出手牌{hand_slot}号法术"
                                 f"{card_label(card)}选择{option_name}"),
                    choice_name=option_name,
                    choice_card_id=option_id)
                added = True
        return added

    # ================================================================
    # 0. 发现 / 选择菜单（阻塞式：选完重新搜索）
    # ================================================================
    # 有的选项选完还要再点一个目标（例如某个发现选项指向一个敌方随从）。
    # 只生成“点选项”的候选会让执行层点完就以为做完了，游戏一直停在等目标
    # 的状态：之后所有点击都无效，直到烧绳自动结束回合（实测 2026-10-23
    # 18:01/18:22 两批 result_not_confirmed 都是它引起的）。
    # 菜单是否属于我方、是否已就绪，只信日志解析出来的 discover_choice_count
    # （它同时校验 general_choice_ready 和 general_choice_player）。
    # 绝不能用 general_choice_cards 非空来推断：对手的选择菜单也会被日志解析器
    # 记进同一张表，误判成我方菜单会把整回合的出牌都堵死。
    choice_count = getattr(state, "discover_choice_count", None)
    options = getattr(state, "general_choice_cards", {}) or {}
    if choice_count in (1, 2, 3, 4):
        actions = []
        for choice_index in range(1, choice_count + 1):
            # general_choice_cards 是日志 0 基下标，选项号位是 1 基。
            card_id = (options.get(choice_index - 1)
                       or options.get(choice_index))
            targets = ()
            required = False
            if card_id:
                item = card_item_by_id(card_id)
                pseudo = SimpleNamespace(
                    card_id=card_id,
                    cardtype=item.get("type") or item.get("cardtype"))
                targets = _possible_targets(pseudo, state)
                required = _requires_target(pseudo)
            source = _slot("discover_slot", "friendly", choice_index)
            if targets:
                for target in targets:
                    actions.append(AiAction(
                        action=ActionKind.CHOOSE_DISCOVER,
                        instruction=(f"选择我方{choice_index}号位卡牌"
                                     "并指定目标"),
                        source=source,
                        target=target,
                    ))
            elif not required:
                actions.append(AiAction(
                    action=ActionKind.CHOOSE_DISCOVER,
                    instruction=f"选择我方{choice_index}号位卡牌",
                    source=source,
                ))
        blocked = getattr(state, "blocked_action_keys", set())
        if blocked:
            allowed = [action for action in actions
                       if action_identity_key(state, action) not in blocked]
            # 菜单是阻塞式的，此时没有可点的“结束回合”：全部选项都被拦时
            # 宁可再点一次选项，也不要把 end_turn 当合法动作返回（执行层点了
            # 没用，游戏继续等选项，脚本会无限重试卡死）。
            actions = allowed or actions
        return actions or [AiAction.end_turn()]

    # ================================================================
    # 1. 随从攻击
    # ================================================================
    for source_slot, entry in enumerate(friendly_board, 1):
        if entry.kind != "minion":
            continue
        minion = entry.entity
        if not getattr(minion, "can_attack_minion", False):
            continue
        # 古神本体的禁攻由 ai/card_rules 统一判断：日志 cant_attack 优先，
        # 没有该标签时用「4 个技能未点亮」兜底。
        if not can_minion_attack(state, minion):
            continue

        # 有嘲讽：只能攻击嘲讽；没有嘲讽：可以打任意可被攻击的随从。
        if enemy_taunts:
            targets = enemy_taunts
        else:
            targets = [enemy for enemy in enemy_minions
                       if getattr(enemy.entity, "can_be_attacked", True)]

        for target_entry in targets:
            target_slot = board_slot_number(enemy_board, target_entry)
            if target_slot is None:
                continue
            actions.append(AiAction(
                action=ActionKind.ATTACK,
                instruction=(f"我方{source_slot}号随从{card_label(minion)}"
                             f"攻击敌方{target_slot}号随从"
                             f"{card_label(target_entry.entity)}"),
                source=_slot("board_slot", "friendly", source_slot),
                target=_slot("board_slot", "enemy", target_slot),
                card=minion,
                card_id=getattr(minion, "card_id", None),
                entity_id=getattr(minion, "entity_id", None),
            ))

        # 没有嘲讽才能打脸。
        if (not enemy_taunts
                and getattr(minion, "can_beat_face", False)
                and getattr(state, "oppo_hero", None) is not None):
            actions.append(AiAction(
                action=ActionKind.ATTACK,
                instruction=(f"我方{source_slot}号随从{card_label(minion)}"
                             "攻击敌方英雄"),
                source=_slot("board_slot", "friendly", source_slot),
                target=_slot("hero", "enemy"),
                card=minion,
                card_id=getattr(minion, "card_id", None),
                entity_id=getattr(minion, "entity_id", None),
            ))

    # ================================================================
    # 2. 英雄攻击
    # ================================================================
    my_hero = getattr(state, "my_hero", None)
    if my_hero is not None and getattr(my_hero, "can_attack", False):
        if enemy_taunts:
            hero_targets = enemy_taunts
        else:
            hero_targets = [enemy for enemy in enemy_minions
                            if getattr(enemy.entity, "can_be_attacked", True)]

        for target_entry in hero_targets:
            target_slot = board_slot_number(enemy_board, target_entry)
            if target_slot is None:
                continue
            actions.append(AiAction(
                action=ActionKind.ATTACK,
                instruction=(f"我方英雄攻击敌方{target_slot}号随从"
                             f"{card_label(target_entry.entity)}"),
                source=_slot("hero", "friendly"),
                target=_slot("board_slot", "enemy", target_slot),
                card=my_hero,
                card_id=getattr(my_hero, "card_id", None),
                entity_id=getattr(my_hero, "entity_id", None),
            ))

        if (not enemy_taunts
                and getattr(state, "oppo_hero", None) is not None):
            actions.append(AiAction(
                action=ActionKind.ATTACK,
                instruction="我方英雄攻击敌方英雄",
                source=_slot("hero", "friendly"),
                target=_slot("hero", "enemy"),
                card=my_hero,
                card_id=getattr(my_hero, "card_id", None),
                entity_id=getattr(my_hero, "entity_id", None),
            ))

    # ================================================================
    # 3. 手牌 PLAY_CARD
    # ================================================================
    hand_cards = getattr(state, "my_hand_cards", [])
    board_count = len(friendly_board)

    strategy = current_strategy()
    for hand_slot, card in enumerate(hand_cards, 1):
        if str(getattr(card, "entity_id", "")) in state.prepared_entity_ids:
            continue
        can_play_card = getattr(strategy, "can_play_card", None)
        if callable(can_play_card) and not can_play_card(state, card):
            continue
        card_type = getattr(card, "cardtype", None)
        cost = card_cost(card)
        if cost < 0 or cost > available_mana:
            continue

        # 3.0 抉择牌：为每个选项各生成一个候选（执行层选完牌还要点选项/目标）。
        #     没有合法选项时整张牌本回合打不出，直接跳过。
        if is_choose_one_card(getattr(card, "card_id", None)):
            add_choose_one_actions(hand_slot, card)
            continue

        # 3.1 随从（场面最多 7 个实体）：战吼需要目标的（捉鬼专家这类），
        #     只生成带目标版本——执行层选完牌还要点一下目标；场上没有可点
        #     目标就等价于这张牌本回合上不了场。
        if card_type == "MINION":
            if board_count >= 7:
                continue
            # 卡组策略可指定出场落点（如暗誓信徒/古加尔士兵的站位规则）；
            # 不实现该钩子的卡组保持旧行为（None = 默认最右）。
            destination = None
            play_destination = getattr(strategy, "play_destination", None)
            if callable(play_destination):
                destination = play_destination(state, card)
            targets = _possible_targets(card, state)
            if targets:
                for target in targets:
                    add_play_card(
                        hand_slot, card, target=target,
                        instruction=(f"打出手牌{hand_slot}号随从"
                                     f"{card_label(card)}并指定目标"),
                        destination=destination)
            elif not _requires_target(card):
                add_play_card(
                    hand_slot, card,
                    instruction=f"打出手牌{hand_slot}号随从{card_label(card)}",
                    destination=destination)

        # 3.2 法术：卡面注明「有目标」的（恢复/单体伤害/使一个随从…）只生成
        #     带目标版本，没有合法目标 = 本回合打不出；其余先生成无目标版本，
        #     实体自带可选目标时再补目标版本。
        elif card_type == "SPELL":
            targets = _possible_targets(card, state)
            if targets:
                for target in targets:
                    add_play_card(
                        hand_slot, card, target=target,
                        instruction=(f"打出手牌{hand_slot}号法术"
                                     f"{card_label(card)}并指定目标"))
            elif not _requires_target(card):
                add_play_card(
                    hand_slot, card,
                    instruction=f"打出手牌{hand_slot}号法术{card_label(card)}")

        # 3.3 武器
        elif card_type == "WEAPON":
            add_play_card(hand_slot, card,
                          instruction=f"打出手牌{hand_slot}号武器{card_label(card)}")

        # 3.4 地标（也占场面格）
        elif card_type == "LOCATION":
            if board_count >= 7:
                continue
            add_play_card(hand_slot, card,
                          instruction=f"打出手牌{hand_slot}号地标{card_label(card)}")

        # 3.5 英雄牌
        elif card_type == "HERO":
            add_play_card(hand_slot, card,
                          instruction=f"打出手牌{hand_slot}号英雄牌{card_label(card)}")

    # ================================================================
    # 4. 交易 / 预备 Trade & Tempt
    # ================================================================
    # 预备（把卡牌拖到牌库：消耗全部剩余法力，费用额外 -1，卡牌回到手牌）
    # 没有独立日志字段，靠卡面文本里的「预备」认出来。执行层（适配器 + 拖拽）
    # 对交易/锻造/预备是同一条路径，因此复用 TRADE_CARD 动作，只是指令文案
    # 区分开（文案只进日志，解析与执行都只认动作类型和手牌号位）。
    for hand_slot, card in enumerate(hand_cards, 1):
        # 本回合已经预备过的牌不能再用：Power.log 给它打上
        # LITERALLY_UNPLAYABLE 且不再提供 DECK_ACTION 选项（2026-10-29
        # 18:14 实战连续 6 次“预备手牌2号”失败即此因）。出牌分支前面已按
        # prepared_entity_ids 过滤，这里补上预备/交易分支，否则它会被当成
        # 唯一候选推荐出去。
        if str(getattr(card, "entity_id", "")) in state.prepared_entity_ids:
            continue
        tradeable = getattr(card, "tradeable",
                            getattr(card, "is_tradeable", False))
        temptable = not tradeable and "预备" in _card_text(
            getattr(card, "card_id", None))
        if not (tradeable or temptable):
            continue
        actions.append(AiAction(
            action=ActionKind.TRADE_CARD,
            instruction=(f"预备手牌{hand_slot}号卡牌{card_label(card)}"
                         if temptable else
                         f"交易手牌{hand_slot}号卡牌{card_label(card)}"),
            source=_slot("hand", "friendly", hand_slot),
            card_type=getattr(card, "cardtype", None),
            card_id=getattr(card, "card_id", None),
            entity_id=getattr(card, "entity_id", None),
            card=card,
        ))

    # ================================================================
    # 5. 使用英雄技能
    # ================================================================
    hero_power = getattr(state, "my_hero_power", None)
    if hero_power is not None:
        power_cost = card_cost(hero_power)
        power_exhausted = getattr(hero_power, "exhausted", 0)
        if 0 <= power_cost <= available_mana and not power_exhausted:
            power_targets = _possible_targets(hero_power, state)
            if power_targets:
                # 指向性英雄技能（牧师恢复类）：必须点目标，只生成带目标版本。
                for target in power_targets:
                    actions.append(AiAction(
                        action=ActionKind.USE_HERO_POWER,
                        instruction=(f"使用英雄技能{card_label(hero_power)}"
                                     "并指定目标"),
                        source=_slot("hero_power", "friendly"),
                        target=target,
                        cost=power_cost,
                        card=hero_power,
                        card_id=getattr(hero_power, "card_id", None),
                        entity_id=getattr(hero_power, "entity_id", None),
                    ))
            elif not _requires_target(hero_power):
                actions.append(AiAction(
                    action=ActionKind.USE_HERO_POWER,
                    instruction=f"使用英雄技能{card_label(hero_power)}",
                    source=_slot("hero_power", "friendly"),
                    cost=power_cost,
                    card=hero_power,
                    card_id=getattr(hero_power, "card_id", None),
                    entity_id=getattr(hero_power, "entity_id", None),
                ))

    # ================================================================
    # 5.5 站场随从技能（古神本体）
    # ================================================================
    # 技能规格、可用条件、中文名全部由 ai/card_rules 提供；搜索只负责生成
    # 「点本体 → 点技能卡 → 如需目标再点目标」的候选动作，不认任何卡牌 id。
    for slot_number, entry in enumerate(friendly_board, 1):
        if entry.kind != "minion":
            continue
        body = entry.entity
        for ability in board_ability_options(state, body):
            common = dict(
                action=ActionKind.USE_MINION_ABILITY,
                source=_slot("board_slot", "friendly", slot_number),
                card_type="ABILITY",
                choice_card_id=ability.ability_id,
                card_id=ability.ability_id,
                entity_id=getattr(body, "entity_id", None),
                card=body,
            )
            if not ability.requires_target:
                actions.append(AiAction(
                    instruction=(f"使用我方{slot_number}号位"
                                 f"{card_label(body)}的{ability.name}技能"),
                    **common))
                continue
            pseudo = SimpleNamespace(
                card_id=ability.ability_id,
                cardtype=ability.target_cardtype)
            for target in _possible_targets(pseudo, state):
                if (ability.target_side is not None
                        and target.owner != ability.target_side):
                    continue
                if (ability.target_kind == "minion"
                        and target.kind != "board_slot"):
                    continue
                if (ability.target_kind == "hero"
                        and target.kind != "hero"):
                    continue
                actions.append(AiAction(
                    instruction=(f"使用我方{slot_number}号位"
                                 f"{card_label(body)}的{ability.name}技能"
                                 "并指定目标"),
                    target=target,
                    **common))

    # ================================================================
    # 6. 使用场上地标
    # ================================================================
    for slot_number, entry in enumerate(friendly_board, 1):
        if entry.kind != "location":
            continue
        location = entry.entity
        # 没有 can_activate 的实体不强行猜测，默认允许生成动作。
        if not getattr(location, "can_activate", True):
            continue
        actions.append(AiAction(
            action=ActionKind.USE_LOCATION,
            instruction=f"使用我方{slot_number}号地标{card_label(location)}",
            source=_slot("board_slot", "friendly", slot_number),
            card_id=getattr(location, "card_id", None),
            entity_id=getattr(location, "entity_id", None),
            card=location,
        ))
        for target in _possible_targets(location):
            actions.append(AiAction(
                action=ActionKind.USE_LOCATION,
                instruction=(f"使用我方{slot_number}号地标"
                             f"{card_label(location)}并指定目标"),
                source=_slot("board_slot", "friendly", slot_number),
                target=target,
                card_id=getattr(location, "card_id", None),
                entity_id=getattr(location, "entity_id", None),
                card=location,
            ))

    # ================================================================
    # 7. 发射星舰
    # ================================================================
    if is_starship_card is not None:
        for slot_number, entry in enumerate(friendly_board, 1):
            if entry.kind != "minion":
                continue
            card_id = getattr(entry.entity, "card_id", None)
            try:
                is_starship = is_starship_card(card_id)
            except Exception:
                is_starship = False
            if not is_starship:
                continue
            actions.append(AiAction(
                action=ActionKind.LAUNCH_STARSHIP,
                instruction=(f"发射我方{slot_number}号位星舰"
                             f"{card_label(entry.entity)}"),
                source=_slot("board_slot", "friendly", slot_number),
                card_id=card_id,
                entity_id=getattr(entry.entity, "entity_id", None),
                card=entry.entity,
            ))

    # ================================================================
    # 8. 发现 / 选择
    # ================================================================
    # ================================================================
    # 9. 当前没有任何动作 -> END_TURN
    # ================================================================
    blocked = getattr(state, "blocked_action_keys", set())
    if blocked:
        actions = [action for action in actions
                   if action_identity_key(state, action) not in blocked]
    if not actions:
        actions.append(AiAction.end_turn())

    return actions


class Search:
    @staticmethod
    def pri(state: AiState, cost: int) -> float:
        # 暂时没用，将来如果修改搜索节点优先级使用
        best_actions: list[AiAction] = []
        if state.rank <= UNRANKED:
            state.rank = rankState(state, best_actions)
        return state.rank

    def aiSearch(self, state: AiState, cost: int,
                 stopped=None, time_limit=None) -> tuple[list[AiAction], float]:
        # 关键搜索代码（内部实现）：输入输出都是精简的 AiState / AiAction。
        # 基于优先级队列，优先展开当前评分最高的场面，直到展开 MAX_SEARCH 个
        # 场面为止，返回最优的动作序列和分数。
        #
        # 根节点是唯一例外：先让每个首步各展开一次，之后才回到按分数贪心。
        # 纯贪心会把「首步不涨分的铺垫动作」整条剪掉——法术打出后场面不变，
        # 深度 1 天然低分（2026-10-25 111710 t011-s0：先漂浮教程 14 分 vs
        # 先幼龙 26 分），高分分支因此独占预算，铺垫分支一次都轮不到，哪怕它
        # 深度 2 反而更优（52 分 vs 50.6 分）。首步公平展开只多花「首步数 ×
        # 单节点候选数」的预算，却保证每条首步线都被真正推演一次。
        # 用户 2026-10-25 确认：首步公平 + MAX_SEARCH=1000 两个方案都保留。
        best_actions: list[AiAction] = []
        # 停止/时间预算：自动化线程里 Ctrl+Q 必须能秒退；单次决策也不能把
        # 回合拖死（max_search 只限节点数，复杂场面仍可能超时）。超时/停止
        # 时返回当前最优，不抛异常（上层只关心“尽快出一个可执行动作”）。
        started_at = monotonic()

        def _aborted() -> bool:
            try:
                if stopped is not None and stopped():
                    return True
            except Exception:
                pass
            return (time_limit is not None and time_limit > 0
                    and monotonic() - started_at >= time_limit)

        if state.rank <= UNRANKED:
            state.rank = rankState(state, best_actions)
        # 新语义策略（如动物园术）要求 rankState 的 state 是整条动作序列的
        # 起点；旧策略仍按“最后一个动作之前的节点”评分，这里用策略开关隔离。
        rank_from_root = bool(getattr(
            current_strategy(), "RANKS_FROM_SEQUENCE_START", False))
        # 发现/选择菜单是阻塞式的：这时唯一的合法动作就是“点某个选项”，
        # 场上并没有可点的“结束回合”。旧实现把根节点静态分当初始最优，
        # 当每个选项的增益都是负数时（基希克斯 BE_090 的罪孽仪式菜单遇到
        # 下回合会被斩杀，三个选项统一 -50），没有任何动作能超过初值，
        # 搜索就退化成返回 end_turn；执行层点结束回合无效，游戏一直停在
        # 等选项的状态，脚本无限重试（2026-10-25 19:06 实测卡死）。
        # 这里让菜单状态下只比较选项之间的分，必须选一个。
        in_choice_menu = getattr(
            state, "discover_choice_count", None) in (1, 2, 3, 4)
        # 初始最优分在 plan_value 定义后写入（「直接结束回合」也是一条计划）。
        best_score = float("-inf")
        # 策略支持“斩杀动作”判定时，搜索发现这种动作就立即收工。
        lethal_hit = False
        rank_calls = 1
        serial = count()
        budget = max(0, int(cost))
        heap = []
        first_moves = []
        # 首步攻击同样按“同一只随从只留最高分目标”归组（与 expand 同口径）。
        first_attacks = {}
        # 场面去重：hash -> 该场面已入堆的最高优先级。不同动作顺序走到同一
        # 个可见场面时，低分路径直接丢弃；堆里旧的低分条目弹出时也跳过。
        seen_states: dict[str, float] = {}
        # 已经展开过的场面 -> 展开时的优先级。同一场面分不高时不再重复展开；
        # 若之后又出现严格更高分的路径，则允许再展开一次，避免先到的高分
        # 路径被后来的低分路径“占位”后丢掉更优子树。
        expanded_states: dict[str, float] = {}
        if DEDUPE_STATES:
            seen_states[state.state_hash()] = state.rank

        def record_state(new_state, score) -> bool:
            """记录新场面；返回 True 表示这是目前该场面的最高优先级。"""
            if not DEDUPE_STATES:
                return True
            key = new_state.state_hash()
            best = seen_states.get(key)
            if best is not None and score <= best:
                return False
            seen_states[key] = score
            return True

        # ---------------------------------------------------------- 敌方回合
        # 每条候选计划都按“做完这些动作就结束回合、敌方接着来一轮最佳攻击”
        # 评估：最终分 = 原分 + (S(敌方攻击后) - S(终局场面))。敌方也可以选择
        # 不出手，所以增量恒 <= 0，原分已经赢不了的路线不必再做敌方模拟。
        enemy_scorer = None
        if SIMULATE_ENEMY:
            scorer = getattr(current_strategy(), "rankStaticState", None)
            enemy_scorer = scorer if callable(scorer) else None
        # 同一终局场面的敌方增量只算一次（不同动作顺序可能走到相同场面）。
        enemy_cache: dict[str, tuple[bool, float]] = {}
        # 策略可选钩子：本回合还能打在敌方英雄脸上的最大伤害。
        strategy = current_strategy()
        lethal_ceiling = getattr(strategy, "lethal_ceiling", None)

        def lethal_pressure(plan_state, remaining_cost):
            """这个场面本回合还能不能把敌方英雄打到 0（用于抬高优先级）。"""
            if lethal_ceiling is None:
                return False
            hero = getattr(plan_state, "oppo_hero", None)
            if hero is None:
                return False
            health = getattr(hero, "health", 0) or 0
            if health <= 0:
                return False
            return lethal_ceiling(plan_state, remaining_cost) >= health

        def plan_value(plan_state, raw_score):
            """候选计划的最终分：原分 + 敌方一轮最佳攻击的增量。

            敌方能斩杀时直接取 ENEMY_LETHAL_SCORE（绝对值）。
            """
            if enemy_scorer is None:
                return raw_score
            key = plan_state.state_hash()
            cached = enemy_cache.get(key)
            if cached is None:
                before = enemy_scorer(plan_state)
                after = _enemy_best_board_score(plan_state, enemy_scorer)
                lethal = after <= ENEMY_LETHAL_SCORE
                cached = (lethal, 0.0 if lethal else after - before)
                enemy_cache[key] = cached
            lethal, delta = cached
            return ENEMY_LETHAL_SCORE if lethal else raw_score + delta

        def consider(plan_state, raw_score, actions):
            """所有候选计划同口径比较：都要扛过敌方一轮才参与排名。"""
            nonlocal best_actions, best_score
            if raw_score <= best_score:
                return
            value = plan_value(plan_state, raw_score)
            if value > best_score:
                best_score = value
                best_actions = actions

        # 「直接结束回合」本身也是一条候选计划（动作为空），按同一口径计分。
        # 选择菜单里没有“结束回合”可点，保持 -inf，保证必须选一个选项。
        if not in_choice_menu:
            best_score = plan_value(state, state.rank)

        def expand(current_state, current_actions, remaining_cost, priority):
            """展开一个节点：候选继续入堆，同时顺手更新全局最优序列。

            priority 是这条分支继承下来的优先级：只对攻击动作做“地板”，
            见下面入堆处。
            """
            nonlocal best_actions, best_score, rank_calls, lethal_hit
            if lethal_hit or _aborted():
                return
            # 同一只随从的多个攻击方向先攒在这里，循环结束后只把其中得分最
            # 高的一个入队（用户 2026-10-25 要求）。
            attack_children = {}
            for action in getActions(current_state):
                if rank_calls >= MAX_SEARCH:
                    break
                if card_mana_cost(action) > remaining_cost:
                    continue

                new_actions = current_actions + [action]
                # 剪枝（用户 2026-10-30）：咒怨之墓/英雄技能只能当首步，打脸
                # 必须低攻在前。这些分支不评分、不模拟、不入堆。
                if _pruned(current_state, action, current_actions):
                    continue
                # 新语义：从序列起点重放整条序列，拿到终局场面分与每个动作
                # 各自的前缀收益；旧策略仍用当前节点（最后一个动作之前）评分。
                new_score = rankState(
                    state if rank_from_root else current_state, new_actions)
                rank_calls += 1
                new_state = doAction(current_state, action) #模拟操作下，得到新场面
                new_state.rank = new_score

                if _search_lethal_action(current_state, action):
                    # 这一下就能打死对面：不再和别的分支比分数，直接采用这条线
                    # 收工（用户 2026-10-25 要求：能斩杀就足够好，不找最优斩杀）。
                    # 必须先于分数比较强制写回 best_actions：斩杀动作的分数可能
                    # 输给别的更漂亮分支，但退出时不能带着一条没斩杀的线。
                    best_score = new_score
                    best_actions = new_actions
                    lethal_hit = True
                    return
                consider(new_state, new_score, new_actions)
                # 发现选项在真实对局中是一次独立的 UI 操作。若继续展开选择
                # 之后的动作，后续动作的分数会覆盖各选项之间的差异，路径首步
                # 就会退化为候选列表里的第一个选项。
                # 结束回合同样是终止动作：doAction 只把剩余法力记成已用，
                # 继续展开只会生成同分节点（而且 end_turn 还能无限自循环），
                # 白白吃光 MAX_SEARCH 的搜索预算，把“先铺/先抽再攻击”的
                # 分支饿死。
                if action.action in (ActionKind.CHOOSE_DISCOVER,
                                     ActionKind.END_TURN):
                    continue
                # 攻击分支的“优先级地板”：随从换掉后场面静态分会下跌
                # （205200 t009-s3：突袭解 5/5 是正解，该节点却从 95 跌到 67），
                # 纯贪心于是再也不展开这条线，哪怕它最终分数更高。攻击动作
                # 推出的子节点继承父节点优先级（取最大值），攻击序列能继续
                # 往下走；出牌/法术分支仍按自身分数入堆。
                remaining_after = (remaining_cost - card_mana_cost(action)
                                   + coin_mana_gain(action.card_id))
                inherited = (max(new_score, priority)
                             if action.action == ActionKind.ATTACK
                             else new_score)
                if lethal_pressure(new_state, remaining_after):
                    # 本回合还能斩杀：不管当前分多低，都先展开这条线。
                    # 优先级带上已走步数（900 + 深度），同分时更深的节点先出堆，
                    # 斩杀线因此是“一条道走到底”，不会铺成一层层广度展开。
                    inherited = max(inherited,
                                    _lethal_priority(len(new_actions),
                                                     new_score))
                # 解场动作：按打掉敌方场面的量抬优先级，让“先解场再收头”
                # 这条线抢在铺场节点之前被展开（用户 2026-10-29）。
                clear = _clear_priority(current_state, new_state)
                if clear > 0:
                    inherited = max(inherited, new_score + clear)
                child = (-inherited, next(serial), remaining_after,
                         new_state, new_actions, inherited)
                group = _attack_source_key(action)
                if group is None:
                    if record_state(new_state, inherited):
                        heapq.heappush(heap, child)
                    continue
                # 同一只随从攻击不同敌人：同组只留下得分最高的方向。
                # 比较的是最终分（原始分 + 敌方一轮最佳攻击增量），与 consider()
                # 排名同口径。只比原始分会把“原始分相同、但留下更好的场面”的
                # 方向丢掉（2026-10-30 190729 t012-s3：龙卵撞 3/2 与撞 3/4 原始
                # 分都是 89.6，敌方增量 -3.0 vs 0，被丢掉的恰好是高一档的那条）。
                # plan_value 的敌方模拟已由上面的 consider() 按场面缓存，不重复算。
                value = plan_value(new_state, new_score)
                previous = attack_children.get(group)
                if previous is None or value > previous[0]:
                    attack_children[group] = (value, child)

            for _child_score, child in attack_children.values():
                # child 里带着 inherited 优先级（下标 5），用它和已见场面比较。
                if record_state(child[3], child[5]):
                    heapq.heappush(heap, child)

        # 第一层：只给根节点的首步算分记账。出牌/技能首步留给下一步公平展开，
        # 攻击首步则按分数正常入堆——攻击顺序（先打脸还是先解场、用哪只换）
        # 完全由分数排序体现，公平展开低分攻击线会让人类标注的收头顺序翻掉
        # （2026-10-25 205200 t009/t011、190237 t007、173523 t008；仓库严格用例
        # case_0022 也从「生命分流」变成全员打脸）。
        for action in getActions(state):
            if _aborted():
                break
            if card_mana_cost(action) > budget:
                continue
            # 剪枝对首步同样生效：打脸不是最小攻击随从出手的分支不评估、不入堆
            # （咒怨之墓/英雄技能作为首步本身合法，_pruned 会放行）。
            if _pruned(state, action, ()):
                continue
            first_actions = [action]
            first_score = rankState(state, first_actions)
            rank_calls += 1
            first_state = doAction(state, action)
            first_state.rank = first_score
            if _search_lethal_action(state, action):
                # 首步就已经能打死对面：直接采用它收工，不再展开其它首步
                # （用户 2026-10-25 要求：能斩杀就足够好，不找最优斩杀）。
                best_score = first_score
                best_actions = first_actions
                lethal_hit = True
                break
            consider(first_state, first_score, first_actions)
            if action.action in (ActionKind.CHOOSE_DISCOVER,
                                 ActionKind.END_TURN):
                continue
            remaining = (budget - card_mana_cost(action)
                         + coin_mana_gain(action.card_id))
            # 首步之后仍然够斩杀：优先级顶到最高，让斩杀线优先展开。
            first_priority = first_score
            if lethal_pressure(first_state, remaining):
                first_priority = max(
                    first_priority,
                    _lethal_priority(len(first_actions), first_score))
            # 解场首步同样抬优先级（只影响展开顺序）。
            clear = _clear_priority(state, first_state)
            if clear > 0:
                first_priority = max(first_priority, first_score + clear)
            if action.action == ActionKind.ATTACK:
                child = (-first_priority, next(serial), remaining,
                         first_state, first_actions, first_priority)
                group = _attack_source_key(action)
                if group is None:
                    if record_state(first_state, first_priority):
                        heapq.heappush(heap, child)
                else:
                    # 同组按最终分选方向（原始分 + 敌方一轮），解场/斩杀优先级
                    # 只决定展开顺序，不参与淘汰——否则高分方向会在首层就被挤掉
                    # （2026-10-29 ann-20261024_152250_hsbox-t008-s4：打脸 54 被
                    # 解场 57.97 顶掉，结果「打脸两次 62 分」这条线一次都没展开）。
                    # 与 expand() 同口径，敌方模拟已由上面的 consider() 缓存。
                    # 唯一例外：首步之后还能斩杀的方向必须留在堆里。
                    group_score = (float("inf")
                                   if first_priority >= LETHAL_POTENTIAL_PRIORITY
                                   else plan_value(first_state, first_score))
                    previous = first_attacks.get(group)
                    if previous is None or group_score > previous[0]:
                        first_attacks[group] = (group_score, child)
                continue
            # 首步公平展开也去重：不同首步走到同一场面时，只展开分数最高的那条。
            if not record_state(first_state, first_priority):
                continue
            first_moves.append(
                (first_priority, remaining, first_state, first_actions))
        for _first_score, child in first_attacks.values():
            if record_state(child[3], child[5]):
                heapq.heappush(heap, child)
        for _score, remaining_cost, first_state, first_actions in sorted(
                first_moves, key=lambda item: item[0], reverse=True):
            if _aborted():
                break
            if rank_calls >= MAX_SEARCH or lethal_hit:
                break
            # 同一场面后面又出现了更高分的首步：这条低分首步已过期，不再展开。
            if DEDUPE_STATES:
                key = first_state.state_hash()
                if _score < seen_states.get(key, _score):
                    continue
                if _score <= expanded_states.get(key, float("-inf")):
                    continue
                expanded_states[key] = _score
            expand(first_state, first_actions, remaining_cost, _score)
            if lethal_hit:
                break
            if _score >= LETHAL_POTENTIAL_PRIORITY:
                # 这条首步还留着斩杀希望：别急着把其它首步都公平展开（那会
                # 把预算吃光），直接进堆循环沿这条线一路走到底。
                break

        # 之后回到 best-first：谁的分数高先展开谁。
        while heap and rank_calls < MAX_SEARCH and not lethal_hit:
            if _aborted():
                break
            _, _, remaining_cost, current_state, current_actions, priority = (
                heapq.heappop(heap))
            # 同一场面后来出现了更高优先级的路径：这条旧条目已过期，跳过。
            if DEDUPE_STATES:
                key = current_state.state_hash()
                if priority < seen_states.get(key, priority):
                    continue
                if priority <= expanded_states.get(key, float("-inf")):
                    continue
                expanded_states[key] = priority
            expand(current_state, current_actions, remaining_cost, priority)

        if not best_actions:
            best_actions = [AiAction.end_turn()]
        return best_actions, best_score

    def _prepare_ai_state(self, state) -> AiState:
        """StrategyState / AiState -> 可搜索的 AiState（补卡组上下文与禁手）。

        对局与标注/回归测试共用：真实战局传 StrategyState，测试传反序列化的
        StrategyState 替身或上一步模拟出的 AiState。
        """
        ai_state = AiState.from_strategy_state(state)
        # 卡组上下文（任务进度等"场面之外"的信息）：StrategyState 没有该属性，
        # from_strategy_state 读到 None，这里交给当前策略从真实战局补种
        # （任务牧从 my_graveyard 重建；鱼人萨没有 build_deck_context，跳过）。
        strategy = current_strategy()
        if strategy is not None and ai_state.deck_context is None:
            builder = getattr(strategy, "build_deck_context", None)
            if callable(builder):
                ai_state.deck_context = builder(state)
        ai_state.prepared_entity_ids = set(
            getattr(state, "prepared_entity_ids", ()) or ())
        ai_state.blocked_action_keys = set(
            getattr(state, "blocked_action_keys", ()) or ())
        return ai_state

    def search(self, state: StrategyState, cost: int,
               stopped=None, time_limit=None) -> tuple[list[ProposedAction], float]:
        #public 本文件唯一被外部调用函数，所以用了外部数据结构StrategyState和ProposedAction
        #关键搜索代码，基于优先级队列，优先搜索当前最优的场面，直到搜索到MAX_SEARCH个场面，返回最优的动作序列和分数
        #实现过程：基于输入的state，构造出AiState对象，调用aiSearch函数得到 Aiaction后，再构造出ProposedAction列表，并返回
        #
        # 这里是"搜索模块对外"的唯一进出口：
        #   进：StrategyState --AiState.from_strategy_state()--> AiState
        #   出：list[AiAction] --Search._to_proposed()-----------> list[ProposedAction]
        # 中间全程只用 AiState / AiAction，不碰两边的旧协议。
        _apply_max_search()
        ai_state = self._prepare_ai_state(state)
        # 线上开关：设置了 HSAI_VALUE_CKPT、且当前卡组带 use_static_evaluator
        # （ai/强化学习动物园术）时，搜索的静态分改用学到的 V(s)。未设置时
        # `_value_context()` 是空上下文，行为与之前逐位一致。
        with _value_context():
            ai_actions, best_score = self.aiSearch(
                ai_state, cost, stopped=stopped, time_limit=time_limit)
        strategy = current_strategy()
        log_result = getattr(strategy, "log_search_result", None)
        if callable(log_result):
            log_result(ai_state, ai_actions)
        proposed = [self._to_proposed(state, action) for action in ai_actions]
        return proposed, best_score

    # ------------------------------------------------------------------ 出：转换
    @staticmethod
    def _to_slot_ref(slot: AiSlot | None) -> SlotRef | None:
        """AiSlot -> SlotRef（字段一一对应；None 原样返回）。"""
        if slot is None:
            return None
        return SlotRef(slot.kind, slot.owner, slot.index)

    @classmethod
    def _to_proposed(cls, state: StrategyState,
                     action: AiAction) -> ProposedAction:
        """把搜索内部动作还原成执行层动作协议 ProposedAction。

        字段语义与旧 get_action._make_action() 完全一致（同样的 turn_number /
        log_revision / frame_id / 置信度 / 指令文本），因此执行层（adapt_action）
        拿到的东西和改造前没有区别，不需要跟着改。
        """
        log_revision = getattr(state, "log_revision", 0)
        turn_number = getattr(state, "game_num_turns_in_play", 0)
        source_entity = _entity_for_slot(state, action.source)
        target_entity = _entity_for_slot(state, action.target)
        # 多英雄技能（如古神/泰坦双技能）时，执行适配层按名字选槽；本地动作
        # 把技能实体带在 action.card 上，这里补出名字，避免 "hero_power_name_required"。
        source_name = None
        if action.action == ActionKind.USE_HERO_POWER:
            source_name = (getattr(action.card, "name", None)
                           or getattr(source_entity, "name", None))
        return ProposedAction(
            action_id=str(uuid4()),
            frame_id=f"search:{turn_number}:{log_revision}",
            created_at=monotonic(),
            turn_number=turn_number,
            log_revision=log_revision,
            raw_instruction=action.instruction,
            normalized_instruction=action.instruction,
            action=action.action,
            source=cls._to_slot_ref(action.source),
            destination=cls._to_slot_ref(action.destination),
            target=cls._to_slot_ref(action.target),
            entity_id=(getattr(source_entity, "entity_id", None)
                       or action.entity_id),
            target_entity_id=(getattr(target_entity, "entity_id", None)
                              or action.target_entity_id),
            source_name=source_name,
            card_type=action.card_type,
            choice_name=action.choice_name,
            choice_card_id=action.choice_card_id,
            ocr_confidence=1.0,
            semantic_confidence=1.0,
            cost=action.cost,
        )


# ---------------------------------------------------------------- 测试 / 标注入口
def search_ai_state(state, cost: int | None = None):
    """测试与标注共用入口：直接吃 StrategyState（或其序列化替身）。

    与 Search.search 走完全相同的动作生成 / 模拟 / 评分代码，返回
    (list[AiAction], score)，不转成 ProposedAction。
    """
    searcher = Search()
    _apply_max_search()
    ai_state = searcher._prepare_ai_state(state)
    if cost is None:
        cost = getattr(state, "my_last_mana", 0)
    with _value_context():
        return searcher.aiSearch(ai_state, int(cost or 0))


def rank_sequence_detailed(state, actions):
    """rank_sequence 的带明细版本：返回 (score, breakdown | None)。

    只有使用新语义（RANKS_FROM_SEQUENCE_START）且策略实现了
    rankStateDetailed 的卡组（动物园术）才返回明细；其他卡组返回
    (score, None)，标注台据此提示「暂不支持得分明细」。
    """
    root = (state if isinstance(state, AiState)
            else Search()._prepare_ai_state(state))
    strategy = current_strategy()
    rank_from_root = bool(getattr(strategy, "RANKS_FROM_SEQUENCE_START", False))
    detailed = getattr(strategy, "rankStateDetailed", None)
    current = root
    accumulated: list[AiAction] = []
    score = (0.0 if rank_from_root
             else rankState(current, accumulated))
    for item in (actions or ()):
        action = (item if isinstance(item, AiAction)
                  else ai_action_from_dict(current, item))
        if action is None:
            return float("-inf"), None
        accumulated.append(action)
        if not rank_from_root:
            score = rankState(current, accumulated)
        current = doAction(current, action)
    if rank_from_root:
        if callable(detailed):
            score, breakdown = detailed(root, accumulated)
        else:
            score, breakdown = rankState(root, accumulated), None
    else:
        breakdown = None
    delta = _enemy_turn_adjustment(current, score)
    score += delta
    if breakdown is not None:
        # 敌方行动分单独成段：总分 = 场面总分 + 动作总分 + 敌方行动分。
        breakdown["enemy_total"] = float(delta)
        breakdown["enemy_items"] = [{
            "label": "敌方一轮最佳攻击后",
            "detail": f"增量 {delta:+.1f}",
            "value": float(delta),
        }]
        breakdown["total"] = float(score)
    return score, breakdown


def candidate_actions(state) -> list[AiAction]:
    """当前局面所有合法动作（标注台候选面板 / 标注合法性校验）。"""
    return getActions(Search()._prepare_ai_state(state))


def ai_action_from_dict(state, data: dict) -> AiAction | None:
    """标注 JSON 动作 -> AiAction（按当前局面补回 card 实体引用）。"""
    if not isinstance(data, dict):
        return None
    try:
        action_kind = ActionKind(
            str(data.get("action") or "").strip().lower())
    except ValueError:
        return None

    def slot(value):
        if not isinstance(value, dict):
            return None
        index = value.get("index")
        try:
            index = int(index) if index is not None else None
        except (TypeError, ValueError):
            index = None
        return AiSlot(value.get("kind"), value.get("owner"), index)

    source = slot(data.get("source"))
    target = slot(data.get("target"))
    destination = slot(data.get("destination"))
    card = _entity_for_slot(state, source)
    card_id = data.get("card_id")
    if card_id is None and card is not None:
        card_id = getattr(card, "card_id", None)
    return AiAction(
        action=action_kind,
        instruction=str(data.get("instruction") or ""),
        source=source,
        target=target,
        destination=destination,
        cost=int(data.get("cost") or 0),
        card_type=data.get("card_type"),
        choice_name=data.get("choice_name"),
        choice_card_id=data.get("choice_card_id"),
        card_id=card_id,
        entity_id=(data.get("entity_id")
                   or getattr(card, "entity_id", None)),
        target_entity_id=data.get("target_entity_id"),
        card=card,
    )


def apply_action(state, action: AiAction) -> AiState:
    """在模拟副本上执行一个动作（标注页逐步生成候选 / 测试推演）。"""
    ai_state = (state if isinstance(state, AiState)
                else Search()._prepare_ai_state(state))
    return doAction(ai_state, action)


def rank_sequence(state, actions) -> float:
    """按 aiSearch 的口径给一条动作序列打分（preference 断言用）。

    * 新语义策略（RANKS_FROM_SEQUENCE_START）：动作序列解析完后，一次性
      `rankState(序列起点, 完整动作)`，由策略重放全程并算终局场面分。
    * 旧策略：维持逐节点 `rankState(该动作前的场面, 累计动作)`。
    """
    root = (state if isinstance(state, AiState)
            else Search()._prepare_ai_state(state))
    rank_from_root = bool(getattr(
        current_strategy(), "RANKS_FROM_SEQUENCE_START", False))
    current = root
    accumulated: list[AiAction] = []
    score = (0.0 if rank_from_root
             else rankState(current, accumulated))
    for item in (actions or ()):
        action = (item if isinstance(item, AiAction)
                  else ai_action_from_dict(current, item))
        if action is None:
            return float("-inf")
        accumulated.append(action)
        if not rank_from_root:
            score = rankState(current, accumulated)
        current = doAction(current, action)
    if rank_from_root:
        score = rankState(root, accumulated)
    return score + _enemy_turn_adjustment(current, score)


# 盒子解析出来的 SlotRef 用 *_slot 命名，搜索内部用 shorthand，转换时对齐一下。
_SLOT_KIND_ALIASES = {
    "hand_slot": "hand",
    "board": "board_slot",
    "discover_slot": "discover",
}


def normalize_action_dict(data) -> dict:
    """把动作 JSON 的 slot kind 转成搜索内部命名（hand_slot -> hand 等）。"""
    item = dict(data or {})
    for key in ("source", "target", "destination"):
        slot = item.get(key)
        if isinstance(slot, dict):
            slot = dict(slot)
            slot["kind"] = _SLOT_KIND_ALIASES.get(
                str(slot.get("kind") or ""), slot.get("kind"))
            item[key] = slot
    return item


def complete_sequence(state, prefix_actions):
    """以 prefix_actions 开头、由搜索补齐后续动作的最高分序列（联合模式用）。

    盒子只给下一步动作，直接拿单步分和本地整回合计划比会低估盒子；这里把
    盒子首步重放到局面上，再从剩余局面搜出最好的后续动作，拼成整回合序列，
    与本地最优序列用同一口径排名。

    返回 (完整动作列表, 得分)；前缀无法重放时返回 (None, None)。
    """
    root = (state if isinstance(state, AiState)
            else Search()._prepare_ai_state(state))
    _apply_max_search()
    current = root
    prefix = []
    for item in (prefix_actions or ()):
        action = (item if isinstance(item, AiAction)
                  else ai_action_from_dict(current, item))
        if action is None:
            return None, None
        prefix.append(action)
        current = apply_action(current, action)
    suffix, _score = search_ai_state(current)
    full = prefix + list(suffix or ())
    score, _detail = rank_sequence_detailed(root, full)
    return full, score


def joint_prefers_local(local_score, box_score, margin: float = 0.2) -> bool:
    """联合模式判定：本地是否显著更优（默认高 20% 以上）。

    用「绝对差 + 相对量」：local >= box + margin*|box|。正分时等价于 1.2 倍，
    负分/0 分也成立（不比乘法在负数上翻转方向）。
    """
    try:
        local = float(local_score)
        box = float(box_score)
    except (TypeError, ValueError):
        return False
    if local != local or box != box:      # NaN
        return False
    if box == float("-inf"):
        return local != float("-inf")
    if abs(box) < 1e-9:
        # 盒子 0 分时没有「20%」可乘：本地要真的更高才算显著更优。
        return local > box
    return local >= box + abs(box) * margin


def joint_margin(local_score, box_score) -> float:
    """本地相对盒子的超出幅度（0.2 = 高 20%）；除零时返回 0。"""
    try:
        local = float(local_score)
        box = float(box_score)
    except (TypeError, ValueError):
        return 0.0
    base = abs(box)
    if base < 1e-9:
        return 0.0 if local <= 0 else 1.0
    return (local - box) / base
