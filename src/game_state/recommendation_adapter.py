"""Map display slots in HSAng instructions to identity-bound manual actions."""

from dataclasses import dataclass, replace
import re

from manual_controller import (
    AttackAction, DiscoverChoiceAction, EndTurnAction, HeroPowerAction,
    LaunchStarshipAction, PlayCardAction, Target, TimelineAction,
    TradeCardAction, UseLocationAction,
)
from src.recommendation_models import ActionKind
from src.game_state.choose_one import (
    card_text,
    choose_one_card_name,
    choose_one_option_card_ids,
    is_choose_one_card,
)
from src.game_state.hand_target import (
    hand_target_cardtype_mismatch,
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
                    if hand_target_cardtype_mismatch(location.card_id,
                                                     target_card):
                        raise RecommendationStateError(
                            "hand_target_cardtype_mismatch")
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
        choice_index = proposed.source.index - 1
        target, target_id = _discover_target(proposed, state)
        parent_entity_id, choice_entity_id = _discover_choice_identity(
            proposed, state, choice_index)
        return AdaptedAction(
            DiscoverChoiceAction(
                choice_index, choice_count,
                timeline_card_id=timeline_card_id,
                target=target,
                choice_entity_id=choice_entity_id,
                choice_parent_entity_id=parent_entity_id),
            None, target_id, "choice_resolved")
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


def _discover_target(proposed, state):
    """发现/选项菜单后面还要点目标时（鲍勃「招募随从」→ 对方随从），解析它。

    返回 (Target|None, entity_id|None)。只接受场面随从与英雄两类目标：
    发现菜单之后的目标永远是场上的实体，不会是自己手牌。
    """
    target = proposed.target
    if target is None:
        return None, None
    side = target.owner
    if side not in {"friendly", "enemy"}:
        raise RecommendationStateError("discover_target_unsupported")
    if target.kind == "hero":
        hero = (getattr(state, "my_hero", None) if side == "friendly"
                else getattr(state, "oppo_hero", None))
        if hero is None:
            raise RecommendationStateError("discover_target_missing")
        entity_id = getattr(hero, "entity_id", None)
        return Target(side, "hero", None, entity_id), entity_id
    if target.kind != "board_slot":
        raise RecommendationStateError("discover_target_unsupported")
    try:
        entry = board_slot(state, side, target.index)
    except RecommendationStateError as exc:
        raise RecommendationStateError("discover_target_out_of_range") from exc
    if entry.kind != "minion":
        raise RecommendationStateError("target_not_minion")
    entity_id = getattr(entry.entity, "entity_id", None)
    return Target(side, "minion", entry.collection_index, entity_id), entity_id


def _discover_choice_identity(proposed, state, choice_index):
    """(父实体 id, 选项实体 id)：执行前用它复核"还是原来那批选项"。

    对不上就返回 (None, None)：这只是加分项，绝不能让本来能点的发现动作失败。
    """
    name = getattr(proposed, "choice_name", None)
    general = getattr(state, "general_choice_cards", {}) or {}
    for parent_id, option in (getattr(state, "power_options", {}) or {}).items():
        choice = (option.get("choices") or {}).get(choice_index)
        if choice is None:
            continue
        if not name or _name_matches(name, choice.get("name")) or _name_matches(
                name, _card_name_of(state, choice.get("card_id"))):
            return parent_id, choice.get("entity_id")
        return None, None
    if choice_index in general:
        # 通用选择（发现/选择一项）：日志里也有选项的实体 id，绑上它同样能在
        # 执行前复核"选项没变"；取不到 id 时只按号位走，不新增失败路径。
        entity_ids = getattr(state, "general_choice_entity_ids", {}) or {}
        return None, entity_ids.get(choice_index)
    return None, None


def _card_name_of(state, card_id):
    if not card_id:
        return None
    try:
        from log_state import query_json_dict
        return query_json_dict(card_id)
    except Exception:
        return None


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
    """把「选择卡牌 X」变成「打出去之后点第 N 个选项」。

    选项定位分四档，任何一档判不出来都**退回原动作**（正常打牌/用技能、不点
    选项），绝不抛错进死循环——线上实测 `choose_one_name_not_unique` 连续重试
    过 3090 次、最长空转 1 分半，就是这里抛错导致的。

      1. 名字全等（归一化后比较，容忍 OCR 的空格/标点/全角差异）；
      2. 小差异容错（编辑距离 ≤1 且长度 ≥3）：OCR 认错一两个字仍然认得出；
      3. 盒子写的是**牌名**（如「暮光侵扰」）：日志里这批 subOption 必须全部属于
         这张卡的选项子卡，才允许用盒子自己的号位；
      4. 都判不出 → 只出牌，不点选项。

    安全约束：盒子点名了某个选项时，该选项必须是唯一命中的那一个；命中多个或
    名字对不上号位，一律拒绝而不是猜——抉择牌两个选项常常差很远（凶险梦魇：
    全场 1 伤 vs 一个受伤随从 +2/+2），点错比不点更糟。
    """
    name = getattr(proposed, "choice_name", None)
    if name is None or source is None:
        return adapted
    card_id = getattr(source, "card_id", None)
    if not is_choose_one_card(card_id):
        # 星舰发射面板也带「选择卡牌」一行（选择卡牌 / 发射星舰），源牌不是抉择牌
        # 就完全不动，保持改动前的行为。
        return adapted
    if (isinstance(adapted.manual_action, PlayCardAction)
            and adapted.manual_action.cardtype not in {"SPELL", "MINION"}):
        raise RecommendationStateError("choose_one_cardtype_unsupported")
    option = getattr(state, "power_options", {}).get(
        getattr(source, "entity_id", None))
    if (option is None or option.get("card_id") != card_id
            or option.get("player") != getattr(state, "my_player_id", None)):
        raise RecommendationStateError("choose_one_options_unavailable")
    choices = option["choices"]
    count = len(choices)
    if count not in (1, 2, 3, 4) or set(choices) != set(range(count)):
        raise RecommendationStateError("choose_one_options_incomplete")
    if count < 2:
        # 单选项抉择游戏自己就结算了，不需要玩家再点一下。
        return adapted

    usable = {index for index, choice in choices.items()
              if _choice_available(choice)}
    index, named = _resolve_choice_index(
        name, card_id, choices, usable, has_target=proposed.target is not None)
    if index is None:
        # 判不出来：只出牌，不点选项（退回改动前的"能打出去"行为，不再死循环）。
        print(f"[推荐] 抉择选项未能确定（面板：{name}），本次只出牌、不点选项。")
        return adapted
    if index not in usable:
        # 盒子点名的那个选项当前不可选（`REQ_ENOUGH_MANA` 之类）：拒绝重试，
        # 绝不静默改成另一个选项——抉择牌两个选项常常差很远。
        raise RecommendationStateError("choose_one_option_unavailable")
    print(f"[推荐] 抉择：{card_id} → {index + 1}号位"
          f"（{choices[index].get('name') or '?'}）")
    return replace(adapted, manual_action=replace(
        adapted.manual_action, choose_one=DiscoverChoiceAction(
            index, count,
            choice_entity_id=choices[index].get("entity_id"),
            choice_parent_entity_id=getattr(source, "entity_id", None))))


def _choice_available(choice):
    """日志里这个选项现在是不是"可点"（REQ_ENOUGH_MANA 这类一律不行）。

    注意：**不能**拿这里的 error 判断"要不要目标"。实测日志里带目标的选项
    在待抉择阶段报的仍是 `NONE`（`EX1_154a` 真实面板就是这种情况），所以
    要不要点目标只信盒子面板上的目标行，不在这里推断。
    """
    return (choice or {}).get("error") in (None, "NONE")


def _choice_needs_target(choice):
    """这个选项**大概**要不要再点一个目标。

    依据两条，任一条成立即算"要目标"：
      * 日志的 `error` 是 `REQ_TARGET_*` 系列（实测不总出现，所以不够）；
      * 卡面文字里有「使…一个随从」「对一个随从」「消灭一个随从」这类写法
        （选项子卡的 card_id 能查到，见 choose_one.card_text）。

    这只用于「号位兜底」那一档判断选哪个选项，**不会**用它去删掉盒子给的
    目标行——实测待抉择阶段带目标的选项也可能报 `NONE`（EX1_154a）。
    """
    error = (choice or {}).get("error") or ""
    if error.startswith("REQ_TARGET"):
        return True
    text = (choice or {}).get("text") or ""
    if not text:
        text = card_text((choice or {}).get("card_id"))
    return bool(_CHOICE_TARGET_TEXT.search(text))


# 卡面里"要点一个目标"的写法（`$` 是卡表的伤害占位符，直接吃掉）。
# 「一个」和「随从」之间可能夹着很长的定语（「消灭一个攻击力小于或等于3的随从」
# 中间有 10 个字），所以第二段给 20 字余量；只认「使/对/消灭/沉默/冻结/变形」
# 打头的句式，避免把「随机召唤一个…随从」这种不用点的也算进来。
_CHOICE_TARGET_TEXT = re.compile(
    r"(?:使|对|消灭|沉默|冻结|变形)[^。；\n]{0,8}?一个[^。；\n]{0,20}?"
    r"(?:随从|角色|地标|恶魔|野兽|龙|鱼人|机械)")


def _resolve_choice_index(name, card_id, choices, usable, has_target):
    """选项号位解析：见 `_with_choose_one` 的四档说明。

    返回 (index|None, named)。named=True 表示"盒子点名了这个选项"，
    False 表示是靠牌名/号位兜底认出来的。
    """
    wanted = _normalize_choice_name(name)
    # 第 1 档：名字全等。
    exact = [index for index, choice in choices.items()
             if _normalize_choice_name(choice.get("name")) == wanted and wanted]
    if len(exact) == 1:
        return exact[0], True
    if len(exact) > 1:
        # 两个选项同名（实测偶发）：不猜，也不退到牌名兜底。
        return None, False
    # 第 2 档：小差异容错（OCR 差一两个字）。
    near = [index for index, choice in choices.items()
            if _names_close(wanted, _normalize_choice_name(choice.get("name")))]
    if len(near) == 1:
        return near[0], True
    if len(near) > 1:
        return None, False
    # 盒子写的是母卡名（真实日志里偶尔出现）：只在这批 subOption 确实都是这张卡
    # 的选项时才敢用号位兜底。分流依据就是盒子有没有给目标行——给目标就挑"要目标"
    # 的那个选项，没给就挑"不要目标"的；仍然分不出来就放弃（退回只出牌）。
    source_name = _normalize_choice_name(choose_one_card_name(card_id))
    if not source_name or wanted != source_name:
        return None, False
    if not _all_choices_are_options(card_id, choices):
        return None, False
    return _pick_by_target_need(choices, usable, has_target), False


def _all_choices_are_options(card_id, choices):
    """日志里这批 subOption 是不是全都是 card_id 的选项子卡。

    只有这里为真，才允许用盒子给的号位兜底——否则宁可不点。
    """
    family = choose_one_option_card_ids(card_id)
    if not family:
        return False
    card_ids = [choice.get("card_id") for choice in choices.values()]
    return bool(card_ids) and all(
        card_id_ in family for card_id_ in card_ids if card_id_)


def _pick_by_target_need(choices, usable, has_target):
    """按"要不要目标"分流：盒子给了目标就挑要目标的那个，没给就挑不要的。

    只在候选唯一时才返回号位；分不出来（含"可点选项一个都不剩"）返回 None，
    由调用方决定拒绝还是退回只出牌。
    """
    if not usable:
        return None
    pool = sorted(usable)
    wanted = [index for index in pool
              if _choice_needs_target(choices[index]) == has_target]
    return wanted[0] if len(wanted) == 1 else None


def _normalize_choice_name(value):
    """卡名归一化：去空白/中英标点/间隔号，全角转半角，统一小写。"""
    if not value:
        return ""
    text = str(value).translate(_FULLWIDTH_TO_HALFWIDTH)
    return _NAME_NOISE.sub("", text).lower()


def _names_close(left, right):
    """两个归一化名字是不是"差一两个字"（OCR 容错）。

    只在长度 ≥3 且编辑距离 ≤1 时算命中：要求长度是为了避免「冰雪绽放」被
    「冰雪」这种截断误命中，限制距离是为了避免把两个不同的选项认成同一个。
    """
    if not left or not right or min(len(left), len(right)) < 3:
        return False
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    return _edit_distance_at_most_one(left, right)


def _edit_distance_at_most_one(left, right):
    """左右是否只差一个增删改（长度差 ≤1 时用）。"""
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = 0
    skipped = False
    for char in longer:
        if index < len(shorter) and shorter[index] == char:
            index += 1
            continue
        if skipped:
            return False
        skipped = True
    return True


_FULLWIDTH_TO_HALFWIDTH = str.maketrans(
    "０１２３４５６７８９（）【】《》，。、；：？！“”‘’ＡＢＣＤＥＦＧ",
    "0123456789()[]<>,.、;:?!\"\"''ABCDEFG")
_NAME_NOISE = re.compile(r"[\s·・．.,，。！!？?、：:；;（）()\[\]【】「」『』<>《》\-—_/～~\"']")


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
    # 卡面要求「一张法术牌 / 随从牌 …」时，号位那张牌的类型必须对得上，
    # 否则宁可拒绝也不要盲点（盒子的号位偶尔会指到不符合条件的牌）。
    if hand_target_cardtype_mismatch(card.card_id, target_card):
        raise RecommendationStateError("hand_target_cardtype_mismatch")
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
