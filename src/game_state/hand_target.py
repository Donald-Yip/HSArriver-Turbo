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
适配层先比内容（手牌那张是不是随从、场面那格是不是随从、是不是正在打出去的
自己），再拿那一行卡名去手牌和场面里对，对得上哪边就点哪边；名字掉了、或者
两边同名两边都说得通，就按场面走，也就是这类卡在这次改动之前的行为——不猜
手牌，也不因为判不出来卡住回合。
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
# 「检视你手牌中的三张牌，选择一张」：先给你看手牌里的若干张，再从中选一张
# （维希度斯的窟穴）。跟上面那条的区别只是张数写法，所以单独一条。
_CHOOSE_HAND_SUBSET = re.compile(
    r"你手牌中的(?:\d+|[一二三四五六七八九十]+)张(?:牌)?(?:，|,)?选(?:择)?一?张")
# 「从你的3张手牌中选择一张」：选择写在手牌后面（处理证据）。
_CHOOSE_FROM_HAND = re.compile(
    r"(?:从|由)你(?:的)?(?:\d+|[一二三四五六七八九十]+)张手牌中[^。；\n]{0,6}选(?:择)?")
# 「使你手牌中一张牌的法力值消耗减少…」：卡面不写「选择」，但打出去后同样要点
# 手牌里的一张（纯净圣母、塔姆辛、活体园林…）。只收「获得/法力值消耗」这类
# 指定一张的效果，并且要求效果不是被随机/触发条件决定的（见 _is_automatic_pick）。
_SPECIFY_HAND_CARD = re.compile(
    r"使[^，。；\n]{0,6}你手牌中一(?:张|个)[^，。；\n]{0,12}(?:获得|法力值消耗)")
# 自动挑牌（不看玩家点哪张）的写法：随机挑一张，或者「在你的英雄攻击后 /
# 在你的回合结束时 / 每当…」这种由触发条件自己决定的对象。
_AUTOMATIC_PICK = re.compile(
    r"随机|一张随机|在你的英雄攻击后|在你的回合结束时|在你的回合开始时|每当"
    r"|在[^，。；\n]{0,6}后，")
# 卡面里的「一张…牌」写法 → 目标必须是什么类型（供执行前校验，见 cardtypes）。
# 只认类型名，学派（邪能/火焰）不在内：Power.log 是否输出 SPELL_SCHOOL 还没验证。
_CARD_TYPE_WORDS = (("法术", "SPELL"), ("随从", "MINION"), ("地标", "LOCATION"),
                    ("武器", "WEAPON"), ("英雄", "HERO"))
_SENTENCE_END = re.compile(r"[。；！\n]")


def _describe(card):
    return f"{card.get('targetingArrowText') or ''}\n{card.get('text') or ''}"


def _sentence_of(text, match):
    """取正则命中处所在的「句」（按 。；！换行 切，不跨中文逗号）。"""
    start = max((text.rfind(ch, 0, match.start()) for ch in "。；！\n"),
                default=-1)
    ends = [pos for pos in (text.find(ch, match.end()) for ch in "。；！\n")
            if pos != -1]
    return text[start + 1:min(ends) if ends else len(text)]


def _is_automatic_pick(text, match) -> bool:
    """命中处这一句是不是「随机/触发条件自己挑一张」（玩家不用点）。"""
    return bool(_AUTOMATIC_PICK.search(_sentence_of(text, match)))


def _match_hand_pattern(text):
    """text 命中的手牌目标句式；没有就返回 None。

    只有「指定一张自己的手牌」的写法才算，随机/自动挑一张的一律不算。
    """
    for pattern in (_CHOOSE_IN_HAND, _CHOOSE_HAND_SUBSET, _CHOOSE_FROM_HAND,
                    _SPECIFY_HAND_CARD):
        match = pattern.search(text)
        if match is not None and not _is_automatic_pick(text, match):
            return match
    return None


