# AiState类，仅供search.py使用，是StrategyState一个简化版，去掉了很多不必要的属性和方法，只保留了搜索需要的部分。
#
# 为什么需要它：
#   StrategyState 是"给执行层用"的完整战局快照（武器、双方坟场、手牌入场计数、
#   可选目标表 power_options、玩家 id、日志版本…… 几十个字段），而搜索每展开一个
#   节点都要 copy() 出一份模拟场面。背着这些搜索根本不读的字段，既慢又容易在
#   模拟里改错东西。AiState 只保留"生成动作 / 模拟动作 / 评分"真正会读到的字段。
#
# 三条设计约定：
#   1. 字段名与 StrategyState 保持一致（my_minions / my_hand_cards / my_hero /
#      my_last_mana / game_num_turns_in_play ...）。这样卡组策略（ai/*.py 的
#      rankState、rankStaticState、rankActions）和 ordered_board() 一行都不用改
#      就能直接吃 AiState：要解耦的是"状态有多重"，不是"字段叫什么名字"。
#   2. AiState 本身只持有实体的引用（列表只做浅拷贝）。任何会改场面的模拟都必须
#      先 copy()（深拷贝），保证 doAction() 永远改不到真实战局。
#   3. 本文件不 import 本文件夹以外的任何东西：StrategyState 用鸭子类型读
#      （from_strategy_state 只 getattr），搜索模块因此可以独立演进。
from copy import deepcopy
import hashlib

# ---------------------------------------------------------------- 去重指纹字段
# 只纳入"评分 / 动作生成 / 斩杀判断"会读到的字段。entity_id、zone、
# log_revision 不影响评分，而且不同路径模拟出的同一场面 entity_id 可能不同，
# 故意不参与指纹，让同场面能命中。
_MINION_KEY_FIELDS = (
    "card_id", "zone_pos", "current_cost", "overload", "temporary",
    "attack", "max_health", "damage", "taunt", "divine_shield", "stealth",
    "windfury", "poisonous", "life_steal", "spell_power", "freeze",
    "battlecry", "not_targeted_by_spell", "not_targeted_by_power",
    "charge", "rush", "attackable_by_rush", "frozen", "dormant",
    "untouchable", "immune", "reborn", "cant_attack", "exhausted",
    "num_turns_in_play", "ability_used", "ability_uses_turn",
)
_LOCATION_KEY_FIELDS = (
    "card_id", "zone_pos", "current_cost", "max_health", "damage",
    "action_cooldown", "exhausted", "just_played",
)
_HERO_KEY_FIELDS = (
    "card_id", "max_health", "damage", "armor", "attack", "exhausted",
    "frozen", "cant_attack", "stealth", "immune",
    "not_targeted_by_spell", "not_targeted_by_power",
)
_HAND_KEY_FIELDS = (
    "card_id", "cardtype", "zone_pos", "current_cost", "overload", "temporary",
    "created_by",
)
_HERO_POWER_KEY_FIELDS = ("card_id", "current_cost", "exhausted")


def _entity_key(entity, fields) -> str:
    if entity is None:
        return "-"
    return ",".join(str(getattr(entity, field, "")) for field in fields)


