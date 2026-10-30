"""跨卡组共享的炉石卡牌规则。

原则（ai/agents.md 第 1 条）：留牌、打法、评分、认牌等知识与 search 解耦，
`search/` 不得出现具体卡牌 id。所有卡牌 id、技能面板顺序、费用特例统一放在
本模块，搜索与执行适配层只调用这里的通用接口。

本模块刻意不导入 search / recommendation_adapter，避免循环依赖。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from strategy_entity import StrategyMinion

# 幸运币：打出后本回合多 1 点法力。
COIN_CARD_ID = "GAME_005"
# 咒怨之墓：搜索剪枝用——它换来的“临时牌”必须当回合先出，放到序列中间既不
# 会带来更高分，又会让每条分支再复制一份（用户 2026-10-30）。
CRYPT_CARD_ID = "TLC_451"
# 不消耗法力、改用其它资源支付的牌：重生的翼手龙「消耗残骸而非法力值」。
CORPSE_PAID_CARD_IDS = frozenset({"TLC_436"})

# 站场古神本体：克苏恩，帝国之眼。技能面板从左到右 = 0~3 号位，与日志
# PLAY 块的 SubOption 一一对应；每回合最多激活 2 次，每个技能一局只能用一次。
CUTHUN_BODY_CARD_ID = "BE_013"
CUTHUN_ABILITY_MAX_PER_TURN = 2
CUTHUN_ABILITY_ORDER = ("BE_013t1", "BE_013t2", "BE_013t3", "BE_013t4")

# 场面实体上限（随从 + 地标）；仆从技能召唤前用它判断空位。
BOARD_SLOT_LIMIT = 7


@dataclass(frozen=True)
class BoardAbility:
    """站场随从的一个可激活技能。

    requires_target / target_side / target_kind 描述「点完技能卡还要点谁」；
    requires 描述不依赖目标的额外条件（领域要有其他友方、仆从要有空位）。
    random_outcome 表示 doAction 只能确定化模拟，评分需要补一份期望值。
    """

    ability_id: str
    name: str
    index: int
    requires_target: bool = False
    target_side: str | None = None       # "enemy" / "friendly"
    target_kind: str | None = None       # "minion" / "hero"
    target_cardtype: str = "SPELL"       # 目标过滤用（扰魔只挡法术）
    requires: str | None = None          # "other_friendly" / "free_slot"
    random_outcome: bool = False


# 克苏恩，帝国之眼：四张技能卡，顺序与游戏面板一致。
_CUTHUN_ABILITIES = (
    BoardAbility("BE_013t1", "愤怒", 0, random_outcome=True),
    BoardAbility("BE_013t2", "领域", 1, requires="other_friendly",
                 random_outcome=True),
    BoardAbility("BE_013t3", "凝视", 2, requires_target=True,
                 target_side="enemy", target_kind="minion"),
    BoardAbility("BE_013t4", "仆从", 3, requires="free_slot"),
)

# ---------------------------------------------------------------- 确定性法术伤害
# doAction 必须精确模拟的确定性法术伤害（单体 / AOE）。伤害写进模拟场面后由
# 终局静态分体现，评分不得再为同一份伤害加动作分（ai/agents.md 原则 4）。
# 随机伤害（克苏恩的愤怒 BE_013t1、领域 BE_013t2）不在这里注册，仍由评分补期望值。
DEFILE_CARD_ID = "BE_086"                    # 污亵：AOE
DEFILE_DAMAGE_PER_WAVE = 2
DEFILE_MAX_WAVES = 2
CUTHUN_GAZE_CARD_ID = "BE_013t3"             # 克苏恩的凝视：单体 5 伤 + 吸血
CUTHUN_GAZE_DAMAGE = 5
# 确定性法术增强：增益写进模拟场面，评分不再重复计这份身材。
TUTORIAL_CARD_ID = "BE_094"                  # 漂浮教程：指定随从 +2×费用/+2×费用
TUTORIAL_BUFF_PER_COST = 2
ENTROPY_CARD_ID = "TIME_026"                 # 续连熵能：我方随从全体 +1/+1
ENTROPY_BUFF = 1
# 只增益“已在场”随从的牌：空位还在时先打它、再上随从，新随从白丢这份加成
# （用户 2026-10-30 搜索剪枝）。
BOARD_ONLY_BUFF_CARD_IDS = frozenset({ENTROPY_CARD_ID})
# 亡语：预言师 JAIL_912（牧师 7 费 6/6 嘲讽）。死亡时为拥有者英雄恢复 6 点
# 生命；亡语里的「随机召唤一个 6 费随从」是随机的，不写进场面（用户
# 2026-10-26：只要求把确定性的 +6 治疗算进 doAction，否则杀伤预言师的线
# 会把对面血量估高、误判斩杀）。
PROPHET_CARD_ID = "JAIL_912"                 # 预言师
PROPHET_DEATH_HEAL = 6

# 亡语给拥有者英雄的回血量（card_id -> 治疗量）。只登记确定值；随机效果
# （召唤 6 费随从）留给评分补期望值。
DEATHRATTLE_HERO_HEAL = {PROPHET_CARD_ID: PROPHET_DEATH_HEAL}

# 亡语召唤的确定衍生物（card_id -> 衍生 card_id）。
# 暮光龙卵 CATA_210「亡语：召唤一条 {0}/{1} 的雏龙。（在你的回合开始时获得
# +1/+1！）」：雏龙基础 2/1（35.6.2 从 2/2 砍到 2/1），并且跟着龙卵一起长
# 大——实际对局 20261030_130513 里敌方龙卵 num_turns_in_play=7 破卵，召唤出
# 来的是 5/4（2/1 + 3/+3）；20261026_092855 我方龙卵 nt=2 破卵是 3/2。
# 用户 2026-10-30：破卵之后场上要留一条雏龙，否则会把「解掉龙卵」白算成清场。
EGG_CARD_ID = "CATA_210"
EGG_TOKEN_CARD_ID = "CATA_210t"              # 速生雏龙
EGG_TOKEN_STATS = (2, 1)
# num_turns_in_play 双方每个回合各 +1，龙卵只在拥有者回合开始时 +1/+1，
# 所以成长次数 = num_turns_in_play // 2。
EGG_TOKEN_GROWTH_TURNS = 2
DEATHRATTLE_SUMMON = {EGG_CARD_ID: EGG_TOKEN_CARD_ID}

DETERMINISTIC_SPELL_CARD_IDS = frozenset(
    {DEFILE_CARD_ID, CUTHUN_GAZE_CARD_ID,
     TUTORIAL_CARD_ID, ENTROPY_CARD_ID})


def _card_cost_value(card) -> int:
    try:
        return max(0, int(getattr(card, "current_cost", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _same_card(card, played_card) -> bool:
    """同一张牌：对象同一性，或 entity_id 相同（模拟场面是深拷贝）。"""
    if card is None or played_card is None:
        return False
    if card is played_card:
        return True
    first = getattr(card, "entity_id", None)
    second = getattr(played_card, "entity_id", None)
    return bool(first) and first == second


def must_be_first_action(card_id) -> bool:
    """这张牌只能作为动作序列的第一步打出（搜索剪枝用，不影响得分）。

    咒怨之墓发现的牌是临时牌、必须当回合先出；后面再打只会把每条分支再
    复制一遍，不会让最终分更高（用户 2026-10-30）。
    """
    return str(card_id or "") == CRYPT_CARD_ID


def buffs_board_only(card_id) -> bool:
    """这张牌只增益已在场的随从（搜索剪枝用，不影响得分）。

    先打它、再上随从，新随从吃不到加成；只要还有随从空位，把随从放到它前面
    打出永远不差（用户 2026-10-30）。
    """
    return str(card_id or "") in BOARD_ONLY_BUFF_CARD_IDS


def defile_wave_count(state, played_card=None) -> int:
    """污亵结算几波：本牌是手牌里最贵的一张（无人严格更贵）时 2 波，否则 1 波。

    调用方可以不 pop 手牌——本牌自己用同一性 / entity_id 排除。
    """
    if played_card is None:
        return DEFILE_MAX_WAVES
    cost = _card_cost_value(played_card)
    for card in getattr(state, "my_hand_cards", ()) or ():
        if _same_card(card, played_card):
            continue
        if _card_cost_value(card) > cost:
            return 1
    return DEFILE_MAX_WAVES


def defile_killed_card_ids(state, played_card=None) -> tuple[str, ...]:
    """污亵结算后会被打死的敌方随从 card_id（圣盾吸收第一波，复生多耗一波）。"""
    waves = defile_wave_count(state, played_card)
    killed = []
    for enemy in getattr(state, "oppo_minions", ()) or ():
        health = max(0, getattr(enemy, "health", 0) or 0)
        hits = waves - 1 if getattr(enemy, "divine_shield", 0) else waves
        if getattr(enemy, "reborn", 0):
            # 复生：第一次死亡后以 1 血回场，得再吃一波才算真死。
            hits -= 1
        if health > 0 and hits > 0 and health <= hits * DEFILE_DAMAGE_PER_WAVE:
            killed.append(str(getattr(enemy, "card_id", "") or ""))
    return tuple(killed)


def _damage_minion(minion, damage: int) -> int:
    """对随从结算伤害，返回实际造成的伤害；圣盾吸收当次伤害并破盾。"""
    if minion is None or damage <= 0:
        return 0
    if getattr(minion, "divine_shield", 0):
        minion.divine_shield = 0
        return 0
    minion.damage += damage
    return damage


def revive_reborn_minion(minion) -> bool:
    """复生：把一只已经死亡（生命 <= 0）的复生随从改成 1 血回场。

    返回是否真的复活了。复活的是“重新召唤”的复制体：攻击力不变、生命 1、
    复生词缀就此消耗（再死就是真死），并且当回合不能再攻击。
    """
    if minion is None or not getattr(minion, "reborn", 0):
        return False
    minion.reborn = 0
    max_health = max(1, int(getattr(minion, "max_health", 0) or 0))
    minion.damage = max_health - 1
    if hasattr(minion, "exhausted"):
        minion.exhausted = 1
    return True


def surviving_minions(minions) -> list:
    """过滤死亡随从：带复生的先以 1 血回场，其余才算真死。

    必须在亡语结算之后调用——亡语只结算一次，复活出来的是没有复生的新随从；
    也不能用列表推导顺手改状态，否则复活的随从照样会被过滤掉。
    """
    survivors = []
    for minion in minions:
        if getattr(minion, "health", 1) > 0:
            survivors.append(minion)
            continue
        # 生命 <= 0：只有复生能把它们以 1 血拉回场，其余按真死丢弃。
        if revive_reborn_minion(minion):
            survivors.append(minion)
    return survivors


def _drop_dead_enemies(state) -> None:
    dead = [minion for minion in state.oppo_minions
            if getattr(minion, "health", 1) <= 0]
    # 亡语在死亡瞬间结算（预言师给敌方英雄回 6 血）：必须在从场面抹掉之前，
    # 否则后续攻击会把已经恢复的血量重新算成斩杀线。
    for minion in dead:
        apply_deathrattle(state, minion, "enemy")
    state.oppo_minions = surviving_minions(state.oppo_minions)


def _heal_hero(hero, amount: int) -> None:
    """给英雄回血：扣减 damage，最低到 0（封顶最大生命 + 护甲）。"""
    if hero is None or amount <= 0:
        return
    hero.damage = max(0, getattr(hero, "damage", 0) - amount)


def deathrattle_hero_heal(card_id) -> int:
    """这张牌的亡语给拥有者英雄回复多少血（未登记返回 0）。"""
    return DEATHRATTLE_HERO_HEAL.get(str(card_id or ""), 0)


def egg_token_stats(minion) -> tuple[int, int]:
    """龙卵破卵时雏龙的身材：(2, 1) + 龙卵自己回合开始次数 (+1/+1 各一次)。

    龙卵「在你的回合开始时获得 +1/+1」存的是亡语的召唤值，不是龙卵自己的
    身材（龙卵可能还吃到别的团队增益，那些不会传给雏龙）。num_turns_in_play
    双方每回合各 +1，所以拥有者的回合开始次数 = num_turns_in_play // 2。
    """
    turns = max(0, int(getattr(minion, "num_turns_in_play", 0) or 0))
    growth = turns // EGG_TOKEN_GROWTH_TURNS
    base_attack, base_health = EGG_TOKEN_STATS
    return base_attack + growth, base_health + growth


def summon_deathrattle_minions(state, minion, owner="enemy") -> bool:
    """亡语召唤确定衍生物（暮光龙卵 -> 速生雏龙）；返回是否改了场面。

    敌方龙卵死在我们的模拟序列里，雏龙留在敌方场面（静态分要吃这只随从的
    负分）；我方龙卵死掉，雏龙回到我方场面。空位按「还活着的随从」算：
    龙卵自己马上就要离场，7 只满场时它腾出的那一格正好给雏龙。
    雏龙落在龙卵原来的号位（游戏里亡语召唤物就出现在死亡随从的位置），
    这样后续按「敌方 2 号随从」指目标时不会打到别处。召唤物有召唤失调。
    """
    card_id = DEATHRATTLE_SUMMON.get(str(getattr(minion, "card_id", "") or ""))
    if card_id is None:
        return False
    board = getattr(state, "my_minions" if owner == "friendly"
                    else "oppo_minions", None)
    if board is None:
        return False
    alive = sum(1 for entity in board if getattr(entity, "health", 1) > 0)
    if alive >= BOARD_SLOT_LIMIT:
        return False
    attack, health = egg_token_stats(minion)
    zone_pos = int(getattr(minion, "zone_pos", 0) or 0)
    if zone_pos <= 0:
        zone_pos = max((getattr(entity, "zone_pos", 0) or 0
                        for entity in board), default=0) + 1
    token = StrategyMinion(
        entity_id=None, card_id=card_id, zone="PLAY", zone_pos=zone_pos,
        current_cost=0, overload=0, is_mine=(owner == "friendly"),
        attack=attack, max_health=health, damage=0,
        exhausted=1, num_turns_in_play=0)
    try:
        board.insert(board.index(minion) + 1, token)
    except ValueError:
        board.append(token)
    board.sort(key=lambda entity: getattr(entity, "zone_pos", 0) or 0)
    return True


def apply_deathrattle(state, minion, owner="enemy") -> bool:
    """在模拟场面上结算一张随从死亡时的亡语（只算能确定的部分）。

    已模拟：预言师（JAIL_912）为英雄恢复 6 点；暮光龙卵（CATA_210）召唤
    一条 2/1 雏龙。随机召唤一个 6 费随从无法确定具体是哪只，不写进场面
    （随机性留给评分补期望值）。
    owner 是阵亡随从的归属："friendly" 回我方英雄，其余回敌方英雄。
    返回是否改了场面。
    """
    changed = summon_deathrattle_minions(state, minion, owner)
    heal = deathrattle_hero_heal(getattr(minion, "card_id", None))
    if heal:
        hero = state.my_hero if owner == "friendly" else state.oppo_hero
        _heal_hero(hero, heal)
        changed = True
    return changed


def _buff_minion(minion, attack: int, health: int) -> None:
    """给随从加身材：+攻/+最大生命（当前生命随最大生命同步提高）。"""
    minion.attack += attack
    minion.max_health += health


def _apply_gaze(state, target_minion) -> bool:
    """凝视：对一个敌方随从造成 5 伤，按实际伤害吸血回我方英雄。"""
    if target_minion is None:
        return False
    dealt = _damage_minion(target_minion, CUTHUN_GAZE_DAMAGE)
    _heal_hero(state.my_hero, dealt)
    _drop_dead_enemies(state)
    return True


def apply_deterministic_spell(state, card_id, played_card=None,
                              target_minion=None) -> bool:
    """在模拟副本上结算确定性法术伤害；未注册的牌返回 False 且不改场面。

    调用方必须传 copy() 出来的模拟场面（与 doAction 的约定一致）。
    不 pop 手牌、不扣费——那是 doAction 的职责。
    """
    text = str(card_id or "")
    if text == DEFILE_CARD_ID:
        waves = defile_wave_count(state, played_card)
        for _ in range(waves):
            for enemy in state.oppo_minions:
                _damage_minion(enemy, DEFILE_DAMAGE_PER_WAVE)
            if state.oppo_hero is not None:
                state.oppo_hero.damage += DEFILE_DAMAGE_PER_WAVE
            _drop_dead_enemies(state)
        return True
    if text == CUTHUN_GAZE_CARD_ID:
        return _apply_gaze(state, target_minion)
    if text == TUTORIAL_CARD_ID:
        # 漂浮教程：指定随从 +2×费用 / +2×费用。手牌「随机一张随从」那一半
        # 无法确定落到哪张牌，不模拟（用户 2026-10-26：随机性统一交给动作分）。
        if target_minion is None:
            return False
        bonus = TUTORIAL_BUFF_PER_COST * _card_cost_value(played_card)
        if bonus <= 0:
            return False
        _buff_minion(target_minion, bonus, bonus)
        return True
    if text == ENTROPY_CARD_ID:
        # 续连熵能：我方所有随从 +1/+1。洗入牌库的时空撕裂不建模
        # （牌库顺序不确定，且本回合看不见收益）。
        for minion in state.my_minions:
            _buff_minion(minion, ENTROPY_BUFF, ENTROPY_BUFF)
        return True
    return False


def coin_mana_gain(card_id) -> int:
    """幸运币打出后本回合多出来的法力（经典币 GAME_005 / 各版本 COIN）。"""
    text = str(card_id or "")
    return 1 if text == COIN_CARD_ID or "COIN" in text else 0


# 抽牌/发现类卡牌要预留的法力（用户 2026-10-30）：
#   咒怨之墓 TLC_451（从牌库发现一张，变成临时牌）、
#   栉龙 TLC_603（战吼抽一张牌）。
# 搜索模拟不会把抽到的那张牌塞进手牌，这条线等于“白抽”；打出这两张牌时
# 立刻把一次典型操作（3 费）记为已用，后续动作就按剩下的法力规划。
DRAWN_CARD_PLAY_CARD_IDS = frozenset({"TLC_451", "TLC_603"})
DRAWN_CARD_PLAY_RESERVE_MANA = 3


def drawn_card_mana_reserve(card_id) -> int:
    """打出这张牌后要预留、模拟“把抽到的牌当回合打出去”的法力。"""
    return (DRAWN_CARD_PLAY_RESERVE_MANA
            if str(card_id or "") in DRAWN_CARD_PLAY_CARD_IDS else 0)


# ---------------------------------------------------------------- 战吼召唤复制
# 灾毁迅疾幼龙 TIME_029：「突袭。战吼：从你的牌库中施放一张时空撕裂以召唤一个
# 本随从的复制」。牌库里有撕裂时战吼点亮，打出来要多占一只 5/5 突袭；不模拟
# 这层的话终局静态分少算一只随从（用户 2026-10-30 ann-20261028_204524-t010-s0）。
DRAKE_CARD_ID = "TIME_029"
TEAR_CARD_ID = "TIME_025t"
# 战吼「从你的牌库中施放一张时空撕裂」：撕裂只要被施放（不只是抽到）就按
# 它的自伤结算，己方英雄掉 3 血。20261025_211156 第 10 回合实测：打出灾毁
# 前后我方英雄伤害 18 -> 21，坟场多一张时空撕裂（用户 2026-10-30 t010-s0）。
TEAR_SELF_DAMAGE = 3


def _deck_has(state, card_id) -> bool:
    """牌库里还有没有这张牌：走卡组上下文的通用钩子（AiState.deck_context）。"""
    context = getattr(state, "deck_context", None)
    contains = getattr(context, "deck_contains", None)
    if callable(contains):
        return bool(contains(card_id))
    return False


def battlecry_condition_live(state, played_card, deck_card_id) -> bool:
    """战吼的条件是否点亮：日志只用来否决，条件本身看牌库。

    power.log 的 BATTLECRY 标签只表示"这张牌带战吼"（1），不带战吼是 0；
    它不代表条件已满足——实测在同一局里，场上早已结算完战吼的暮光时空
    跃迁者、以及牌库根本没有撕裂时手里的灾毁，标签都还是 1。所以标签为 0
    时直接判不点亮（被沉默等），其余情况一律交给牌库上下文判断。
    """
    tag = getattr(played_card, "battlecry", None)
    try:
        tag = int(tag)
    except (TypeError, ValueError):
        tag = -1
    if tag == 0:
        return False
    return _deck_has(state, deck_card_id)


def summon_battlecry_copy(state, card_id, played_card=None) -> bool:
    """战吼召唤本体复制（灾毁迅疾幼龙）；返回是否改了模拟场面。

    调用方必须传 copy() 出来的模拟场面（与 doAction 的约定一致），并且在把本体
    加入 my_minions 之后调用：复制体要排在本体右侧。
    战吼先施放牌库里的一张时空撕裂（己方英雄吃 3 点自伤），再召唤复制体；
    牌库里撕裂的存量由卡组上下文在 on_card_played 里扣减。
    返回是否结算了战吼（撕裂施放或复制体召唤，任一发生即为 True）。
    """
    if str(card_id or "") != DRAKE_CARD_ID or played_card is None:
        return False
    if not battlecry_condition_live(state, played_card, TEAR_CARD_ID):
        return False
    # 战吼先施放牌库里那张撕裂（自伤立刻结算），再召唤复制体；场面满只是
    # 复制体召唤失败，撕裂照样被施放、英雄照样掉血。
    hero = getattr(state, "my_hero", None)
    if hero is not None:
        hero.damage += TEAR_SELF_DAMAGE
    if len(getattr(state, "my_minions", ()) or ()) >= BOARD_SLOT_LIMIT:
        return True
    duplicate = copy.deepcopy(played_card)
    duplicate.zone = "PLAY"
    duplicate.zone_pos = max(
        (getattr(entity, "zone_pos", 0) or 0
         for entity in state.my_minions), default=0) + 1
    duplicate.num_turns_in_play = 0
    # 突袭召唤物当回合可以攻击随从、不能打脸：和出牌模拟同一套标记。
    if hasattr(duplicate, "exhausted"):
        duplicate.exhausted = 1
    if (getattr(duplicate, "rush", 0)
            and hasattr(duplicate, "attackable_by_rush")):
        duplicate.attackable_by_rush = 1
    state.my_minions.append(duplicate)
    state.my_minions.sort(key=lambda entity: getattr(entity, "zone_pos", 0) or 0)
    return True


def card_mana_cost(action) -> int:
    """这张牌实际占用的本回合法力（残骸付费牌计 0 费）。"""
    card_id = getattr(action, "card_id", None)
    if card_id is None:
        card_id = getattr(getattr(action, "card", None), "card_id", None)
    if str(card_id or "") in CORPSE_PAID_CARD_IDS:
        return 0
    return max(0, getattr(action, "cost", 0) or 0)


# ---------------------------------------------------------------- 手牌费用修改
# 喉魔异教徒：「所有法术的法力值消耗增加（1）点」——光环，双方手牌都算。
# 真实对局的手牌 current_cost 已经含这个光环，所以模拟里只能按「动作前后
# 场上数量差」增减，不能一入场就从头顶加一遍。
SPELL_COST_AURA_CARD_IDS = frozenset({"BE_729"})
SPELL_COST_AURA_DELTA = 1
# 腐蚀者扎卡兹：「手牌和牌库中的随从牌 -1，法术牌 +1」——一次性战吼，只改
# 自己。牌库在 AiState 里看不见，只改手牌。
ZAKAZ_CARD_ID = "BE_728"
ZAKAZ_MINION_COST_DELTA = -1
ZAKAZ_SPELL_COST_DELTA = 1


def spell_cost_aura_count(state) -> int:
    """场上生效的法术费用光环数量（喉魔异教徒，双方场面都算）。"""
    total = 0
    for minion in (tuple(getattr(state, "my_minions", ()) or ())
                   + tuple(getattr(state, "oppo_minions", ()) or ())):
        if str(getattr(minion, "card_id", "") or "") in SPELL_COST_AURA_CARD_IDS:
            total += 1
    return total


def _shift_hand_cost(state, cardtype: str, delta: int) -> bool:
    """把某种类型的手牌费用整体加/减 delta（最低 0）；返回是否有牌变化。"""
    if not delta:
        return False
    changed = False
    for card in getattr(state, "my_hand_cards", ()) or ():
        if getattr(card, "cardtype", None) != cardtype:
            continue
        try:
            cost = int(getattr(card, "current_cost", 0) or 0) + delta
        except (TypeError, ValueError):
            continue
        cost = max(0, cost)
        if cost != getattr(card, "current_cost", None):
            card.current_cost = cost
            changed = True
    return changed


def apply_hand_cost_effects(before_state, result, played_card=None) -> bool:
    """把一次动作带来的手牌费用变化写进模拟副本；返回是否改了费用。

    * 喉魔异教徒光环：按 `before_state` 与 `result` 场上光环数量的差额增减
      全部手牌法术。差额 +1 是光环随从入场，-1 是它被打死（解掉对面的喉魔
      后，手里的法术立刻变便宜）。基线记在 `result.spell_aura_applied` 上，
      连续 doAction 不会重复计算。
    * 腐蚀者扎卡兹：打出时一次性把手牌随从 -1、法术 +1（战吼）。

    调用方必须传 copy() 出来的模拟场面（与 doAction 的约定一致）。
    """
    applied = getattr(result, "spell_aura_applied", None)
    if applied is None:
        # 根状态来自真实对局：当时的手牌费用已经含场上的光环，记为基线。
        applied = spell_cost_aura_count(before_state)
    current = spell_cost_aura_count(result)
    result.spell_aura_applied = current
    changed = _shift_hand_cost(
        result, "SPELL", (current - int(applied)) * SPELL_COST_AURA_DELTA)
    if str(getattr(played_card, "card_id", "") or "") == ZAKAZ_CARD_ID:
        changed = _shift_hand_cost(
            result, "MINION", ZAKAZ_MINION_COST_DELTA) or changed
        changed = _shift_hand_cost(
            result, "SPELL", ZAKAZ_SPELL_COST_DELTA) or changed
    return changed


def ability_spec(ability_id) -> BoardAbility | None:
    """按技能 id 取规格；不是本站场技能返回 None。"""
    text = str(ability_id or "")
    for ability in _CUTHUN_ABILITIES:
        if ability.ability_id == text:
            return ability
    return None


def board_ability_specs(state, minion) -> tuple[BoardAbility, ...]:
    """这个随从的全部技能规格；不是古神本体返回空。"""
    if getattr(minion, "card_id", None) != CUTHUN_BODY_CARD_ID:
        return ()
    return _CUTHUN_ABILITIES


def board_ability_limit(state, minion) -> int:
    """本回合最多能激活几次；不是古神本体返回 0。"""
    return (CUTHUN_ABILITY_MAX_PER_TURN
            if board_ability_specs(state, minion) else 0)


def _meets_requirement(state, ability: BoardAbility) -> bool:
    if ability.requires == "other_friendly":
        # my_minions 包含本体自己，所以「其他友方」要求总数至少 2。
        return len(getattr(state, "my_minions", ()) or ()) >= 2
    if ability.requires == "free_slot":
        return getattr(state, "my_board_slot_num", 0) < BOARD_SLOT_LIMIT
    return True


def board_ability_options(state, minion) -> tuple[BoardAbility, ...]:
    """当前可以点的技能：过滤已点亮、本回合次数上限、额外条件不满足的。

    凝视的目标合法性不在这里判断——搜索生成候选时再按 target_side /
    target_kind 过滤；没有合法目标就不生成候选。
    """
    specs = board_ability_specs(state, minion)
    if not specs:
        return ()
    used = set(getattr(minion, "ability_used", ()) or ())
    uses = int(getattr(minion, "ability_uses_turn", 0) or 0)
    if uses >= CUTHUN_ABILITY_MAX_PER_TURN:
        return ()
    return tuple(ability for ability in specs
                 if ability.index not in used
                 and _meets_requirement(state, ability))


def apply_board_ability(state, body, ability_id, target_minion=None,
                        check_requirements=True) -> bool:
    """在模拟副本上记录并结算一次站场技能；校验失败返回 False 且不改状态。

    调用方必须传 copy() 出来的模拟场面（与 doAction 的约定一致）。
    """
    spec = ability_spec(ability_id)
    if spec is None or not board_ability_specs(state, body):
        return False
    used = set(getattr(body, "ability_used", ()) or ())
    uses = int(getattr(body, "ability_uses_turn", 0) or 0)
    if spec.index in used or uses >= CUTHUN_ABILITY_MAX_PER_TURN:
        return False
    if check_requirements and not _meets_requirement(state, spec):
        return False
    body.ability_used = tuple(sorted(used | {spec.index}))
    body.ability_uses_turn = uses + 1
    _apply_ability_effect(state, spec, body, target_minion)
    return True


def _apply_ability_effect(state, spec, body, target_minion):
    """按技能 id 结算模拟效果（真实效果以游戏结算为准）。"""
    if spec.index == 0:
        # 愤怒：7 点伤害随机分配在所有敌人之间。按「血少的先吃伤害、
        # 剩余打脸」确定化，保证后续攻击可推演。
        pool = 7
        for enemy in sorted(
                state.oppo_minions,
                key=lambda minion: (minion.health, -minion.attack)):
            if pool <= 0:
                break
            if getattr(enemy, "divine_shield", 0):
                enemy.divine_shield = 0
                continue
            damage = min(pool, max(0, enemy.health))
            if damage <= 0:
                continue
            enemy.damage += damage
            pool -= damage
        if pool > 0 and state.oppo_hero is not None:
            state.oppo_hero.damage += pool
        _drop_dead_enemies(state)
        return
    if spec.index == 1:
        # 领域：随机使两个其他友方随从 +2/+3。按攻击力最高的两个确定化。
        others = [minion for minion in state.my_minions if minion is not body]
        for minion in sorted(others, key=lambda item: -item.attack)[:2]:
            minion.attack += 2
            minion.max_health += 3
        return
    if spec.index == 2:
        # 凝视：吸血，对一个敌方随从造成 5 点伤害。与法术牌路径共用同一套
        # 结算（伤害 / 吸血 / 死亡过滤），避免两处口径不一致。
        _apply_gaze(state, target_minion)
        return
    if spec.index == 3:
        # 仆从：召唤两个 2/3 嘲讽（场上没位置时少召）。
        free = max(0, BOARD_SLOT_LIMIT - state.my_board_slot_num)
        base = max((getattr(entity, "zone_pos", 0)
                    for entity in state.my_minions + state.my_locations),
                   default=0)
        for offset in range(min(2, free)):
            state.my_minions.append(StrategyMinion(
                entity_id=None, card_id="BE_013t4", zone="PLAY",
                zone_pos=base + offset + 1, current_cost=0, overload=0,
                is_mine=True, attack=2, max_health=3, taunt=1, exhausted=1))


def can_minion_attack(state, minion) -> bool:
    """这只随从本回合能不能进攻。

    优先相信日志属性 cant_attack（2026-10-26 实测 Power.log 里没有古神本体
    的 CANT_ATTACK 标签，所以保留下面的技能兜底）：古神本体必须把 4 个技能
    都点亮才能进攻。
    """
    if getattr(minion, "cant_attack", 0):
        return False
    specs = board_ability_specs(state, minion)
    if specs:
        required = {spec.index for spec in specs}
        used = set(getattr(minion, "ability_used", ()) or ())
        if len(used & required) < len(required):
            return False
    return True


# 四古神打出时客户端立刻弹出「选择一个技能」；面板无 EntityChoices 日志，
# 搜索层看不见，只能打出后确定性地点一张技能卡。技能顺序 = 面板从左到右。
_GOD_ABILITY_ORDER = {
    "BE_013": ("BE_013t1", "BE_013t2", "BE_013t3", "BE_013t4"),  # 克苏恩，帝国之眼
    "BE_009": ("BE_009t1", "BE_009t2", "BE_009t3", "BE_009t4"),  # 古神主谋尤格-萨隆
    "BE_017": ("BE_017t3", "BE_017t4", "BE_017t5", "BE_017t6"),  # 恩佐斯，梦境之末
    "BE_784": ("BE_784t1", "BE_784t2", "BE_784t3", "BE_784t4"),  # 亚煞极，泰坦杀神
}


def old_god_ability_index(state, card_id) -> int | None:
    """四古神打出后默认点第几张技能卡（0 基）；不是古神返回 None。

    只选**不需要再点目标**的技能：执行层只点一张卡，不会补目标，点到凝视/
    操纵这类技能会让游戏停在「等目标」，反而卡死。
    """
    my_count = getattr(state, "my_minion_num", 0) or 0
    if card_id == "BE_013":
        # 0 愤怒 / 1 领域（两个其他友方 +2/+3）/ 2 凝视（要目标）/ 3 仆从
        if 2 <= my_count <= 5:
            return 1                      # 领域
        if my_count <= 5:
            return 3                      # 仆从：召唤两个 2/3 嘲讽
        return 0
    if card_id == "BE_009":
        # 0 头脑（下个法术施放两次）/ 1 疯狂（要目标）/ 2 崛起（给我方法术加费）/ 3 领域（抽五张法术）
        has_spell = any(getattr(card, "cardtype", None) == "SPELL"
                        for card in getattr(state, "my_hand_cards", ()) or ())
        return 0 if has_spell else 3
    if card_id == "BE_017":
        # 0 召唤（发现并召唤 >=5 费亡语，后续发现菜单有日志）/ 1 操纵（要目标）/
        # 2 大军（复活 4 个亡语）/ 3 领域（要目标）
        return 0
    if card_id == "BE_784":
        # 0 凝视 / 1 子嗣（随机召唤 >=8 费并复制）/ 2 威力 / 3 领域（下一张牌 -5 费）
        return 1 if my_count <= 5 else 3
    return None


def auto_board_ability(state, card_id) -> str | None:
    """打出古神时客户端自动选中的技能 id；不是古神返回 None。

    state 允许只带 my_minion_num（搜索用打出前的视图），其余字段按默认值。
    """
    index = old_god_ability_index(state, card_id)
    order = _GOD_ABILITY_ORDER.get(str(card_id or ""))
    if index is None or order is None or not 0 <= index < len(order):
        return None
    return order[index]
