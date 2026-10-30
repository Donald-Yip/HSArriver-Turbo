"""搜索模拟里的「幻影卡」：只承担手牌张数，不承担具体牌面。

为什么需要它
------------
牌库内容对执行层不可见（StrategyState 没有牌库字段，Power.log 也不给对手
牌库顺序），所以"抽一张牌"在模拟里不可能知道抽到的是哪一张。可是**如果模拟
里手牌不 +1，搜索就永远学不到抽牌的价值**——生命分流、栉龙、咒怨之墓在评分
看来等于"什么都没做"，只能靠动作分逐条硬补。

反过来，如果模拟里塞一张真卡（比如"平均随从"），搜索就会规划出"抽到它、
再打出它"的连招；执行层手里并没有那张牌，计划一执行就落空。

所以做成幻影卡：
  * cardtype = "SIMULATED"，getActions() 不为它生成任何动作；
  * card_id = None，按卡牌 id 查属性的地方一律读到空；
  * temporary 跟随来源（咒怨之墓发现的是临时牌，本回合不用就消失）；
  * 手牌已满（10 张）时抽牌直接爆掉，不加卡——这也是真实规则。

于是「手牌 +1 / 牌库 -1」在状态里可见（my_hand、my_deck_left、my_temp_cards
都只读张数），但搜索不会规划出执行层打不出的牌。
"""

from __future__ import annotations

from dataclasses import dataclass

# 手牌上限：满 10 张时抽到的牌会被烧掉，模拟必须一致，否则"满手牌分流"
# 会被算成白赚一张。
HAND_LIMIT = 10

# 幻影卡的类型标记。故意不是 MINION/SPELL：任何按"能不能打出"分支的代码
# （getActions 的 3.1~3.5、dwys 的 _card_play_value）都会自然跳过它。
SIMULATED_CARDTYPE = "SIMULATED"


@dataclass
class SimulatedCard:
    """模拟抽到的一张"匿名牌"。"""

    card_id: str | None = None
    cardtype: str = SIMULATED_CARDTYPE
    current_cost: int = 0
    attack: int = 0
    max_health: int = 0
    damage: int = 0
    zone_pos: int = 0
    temporary: int = 0
    # 标记位：getActions / _has_followup_action 见到它就跳过。
    simulated: int = 1
    name: str = "模拟抽牌"
    entity_id: int = -1

    @property
    def health(self) -> int:
        return self.max_health - self.damage


def is_simulated(entity) -> bool:
    """这张牌是不是模拟注入的幻影卡。"""
    return bool(getattr(entity, "simulated", 0))


def add_simulated_card(state, count: int = 1, temporary: int = 0) -> int:
    """往手牌末尾追加 count 张幻影卡，返回实际加进去的张数。

    手牌满 10 张时返回 0（抽到的牌被烧掉）。zone_pos 取当前最大值 +1，
    保证真实手牌的号位（1 基下标）不变——执行层按号位点牌，模拟绝不能
    因为插队把号位挪了。
    """
    hand = getattr(state, "my_hand_cards", None)
    if hand is None:
        return 0
    added = 0
    next_pos = max((int(getattr(card, "zone_pos", 0) or 0) for card in hand),
                   default=0)
    for _ in range(max(0, int(count))):
        if len(hand) >= HAND_LIMIT:
            break
        next_pos += 1
        hand.append(SimulatedCard(zone_pos=next_pos,
                                  temporary=1 if temporary else 0))
        added += 1
    if added:
        state.simulated_draws = int(getattr(state, "simulated_draws", 0) or 0) + added
    return added