def _value_key(value) -> str:
    """把卡组上下文的值稳定序列化；支持 dict / set / list / 标量。"""
    if isinstance(value, dict):
        return "{" + ";".join(
            f"{key}:{_value_key(item)}"
            for key, item in sorted(value.items(),
                                    key=lambda pair: str(pair[0]))
        ) + "}"
    if isinstance(value, (set, frozenset)):
        return "{" + ";".join(sorted(str(item) for item in value)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ";".join(_value_key(item) for item in value) + "]"
    return str(value)



class DeckContext:
    """卡组上下文基类：挂在 AiState 上、随模拟分支深拷贝演进的对局进度信息。

    场面快照（随从/手牌/法力…）之外，不同卡组还需要"这一局走到哪了"的信息
    （任务施放了几个法术、关键随从是否死亡过…）。这些信息不来自当前场面，
    搜索模拟又必须能推演"打完这串动作后进度变成什么样"，所以：

      * 真实对局：由各卡组策略从 StrategyState 的公开数据（如我方坟场）构建，
        在 Search.search() 里种到根 AiState 上（见 ai/renwumu_context.py 的
        RenwumuDeckContext，Renwumu.build_deck_context）；
      * 搜索模拟：doAction() 每次模拟出牌/攻击致死时回调下面的钩子，进度在
        copy() 出来的分支上下文上推进，互不污染真实战局。

    基类默认什么都不记（钩子全是 no-op），各卡组按需继承并实现。
    """

    def on_card_played(self, card):
        """模拟打出一张牌后回调（card 是打出的那张牌实体，只读）。"""

    def on_minion_died(self, minion):
        """模拟中我方随从死亡后回调（minion 是死亡随从实体，只读）。"""


class AiState:
    # 搜索节点"还没评过分"的哨兵值（沿用旧版 search.py 的 UNRANKED）。
    UNRANKED = -10000.0

    def __init__(self, my_minions=None, oppo_minions=None, my_locations=None,
                 oppo_locations=None, my_hand_cards=None, my_hero=None,
                 oppo_hero=None, my_hero_power=None,
                 my_weapon=None, oppo_weapon=None,
                 my_total_mana=0, my_used_mana=0, my_temp_mana=0,
                 game_num_turns_in_play=0, log_revision=0,
                 acted_this_turn=False,
                 discover_choice_count=None, general_choice_cards=None,
                 deck_context=None, prepared_entity_ids=None,
                 blocked_action_keys=None):
        # ---------------------------------------------------------- 场面
        # 实体仍是真实战局里的对象（浅拷贝列表、共享引用）；要改场面先 copy()。
        self.my_minions = list(my_minions or [])
        self.oppo_minions = list(oppo_minions or [])
        self.my_locations = list(my_locations or [])
        self.oppo_locations = list(oppo_locations or [])
        self.my_hand_cards = list(my_hand_cards or [])
        self.my_hero = my_hero
        self.oppo_hero = oppo_hero
        self.my_hero_power = my_hero_power
        # 武器本身不改变动作生成，但策略评分要看“敌方英雄头上那把刀”
        # （冰川冻英雄 = 这一回合武器打不出来），所以原样带进模拟场面。
        self.my_weapon = my_weapon
        self.oppo_weapon = oppo_weapon

        # ---------------------------------------------------------- 法力
        # my_last_mana（可用法力）由下面三个属性算出来，生成/过滤动作时要用。
        self.my_total_mana = int(my_total_mana or 0)
        self.my_used_mana = int(my_used_mana or 0)
        self.my_temp_mana = int(my_temp_mana or 0)

        # ---------------------------------------------------------- 对局标识
        # 公开入口 search() 把这些原样带进 ProposedAction，保证执行层校验回合/
        # 日志版本时，和自己手里的 StrategyState 对得上。
        self.game_num_turns_in_play = int(game_num_turns_in_play or 0)
        self.log_revision = int(log_revision or 0)
        # 本回合我方是否已经执行过动作。搜索每步只执行最优序列的第一个动作
        # 就重新搜索，单看“当前序列长度”判断不出这一点；执行层在搜索前把
        # 真实信号传进来，供“只允许回合开头使用”的卡牌（术士英雄技能）评分。
        self.acted_this_turn = bool(acted_this_turn)

        # ---------------------------------------------------------- 发现/选择
        # 有值（1~4）时搜索会为每个选项生成一个候选动作。
        self.discover_choice_count = discover_choice_count
        # 选择项下标(1 基) -> card_id：发现/选择评分时要认出每个选项是什么牌。
        self.general_choice_cards = dict(
            general_choice_cards) if general_choice_cards else {}

        # ---------------------------------------------------------- 卡组上下文
        # 各卡组特有的对局进度（任务牧：法术学派计数、关键随从死亡标记…）。
        # 真实值由 Search.search() 通过策略的 build_deck_context() 种进来；
        # 模拟值由 doAction() 的钩子在深拷贝分支上推进。
        self.deck_context = deck_context
        self.prepared_entity_ids = set(prepared_entity_ids or ())
        self.blocked_action_keys = set(blocked_action_keys or ())

        # ---------------------------------------------------------- 搜索缓存
        # 该节点的评分缓存；UNRANKED 表示"还没算过"（见 Search.pri）。
        self.rank = AiState.UNRANKED

    # ------------------------------------------------------------------ 构造
    @classmethod
    def from_strategy_state(cls, state):
        """把执行层的 StrategyState（鸭子类型）精简成 AiState。

        只读不写：不会改动传进来的 state。列表做浅拷贝、实体对象共享引用，
        真正的模拟场面由 doAction() 里的 copy() 负责深拷贝。
        """
        return cls(
            my_minions=getattr(state, "my_minions", None),
            oppo_minions=getattr(state, "oppo_minions", None),
            my_locations=getattr(state, "my_locations", None),
            oppo_locations=getattr(state, "oppo_locations", None),
            my_hand_cards=getattr(state, "my_hand_cards", None),
            my_hero=getattr(state, "my_hero", None),
            oppo_hero=getattr(state, "oppo_hero", None),
            my_hero_power=getattr(state, "my_hero_power", None),
            my_weapon=getattr(state, "my_weapon", None),
            oppo_weapon=getattr(state, "oppo_weapon", None),
            my_total_mana=getattr(state, "my_total_mana", 0),
            my_used_mana=getattr(state, "my_used_mana", 0),
            my_temp_mana=getattr(state, "my_temp_mana", 0),
            game_num_turns_in_play=getattr(
                state, "game_num_turns_in_play", 0),
            log_revision=getattr(state, "log_revision", 0),
            acted_this_turn=getattr(state, "acted_this_turn", False),
            discover_choice_count=getattr(
                state, "discover_choice_count", None),
            general_choice_cards=getattr(
                state, "general_choice_cards", None),
            # StrategyState 目前没有 deck_context 属性，鸭子类型读到 None；
            # 真实上下文由 Search.search() 用卡组策略的 build_deck_context() 补种。
            deck_context=getattr(state, "deck_context", None),
            prepared_entity_ids=getattr(state, "prepared_entity_ids", None),
            blocked_action_keys=getattr(state, "blocked_action_keys", None),
        )

    def copy(self):
        """深拷贝一份"可以随便改"的模拟场面。

        doAction() 对随从血量、法力、手牌的所有修改都发生在 copy() 出来的这份
        场面上，因此原始战局（以及它背靠的 StrategyState 实体）不会被污染。
        """
        return deepcopy(self)

    def state_hash(self) -> str:
        """评分相关场面的稳定指纹（搜索去重用）。

        不同动作顺序可能走到完全相同的可见场面。把影响评分、候选生成、
        斩杀判断的字段拼成规范串再做哈希，就能在优先级队列里识别出这种
        重复场面，避免对同一棵子树反复展开。

        只比较可见信息：抽牌、牌库顺序等隐藏信息不参与（用户 2026-10-26
        确认按同场面处理）。entity_id / zone / log_revision 同样不参与，
        它们不影响评分，且会让模拟出的同一场面指纹不同。
        """
        parts = [
            _entity_key(self.my_hero, _HERO_KEY_FIELDS),
            _entity_key(self.oppo_hero, _HERO_KEY_FIELDS),
            _entity_key(self.my_hero_power, _HERO_POWER_KEY_FIELDS),
            "|".join(_entity_key(item, _MINION_KEY_FIELDS)
                     for item in self.my_minions),
            "|".join(_entity_key(item, _MINION_KEY_FIELDS)
                     for item in self.oppo_minions),
            "|".join(_entity_key(item, _LOCATION_KEY_FIELDS)
                     for item in self.my_locations),
            "|".join(_entity_key(item, _LOCATION_KEY_FIELDS)
                     for item in self.oppo_locations),
            "|".join(_entity_key(item, _HAND_KEY_FIELDS)
                     for item in self.my_hand_cards),
            f"{self.my_total_mana},{self.my_used_mana},{self.my_temp_mana}",
            f"{self.game_num_turns_in_play},{int(self.acted_this_turn)}",
            str(self.discover_choice_count),
            ";".join(f"{index}:{card_id}" for index, card_id
                     in sorted(self.general_choice_cards.items())),
            (_value_key(vars(self.deck_context))
             if self.deck_context is not None else "-"),
            ";".join(sorted(str(item) for item in self.prepared_entity_ids)),
            ";".join(sorted(str(item) for item in self.blocked_action_keys)),
        ]
        digest = hashlib.blake2b(digest_size=16)
        digest.update("\x1f".join(parts).encode("utf-8", "surrogatepass"))
        return digest.hexdigest()


    # ------------------------------------------------------------------ 派生属性
    # 名字与 StrategyState 的同名属性保持一致，供 aiActions / doAction 直接读。
    @property
    def my_last_mana(self):
        """本回合还剩多少可用法力。"""
        return self.my_total_mana - self.my_used_mana + self.my_temp_mana

    @property
    def my_minion_num(self):
        return len(self.my_minions)

    @property
    def oppo_minion_num(self):
        return len(self.oppo_minions)

    @property
    def my_board_slot_num(self):
        """己方场面占用格数（随从 + 地标），上场时要判断是否已满 7 格。"""
        return len(self.my_minions) + len(self.my_locations)

    @property
    def oppo_board_slot_num(self):
        return len(self.oppo_minions) + len(self.oppo_locations)

    def __repr__(self):
        return (f"AiState(turn={self.game_num_turns_in_play}, "
                f"mana={self.my_last_mana}/{self.my_total_mana}, "
                f"board={self.my_minion_num}v{self.oppo_minion_num}, "
                f"hand={len(self.my_hand_cards)}, rank={self.rank})")
