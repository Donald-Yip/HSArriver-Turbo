# 通用策略（通用牌组基线）：不依赖具体卡组、不写任何卡牌 ID。
#
# 定位：
#   * 盒子 AI 推荐失效时的兜底决策源（网页「盒子推荐失效兜底」）；
#   * 也可以直接在网页「策略选择」里选它，用一套通用打法把任何卡组跑起来。
#   它不是替代卡组专属策略的“最强 AI”：doAction 对未知卡牌效果模拟不完整，
#   通用策略认识不到具体连招；它的价值是“盒子失效时别停摆”，并且给用户一个
#   可自己调权重的基线。
#
# 评分框架与 动物园术 的新语义一致（见 ai/agents.md）：
#   RANKS_FROM_SEQUENCE_START = True
#   rankState(state, actions)
#       = rankStaticState(终局场面) + rankActions(动作序列)
#   * rankStaticState —— 终局场面分：英雄有效生命、手牌数、双方随从攻血/关键字、
#     武器、地标、威胁等级、斩杀线；
#   * rankActions     —— 只补“终局场面相同、但动作顺序有浪费”的溢出伤害惩罚
#     （Silverfish 的 lostDamage 思路），不引入具体卡牌规则。
#
# 公开脚本参考（只取通用的部分，卡牌专属表一律不搬）：
#   * MetaStone ThreatBasedHeuristic / FeatureVector：线性特征加权、威胁等级
#     （红<1 / 黄<15 / 绿）、嘲讽价值随威胁升高、手牌/英雄/随从权重；
#   * Silverfish BehaviorFace：英雄/手牌/随从/武器权重、敌方低血加分、溢出伤害；
#   * Jev-hearthstone plan.mjs：终局场面差分、低血权重、敌方斩杀线惩罚、
#     “每次只走一步、下一步重新观察”的安全近似。
#
# 认牌约定（与现有卡组策略一致）：本文件**不出现任何具体 card_id**；只读
# AiState 里的实体字段（life/mana/attack/health/taunt/…）。
#
# 敌方手牌数：AiState 当前不带 oppo_hand_card_num，本策略不使用该特征（已知缺口）。

from src.recommendation_models import ActionKind
from search.ai_action import AiAction, AiSlot
from search.ai_state import AiState
from src.game_state.recommendation_adapter import ordered_board


# ---------------------------------------------------------------- 评分常量
# 生命价值：低血每点都值钱；高血只是一种资源（Jev 的 0.1/1.0 分段思路）。
LOW_HP_THRESHOLD = 15.0
HEALTH_ABOVE_THRESHOLD_FACTOR = 0.2
ENEMY_HERO_VALUE_FACTOR = 1.2

# 手牌价值（MetaStone/Silverfish 都给 3 左右）。
OWN_CARD_COUNT = 3.0

# 随从基础价值（MetaStone：固有 1.18、攻击 2.42、生命 3；这里取折中）。
MINION_INTRINSIC_VALUE = 1.0
MINION_ATTACK_FACTOR = 2.0
MINION_HEALTH_FACTOR = 2.0

# 关键字附加分（MetaStone/Silverfish 的折中；现代关键词补上突袭/吸血/复生）。
TAUNT_BONUS_GREEN = 0.7
TAUNT_BONUS_YELLOW = 4.0
TAUNT_BONUS_RED = 8.0
WINDFURY_VALUE = 2.0
DIVINE_SHIELD_VALUE = 2.0
SPELL_POWER_VALUE = 1.0
STEALTH_VALUE = 1.0
RUSH_VALUE = 1.0
POISONOUS_VALUE = 3.0
LIFESTEAL_VALUE = 1.0
REBORN_VALUE = 1.5
FROZEN_DORMANT_FACTOR = 0.7

# 武器与地标（Silverfish：武器按攻×耐久计价值）。
WEAPON_VALUE_FACTOR = 1.0
LOCATION_DURABILITY_VALUE = 1.5