def _cardtypes_in(text, match) -> frozenset:
    """命中处那一句里要求的目标类型（「一张法术牌」→ {SPELL}）。

    只看「手牌」到句式结尾这一小段（含正则没吃掉的尾巴，例如「一张法力值
    消耗小于等于…的法术牌」），解析不出来就返回空集合，调用方不做类型校验。
    """
    sentence = _sentence_of(text, match)
    hand_at = sentence.find("手牌")
    head = sentence[hand_at + 2:] if hand_at >= 0 else sentence
    return frozenset(name for word, name in _CARD_TYPE_WORDS if word in head)


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
def _metadata_extended_hand_target_card_ids():
    """「检视/从 N 张手牌中选一张」「使…你手牌中一张…」这些句式命中的卡。"""
    # Read the project's local metadata only; never download in the click path.
    with (Path(__file__).resolve().parents[2] / 'cards.json').open(
            encoding='utf8') as source:
        cards = json.load(source)
    return frozenset(card['id'] for card in cards
                     if _match_hand_pattern(_describe(card)) is not None)


@lru_cache(maxsize=1)
def _metadata_hand_target_cardtypes():
    """卡 → 目标手牌必须是什么类型（空集合 = 不限制）。"""
    with (Path(__file__).resolve().parents[2] / 'cards.json').open(
            encoding='utf8') as source:
        cards = json.load(source)
    found = {}
    for card in cards:
        text = _describe(card)
        match = _match_hand_pattern(text)
        if match is None:
            continue
        types = _cardtypes_in(text, match)
        if types:
            found[card['id']] = types
    return found


@lru_cache(maxsize=1)
def friendly_hand_target_card_ids():
    """只能点自己手牌当目标的卡（数据判定 + 老名单兜底）。"""
    try:
        found = _metadata_hand_target_card_ids()
        extended = _metadata_extended_hand_target_card_ids()
    except Exception:
        return LEGACY_HAND_TARGET_CARD_IDS
    return found | extended | LEGACY_HAND_TARGET_CARD_IDS


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


def hand_target_allowed_cardtypes(card_id) -> frozenset:
    """card_id 要求的手牌目标类型（空集合 = 不限制，例如泛指的「一张牌」）。

    只认卡面里写明的类型词（法术/随从/地标/武器/英雄）；「邪能法术」这类学派
    信息不在内（Power.log 是否输出 SPELL_SCHOOL 还没验证）。
    """
    try:
        return _metadata_hand_target_cardtypes().get(card_id, frozenset())
    except Exception:
        return frozenset()


def hand_target_rule(card_id) -> dict:
    """card_id 的手牌目标规则：{"kind": ..., "cardtypes": frozenset}。

    kind="none"          打出去不会点自己手牌；
    kind="hand"          只能点自己手牌；
    kind="hand_or_board" 手牌/场面两可（靠内容与目标卡名区分，见适配层）。
    """
    if is_friendly_hand_target_card(card_id):
        kind = "hand"
    elif is_hand_or_board_target_card(card_id):
        kind = "hand_or_board"
    else:
        return {"kind": "none", "cardtypes": frozenset()}
    return {"kind": kind, "cardtypes": hand_target_allowed_cardtypes(card_id)}


def hand_target_cardtype_mismatch(card_id, hand_card) -> bool:
    """手牌里这张牌能不能当该卡的手牌目标（只看类型）。

    * 卡面要求具体类型（「一张法术牌」…）而这张牌不是 → True（调用方拒绝）；
    * 卡面只写「一张牌」、或者这张牌的类型未知 → False（不拦，保持原行为）。
    """
    allowed = hand_target_allowed_cardtypes(card_id)
    if not allowed:
        return False
    cardtype = getattr(hand_card, "cardtype", None)
    if not cardtype:
        return False
    return cardtype not in allowed


def allows_hand_target(card_id) -> bool:
    """card_id 打出去时有没有可能点自己手牌（两类合起来，执行前校验用）。"""
    return is_friendly_hand_target_card(card_id) or is_hand_or_board_target_card(
        card_id)
