"""Map display slots in HSAng instructions to identity-bound manual actions."""

from dataclasses import dataclass, replace
import re

from manual_controller import (
    AttackAction, DiscoverChoiceAction, EndTurnAction, HeroPowerAction,
    LaunchStarshipAction, PlayCardAction, Target, TimelineAction,
    TradeCardAction, UseLocationAction,
)
from src.recommendation_models import ActionKind
from src.game_state.choose_one import choose_one_card_ids
from src.game_state.hand_target import (
    is_friendly_hand_target_card,
    is_hand_or_board_target_card,
)
from src.game_state.starship import is_starship_card


class RecommendationStateError(ValueError):
    pass


@dataclass(frozen=True)
class BoardEntry:
    kind: str
    collection_index: int
    entity: object


@dataclass(frozen=True)
class AdaptedAction:
    manual_action: object
    source_entity_id: str | None
    target_entity_id: str | None
    postcondition: str


def ordered_board(state, side):
    minions = state.my_minions if side == "friendly" else state.oppo_minions
    locations = (getattr(state, "my_locations", []) if side == "friendly"
                 else getattr(state, "oppo_locations", []))
    entries = [BoardEntry("minion", i, entity)
               for i, entity in enumerate(minions)]
    entries.extend(BoardEntry("location", i, entity)
                   for i, entity in enumerate(locations))
    positions = [getattr(entry.entity, "zone_pos", 0) for entry in entries]
    if any(position <= 0 for position in positions):
        raise RecommendationStateError("board_position_unknown")
    if len(set(positions)) != len(positions):
        raise RecommendationStateError("duplicate_board_position")
    return tuple(sorted(entries, key=lambda entry: entry.entity.zone_pos))


def board_slot(state, side, one_based_index):
    board = ordered_board(state, side)
    if not 1 <= one_based_index <= len(board):
        raise RecommendationStateError("board_slot_out_of_range")
    return board[one_based_index - 1]


def adapt_action(proposed, state):
    if proposed.turn_number != state.game_num_turns_in_play:
        raise RecommendationStateError("turn_changed")
    if proposed.action == ActionKind.PLAY_CARD:
        adapted = _adapt_play_card(proposed, state)
        card = state.my_hand_cards[proposed.source.index - 1]
        return _with_choose_one(proposed, state, adapted, card)
    if proposed.action == ActionKind.TRADE_CARD:
        return _adapt_trade_card(proposed, state)
    if proposed.action == ActionKind.USE_HERO_POWER:
        adapted = _adapt_hero_power(proposed, state)
        return _with_choose_one(
            proposed, state, adapted, getattr(state, "my_hero_power", None))
    if proposed.action == ActionKind.ATTACK:
        return _adapt_attack(proposed, state)
    if proposed.action == ActionKind.LAUNCH_STARSHIP:
        return _adapt_starship(
            board_slot(state, "friendly", proposed.source.index))
    if proposed.action == ActionKind.USE_LOCATION:
        entry = board_slot(state, "friendly", proposed.source.index)
        if entry.kind != "location":
            raise RecommendationStateError("source_not_location")
        location = entry.entity
        target = None
        target_id = None
        if proposed.target is not None:
            side = proposed.target.owner
            if side not in {"friendly", "enemy"}:
                raise RecommendationStateError(
                    "location_target_unsupported")
            if proposed.target.kind == "hero":
                hero = (getattr(state, "my_hero", None)
                        if side == "friendly"
                        else getattr(state, "oppo_hero", None))
                if hero is None:
                    raise RecommendationStateError(
                        "location_target_missing")
                target_id = getattr(hero, "entity_id", None)
                target = Target(side, "hero", None, target_id)
            elif proposed.target.kind == "board_slot":
                if (side == "friendly"
                        and is_friendly_hand_target_card(location.card_id)):
                    # 守护巨龙之厅这类地标让你选「手牌中的一张随从牌」，
                    # 盒子同样写成「目标是我方N号位」，指的是手牌不是场面。
                    target_index = proposed.target.index - 1
                    if not 0 <= target_index < len(state.my_hand_cards):
                        raise RecommendationStateError(
                            "hand_target_out_of_range")
                    target_card = state.my_hand_cards[target_index]
                    target_id = getattr(target_card, "entity_id", None)
                    target = Target(
                        "friendly", "hand", target_index, target_id)
                else:
                    target_entry = board_slot(
                        state, side, proposed.target.index)
                    if target_entry.kind != "minion":
                        raise RecommendationStateError("target_not_minion")
                    target_id = getattr(
                        target_entry.entity, "entity_id", None)
                    target = Target(
                        side, "minion", target_entry.collection_index,
                        target_id)
            else:
                raise RecommendationStateError(
                    "location_target_unsupported")
        manual = UseLocationAction(
            entry.collection_index, location.card_id,
            getattr(location, "entity_id", None), target=target)
        return AdaptedAction(manual, getattr(location, "entity_id", None),
                             target_id, "location_changed")
    if proposed.action == ActionKind.CHOOSE_DISCOVER:
        choice_count = getattr(state, "discover_choice_count", None)
        if choice_count not in (1, 2, 3, 4):
            raise RecommendationStateError("discover_choice_count_unavailable")
        if (proposed.source is None
                or proposed.source.kind != "discover_slot"
                or proposed.source.owner != "friendly"
                or not 1 <= proposed.source.index <= choice_count):
            raise RecommendationStateError("discover_slot_out_of_range")
        # 盒子只写「选择我方1/2号位卡牌」时，光看编号分不出这是发现还是时间线，
        # 得看日志里的选项卡牌 ID：正好是回溯/维持那两张就走时间线按钮。
        choices = getattr(state, "general_choice_cards", {})
        timeline_card_id = None
        if (choice_count == 2 and set(choices) == {0, 1}
                and set(choices.values()) == {"TIME_000ta", "TIME_000tb"}):
            timeline_card_id = choices[proposed.source.index - 1]
        return AdaptedAction(
            DiscoverChoiceAction(
                proposed.source.index - 1, choice_count,
                timeline_card_id=timeline_card_id),
            None, None, "choice_resolved")
    if proposed.action == ActionKind.END_TURN:
        return AdaptedAction(EndTurnAction(), None, None, "turn_changed")
    if proposed.action == ActionKind.TIMELINE_UNDO:
        # 日志能确认是时间线选择时优先走这条（点的是选项卡，不是文字按钮）。
        timeline = _logged_timeline(state, "TIME_000tb")
        if timeline is not None:
            return timeline
        # 回溯：撤销 HSAng 时间线里上一步操作，纯 HSAng 侧 UI，不动 Power.log。
        return AdaptedAction(
            TimelineAction("undo"), None, None, "timeline_clicked")
    if proposed.action == ActionKind.TIMELINE_KEEP:
        timeline = _logged_timeline(state, "TIME_000ta")
        if timeline is not None:
            return timeline
        # 维持：保留当前操作、关掉 HSAng 的撤销提示，同样不改 Power.log。
        return AdaptedAction(
            TimelineAction("keep"), None, None, "timeline_clicked")
    raise RecommendationStateError("unsupported_action")


