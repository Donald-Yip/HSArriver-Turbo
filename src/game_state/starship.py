"""Starship recognition is driven by local card metadata, not one class card ID.

群友的星舰识别改进：原先只认 `SC_999t` 一张卡，实际带 STARSHIP 机制的卡有多张
（本地 cards.json 里有 8 张），所以改成查卡牌元数据。
"""

import json
from functools import lru_cache
from pathlib import Path

# 卡牌数据缺失/损坏时的兜底：至少保留原实现唯一硬编码的那张星舰卡，
# 避免“读不到卡片数据”把普通攻击一起拖垮。
LEGACY_STARSHIP_CARD_ID = "SC_999t"


@lru_cache(maxsize=1)
def starship_card_ids():
    # Read the project's local metadata only; never download in the click path.
    with (Path(__file__).resolve().parents[2] / 'cards.json').open(encoding='utf8') as source:
        return frozenset(card['id'] for card in json.load(source)
                         if 'STARSHIP' in card.get('mechanics', []))


def is_starship_card(card_id) -> bool:
    """card_id 是不是星舰卡；卡牌数据读不出来时退回原来的单卡判断。"""
    try:
        return card_id in starship_card_ids()
    except Exception:
        return card_id == LEGACY_STARSHIP_CARD_ID
