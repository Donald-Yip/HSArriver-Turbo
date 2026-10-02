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
    一个随从投向选中的随从」属于**第三类**：前者（残恶梦魇）手牌和场面都能选，
    但盒子只给号位，两个编号空间又不通用，所以要另想办法（见下）；后者目标
    其实在场面，直接不收。

残恶梦魇这类两可目标：真实面板在「目标是己方N号位」这一行后面还会打印一行
目标卡名（对真实运行日志做了统计——带目标行的推荐比不带的多正好一行），所以
适配层用那一行卡名去手牌和场面里对，对得上哪边就点哪边；对不上（OCR 掉了名字、
或者两边同名）就退回「只有一边能对上号位」的判断，两边都能对上宁可报错不点，
也不点错目标。
"""

import json
import re
from functools import lru_cache
from pathlib import Path

# 卡牌数据缺失/损坏时的兜底：至少保留新实现之前硬编码的那两张卡，
# 避免「读不到卡片数据」让本来能用的弃牌流程一起失效。
LEGACY_HAND_TARGET_CARD_IDS = frozenset({"CATA_490", "CATA_563"})

# 手牌和场面都能选的卡（残恶梦魇）：盒子只给号位，而手牌和场面各有一套编号，
# 所以这类卡不能只看号位，要靠面板上的目标卡名区分（见 recommendation_adapter）。
# 数据缺失时兜底这一张。
LEGACY_HAND_OR_BOARD_CARD_IDS = frozenset({"CATA_161"})

# 「选择你手牌中的一张X」：允许「选择并吸收你手牌中一张…」这种多几个字的写法，
# 但不跨逗号/句号，免得把「选择一个敌方随从……手牌中」也算进来。
_CHOOSE_IN_HAND = re.compile(
    r"选择[^，。；\n]{0,6}?手牌中(?:的)?一(?:张|个)"
    r"|选择一张你?的?手牌")
# 「选择你手牌中或战场上的一个随从」：目标两可，单独一类。
_CHOOSE_IN_HAND_OR_BOARD = re.compile(
    r"选择[^，。；\n]{0,6}?手牌中?或(?:战场|场上)")


def _describe(card):
    return f"{card.get('targetingArrowText') or ''}\n{card.get('text') or ''}"


def _metadata_card_ids(pattern):
    # Read the project's local metadata only; never download in the click path.
    with (Path(__file__).resolve().parents[2] / 'cards.json').open(
            encoding='utf8') as source:
        cards = json.load(source)
    return frozenset(card['id'] for card in cards
                     if pattern.search(_describe(card)))


@lru_cache(maxsize=1)
def _metadata_hand_target_card_ids():
    return _metadata_card_ids(_CHOOSE_IN_HAND)


@lru_cache(maxsize=1)
def _metadata_hand_or_board_card_ids():
    return _metadata_card_ids(_CHOOSE_IN_HAND_OR_BOARD)


@lru_cache(maxsize=1)
def friendly_hand_target_card_ids():
    """只能点自己手牌当目标的卡（数据判定 + 老名单兜底）。"""
    try:
        found = _metadata_hand_target_card_ids()
    except Exception:
        return LEGACY_HAND_TARGET_CARD_IDS
    return found | LEGACY_HAND_TARGET_CARD_IDS


@lru_cache(maxsize=1)
def hand_or_board_target_card_ids():
    """手牌和场面都能当目标的卡，光看号位分不出是哪一边。"""
    try:
        found = _metadata_hand_or_board_card_ids()
    except Exception:
        return LEGACY_HAND_OR_BOARD_CARD_IDS
    return found | LEGACY_HAND_OR_BOARD_CARD_IDS


def is_friendly_hand_target_card(card_id) -> bool:
    """card_id 打出去时只能选一张自己的手牌当目标。"""
    return card_id in friendly_hand_target_card_ids()


def is_hand_or_board_target_card(card_id) -> bool:
    """card_id 打出去时可以选自己的手牌，也可以选场上的随从。"""
    return card_id in hand_or_board_target_card_ids()


def allows_hand_target(card_id) -> bool:
    """card_id 打出去时有没有可能点自己手牌（两类合起来，执行前校验用）。"""
    return is_friendly_hand_target_card(card_id) or is_hand_or_board_target_card(
        card_id)
