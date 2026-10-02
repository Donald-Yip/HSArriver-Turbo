"""手牌目标识别改为读本地卡牌数据，不再写死单张卡。

有些战吼/地标会让你**点自己手牌里的一张牌**（弃掉、变形、洗回牌库、吸走、
加成……），盒子对这种目标同样显示成「目标是我方N号位」，点击流程和普通随从
目标完全不同（要点手牌扇形区，而且要按打完牌之后的手牌数量算位置）。

原先这份名单写死在 `manual_controller.FRIENDLY_HAND_TARGET_CARD_IDS` 里，只有
魔眼秘术师（CATA_490）和雷鸣流云（CATA_563）两张；实测同类卡还有一批（魔眼
秘术师所在版本的一整套选牌循环，以及 AV_308 墓地污染者、JAIL_313 偷贩炼金师
等），所以改成按卡牌描述判定。

判定只在卡牌数据里找「选择你手牌中的一张牌」这种说法，并且要求「选择」和
「手牌中一张」紧挨着：
  * 「将一个随从移回你的手牌」「将一张复制置入你的手牌」这类手牌只是结果
    落点，目标其实在战场上 —— 不能收；
  * 「随机使你手牌中的一张随从牌获得+1/+1」是随机不看目标 —— 不能收；
  * 「选择你手牌中或战场上的一个随从」「选择一个敌方随从。随机将你手牌中的
    一个随从投向选中的随从」的目标可能在场面 —— 也不能收，否则会把场面目标
    误当成手牌目标点错牌。
"""

import json
import re
from functools import lru_cache
from pathlib import Path

# 卡牌数据缺失/损坏时的兜底：至少保留新实现之前硬编码的那两张卡，
# 避免「读不到卡片数据」让本来能用的弃牌流程一起失效。
LEGACY_HAND_TARGET_CARD_IDS = frozenset({"CATA_490", "CATA_563"})

# 「选择你手牌中的一张X」：允许「选择并吸收你手牌中一张…」这种多几个字的写法，
# 但不跨逗号/句号，免得把「选择一个敌方随从……手牌中」也算进来。
_CHOOSE_IN_HAND = re.compile(
    r"选择[^，。；\n]{0,6}?手牌中(?:的)?一(?:张|个)"
    r"|选择一张你?的?手牌")


@lru_cache(maxsize=1)
def _metadata_hand_target_card_ids():
    # Read the project's local metadata only; never download in the click path.
    with (Path(__file__).resolve().parents[2] / 'cards.json').open(
            encoding='utf8') as source:
        cards = json.load(source)
    return frozenset(
        card['id'] for card in cards
        if _CHOOSE_IN_HAND.search(
            f"{card.get('targetingArrowText') or ''}\n{card.get('text') or ''}")
    )


@lru_cache(maxsize=1)
def friendly_hand_target_card_ids():
    """需要点自己手牌当目标的卡牌集合（数据判定 + 老名单兜底）。"""
    try:
        found = _metadata_hand_target_card_ids()
    except Exception:
        return LEGACY_HAND_TARGET_CARD_IDS
    return found | LEGACY_HAND_TARGET_CARD_IDS


def is_friendly_hand_target_card(card_id) -> bool:
    """card_id 打出去时是不是要选一张自己的手牌当目标。"""
    return card_id in friendly_hand_target_card_ids()
