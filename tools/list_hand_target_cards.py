# -*- coding: utf-8 -*-
"""手牌目标卡名单审计：列出 cards.json 里哪些卡会让脚本去点自己手牌。

用法（在仓库根目录执行）：
    python tools/list_hand_target_cards.py

输出三段：
  1. 收录：判定通过、会被当成「点名自己手牌」的卡（kind + 目标类型要求）；
  2. 排除：卡面里出现「手牌」且像在选牌，但被规则挡掉的卡（随机取一张 /
     触发条件自己挑 / 把场面当目标），附上判定用的那一句，便于人工复核；
  3. 名单与 cards.json 对不上时（例如卡表更新导致新句式漏网），直接看这里。

只读本地 cards.json，不联网、不点击。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.game_state.hand_target import (          # noqa: E402
    _AUTOMATIC_PICK, _describe, _match_hand_pattern, _sentence_of,
    hand_target_allowed_cardtypes, hand_target_rule,
)

CARDS_PATH = ROOT / "cards.json"
# 卡面里出现这些字样就值得复核一遍（多了不怕，怕的是漏）。
WATCH_WORDS = ("手牌",)
# 复核清单只列「看起来在选牌」的写法，避免把几千张随机加成的卡都打出来。
LOOKS_LIKE_PICK = ("选择", "检视", "选出", "挑")


def load_cards():
    with CARDS_PATH.open(encoding="utf8") as source:
        cards = json.load(source)
    return cards


def main() -> int:
    if not CARDS_PATH.exists():
        print(f"找不到卡表：{CARDS_PATH}")
        return 1
    cards = load_cards()
    included, excluded = [], []
    for card in cards:
        text = _describe(card)
        if not any(word in text for word in WATCH_WORDS):
            continue
        match = _match_hand_pattern(text)
        if match is not None:
            included.append((card, match))
            continue
        if not any(word in text for word in LOOKS_LIKE_PICK):
            continue
        automatic = _AUTOMATIC_PICK.search(text)
        if automatic is not None:
            excluded.append((card, _sentence_of(text, automatic)))

    print(f"cards.json：{CARDS_PATH}")
    print(f"卡牌总数 {len(cards)}；收录 {len(included)} 张；排除 {len(excluded)} 张\n")

    print("== 收录：会按「点自己手牌」处理 ==")
    for card, match in sorted(included, key=lambda item: item[0]["id"]):
        rule = hand_target_rule(card["id"])
        types = ",".join(sorted(hand_target_allowed_cardtypes(card["id"])))
        print(f"  [{rule['kind']:<14}] {card['id']:<16} {card.get('name')}"
              f"{'  目标类型=' + types if types else ''}")
        print(f"      命中：{_sentence_of(_describe(card), match)}")

    print("\n== 排除：像在选牌，但规则判定不该点手牌（人工复核用） ==")
    for card, sentence in sorted(excluded, key=lambda item: item[0]["id"]):
        print(f"  {card['id']:<16} {card.get('name')}")
        print(f"      {sentence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
