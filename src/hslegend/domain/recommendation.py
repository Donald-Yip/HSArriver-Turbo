"""Pure recommendation action semantics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .primitives import PrimitiveMixin


class ActionKind(str, Enum):
    MULLIGAN = "mulligan"
    PLAY_CARD = "play_card"
    TRADE_CARD = "trade_card"
    USE_HERO_POWER = "use_hero_power"
    ATTACK = "attack"
    USE_LOCATION = "use_location"
    LAUNCH_STARSHIP = "launch_starship"
    # 站场随从（古神本体）的技能激活：本地卡组策略用它，执行层复用
    # ``_adapt_use_ability`` 走「点本体 → 点技能面板 → 目标」的同一条手势。
    USE_MINION_ABILITY = "use_minion_ability"
    USE_ABILITY = "use_ability"
    ASSEMBLE_CHOICE = "assemble_choice"
    CHOOSE_DISCOVER = "choose_discover"
    SELECT_TARGET = "select_target"
    END_TURN = "end_turn"
    TIMELINE_UNDO = "timeline_undo"
    TIMELINE_KEEP = "timeline_keep"


@dataclass(frozen=True, slots=True)
class SlotRef(PrimitiveMixin):
    kind: str
    owner: str | None = None
    index: int | None = None


@dataclass(frozen=True, slots=True)
class ProposedAction(PrimitiveMixin):
    action_id: str
    frame_id: str
    created_at: float
    turn_number: int
    log_revision: int
    raw_instruction: str
    normalized_instruction: str
    action: ActionKind
    source: SlotRef | None = None
    destination: SlotRef | None = None
    target: SlotRef | None = None
    mulligan_slots: tuple[int, ...] = ()
    card_type: str | None = None
    ocr_confidence: float = 0.0
    semantic_confidence: float = 0.0
    choice_name: str | None = None
    source_name: str | None = None
    target_name: str | None = None
    # 本地卡组策略搜索出来的动作才带这些字段；盒子 OCR 路径保持默认值，
    # 因此既有序列化/金样不受影响。
    #   cost            —— 动作消耗的法力（搜索剪枝/日志显示用）。
    #   choice_card_id  —— 选择/技能子卡 id（技能面板按下标反查，不靠名字）。
    #   entity_id / target_entity_id —— 本地动作的稳定实体身份（去重/失败拉黑用）。
    cost: int = 0
    choice_card_id: str | None = None
    entity_id: str | None = None
    target_entity_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, ActionKind):
            object.__setattr__(self, "action", ActionKind(self.action))
        object.__setattr__(self, "mulligan_slots", tuple(self.mulligan_slots))


@dataclass(frozen=True, slots=True)
class ValidationResult(PrimitiveMixin):
    accepted: bool
    code: str
    diagnostics: tuple[str, ...] = ()
    value: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @classmethod
    def allow(cls, value: Any) -> "ValidationResult":
        return cls(True, "accepted", value=value)

    @classmethod
    def reject(cls, code: str, *diagnostics: str) -> "ValidationResult":
        return cls(False, code, tuple(diagnostics))


__all__ = ["ActionKind", "ProposedAction", "SlotRef", "ValidationResult"]
