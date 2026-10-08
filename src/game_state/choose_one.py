"""抉择牌（Choose One）识别：只读本地卡表，不写死任何具体卡 id。

盒子对抉择牌的推荐会多出一段「选择卡牌 + 选项名」（见真实日志）：

    打出2号位法术 / 目标是己方3号位 / 选择卡牌 / 动荡能量

其中「动荡能量」是母卡（凶险梦魇 EDR_570）的**选项子卡** EDR_570B 的名字。
Power.log 的 DebugPrintOptions 里同时有 option（母卡）和 subOption（选项），
本模块负责从 cards.json 推导两者的关系，供适配层判断「日志里这批 subOption
确实是这张卡的选项」——判断通过才允许用盒子给的号位兜底，否则一律拒绝。

设计上刻意与 Discover 分开：星舰发射面板同样带「选择卡牌」一行
（`发射1号位星舰 / 选择卡牌 / 发射星舰`），所以只有在源牌确实是 CHOOSE_ONE 卡
时才允许走抉择逻辑。
"""

import json
import re
from functools import lru_cache
from pathlib import Path

# 选项子卡的命名实测有三种：EDR_463a/EDR_463b（小写）、EDR_570A/EDR_570B（大写）、
# EX1_154a/EX1_154b（老卡）。统一成「母卡 id + 一个字母」这一条规则。
_OPTION_SUFFIX = re.compile(r"^[A-Za-z]$")

# 选项一定是「能打出去的东西」，所以只认这几种卡牌类型。母卡旁边常带一张
# 附魔子卡（例如 EDR_570e 暗夜惊怖 +2/+2），它不是选项，排除掉。
_OPTION_TYPES = frozenset({"SPELL", "MINION", "WEAPON", "LOCATION", "HERO"})


def _cards():
    # Read the project's local metadata only; never download in the click path.
    with (Path(__file__).resolve().parents[2] / 'cards.json').open(encoding='utf8') as source:
        return json.load(source)


@lru_cache(maxsize=1)
def choose_one_card_ids():
    return frozenset(card['id'] for card in _cards()
                     if 'CHOOSE_ONE' in card.get('mechanics', []))


@lru_cache(maxsize=1)
def choose_one_card_names():
    """母卡 id → 牌名（例如 EDR_570 → 凶险梦魇）。

    盒子偶尔会把母卡名写进「选择卡牌」那一行，所以适配层需要一个照名字查
    母卡身份的入口。卡表读不出来时返回空表，调用方按「不知道」处理。
    """
    return {card['id']: card.get('name') for card in _cards()
            if 'CHOOSE_ONE' in card.get('mechanics', []) and card.get('name')}


@lru_cache(maxsize=1)
def _card_texts():
    return {card['id']: (card.get('text') or '') for card in _cards()}


def card_text(card_id):
    """任意卡的卡面文字；读不到时返回空串。

    用于「盒子只写了母卡名、而日志里的 subOption 没有名字」时判断某个选项
    要不要目标：这一档无法靠日志区分时，卡面文字是唯一可用的依据。
    """
    if not card_id:
        return ""
    try:
        return _card_texts().get(card_id, "")
    except Exception:
        return ""


@lru_cache(maxsize=1)
def _option_card_ids_by_source():
    """母卡 id → 该卡的选项子卡 id 集合（母卡 id + 一个字母）。"""
    cards = _cards()
    ids = {card['id'] for card in cards}
    by_source = {}
    for card in cards:
        card_id = card['id']
        if card.get('type') not in _OPTION_TYPES:
            continue
        if len(card_id) < 2 or not _OPTION_SUFFIX.match(card_id[-1]):
            continue
        source = card_id[:-1]
        if source in ids:
            by_source.setdefault(source, set()).add(card_id)
    return {source: frozenset(options)
            for source, options in by_source.items()}


def choose_one_option_card_ids(source_card_id):
    """source_card_id 的选项子卡 id 集合；推不出来时返回空集合。

    只用于「日志里这批 subOption 是不是这张卡的选项」这一个判断，
    因此宁可返回空集合（调用方退到按名字匹配），也不做模糊猜测。
    """
    if not source_card_id:
        return frozenset()
    try:
        return _option_card_ids_by_source().get(source_card_id, frozenset())
    except Exception:
        return frozenset()


def is_choose_one_card(card_id) -> bool:
    """card_id 是不是抉择牌；卡表读不出来时保守返回 False。"""
    try:
        return card_id in choose_one_card_ids()
    except Exception:
        return False


def choose_one_card_name(card_id):
    """母卡牌名；读不到时返回 None。"""
    try:
        return choose_one_card_names().get(card_id)
    except Exception:
        return None
