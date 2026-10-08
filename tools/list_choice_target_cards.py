# -*- coding: utf-8 -*-
"""「抉择 / 发现之后还要再选目标」的卡表审计。

用法（在仓库根目录执行）：
    python tools/list_choice_target_cards.py

背景：`BG31_BOBt2 招募随从`「选完选项还要点一个对方随从」这类牌，脚本必须把
「点选项 → 点目标」当成一个完整手势，否则会停在半途卡到烧绳。名单不写死在代码里，
全部由 cards.json 推导，所以卡表更新后跑一遍这个脚本就能看出有没有漏。

输出三段：
  1. 抉择牌：`CHOOSE_ONE` 且选项子卡里有「要目标」的（会走"先抉择再选目标"）；
  2. 选项子卡明细：每张选项子卡的 card_id、要不要目标、判定依据；
  3. 发现类：卡面同时出现「发现」和「一个随从/角色」的卡（可能也要先选目标）。

只读本地 cards.json，不联网、不点击。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.game_state.choose_one import (          # noqa: E402
    card_text, choose_one_card_ids, choose_one_card_names,
    choose_one_option_card_ids,
)
from src.game_state.recommendation_adapter import (  # noqa: E402
    _CHOICE_TARGET_TEXT,
)

CARDS_PATH = ROOT / "cards.json"
# 发现类里值得复核的写法（卡面同时出现这些词，说明发现前后可能还要点目标）。
DISCOVER_WATCH = ("发现",)
TARGET_HINTS = ("一个随从", "一个角色", "一个敌方", "一个友方", "选择一个")


def load_cards():
    with CARDS_PATH.open(encoding="utf8") as source:
        return json.load(source)


def needs_target(card_id) -> bool:
    return bool(_CHOICE_TARGET_TEXT.search(card_text(card_id)))


def main() -> int:
    if not CARDS_PATH.exists():
        print(f"找不到卡表：{CARDS_PATH}")
        return 1
    cards = load_cards()
    by_id = {card["id"]: card for card in cards}
    names = choose_one_card_names()

    with_options, option_detail = [], []
    for card_id in sorted(choose_one_card_ids()):
        options = sorted(choose_one_option_card_ids(card_id))
        if not options:
            continue
        targeted = [option for option in options if needs_target(option)]
        option_detail.append((card_id, names.get(card_id), options, targeted))
        if targeted:
            with_options.append((card_id, names.get(card_id), targeted))

    discover = []
    for card in cards:
        text = card.get("text") or ""
        if not all(word in text for word in DISCOVER_WATCH):
            continue
        if not any(hint in text for hint in TARGET_HINTS):
            continue
        discover.append(card)

    print(f"cards.json：{CARDS_PATH}")
    print(f"卡牌总数 {len(cards)}；抉择牌 {len(choose_one_card_ids())} 张；"
          f"其中「选项需要目标」{len(with_options)} 张\n")

    print("== 抉择牌：有选项需要目标（走「先抉择、再选目标」） ==")
    for card_id, name, targeted in with_options:
        detail = "、".join(
            f"{option}({by_id.get(option, {}).get('name', '?')})"
            for option in targeted)
        print(f"  {card_id:<16} {name:<12} 需要目标的选项：{detail}")

    print("\n== 选项子卡明细（要不要目标由卡面判定） ==")
    for card_id, name, options, targeted in option_detail:
        marks = "、".join(
            f"{option}:{'要目标' if option in targeted else '不要'}"
            for option in options)
        print(f"  {card_id:<16} {name:<12} {marks}")

    print("\n== 发现类：卡面同时出现「发现」+ 选目标字样（可能需要复核） ==")
    for card in sorted(discover, key=lambda item: item["id"]):
        print(f"  {card['id']:<16} {card.get('name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