# 威胁等级：敌方下回合能打出的伤害 vs 我方有效生命（MetaStone 口径）。
THREAT_RED_PENALTY = -40.0
THREAT_YELLOW_PENALTY = -12.0
LETHAL_DANGER_PENALTY = -50.0
LOW_ENEMY_HP_THRESHOLD = 12.0
LOW_ENEMY_HP_BONUS = 1.0

# 溢出伤害惩罚（Silverfish lostDamage）：每多打 1 点扣 0.5 分。
OVERKILL_PENALTY = 0.5

# 换牌：参考 MetaStone「换掉高费牌」，这里更保守；>= 该费用的牌换掉。
MULLIGAN_REPLACE_COST = 5

# 斩杀/阵亡的绝对值，必须压过一切场面分。
LETHAL_SCORE = 10000.0
DEAD_SCORE = -10000.0

# 威胁等级标记。
_THREAT_GREEN = "green"
_THREAT_YELLOW = "yellow"
_THREAT_RED = "red"
_TAUNT_BONUS = {
    _THREAT_GREEN: TAUNT_BONUS_GREEN,
    _THREAT_YELLOW: TAUNT_BONUS_YELLOW,
    _THREAT_RED: TAUNT_BONUS_RED,
}
_THREAT_PENALTY = {
    _THREAT_GREEN: 0.0,
    _THREAT_YELLOW: THREAT_YELLOW_PENALTY,
    _THREAT_RED: THREAT_RED_PENALTY,
}


