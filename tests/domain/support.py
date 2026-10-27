"""Shared immutable fixtures for domain tests."""

from __future__ import annotations

from hslegend.domain import ChoiceView, EntityRef, GameSnapshot, PlayerView


FRIENDLY_HERO = EntityRef(1, "hero_friendly", "friendly", "hero", 0)
FRIENDLY_HAND = EntityRef(10, "card_hand", "friendly", "hand", 3)
FRIENDLY_BOARD = EntityRef(11, "card_board", "friendly", "board", 1)
FRIENDLY_DECK = EntityRef(12, "card_deck", "friendly", "deck", 2)
FRIENDLY_WEAPON = EntityRef(13, "weapon", "friendly", "weapon", 0)
FRIENDLY_SECRET = EntityRef(14, "secret", "friendly", "secret", 1)
OPPONENT_HERO = EntityRef(2, "hero_opponent", "opponent", "hero", 0)
OPPONENT_BOARD = EntityRef(20, "opponent_board", "opponent", "board", 1)
CHOICE_OPTION = EntityRef(30, "discover_option", "friendly", "choice", 1)


def friendly_player() -> PlayerView:
    return PlayerView(
        player_id=100,
        side="friendly",
        name="Player",
        hero=FRIENDLY_HERO,
        mana=5,
        max_mana=7,
        health=27,
        armor=2,
        hand=(FRIENDLY_HAND,),
        board=(FRIENDLY_BOARD,),
        deck=(FRIENDLY_DECK,),
        weapon=FRIENDLY_WEAPON,
        secrets=(FRIENDLY_SECRET,),
    )


def opponent_player() -> PlayerView:
    return PlayerView(
        player_id=200,
        side="opponent",
        name="Opponent",
        hero=OPPONENT_HERO,
        mana=3,
        max_mana=5,
        health=24,
        board=(OPPONENT_BOARD,),
    )


def choice() -> ChoiceView:
    return ChoiceView(
        choice_id="discover-1",
        options=(CHOICE_OPTION,),
        choice_type="discover",
        min_choices=1,
        max_choices=1,
        source_entity_id=FRIENDLY_HAND.entity_id,
    )


def snapshot(
    *,
    schema_version: int = 1,
    snapshot_id: str = "snap-1",
    revision: int = 7,
    generation: int = 3,
    turn: int = 5,
    is_my_turn: bool = True,
    is_end: bool = False,
    mana: int = 5,
) -> GameSnapshot:
    return GameSnapshot(
        schema_version=schema_version,
        snapshot_id=snapshot_id,
        log_revision=revision,
        game_generation=generation,
        observed_at=1000.0 + revision,
        turn_number=turn,
        is_my_turn=is_my_turn,
        is_end=is_end,
        my_player=PlayerView(
            player_id=100,
            side="friendly",
            name="Player",
            hero=FRIENDLY_HERO,
            mana=mana,
            max_mana=7,
            health=27,
            armor=2,
            hand=(FRIENDLY_HAND,),
            board=(FRIENDLY_BOARD,),
            deck=(FRIENDLY_DECK,),
            weapon=FRIENDLY_WEAPON,
            secrets=(FRIENDLY_SECRET,),
        ),
        opponent=opponent_player(),
        choices=(choice(),),
    )