def _logged_timeline(state, card_id):
    """日志里的选择项正好是回溯/维持时，返回绑定该选项卡的动作。"""
    choices = getattr(state, "general_choice_cards", {})
    if (getattr(state, "discover_choice_count", None) != 2
            or set(choices) != {0, 1}
            or set(choices.values()) != {"TIME_000ta", "TIME_000tb"}):
        return None
    index = next(i for i, value in choices.items() if value == card_id)
    return AdaptedAction(
        DiscoverChoiceAction(index, 2, timeline_card_id=card_id),
        None, None, "choice_resolved")


def _with_choose_one(proposed, state, adapted, source):
    name = getattr(proposed, "choice_name", None)
    if name is None or source is None:
        return adapted
    if source.card_id not in choose_one_card_ids():
        return adapted
    if (isinstance(adapted.manual_action, PlayCardAction)
            and adapted.manual_action.cardtype not in {"SPELL", "MINION"}):
        raise RecommendationStateError("choose_one_cardtype_unsupported")
    option = getattr(state, "power_options", {}).get(
        getattr(source, "entity_id", None))
    if (option is None or option.get("card_id") != source.card_id
            or option.get("player") != getattr(state, "my_player_id", None)):
        raise RecommendationStateError("choose_one_options_unavailable")
    choices = option["choices"]
    count = len(choices)
    if count not in (1, 2, 3, 4) or set(choices) != set(range(count)):
        raise RecommendationStateError("choose_one_options_incomplete")
    matches = [index for index, choice in choices.items()
               if choice.get("name") == name]
    if len(matches) != 1:
        raise RecommendationStateError("choose_one_name_not_unique")
    index = matches[0]
    if choices[index].get("error") not in {"NONE", "REQ_TARGET_TO_PLAY"}:
        raise RecommendationStateError("choose_one_option_unavailable")
    return replace(adapted, manual_action=replace(
        adapted.manual_action, choose_one=DiscoverChoiceAction(index, count)))


