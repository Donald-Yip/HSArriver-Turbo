"""Audit the declarative card rule pack and metadata derivations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hslegend.adapters.cards import (  # noqa: E402
    DEFAULT_RULES_PATH,
    CardCatalogError,
    JsonCardSemantics,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate and summarize")
    parser.add_argument("--cards", type=Path, default=ROOT / "cards.json")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    args = parser.parse_args(argv)
    try:
        semantics = JsonCardSemantics(args.cards, rules_path=args.rules)
    except CardCatalogError as exc:
        print(f"[card-rules] FAIL: {exc}", file=sys.stderr)
        return 1
    payload = json.loads(args.rules.read_text(encoding="utf-8"))
    rules = payload.get("cards", {})
    panels = {
        card_id: card.ability_panel
        for card_id, card in semantics.definitions.items()
        if card.ability_panel
    }
    choose_one = {
        card_id: card.choose_one_options
        for card_id, card in semantics.definitions.items()
        if card.choose_one_options
    }
    hand_targets = tuple(sorted(
        card_id for card_id, card in semantics.definitions.items()
        if semantics.allows_hand_target(card_id)
    ))
    if not args.check:
        print(json.dumps({
            "rules": rules,
            "ability_panels": panels,
            "choose_one_options": choose_one,
            "hand_targets": hand_targets,
        }, ensure_ascii=False, indent=2))
        return 0
    print(
        f"[card-rules] PASS: cards={len(semantics.definitions)} "
        f"rule_overrides={len(rules)} ability_panels={len(panels)} "
        f"choose_one={len(choose_one)} hand_targets={len(hand_targets)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
