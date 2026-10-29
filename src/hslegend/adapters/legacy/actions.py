"""Legacy click action DTOs owned by the adapter layer.

These dataclasses describe click gestures only. Card semantics and target
selection live in ``hslegend.adapters.cards`` and the recommendation adapter.
"""

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class Target:
    side: str
    kind: str
    index: Optional[int]
    entity_id: Optional[str] = None


@dataclass(frozen=True)
class PlayCardAction:
    hand_index: int
    card_id: str
    cardtype: str
    gap_index: Optional[int] = None
    target: Optional[Target] = None
    hand_entity_id: Optional[str] = None
    turn_number: Optional[int] = None
    choose_one: Optional["DiscoverChoiceAction"] = None
    # Some mechanics (for example Rewind) keep animating/resolving after the
    # card is committed. Wait before selecting a target or follow-up option.
    settle_delay_seconds: float = 0.0


@dataclass(frozen=True)
class TradeCardAction:
    hand_index: int
    card_id: str
    cardtype: str
    hand_entity_id: Optional[str] = None
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class HeroPowerAction:
    target: Optional[Target] = None
    turn_number: Optional[int] = None
    choose_one: Optional["DiscoverChoiceAction"] = None
    card_id: Optional[str] = None
    hero_power_entity_id: Optional[str] = None
    hero_power_slot: str = "primary"


@dataclass(frozen=True)
class DiscoverChoiceAction:
    choice_index: int
    choice_count: int
    turn_number: Optional[int] = None
    # 时间线选项（回溯/维持）才有：日志里那个选项的卡牌 ID，执行前要再核对一次。
    timeline_card_id: Optional[str] = None
    # 选中选项的实体 id（日志里那个 subOption 的 id）：执行前再核对一次，
    # 选项位置/内容变了就拒绝，避免点错选项。
    choice_entity_id: Optional[str] = None
    # The child card that must occupy this choice slot. Later evidence can bind
    # its entity id without requiring Power.log to be ready before the card play.
    choice_card_id: Optional[str] = None
    # 这个选项属于哪个父实体（抉择牌 = 手牌实体 id）：有它才能精确复核
    # "选项还是原来那批"，不会被日志里同号位的其它残留实体影响。
    choice_parent_entity_id: Optional[str] = None
    # 选项本身还要再点一个目标（鲍勃「招募随从」→ 对方随从）。None = 点完选项就结束。
    target: Optional[Target] = None


@dataclass(frozen=True)
class AssembleChoiceAction:
    """拼装面板：从左右两张牌的上下四个绿色选项中选一个。"""
    choice_index: int
    choice_entity_id: Optional[str] = None
    choice_parent_entity_id: Optional[str] = None
    choice_card_id: Optional[str] = None
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class UseAbilityAction:
    """古神/泰坦随从发动技能：点随从 → 点「选择一个技能」面板里的技能牌 → 目标。

    与 `DiscoverChoiceAction` 的区别：选项面板不是屏幕中间那排发现卡，而是
    「选择一个技能」面板（四张技能牌等距横排，坐标见 `click.SKILL_CARD_POINTS`），
    而且第一下要先点场上的随从把面板打开。号位由适配层按技能名反查记录表得到。
    技能自带目标时（克苏恩的凝视）在同一次手势里接着点，**末尾不右键**——
    右键会把"等待选择目标"取消掉。
    """
    source: Target
    card_id: str
    choice_index: int
    choice_count: int
    choice_name: Optional[str] = None
    target: Optional[Target] = None
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class SelectTargetAction:
    """只点一个场上目标（选项/发现结算后的“选择随从/英雄”）。

    与 `DiscoverChoiceAction.target` 的区别：这里**没有**选项可点，游戏当前
    就停在“请选择目标”状态，盒子打印的是「选择对方N号位卡牌」。只左键点目标，
    不右键取消（取消会把这个目标选择撤销）。
    """
    target: Target
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class UseLocationAction:
    location_index: int
    card_id: str
    location_entity_id: Optional[str] = None
    target: Optional[Target] = None
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class AttackAction:
    attacker: Target
    target: Target
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class LaunchStarshipAction:
    starship_index: int
    card_id: str
    starship_entity_id: Optional[str] = None
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class EndTurnAction:
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class RefreshAction:
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class TimelineAction:
    """点 HSAng 左下时间线提示：回溯(undo) / 维持(keep)。

    HSAng 认为上一手操作失误时会在推荐区弹出“回溯/维持”；OCR 到“回溯”点
    (351,805)，OCR 到“维持”点 (582,805)。该操作只影响 HSAng 侧 UI，不改动
    Power.log，因此 postcondition 是哨兵值 timeline_clicked（由 flow 特判）。
    """
    choice: str  # "undo" 或 "keep"
    turn_number: Optional[int] = None


@dataclass(frozen=True)
class ActionExecutionResult:
    executed: bool
    message: str
    recovery_needed: bool = False
    cancelled: bool = False


class ActionCancelled(RuntimeError):
    """Raised when a cooperative global stop interrupts a click gesture."""


ManualAction = Union[
    PlayCardAction,
    TradeCardAction,
    HeroPowerAction,
    DiscoverChoiceAction,
    AssembleChoiceAction,
    UseAbilityAction,
    SelectTargetAction,
    UseLocationAction,
    AttackAction,
    LaunchStarshipAction,
    EndTurnAction,
    RefreshAction,
    TimelineAction,
]
