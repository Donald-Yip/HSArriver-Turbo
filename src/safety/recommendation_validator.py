"""Validate recommendation evidence immediately before execution."""

from src.recommendation_config import RecommendationConfig
from src.recommendation_models import ValidationResult


def _live_client_size():
    """当前炉石客户区尺寸；拿不到（未运行/最小化/非 Windows）时返回 None。"""
    try:
        from display_mapping import display_mapping
        mapping = display_mapping()
    except Exception:
        return None
    if mapping is None:
        return None
    try:
        return (int(mapping.width), int(mapping.height))
    except Exception:
        return None


def _client_size_matches(frame, config) -> bool:
    """这一帧的客户区尺寸是否与当前一致（证据新鲜度）。

    分辨率不再是判定门槛：客户区几何由 display_mapping 换算，任何桌面分辨率
    都能跑。这里只确认"这帧是不是按**当前客户区**截的"——所以跟**实时客户区**
    比，而不是跟基准分辨率（config.desktop_size）比；窗口化时客户区≠基准分辨率，
    拿基准去比会把整条流程误拒成 desktop_size（用户实测缩放后无法换牌）。
    `client_size is None`（旧证据 / 测试构造）时跳过校验。
    """
    client_size = getattr(frame, "client_size", None)
    if client_size is None:
        return True
    try:
        frame_size = tuple(int(v) for v in client_size)
    except Exception:
        return False
    live = _live_client_size()
    if live is not None:
        return frame_size == live
    # 拿不到实时几何（离线/测试）时退回基准：帧按基准客户区截的才算新鲜。
    return frame_size == tuple(int(v) for v in config.desktop_size)


class ConsumedActionStore:
    def __init__(self):
        self._keys = set()

    @staticmethod
    def key_for(proposed, adapted):
        return (
            proposed.turn_number, proposed.log_revision,
            proposed.normalized_instruction, adapted.source_entity_id,
            adapted.target_entity_id,
        )

    def contains(self, key):
        return key in self._keys

    def mark_consumed(self, key):
        self._keys.add(key)


class RecommendationValidator:
    def __init__(self, config=None):
        self.config = config or RecommendationConfig()

    def validate(self, proposed, adapted, stable_frame, current_frame,
                 current_ocr, state, log_revision,
                 flow_state, consumed, stopped):
        # State/identity freshness must be checked before frame/control gates.
        if proposed.turn_number != state.game_num_turns_in_play:
            return ValidationResult.reject("turn_changed")
        if proposed.log_revision != log_revision:
            return ValidationResult.reject("revision_changed")
        if proposed.ocr_confidence < self.config.min_ocr_confidence:
            return ValidationResult.reject("ocr_confidence")
        if proposed.semantic_confidence < 1.0:
            return ValidationResult.reject("semantic_confidence")
        if not getattr(state, "is_my_turn", False):
            return ValidationResult.reject("not_player_turn")
        if stable_frame.frame_id != proposed.frame_id:
            return ValidationResult.reject("stale_frame")
        if current_ocr.frame_id != current_frame.frame_id:
            return ValidationResult.reject("current_ocr_frame")
        if current_ocr.normalized_text != proposed.normalized_instruction:
            return ValidationResult.reject("recommendation_changed")
        if not _client_size_matches(current_frame, self.config):
            return ValidationResult.reject("desktop_size")
        # DPI 不再参与判定：坐标按物理像素换算，显示缩放不影响截图与点击的对应
        # 关系（进程已声明 DPI 感知）。保留 frame.dpi 只作记录/诊断。
        if not current_frame.window_handle or not current_frame.foreground:
            return ValidationResult.reject("window")
        if not current_frame.panel_visible:
            return ValidationResult.reject("recommendation_panel")
        if stopped:
            return ValidationResult.reject("stopped")
        if flow_state != "PLAYER_TURN":
            return ValidationResult.reject("flow_state")
        key = consumed.key_for(proposed, adapted)
        if consumed.contains(key):
            return ValidationResult.reject("already_consumed")
        return ValidationResult.allow((adapted, key))