def _friendly_hand_target_choice(proposed, state, source_index, card):
    """盒子的「目标是我方N号位」是不是指手牌；不是就返回 None（按场面目标走）。

    两类卡：
      * 只能选自己手牌的（魔眼秘术师…）——号位就是手牌位置，直接用；
      * 手牌和场面都能选的（残恶梦魇）——盒子只给号位，手牌和场面各有一套编号，
        先比内容（手牌那张是不是随从、场面那格是不是随从、是不是正在打的自己），
        再看面板上那行目标卡名；还是分不出就按场面走（改动之前的行为）。
    """
    target = proposed.target
    if (target is None or target.owner != "friendly"
            or target.kind not in {"hand_slot", "board_slot"}):
        return None
    hand_only = is_friendly_hand_target_card(card.card_id)
    if not hand_only and not is_hand_or_board_target_card(card.card_id):
        return None
    target_index = target.index - 1
    if not hand_only:
        # 两可的看内容定不了就按场面走（＝这类卡在这次改动之前的行为）：
        # 不猜也要保证「能打出去」，不然会一直重试到烧绳。
        if _hand_or_board_side(proposed, state, target.index, source_index,
                               ) != "hand":
            return None
    if not 0 <= target_index < len(state.my_hand_cards):
        raise RecommendationStateError("hand_target_out_of_range")
    if target_index == source_index:
        raise RecommendationStateError("hand_target_is_source")
    target_card = state.my_hand_cards[target_index]
    return Target("friendly", "hand", target_index,
                  getattr(target_card, "entity_id", None))


def _hand_card_candidate(state, one_based_index, source_index):
    """手牌里那个号位能不能当随从目标（号位按手牌位置数）。

    正在打出去的那张牌不算——它已经不在手牌里了，盒子的号位是出牌前的手牌位置。
    """
    index = one_based_index - 1
    hand = state.my_hand_cards
    if not 0 <= index < len(hand) or index == source_index:
        return None
    card = hand[index]
    if getattr(card, "cardtype", None) != "MINION":
        return None
    return card


def _board_card_candidate(state, one_based_index):
    """场面那个号位能不能当随从目标（号位按站位，含地标占位）。"""
    try:
        entry = board_slot(state, "friendly", one_based_index)
    except RecommendationStateError:
        return None
    return entry.entity if entry.kind == "minion" else None


def _hand_or_board_side(proposed, state, one_based_index, source_index):
    """「手牌/场面两可」的目标在哪边：'hand' / 'board' / None（判不出）。"""
    hand_card = _hand_card_candidate(state, one_based_index, source_index)
    board_card = _board_card_candidate(state, one_based_index)
    if hand_card is None and board_card is None:
        raise RecommendationStateError("hand_or_board_target_missing")
    name = getattr(proposed, "target_name", None)
    if name:
        from_hand = _name_matches(name, getattr(hand_card, "name", None))
        from_board = _name_matches(name, getattr(board_card, "name", None))
        if from_hand != from_board:
            return "hand" if from_hand else "board"
    if hand_card is not None and board_card is None:
        return "hand"
    if board_card is not None and hand_card is None:
        return "board"
    return None


def _name_matches(ocr_name, card_name):
    """OCR 出来的卡名和日志里的卡名是否是同一张（容忍标点和少字）。"""
    if not ocr_name or not card_name:
        return False

    def normalize(text):
        return re.sub(
            r"[\s·・．.，,。！!？?、：:；;（）()\[\]【】「」『』]",
            "", str(text)).lower()

    left, right = normalize(ocr_name), normalize(card_name)
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= 3 and (left in right or right in left)


def _adapt_play_card(proposed, state):
    index = proposed.source.index - 1
    if not 0 <= index < len(state.my_hand_cards):
        raise RecommendationStateError("hand_slot_out_of_range")
    card = state.my_hand_cards[index]
    gap = None
    manual_target = None
    target_id = None
    if card.cardtype in {"MINION", "LOCATION"}:
        board_count = len(ordered_board(state, "friendly"))
        gap = (board_count if proposed.destination is None
               else proposed.destination.index - 1)
        if not 0 <= gap <= board_count:
            raise RecommendationStateError("minion_destination_out_of_range")
    if proposed.target is not None:
        hand_target = _friendly_hand_target_choice(proposed, state, index, card)
        if hand_target is not None:
            target_id = hand_target.entity_id
            manual_target = hand_target
        elif proposed.card_type in {"SPELL", "MINION"}:
            target_error = ("spell_target" if proposed.card_type == "SPELL"
                            else "minion_target")
            if proposed.target.owner not in {"friendly", "enemy"}:
                raise RecommendationStateError(f"{target_error}_unsupported")
            if proposed.target.kind == "hero":
                hero = (getattr(state, "my_hero", None)
                        if proposed.target.owner == "friendly"
                        else getattr(state, "oppo_hero", None))
                if hero is None:
                    raise RecommendationStateError(f"{target_error}_missing")
                target_id = getattr(hero, "entity_id", None)
                manual_target = Target(
                    proposed.target.owner, "hero", None, target_id)
            elif proposed.target.kind == "board_slot":
                target = board_slot(
                    state, proposed.target.owner, proposed.target.index)
                if target.kind != "minion":
                    raise RecommendationStateError("target_not_minion")
                target_id = getattr(target.entity, "entity_id", None)
                manual_target = Target(
                    proposed.target.owner, "minion",
                    target.collection_index, target_id)
            else:
                raise RecommendationStateError(f"{target_error}_unsupported")
        else:
            raise RecommendationStateError("targeted_action_unsupported")
    manual = PlayCardAction(
        index, card.card_id, card.cardtype, gap_index=gap,
        target=manual_target,
        hand_entity_id=getattr(card, "entity_id", None))
    return AdaptedAction(
        manual, getattr(card, "entity_id", None), target_id,
                         "hand_card_left")