# ---------------------------------------------------------------- 基础取值
def _effective_hp(entity):
    """英雄/随从的当前生命；英雄的 StrategyHero.health 已包含护甲。"""
    try:
        return max(0.0, float(getattr(entity, "health", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _hero_value(hp):
    """生命价值：<=15 时每点 1 分，高于 15 的部分每点 0.2 分。"""
    hp = max(0.0, float(hp or 0))
    if hp <= LOW_HP_THRESHOLD:
        return hp
    return LOW_HP_THRESHOLD + (hp - LOW_HP_THRESHOLD) * HEALTH_ABOVE_THRESHOLD_FACTOR


def _minion_attack(minion):
    """可出手的攻击力；冻结/休眠/无法攻击的随从按 0 计（Silverfish 口径）。"""
    if (getattr(minion, "frozen", 0) or getattr(minion, "dormant", 0)
            or getattr(minion, "cant_attack", 0)):
        return 0
    try:
        return max(0, int(getattr(minion, "attack", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _minion_value(minion, taunt_bonus):
    """单个随从的通用价值：固有 + 攻 + 血 + 关键字。"""
    attack = _minion_attack(minion)
    health = _effective_hp(minion)
    value = (MINION_INTRINSIC_VALUE
             + attack * MINION_ATTACK_FACTOR
             + health * MINION_HEALTH_FACTOR)
    if getattr(minion, "taunt", 0):
        value += taunt_bonus
    if getattr(minion, "windfury", 0):
        value += WINDFURY_VALUE
    if getattr(minion, "divine_shield", 0):
        value += DIVINE_SHIELD_VALUE
    if getattr(minion, "spell_power", 0):
        value += SPELL_POWER_VALUE * int(getattr(minion, "spell_power", 0) or 0)
    if getattr(minion, "stealth", 0):
        value += STEALTH_VALUE
    if getattr(minion, "rush", 0):
        value += RUSH_VALUE
    if getattr(minion, "poisonous", 0):
        value += POISONOUS_VALUE
    if getattr(minion, "life_steal", 0):
        value += LIFESTEAL_VALUE
    if getattr(minion, "reborn", 0):
        value += REBORN_VALUE
    if getattr(minion, "frozen", 0) or getattr(minion, "dormant", 0):
        value *= FROZEN_DORMANT_FACTOR
    return value


def _weapon_value(weapon):
    if weapon is None:
        return 0.0
    try:
        attack = max(0, int(getattr(weapon, "attack", 0) or 0))
        durability = max(0, int(getattr(weapon, "durability", 0) or 0))
    except (TypeError, ValueError):
        return 0.0
    return attack * durability * WEAPON_VALUE_FACTOR


def _location_durability(locations):
    total = 0.0
    for location in locations or ():
        try:
            durability = getattr(location, "durability", None)
            if durability is None:
                durability = (int(getattr(location, "max_health", 0) or 0)
                              - int(getattr(location, "damage", 0) or 0))
            total += max(0, int(durability or 0))
        except (TypeError, ValueError):
            continue
    return total


def _enemy_damage(state):
    """敌方下回合理论上能打到脸上的伤害（MetaStone 的 threat 口径）。

    只算还能出手的随从（冻结/休眠/无法攻击不算）+ 敌方英雄攻击；不猜法术/隐藏牌。
    """
    total = 0.0
    for minion in getattr(state, "oppo_minions", ()) or ():
        if (getattr(minion, "frozen", 0) or getattr(minion, "dormant", 0)
                or getattr(minion, "cant_attack", 0)):
            continue
        attack = _minion_attack(minion)
        total += attack * (2 if getattr(minion, "windfury", 0) else 1)
    hero = getattr(state, "oppo_hero", None)
    if hero is not None and not (getattr(hero, "frozen", 0)
                                 or getattr(hero, "cant_attack", 0)):
        try:
            total += max(0, int(getattr(hero, "attack", 0) or 0))
        except (TypeError, ValueError):
            pass
    return float(total)


def _taunt_wall(state):
    """我方嘲讽墙的总生命（Jev 的 wall）。"""
    total = 0.0
    for minion in getattr(state, "my_minions", ()) or ():
        if getattr(minion, "taunt", 0):
            total += _effective_hp(minion)
    return total


def _threat_level(state):
    """红：敌方伤害够斩杀 / 黄：打完后剩余生命 <15 / 绿：安全。"""
    remaining = _effective_hp(getattr(state, "my_hero", None)) - _enemy_damage(state)
    if remaining < 1:
        return _THREAT_RED
    if remaining < LOW_HP_THRESHOLD:
        return _THREAT_YELLOW
    return _THREAT_GREEN


def _entity_for_slot(state, slot):
    """把 AiSlot（board_slot / hero）换成当前场面实体；认不出返回 None。"""
    if slot is None:
        return None
    owner = getattr(slot, "owner", None)
    side = "enemy" if owner == "enemy" else "friendly"
    kind = getattr(slot, "kind", None)
    if kind == "hero":
        return getattr(state, "oppo_hero" if side == "enemy" else "my_hero", None)
    try:
        index = int(getattr(slot, "index", None))
    except (TypeError, ValueError):
        return None
    try:
        board = ordered_board(state, side)
    except Exception:
        return None
    if not 1 <= index <= len(board):
        return None
    return board[index - 1].entity


# ---------------------------------------------------------------- 策略类
class Tongyong:
    """通用策略：只依赖场面事实，不依赖具体卡组与卡牌 ID。"""

    # 与 动物园术 相同的新语义：rankState 收到的是“序列开始前的场面”。
    RANKS_FROM_SEQUENCE_START = True

    # ------------------------------------------------------------ 换牌
    @staticmethod
    def choose(state: AiState) -> list[int]:
        """换牌：换掉 >= MULLIGAN_REPLACE_COST 费的牌，至少保留一张，硬币不换。

        参考 MetaStone 的“高费换掉”；这里取 5 费门槛更保守。想全部保留时把
        MULLIGAN_REPLACE_COST 调大即可。
        """
        hand = list(getattr(state, "my_hand_cards", ()) or ())
        candidates = []
        for index, card in enumerate(hand):
            if getattr(card, "is_coin", False):
                continue
            try:
                cost = int(getattr(card, "current_cost", 0) or 0)
            except (TypeError, ValueError):
                cost = 0
            if cost > 0:
                candidates.append((index, card, cost))
        if len(candidates) <= 1:
            return []
        cheapest = min(candidates, key=lambda item: item[2])[0]
        return [index for index, _card, cost in candidates
                if index != cheapest and cost >= MULLIGAN_REPLACE_COST]

    # ------------------------------------------------------------ 静态场面分
    @staticmethod
    def rankStaticState(state: AiState) -> float:
        """终局场面分（也是搜索敌方回合模拟用的静态评分）。"""
        if not isinstance(state, AiState):
            state = AiState.from_strategy_state(state)
        my_hero = getattr(state, "my_hero", None)
        enemy_hero = getattr(state, "oppo_hero", None)
        my_hp = _effective_hp(my_hero)
        enemy_hp = _effective_hp(enemy_hero)
        level = _threat_level(state)
        taunt_bonus = _TAUNT_BONUS[level]

        score = _hero_value(my_hp) - ENEMY_HERO_VALUE_FACTOR * _hero_value(enemy_hp)
        score += OWN_CARD_COUNT * len(getattr(state, "my_hand_cards", ()) or ())
        for minion in getattr(state, "my_minions", ()) or ():
            score += _minion_value(minion, taunt_bonus)
        for minion in getattr(state, "oppo_minions", ()) or ():
            score -= _minion_value(minion, taunt_bonus)
        score += _weapon_value(getattr(state, "my_weapon", None))
        score -= _weapon_value(getattr(state, "oppo_weapon", None))
        score += LOCATION_DURABILITY_VALUE * _location_durability(
            getattr(state, "my_locations", ()) or ())
        score -= LOCATION_DURABILITY_VALUE * _location_durability(
            getattr(state, "oppo_locations", ()) or ())

        if enemy_hp <= 0:
            return LETHAL_SCORE
        if my_hp <= 0:
            return DEAD_SCORE
        if enemy_hp < LOW_ENEMY_HP_THRESHOLD:
            score += (LOW_ENEMY_HP_THRESHOLD - enemy_hp) * LOW_ENEMY_HP_BONUS
        if _enemy_damage(state) >= my_hp + _taunt_wall(state):
            score += LETHAL_DANGER_PENALTY
        score += _THREAT_PENALTY[level]
        return float(score)

    # ------------------------------------------------------------ 动作分
    @staticmethod
    def rankActions(states: list[AiState], actions: list[AiAction]) -> float:
        """只补溢出伤害惩罚：终局场面相同，但用大随从撞小随从会浪费伤害。

        `states[i]` 是 `actions[i]` 发生前的场面（与 动物园术 的 rankActions
        入参一致）；打脸/圣盾目标不按溢出算。
        """
        total = 0.0
        for state_before, action in zip(states, actions):
            if action.action != ActionKind.ATTACK:
                continue
            target = _entity_for_slot(state_before, action.target)
            attacker = _entity_for_slot(state_before, action.source)
            if target is None or attacker is None:
                continue
            if str(getattr(target, "cardtype", "") or "").upper() == "HERO":
                continue
            if getattr(target, "divine_shield", 0):
                continue
            try:
                health = max(0, int(getattr(target, "health", 0) or 0))
                attack = max(0, int(getattr(attacker, "attack", 0) or 0))
            except (TypeError, ValueError):
                continue
            if health > 0 and attack > health:
                total -= OVERKILL_PENALTY * (attack - health)
        return float(total)

    # ------------------------------------------------------------ 入口
    @staticmethod
    def rankState(state: AiState, actions: list[AiAction]) -> float:
        """关键函数：终局场面分 + 动作序列分（新语义，按序重放）。"""
        if not isinstance(state, AiState):
            state = AiState.from_strategy_state(state)
        if not actions:
            return Tongyong.rankStaticState(state)
        # 延迟 import：ai 包在模块加载阶段不能反向依赖 search 的搜索流程。
        from search.search import doAction

        prefix_states = []
        current = state
        for action in actions:
            prefix_states.append(current)
            current = doAction(current, action)
        return float(Tongyong.rankStaticState(current)
                     + Tongyong.rankActions(prefix_states, list(actions)))