def _adapt_trade_card(proposed, state):
    index = proposed.source.index - 1
    if not 0 <= index < len(state.my_hand_cards):
        raise RecommendationStateError("hand_slot_out_of_range")
    card = state.my_hand_cards[index]
    entity_id = getattr(card, "entity_id", None)
    manual = TradeCardAction(
        index, card.card_id, card.cardtype, hand_entity_id=entity_id)
    return AdaptedAction(
        manual, entity_id, None, "hand_card_left")


def _adapt_hero_power(proposed, state):
    power = getattr(state, "my_hero_power", None)
    target = None
    target_id = None
    if proposed.target is not None:
        side = proposed.target.owner
        if side not in {"friendly", "enemy"}:
            raise RecommendationStateError("hero_power_target_unsupported")
        if proposed.target.kind == "hero":
            hero = (getattr(state, "my_hero", None)
                    if side == "friendly"
                    else getattr(state, "oppo_hero", None))
            if hero is None:
                raise RecommendationStateError("hero_power_target_missing")
            target_id = getattr(hero, "entity_id", None)
            target = Target(side, "hero", None, target_id)
        elif proposed.target.kind == "board_slot":
            entry = board_slot(state, side, proposed.target.index)
            if entry.kind != "minion":
                raise RecommendationStateError("target_not_minion")
            target_id = getattr(entry.entity, "entity_id", None)
            target = Target(
                side, "minion", entry.collection_index, target_id)
        else:
            raise RecommendationStateError("hero_power_target_unsupported")
    return AdaptedAction(
        HeroPowerAction(target=target),
        getattr(power, "entity_id", None), target_id,
        "hero_power_changed")


def _adapt_starship(source):
    """明确发射指令（「发射N号位星舰」）：只认带 STARSHIP 机制的卡。"""
    if source.kind != "minion" or not is_starship_card(
            getattr(source.entity, "card_id", None)):
        raise RecommendationStateError("source_not_starship")
    source_id = getattr(source.entity, "entity_id", None)
    return AdaptedAction(
        LaunchStarshipAction(
            source.collection_index, source.entity.card_id, source_id),
        source_id, None, "starship_launched")


def _adapt_attack(proposed, state):
    if proposed.source.kind == "hero":
        hero = getattr(state, "my_hero", None)
        if hero is None:
            raise RecommendationStateError("friendly_hero_missing")
        source_target = Target(
            "friendly", "hero", None, getattr(hero, "entity_id", None))
        source_id = getattr(hero, "entity_id", None)
    else:
        source = board_slot(state, "friendly", proposed.source.index)
        if source.kind != "minion":
            raise RecommendationStateError("source_not_minion")
        source_id = getattr(source.entity, "entity_id", None)
        if (proposed.target is None
                and is_starship_card(getattr(source.entity, "card_id", None))):
            # 星舰在场时盒子写的是「操作N号位随从攻击」，但这张卡没有攻击目标
            # 可选——它其实是要发射。带目标的星舰攻击仍按普通攻击处理，
            # 避免把已发射星舰的攻击误当成再次发射。
            manual = LaunchStarshipAction(
                source.collection_index,
                source.entity.card_id,
                source_id,
            )
            return AdaptedAction(
                manual, source_id, None, "starship_launched")
        source_target = Target(
            "friendly", "minion", source.collection_index, source_id)
    if proposed.target is None:
        raise RecommendationStateError("attack_target_required")
    if proposed.target.kind == "hero":
        hero = getattr(state, "oppo_hero", None)
        if hero is None:
            raise RecommendationStateError("enemy_hero_missing")
        target_id = getattr(hero, "entity_id", None)
        manual_target = Target("enemy", "hero", None, target_id)
    else:
        target = board_slot(state, "enemy", proposed.target.index)
        if target.kind != "minion":
            raise RecommendationStateError("target_not_minion")
        target_id = getattr(target.entity, "entity_id", None)
        manual_target = Target("enemy", "minion", target.collection_index,
                               target_id)
    postcondition = ("hero_combat_state_changed"
                     if proposed.source.kind == "hero"
                     else "combat_state_changed")
    return AdaptedAction(AttackAction(source_target, manual_target),
                         source_id, target_id, postcondition)
