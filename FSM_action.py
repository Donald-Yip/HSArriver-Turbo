import _thread
import random
import re
import sys
import threading
import time

import keyboard

import click
import get_screen
import surrender_rules
from config import (
    DEFAULT_AUTO_CONCEDE, DEFAULT_AUTO_SURRENDER, SNAPSHOT_WRITE_INTERVAL,
    human_like_settings, rank_stop_settings,
)
from manual_controller import (
    ClickExecutor, GlobalHotkeyInput, ManualController,
)
from strategy import StrategyState
import log_state as log_state_module
from log_state import *
from src.capture.desktop_capture import DesktopCapture
from src.flow.mulligan_flow import MulliganFlow, MulliganStatus
from src.flow.recommendation_flow import (
    FlowStepStatus, RecommendationFlow,
)
from src.game_state.recommendation_adapter import adapt_action
from src.ocr.paddle_adapter import PaddleOcrAdapter
from src.ocr.stable_reader import StableRecommendationReader
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_config import RecommendationConfig
from src.recommendation_models import ActionKind
from src.safety.hearthstone_liveness import default_monitor
from src.safety.recommendation_validator import RecommendationValidator
from src.safety.restart_watchdog import default_watchdog


FSM_state = ""
time_begin = 0.0
game_count = 0
win_count = 0
concede_count = 0
quitting_flag = False
# 定时计划 / Web 控制台：置 True 表示“本局对战结束后停止自动化”。
# 对局进行中（换牌/对战/结算）不会立即退出，只有回到非对局状态才停止。
stop_after_current_game = False
shutdown_event = threading.Event()
log_state = LogState()
log_iter = log_iter_func(HEARTHSTONE_LOG_ROOT)
choose_hero_count = 0
manual_controller = ManualController(
    input_func=GlobalHotkeyInput(
        keyboard, shutdown_event=shutdown_event),
    executor=ClickExecutor(click),
)
auto_mulligan_flow = None
recommendation_flow = None
recommendation_config = None
recommendation_capture = None
mulligan_capture = None
recommendation_parser = None
recommendation_reader = None
mulligan_reader = None
recommendation_validator = None
active_game_generation = -1
mulligan_delay_generation = None
player_turn_delay_key = None
last_automation_diagnostic = None
_snapshot_cache_key = None
_snapshot_cache = None
_mulligan_diagnostic_key = None
# 自动投降状态：连续低胜率检测 + 触发标记（每局重置）。
_concede_streak = 0
_concede_last_turn = None
_concede_triggered = False
# 供界面显示的最近一次检测结果（None = 本回合没读到/还没检测过）。
_concede_last_rate = None
_concede_last_check = None
# 玩家昵称校验：最近一次“日志玩家名 vs 配置用户 ID”的判定结果，
# 以及已经上报过的 (对局代次, 是否匹配)，避免同一局反复刷同一条提示。
_name_match_result = None
_name_match_reported = None
# 炉石存活检测（进程消失 / Power.log 停滞）：状态机主循环每轮检查一次。
# 与 Web 界面共享同一个检测器实例，浮窗/网页看到的是同一份判定。
hearthstone_liveness = default_monitor()
restart_watchdog = default_watchdog()
# 最近一次“判定炉石已退出/无响应”的醒目告警（供网页横幅显示，下一轮开始时清空）。
_liveness_alert = None
# 连续“推荐读取失败（RETRY）”次数：只作为存活告警文案里的旁证，帮助判断卡在哪。
_ocr_fail_streak = 0
# 调试快照写盘节流：日志每次变化都全量序列化整个 log_state 会拖慢主循环，
# 只在间隔 SNAPSHOT_WRITE_INTERVAL 秒后重新写盘。（定义于 config.py）
_last_snapshot_write = 0.0
# ---------------------------------------------------------------- 活人感：对手新随从
# 记录本局已经见过的对手随从 entity_id：对手回合里出现新 id 就把鼠标移上去
# 随机悬停 0.2~1.5s（只移动不点击）。None = 本局还没取到基线快照（首次只记录、
# 不悬停，避免一进对局就对着一堆已有随从乱晃）。
_oppo_minion_ids = None
# 一次最多悬停几个新随从：对手一口气铺满场（token/亡语）时不至于长时间发呆。
OPPO_MINION_HOVER_PER_BATCH = 3
# ---------------------------------------------------------------- 上分停止 / 控分
# 每局对战结束后读结算界面的段位数字，命中用户设定的目标就把「本局结束后停止」
# 置位（见 _read_rank_target / DEFAULT_RANK_STOP）。
#   _rank_stop_triggered      : 本局已命中并已请求停止（每局由 reset_game_session 复位）。
#   _rank_stop_stop_reason    : 给网页横幅显示的中文停止原因。
#   _keep_score_count         : 控分模式已投降的局数（供页面显示"压了几局"）。
_rank_stop_triggered = False
_rank_stop_stop_reason = None
_keep_score_count = 0
# 最近一次段位读数（供 Web / 浮窗显示，None = 还没读过）与它发生在哪一步
# （"点「开始」前" / "点「开始」后" / "选择套牌界面"）——页面据此说明"到底检测了没有"。
_rank_last_read = None
_rank_last_phase = None

# ---------------------------------------------------------------- 启动耗时
# 用户反馈「浮窗点开始后按钮变了，但鼠标要等好一会才动」。启动链路里原先有几段
# 固定等待（主界面 sleep 3s、选套牌 sleep 2s、界面识别 3s 一次），光看现象无法
# 判断停在哪一步。这里给每个阶段打一行「进入对战 +x.xs：阶段」，一次自动化最多
# 打几条；里程碑只在本轮启动后的 _START_LOG_LIMIT 秒内输出，避免长时间运行后误报。
_START_T0 = None
_START_MILESTONES = set()
_START_LOG_LIMIT = 30.0


def _start_milestone(label: str) -> None:
    """记录并打印一条启动阶段耗时（同一阶段本轮只打一次）。"""
    if _START_T0 is None or label in _START_MILESTONES:
        return
    elapsed = time.monotonic() - _START_T0
    if elapsed > _START_LOG_LIMIT:
        return
    _START_MILESTONES.add(label)
    try:
        sys_print(f"[SYS] 进入对战 +{elapsed:.1f}s：{label}")
    except Exception:
        pass


def _reset_start_milestones() -> None:
    """每轮自动化开始：重新计时、清空已打过的阶段。"""
    global _START_T0
    _START_T0 = time.monotonic()
    _START_MILESTONES.clear()


def _automation_state():
    snapshot = refresh_snapshot()
    if snapshot is None:
        raise RuntimeError("power_log_snapshot_unavailable")
    snapshot.log_revision = log_state.revision
    return snapshot


def _automation_state_with_revision():
    snapshot = _automation_state()
    return snapshot, log_state.revision


def initialize_recommendation_automation():
    """Rebuild per-game flows while reusing expensive OCR components.

    每次调用都会重新读取 ui_config.json 的 recommendation_roi（重建轻量的
    RecommendationConfig + DesktopCapture），因此用校准工具画完框后，直接重开
    对局/重启自动化即可生效，无需重启 web_ui。昂贵的 OCR 引擎（reader 与
    paddle backend）仍只创建一次、跨对局复用。
    """
    global auto_mulligan_flow, recommendation_flow
    global recommendation_config, recommendation_capture, mulligan_capture
    global recommendation_parser, recommendation_reader
    global mulligan_reader, recommendation_validator

    # 轻量：每次都重建，以便拾取校准后的最新 ROI / 尺寸配置。
    recommendation_config = RecommendationConfig()
    recommendation_capture = DesktopCapture(recommendation_config)
    # 换牌推荐区域比回合推荐大（兼容 4 张留牌）：独立一个 capture，避免为了
    # 换牌把每回合的 OCR 截图一起放大。
    mulligan_capture = DesktopCapture(
        recommendation_config,
        roi=getattr(recommendation_config, "mulligan_recommendation_roi",
                    recommendation_config.recommendation_roi))

    if recommendation_parser is None:
        recommendation_parser = RecommendationParser()

    if recommendation_reader is None:
        recommendation_reader = StableRecommendationReader(
            recommendation_config, PaddleOcrAdapter(),
            text_normalizer=recommendation_parser.normalize_action_text)
        # 不设"打法参考A"信标：对战时该标题不在截图区域内（实测
        # 面板直出「打出N号位…」指令文本），设信标会把正确指令清空为
        # recommendation_not_stable 死循环。面板是否在场由 parser
        # 严格句式（打出N号位随从/放置于我方N号位 等）唯一把关，
        # 与换牌 reader 同一设计。
    if mulligan_reader is None:
        # 换牌面板只有留牌建议（无"打法参考A"标题）：不设信标，
        # 面板是否在场由 `替换N号位卡牌` 换牌句式唯一把关。
        # 独立 if 保证每次 initialize（含 config 已存在的后续对局）
        # 都会为闭包绑定该变量。
        mulligan_reader = StableRecommendationReader(
            recommendation_config, recommendation_reader.backend,
            text_normalizer=recommendation_parser.normalize_action_text)
        recommendation_validator = RecommendationValidator(
            recommendation_config)

    def read_mulligan_action():
        # 换牌面板是否在场，由 OCR 证据裁定：识别出的文本必须能解析出
        # `替换N号位卡牌` 换牌句式（无"打法参考A"信标的专用 reader）。
        evidence = mulligan_reader.read(
            lambda: mulligan_capture.capture(ocr_panel_ok=True),
            mulligan_capture.crop_recommendation)
        action = recommendation_parser.parse(
            evidence, log_state.game_num_turns_in_play, log_state.revision)
        if action.action != ActionKind.MULLIGAN:
            raise RuntimeError("recommendation_is_not_mulligan")
        return action

    auto_mulligan_flow = MulliganFlow(
        click, read_mulligan_action, _automation_state,
        action_context=click.hearthstone_action_session,
        stopped=shutdown_event.is_set,
        # 上游时序：OCR 前的每局等待由 ChoosingCardAction 的 ready 延时负责；
        # OCR 成功后立即点击，不再叠加缓冲（mulligan_post_ocr_delay=0）。
        first_delay=recommendation_config.mulligan_post_ocr_delay_seconds,
        retry_delay=recommendation_config.mulligan_post_ocr_delay_seconds,
        post_action_pause=_human_like_post_action_pause)
    recommendation_flow = RecommendationFlow(
        capture=recommendation_capture,
        reader=recommendation_reader,
        parser=recommendation_parser,
        state_supplier=_automation_state_with_revision,
        adapter=adapt_action,
        validator=recommendation_validator,
        controller=manual_controller,
        result_timeout=recommendation_config.result_timeout_seconds,
        post_action_delay=recommendation_config.post_action_delay_seconds,
        # 抽牌额外延时（秒/张）：回合开始的常规抽 1 张不算，回合内抽到的每张都等。
        draw_extra_delay_per_card=(
            recommendation_config.draw_extra_delay_per_card_seconds),
        post_action_pause=_human_like_post_action_pause,
        stopped=shutdown_event.is_set,
    )


def _human_like_post_action_pause(action_kind=None) -> bool:
    """「活人感」延时：只在“对局中识别盒子意见并执行完”之后调用。

    由 RecommendationFlow / MulliganFlow 在动作执行成功后调用，返回 True 表示
    本次延时已被接管（随机 0.5~3s + 鼠标悬停），流程层不再叠加固定延时。
    未开启时返回 False，走原来的固定「操作后延时」。匹配对手、选卡组、错误弹窗
    取消这类非推荐动作不会经过这里。

    action_kind：刚执行完的动作类型（ActionKind，可能为 None）。**攻击类动作不做
    活人感表演**（出手后还盯着手牌最像脚本，而且攻击常常是连着来的）：直接返回
    False，让流程走正常的固定「操作后延时」去读下一条推荐。星舰发射同理——它也是
    场面上的动作（点场上星舰 → 点发射按钮），不看手牌。其它动作（出牌/技能/交易/
    换牌）才看手牌。

    门禁是“总开关 + 看卡牌”：「随机延时」本身就是手牌悬停的那段等待窗口，所以
    关掉「看卡牌」= 连随机延时一起关掉（回到固定「操作后延时」），不再走到
    click.human_like_pause()。对手随从悬停是另一个独立开关，互不影响。
    """
    try:
        settings = human_like_settings()
        if not settings.get("enabled"):
            return False
        if not settings.get("hand_hover_enabled", True):
            return False
        # ActionKind 是 str 枚举，所以字符串 "attack" 也能匹配上。
        if action_kind in (ActionKind.ATTACK, ActionKind.LAUNCH_STARSHIP):
            return False
        click.human_like_pause()
        return True
    except Exception as exc:
        try:
            print(f"[SYS] 活人感延时失败，回退固定延时：{exc}")
        except Exception:
            pass
        return False


def _human_like_opponent_minions(snapshot) -> int:
    """对手场上出现新随从时，鼠标移上去随机悬停 0.2~1.5s（活人感）。

    只在【对手回合】悬停：那时脚本本来就在空转等对手，不会拖慢自己的出牌；
    我方回合出现的“新随从”只记入基线，不悬停（避免打断自己的操作节奏）。
    返回本次实际悬停的随从个数；未开启活人感/没有新随从时返回 0。
    """
    global _oppo_minion_ids
    try:
        minions = list(getattr(snapshot, "oppo_minions", None) or ())
    except Exception:
        return 0
    ids = {mid for mid in (getattr(m, "entity_id", None) for m in minions)
           if mid is not None}
    if _oppo_minion_ids is None:
        # 本局第一份快照：只建立基线，不对已有随从悬停。
        _oppo_minion_ids = ids
        return 0
    new_ids = ids - _oppo_minion_ids
    _oppo_minion_ids = ids
    if not new_ids or getattr(snapshot, "is_my_turn", False):
        return 0
    try:
        settings = human_like_settings()
        if not settings.get("enabled"):
            return 0
        if not settings.get("minion_hover_enabled", True):
            return 0
    except Exception:
        return 0
    total = len(minions)
    hovered = 0
    for index, minion in enumerate(minions):
        if getattr(minion, "entity_id", None) not in new_ids:
            continue
        if hovered >= OPPO_MINION_HOVER_PER_BATCH:
            break
        try:
            click.hover_opponent_minion(index, total)
            hovered += 1
        except Exception as exc:
            try:
                print(f"[SYS] 活人感对手随从悬停失败：{exc}")
            except Exception:
                pass
            break
    return hovered


def reset_game_session():
    """Clear every match-scoped automation state for a newly created game."""
    global active_game_generation, choose_hero_count
    global mulligan_delay_generation, player_turn_delay_key
    global last_automation_diagnostic
    global _snapshot_cache_key, _snapshot_cache, _mulligan_diagnostic_key
    global _concede_streak, _concede_last_turn, _concede_triggered
    global _concede_last_rate, _concede_last_check
    global _name_match_result
    global _oppo_minion_ids
    global _rank_stop_triggered, _rank_stop_stop_reason
    initialize_recommendation_automation()
    active_game_generation = log_state.game_generation
    choose_hero_count = 0
    mulligan_delay_generation = None
    player_turn_delay_key = None
    last_automation_diagnostic = None
    _snapshot_cache_key = None
    _snapshot_cache = None
    _mulligan_diagnostic_key = None
    _concede_streak = 0
    _concede_last_turn = None
    _concede_triggered = False
    _concede_last_rate = None
    _concede_last_check = None
    # 新一局重新允许“段位目标”检测（上分停止 / 控分）：已触发的停止不再撤销，
    # 但本局标志要复位，否则下一局打完不会再检查段位。最近读数保留，方便网页显示。
    _rank_stop_triggered = False
    _rank_stop_stop_reason = None
    # 新一局重新校验昵称（换号提示按局给一次）。
    _name_match_result = None
    # 新一局重新建立“对手随从基线”，第一份快照只记录不悬停。
    _oppo_minion_ids = None
    # 卡组识别投降：清掉上一局的事实（职业/打出/揭示/开局生效卡）。
    surrender_rules.engine.reset()
    click.center_mouse()


def init():
    global log_state, log_iter, choose_hero_count, active_game_generation
    global mulligan_delay_generation, player_turn_delay_key
    global last_automation_diagnostic
    global _snapshot_cache_key, _snapshot_cache, _mulligan_diagnostic_key
    global _concede_streak, _concede_last_turn, _concede_triggered
    global _concede_last_rate, _concede_last_check
    global _name_match_result, _name_match_reported
    global _liveness_alert, _ocr_fail_streak
    global _oppo_minion_ids
    global _rank_stop_triggered, _rank_stop_stop_reason
    global _rank_last_read
    global _keep_score_count

    log_state = LogState()
    log_iter = log_iter_func(HEARTHSTONE_LOG_ROOT)
    choose_hero_count = 0
    active_game_generation = -1
    mulligan_delay_generation = None
    player_turn_delay_key = None
    last_automation_diagnostic = None
    _snapshot_cache_key = None
    _snapshot_cache = None
    _mulligan_diagnostic_key = None
    _concede_streak = 0
    _concede_last_turn = None
    _concede_triggered = False
    _concede_last_rate = None
    _concede_last_check = None
    _name_match_result = None
    _name_match_reported = None
    _liveness_alert = None
    _ocr_fail_streak = 0
    _oppo_minion_ids = None
    # 段位目标（上分停止 / 控分）：新一轮自动化从头开始（最近读数与控分局数
    # 也清掉，避免显示上一次运行的旧值、或把上一轮的投降局数算进上限）。
    _rank_stop_triggered = False
    _rank_stop_stop_reason = None
    _rank_last_read = None
    _keep_score_count = 0
    # 存活检测按“本轮自动化”重新开始计数：本轮没见过的炉石进程不算“消失”，
    # 否则“启动脚本 → 脚本拉起炉石”的正常流程会被误判成闪退。
    try:
        hearthstone_liveness.reset()
    except Exception:
        pass
    shutdown_event.clear()
    initialize_recommendation_automation()
    click.center_mouse()


def _zone_before_update(state, container):
    """这一行会不会改 ZONE？会的话返回改之前的旧区域。

    为什么需要：`update_state()` 直接把新值写进实体，写完之后就再也拿不到
    "原来在哪个区域"，而"打出"的判据正是 HAND → PLAY。所以必须在更新前
    偷看一眼旧值。

    两种写法都要认：
    * `TAG_CHANGE Entity=[...] tag=ZONE value=PLAY` —— 只出现在**自己的牌**
      打出时（日志里自己的手牌一直是已知实体）；
    * `SHOW_ENTITY`/`FULL_ENTITY` 块里的 `tag=ZONE value=PLAY` —— **对手的牌**
      打出时日志只会先 SHOW_ENTITY 揭示 CardID，再在它自己的 tag 块里移进
      PLAY，没有 TAG_CHANGE 行。漏了后者，"对方打出过 X"的条件永远不成立。
    """
    try:
        line_type = getattr(container, "line_type", None)
        info = getattr(container, "info_dict", {}) or {}
        if str(info.get("tag") or "") != "ZONE":
            return None
        if line_type == LOG_LINE_TAG_CHANGE:
            entity = state.entity_dict.get(str(info.get("entity") or ""))
        elif line_type == LOG_LINE_TAG:
            entity = state.current_update_entity
        else:
            return None
        if entity is None:
            return None
        return str(entity.query_tag("ZONE", "") or "")
    except Exception:
        return None


def update_log_state():
    global active_game_generation
    global _last_snapshot_write
    log_container = next(log_iter)
    if log_container.log_type == LOG_CONTAINER_ERROR:
        return False

    previous_revision = log_state.revision
    for log_line_container in log_container.message_list:
        # 卡组识别投降：先记住旧 ZONE（判断"打出"要用），再更新状态，
        # 最后把这行喂给规则引擎（内部 try/except，绝不影响主循环）。
        prev_zone = _zone_before_update(log_state, log_line_container)
        ok = update_state(log_state, log_line_container)
        surrender_rules.engine.observe(log_state, log_line_container,
                                       prev_zone=prev_zone)
        # if not ok:
        #     return False

    if log_state.game_generation != active_game_generation:
        reset_game_session()

    # 昵称校验：日志已经给出双方玩家名时比对配置的用户 ID（对不上就提示一次）。
    check_player_name_match()

    if (DEBUG_FILE_WRITE and log_state.revision != previous_revision
            and time.time() - _last_snapshot_write
            >= SNAPSHOT_WRITE_INTERVAL):
        _last_snapshot_write = time.time()
        with open("./log/game_state_snapshot.txt", "w", encoding="utf8") as f:
            f.write(str(log_state))

    # 注意如果Power.log没有更新, 这个函数依然会返回. 应该考虑到game_state只是被初始化
    # 过而没有进一步更新的可能
    if log_state.game_entity_id == 0:
        return False

    return True


def refresh_snapshot():
    """Read pending Power.log events and build a fresh manual snapshot."""
    global _snapshot_cache_key, _snapshot_cache
    if not update_log_state():
        return None
    cache_key = (log_state.game_generation, log_state.revision)
    if cache_key != _snapshot_cache_key:
        _snapshot_cache = StrategyState(log_state)
        _snapshot_cache_key = cache_key
    return _snapshot_cache


def wait_for_log_update(start_revision=None, timeout=2.0):
    """Wait briefly for evidence that an input changed game state."""
    if start_revision is None:
        start_revision = log_state.revision
    deadline = time.time() + timeout
    while time.time() < deadline:
        if update_log_state() and log_state.revision > start_revision:
            return True
    manual_controller.output("尚未检测到状态变化，请查看游戏后刷新或重试。")
    return False


def wait_until_battle_starts():
    loop_count = 0
    while True:
        if not update_log_state():
            return FSM_ERROR
        if log_state.is_end:
            return FSM_QUITTING_BATTLE
        if log_state.game_num_turns_in_play > 0:
            return FSM_BATTLING
        loop_count += 1
        if loop_count >= 60:
            warn_print("Time out in Choosing Card")
            return FSM_ERROR
        time.sleep(STATE_CHECK_INTERVAL)


def system_exit():
    global quitting_flag

    sys_print(f"一共完成了{game_count}场对战, 赢了{win_count}场")
    print_info_close()

    quitting_flag = True
    shutdown_event.set()
    if threading.current_thread() is threading.main_thread():
        raise SystemExit(0)
    _thread.interrupt_main()


def request_stop_after_game():
    """请求“本局对战结束后停止”。

    对局进行中时不会中断当前操作；当状态机回到非对局状态
    （主菜单/选职业/匹配/炉石未运行等）后自动化线程自动退出。
    再次调用 request_cancel_stop_after_game() 可在本局结束前撤销。
    """
    global stop_after_current_game
    stop_after_current_game = True
    info_print("已请求：本局对战结束后停止自动化。")
    return True


def request_cancel_stop_after_game():
    """撤销“本局结束后停止”，让自动化继续打下去。

    在线程退出前调用即可，无需重启脚本；本局结束前都可自由更改。
    """
    global stop_after_current_game
    stop_after_current_game = False
    info_print("已取消「本局结束后停止」，自动化继续运行。")
    return True


def request_immediate_stop():
    """Web 模式下的立即停止：只终止自动化线程，不影响服务器主线程。"""
    global quitting_flag
    info_print("收到立即停止指令，正在终止自动化……")
    quitting_flag = True
    shutdown_event.set()
    return True


def print_out():
    global FSM_state
    global time_begin
    global game_count

    # sys_print("Enter State " + str(FSM_state))

    if FSM_state == FSM_LEAVE_HS:
        warn_print("HearthStone not found! Try to go back to HS")

    if FSM_state == FSM_CHOOSING_CARD:
        # 只在“真正打完一局”时计数（见 Battling），开局只记录开始时间。
        # sys_print("The " + str(game_count) + " game begins")
        time_begin = time.time()

    if FSM_state == FSM_QUITTING_BATTLE:
        # sys_print("The " + str(game_count) + " game ends")
        time_now = time.time()
        if time_begin > 0:
            info_print("The last game last for : {} mins {} secs"
                       .format(int((time_now - time_begin) // 60),
                               int(time_now - time_begin) % 60))

    return


def ChoosingHeroAction():
    global choose_hero_count

    if quitting_flag or stop_after_current_game:
        sys.exit(0)

    print_out()

    # 有时脚本会卡在某个地方, 从而在FSM_Matching
    # 和FSM_CHOOSING_HERO之间反复横跳. 这时候要
    # 重启炉石
    # choose_hero_count会在每一次开始留牌时重置
    choose_hero_count += 1
    if choose_hero_count >= 20:
        return FSM_ERROR

    # 只留一帧的 UI 稳定时间：原来固定等 2 秒，段位读取本身有重试（卡面没画出来
    # 会重读），这里再死等只会把「点开始匹配」整体往后推。
    time.sleep(0.2)
    # 排队前段位闸门（用户口径）：
    #   action=stop「上分停止」：读到名次已达标 → **不点**「开始匹配」，本局结束后停止；
    #   action=keep「控分」    ：读到名次比目标更靠前 → 进这一局后**开局即投降**，
    #                            把名次压后，再回来重新检查（见 _keep_score_once）。
    # 实测漏过：打完一局后画面自己回到这个界面，结算里的段位检测没跑到，
    # 脚本照样排队进了下一局——所以这道闸放在"点开始之前"。
    try:
        action = _read_rank_target(
            attempts=_CHOOSING_HERO_RANK_READ_ATTEMPTS,
            wait=_CHOOSING_HERO_RANK_RETRY_WAIT,
            label="选择套牌界面",
            min_confidence=_POST_GAME_MIN_CONFIDENCE,
            confirm=True)
    except Exception as exc:
        action = ""
        warn_print(f"选择套牌界面段位检测失败（忽略，继续匹配）：{exc}")
    _start_milestone("段位检测完成")

    if action == "stop":
        info_print(
            "已到上分停止条件：不点「开始匹配」，本局结束后停止"
            "（要继续打请在页面关掉「🏁 段位目标」）。")
        return FSM_CHOOSING_HERO

    if action == "keep":
        # 控分：投一局压名次。投完回到这里重新读名次，直到不再"太靠前"才正常开打。
        if _keep_score_count >= _KEEP_SCORE_MAX_PER_RUN:
            warn_print(
                f"[SYS] 控分：本次运行已投降 {_keep_score_count} 局仍未达到目标名次，"
                "为避免无限投降，改为正常对战（请检查「🏁 段位目标」设置）。")
        else:
            _keep_score_once()
            return FSM_CHOOSING_HERO

    # 选卡组：**不在自动化里自动点**（用户 2026-10-23 口径）。
    # 旧行为是每局按 ui_config.json 的 battle_entry.deck_index 点第 N 格，
    # 会覆盖玩家手选的卡组；现在只保留遥控显式命令：
    #   * hsctl deck-click N       —— 自动化未运行时立即点第 N 格；
    #   * hsctl enter-battle --deck N —— 进入对战时顺带选第 N 格。
    # 需要换卡组时先停自动化再执行上述命令，不要在这里恢复自动点击。
    click.run_hearthstone_action(click.match_opponent)
    _start_milestone("已点「开始匹配」")
    time.sleep(1)
    return FSM_MATCHING


def MatchingAction():
    print_out()
    loop_count = 0

    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        time.sleep(STATE_CHECK_INTERVAL+random.random()+random.random()+random.random())

        click.run_hearthstone_action(click.commit_error_report)

        ok = update_log_state()
        if ok:
            if not log_state.is_end:
                return FSM_CHOOSING_CARD

        curr_state = get_screen.get_state()
        if curr_state == FSM_CHOOSING_HERO:
            return FSM_CHOOSING_HERO

        loop_count += 1
        # print("寻找对手计时器")
        # print(loop_count)
        if loop_count >= 60:
            warn_print("Time out in Matching Opponent")
            return FSM_ERROR


def ChoosingCardAction():
    global choose_hero_count, mulligan_delay_generation
    global quitting_flag, stop_after_current_game, shutdown_event
    choose_hero_count = 0

    print_out()
    snapshot = refresh_snapshot()
    if snapshot is None:
        return FSM_ERROR
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if snapshot.game_num_turns_in_play > 0:
        return FSM_BATTLING

    # CREATE_GAME increments game_generation.  Bind the ready delay to that
    # generation so every match waits once, including matches after the first.
    while mulligan_delay_generation != log_state.game_generation:
        waiting_generation = log_state.game_generation
        delay = recommendation_config.mulligan_ready_delay_seconds
        # 用统一的 _sleep_with_delay：推送"延时 Ns 后"启动浮窗进度条，
        # sleep 后再推"延时结束"清除，与换牌重试的进度表行为一致。
        _sleep_with_delay(delay, "换牌前识别")
        mulligan_delay_generation = waiting_generation
        snapshot = refresh_snapshot()
        if snapshot is None:
            return FSM_ERROR
        if snapshot.is_end:
            return FSM_QUITTING_BATTLE
        if snapshot.game_num_turns_in_play > 0:
            return FSM_BATTLING

    if auto_mulligan_flow is not None:
        auto_mulligan_flow.reset_delay()  # 每局首次用 ready(7)，重试用 post_ocr(5)
        # 换牌自动流（理想流程）：
        #   每局 ready(20s) 等待后进入循环 → 每隔 mulligan_retry(5s) 一次：
        #     ① 先检测屏幕中间“确认”按钮是否在场（面板就绪的物理信号）
        #     ② 在    → OCR 左侧留牌建议并执行换牌（替换+确认）
        #        不在 → 等 mulligan_retry_delay 再试
        #   点击后转为“确认按钮是否消失”校验：消失=已提交，仍在=未提交重试。
        confirmed_waiting = False
        verified = False
        # 重试间隔（面板未就绪/推荐暂不可执行/确认未消失时每轮等待）。
        # 不复用 post_ocr（那是“识别→点击”缓冲）：上游 post_ocr=0 时，
        # 若重试也取 0 会变成 0 秒忙等（CPU 空转 + 疯狂截图）。独立默认 5s。
        retry_delay = recommendation_config.mulligan_retry_delay_seconds
        while True:
            # 循环内必须检查停止标志：主循环只在状态分发处检查，
            # 本循环若能无限运行，立即停止后鼠标会继续点击。
            # 「本局结束后停止」不在此处生效（那是打完本局才停），
            # 本局内随时可通过 request_cancel_stop_after_game 反悔。
            if quitting_flag:
                sys.exit(0)
            fresh = refresh_snapshot()
            if fresh is None:
                return FSM_ERROR
            if fresh.is_end:
                return FSM_QUITTING_BATTLE
            if fresh.game_num_turns_in_play > 0:
                return FSM_BATTLING
            if confirmed_waiting:
                if not verified:
                    verified = True
                    # 点击确认后等界面切换，再检测“确认”按钮是否还在：
                    # 还在 → 换牌未提交成功，重新执行；消失 → 已提交。
                    time.sleep(0.5)
                    if confirm_button_present():
                        confirmed_waiting = False
                        verified = False
                        _report_mulligan_diagnostic(
                            "confirm_still_there",
                            "换牌确认仍在（未提交成功），重新执行……")
                        continue
                time.sleep(0.3)
                continue
            # 每轮开头先检测“确认”按钮：在 → 执行换牌；不在 → 等 retry 再试。
            if not confirm_button_present():
                _report_mulligan_diagnostic(
                    "confirm_absent",
                    "确认按钮未检测到（面板尚未就绪或已提交），等待重试……")
                _sleep_with_delay(retry_delay, "换牌重试")
                continue
            result = auto_mulligan_flow.run()
            if result.status == MulliganStatus.CONFIRMED:
                confirmed_waiting = True
                verified = False
                _report_mulligan_diagnostic(
                    "confirmed", "已执行换牌，检测确认按钮……")
                time.sleep(0.3)
                continue
            message = f"换牌推荐暂不可执行，继续重试：{result.diagnostics}"
            # 换牌面板已不在/阶段已变更时给出更明确提示，避免误以为卡死。
            diag = result.diagnostics
            if (diag == "recommendation_is_not_mulligan"
                    or diag.endswith(":recommendation_is_not_mulligan")
                    or diag == "mulligan_stage_changed"
                    or diag == "hand_changed"
                    or diag == "confirm_button_absent"
                    or diag.endswith(":confirm_button_absent")):
                message = ("换牌阶段未检测到可执行的留牌面板（可能已提交或"
                           "面板未就绪），等待对局开始……")
            _report_mulligan_diagnostic(result.diagnostics, message)
            _sleep_with_delay(retry_delay, "换牌重试")

    selected = manual_controller.choose_mulligan(snapshot)
    fresh_snapshot = refresh_snapshot()
    if fresh_snapshot is None:
        return FSM_ERROR
    if not manual_controller.mulligan_is_current(snapshot, fresh_snapshot):
        manual_controller.output("留牌状态已经变化，本次选择未点击，请重新确认。")
        if fresh_snapshot.is_end:
            return FSM_QUITTING_BATTLE
        if fresh_snapshot.game_num_turns_in_play > 0:
            return FSM_BATTLING
        return FSM_CHOOSING_CARD
    try:
        with click.hearthstone_action_session():
            try:
                for hand_index in selected:
                    click.replace_starting_card(
                        hand_index, fresh_snapshot.my_hand_card_num)
                click.commit_choose_card()
            except Exception:
                try:
                    click.cancel_click()
                except Exception:
                    pass
                raise
    except Exception as exc:
        manual_controller.output(f"留牌鼠标操作失败：{exc}")
        return FSM_ERROR
    return wait_until_battle_starts()


def run_manual_battle_step():
    snapshot = refresh_snapshot()
    if snapshot is None:
        return FSM_ERROR
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if not snapshot.is_my_turn:
        return None

    manual_controller.output(snapshot.format_for_manual_control())
    action = manual_controller.prompt_turn_action(snapshot)
    action = manual_controller.bind_to_turn(action, snapshot)
    fresh_snapshot = refresh_snapshot()
    if fresh_snapshot is None:
        return FSM_ERROR
    revision_before = log_state.revision
    result = manual_controller.execute(action, fresh_snapshot)
    manual_controller.output(result.message)
    if result.recovery_needed:
        return FSM_ERROR
    if result.executed:
        wait_for_log_update(revision_before)
    return None


def _report_automation_diagnostic(code, message):
    """Report a stable automation state once instead of every loop."""
    global last_automation_diagnostic
    if last_automation_diagnostic == code:
        return
    try:
        manual_controller.output(message)
    except Exception:
        pass
    last_automation_diagnostic = code


def _report_mulligan_diagnostic(code, message):
    """Report a stable mulligan retry state once instead of every 0.3s loop.

    换牌阶段如果 OCR 暂时读不出/面板已变更，原逻辑会每 0.3s 打印一次
    「换牌推荐暂不可执行」，在浮窗里刷屏。这里按诊断码去重，只在原因
    变化时输出一次，浮窗能稳定看见卡在哪一步。
    """
    global _mulligan_diagnostic_key
    if _mulligan_diagnostic_key == code:
        return
    try:
        manual_controller.output(message)
    except Exception:
        pass
    _mulligan_diagnostic_key = code


def _sleep_with_delay(seconds: float, desc: str) -> None:
    """等待并驱动浮窗底部延时进度条（进度条识别"延时 Ns 后"字样）。

    换牌重试/等待类延时若直接用 time.sleep，浮窗倒计时表不会启动（它只
    解析含"延时/等待 Ns 后"的日志行）。这里在 sleep 前推送一条带数字的
    延时行让进度条显示当前等待，sleep 后再推"延时结束"清除进度条。
    """
    seconds = max(0.0, float(seconds))
    try:
        manual_controller.output(f"[SYS] {desc}：延时 {seconds:.0f}s 后……")
    except Exception:
        pass
    if seconds > 0:
        time.sleep(seconds)
    try:
        manual_controller.output("[SYS] 延时结束")
    except Exception:
        pass


# ---------------------------------------------------------------- 炉石存活检测
# 「炉石不见了」有两种：进程没了（闪退/被杀）与进程还在但卡死（画面冻结）。
# 原来的代码只看窗口标题，且对局中的 OCR 重试循环不会退出，于是炉石闪退后
# 脚本会对着失效画面空转几个小时（issue 反馈）。这里在主循环里每轮判断一次，
# 判定退出后：日志 ERROR 级别醒目告警 + 推入浮窗 + 自动停止自动化。
# 判定细节（进程信号权威、日志停滞只在对局中参考）见
# src/safety/hearthstone_liveness.py 的模块说明。
_LIVENESS_IN_GAME_STATES = (FSM_CHOOSING_CARD, FSM_BATTLING,
                            FSM_QUITTING_BATTLE)


def _liveness_detail() -> str:
    """存活告警的旁证：最近一次自动化诊断 + 连续推荐读取失败次数。"""
    parts = []
    if last_automation_diagnostic:
        parts.append(f"最近自动化诊断：{last_automation_diagnostic}")
    if _ocr_fail_streak:
        parts.append(f"连续 {_ocr_fail_streak} 次推荐读取失败")
    return "；".join(parts)


def _alert_hearthstone_gone(message: str) -> None:
    """判定炉石已退出/无响应：醒目告警并自动停止自动化。"""
    global quitting_flag, _liveness_alert
    alert = f"⚠️ {message}；自动化已自动停止。请重新启动炉石后再点「开始运行」。"
    _liveness_alert = alert
    try:
        error_print(alert)
    except Exception:
        pass
    try:
        manual_controller.output(f"[SYS] {alert}")
    except Exception:
        pass
    quitting_flag = True
    shutdown_event.set()


def check_hearthstone_liveness():
    """状态机主循环调用：存活检测 + 告警/自动停止。

    返回本次事件（``None`` = 正常或无需处理），方便日志/测试观察。

    **与「中断重启」看门狗的优先级**：看门狗开关打开时，由它接管
    「Power.log 长时间停滞」这件事（重启炉石并重开一局），此时这里**不再**
    触发自动停止 —— 否则两个功能会在同一秒里一个要重启、一个要停机，
    互相打架。看门狗开关关着时行为与从前完全一致。
    """
    try:
        event = hearthstone_liveness.check(
            in_game=FSM_state in _LIVENESS_IN_GAME_STATES,
            detail=_liveness_detail())
    except Exception:
        return None

    # 看门狗判定（独立于上面那条，只在"停滞"这一类事件上接管）。
    watchdog_event = None
    try:
        watchdog_event = restart_watchdog.poll()
    except Exception:
        watchdog_event = None
    if watchdog_event is not None:
        _report_watchdog_event(watchdog_event)

    if event is None:
        return watchdog_event
    if event.get("kind", "").startswith("log_stale") and _watchdog_enabled():
        # 停滞交给看门狗处理：这里只把告警文案写进日志，不触发自动停止。
        try:
            manual_controller.output(
                f"[SYS] 检测到日志停滞：已交由「中断重启」处理"
                f"（{event['message']}）")
        except Exception:
            pass
        return watchdog_event or event
    if event.get("fatal"):
        _alert_hearthstone_gone(event["message"])
    else:
        try:
            manual_controller.output(f"[SYS] {event['message']}")
        except Exception:
            pass
    return event


def _watchdog_enabled() -> bool:
    """看门狗开关是否打开（读配置，失败按关闭处理=保持旧行为）。"""
    try:
        from config import watchdog_settings
        return bool(watchdog_settings().get("enabled", False))
    except Exception:
        return False


def _report_watchdog_event(event: dict) -> None:
    """把看门狗事件写进日志；「重启次数用尽」这类需要人工介入的走醒目告警。"""
    try:
        message = event.get("message") or ""
        if not message:
            return
        if event.get("kind") == "exhausted":
            _alert_hearthstone_gone(message)
        else:
            manual_controller.output(f"[SYS] {message}")
    except Exception:
        pass


def hearthstone_liveness_state() -> dict:
    """供 Web 界面/日志浮窗显示的存活状态（含最近一次告警文案）。"""
    in_game = FSM_state in _LIVENESS_IN_GAME_STATES
    try:
        state = dict(hearthstone_liveness.sample(in_game=in_game))
    except Exception:
        state = {"enabled": True, "status": "unknown", "process": "unknown",
                 "saw_process": False, "log_age": None, "alert": None}
    state["alert"] = state.get("alert") or _liveness_alert
    state["in_game"] = in_game
    return state


# ---------------------------------------------------------------- 玩家昵称校验
def check_player_name_match():
    """校验日志里的玩家名与配置的「用户 ID」是否对得上，对不上就提示。

    脚本靠昵称区分敌我：配置昵称与日志玩家名不匹配时，整局都会被当成对手
    回合而导致一直不出牌（issue 反馈：换战网账号后忘记改昵称）。这里在读到
    双方玩家名之后比对一次，不匹配就输出醒目提示（同一局只提示一次）。
    """
    global _name_match_result, _name_match_reported
    try:
        info = player_name_check(log_state)
    except Exception:
        return None
    if info is None:
        return None
    _name_match_result = info
    key = (log_state.game_generation, info["matched"])
    if key == _name_match_reported:
        return info
    _name_match_reported = key
    if not info["matched"]:
        names = " / ".join(sorted(info["players"].values()))
        warn_print(
            f"⚠️ 用户 ID 与日志玩家名不匹配：日志中玩家为 {names}，"
            f"当前配置为 {info['config']}。若刚换过战网账号，请把网页里的"
            f"「用户 ID」改成现在的完整战网昵称（含 #编号），否则脚本会把"
            f"整局都当成对手回合而不出牌。")
    return info


def player_name_state() -> dict:
    """最近一次昵称校验结果（供 Web 界面显示）；matched=None 表示还无法判断。"""
    if _name_match_result:
        return dict(_name_match_result)
    try:
        config_name = log_state_module.MY_NAME
    except Exception:
        config_name = ""
    return {"config": config_name, "players": {}, "matched": None}


# ---------------------------------------------------------------- 每局结束流程 / 段位
# 一局打完（Power.log 报 COMPLETE）后进入 FSM_QUITTING_BATTLE。结算界面会有
# 胜负横幅、段位升降级动画、奖励面板等好几屏，必须一路点掉，直到出现「开始」
# 按钮才算收尾完成。只在对局结算阶段使用：
#   * _POST_GAME_START_BUTTON_ROI ：结算界面底部「开始」按钮的默认框，
#     用户可以校准（ui_config.json 的 post_game_start_roi），点击点取框中心；
#   * _POST_GAME_START_FALLBACK_ROI：检测「开始」的兜底框（框被拖小后仍能认出）；
#   * _POST_GAME_RANK_ROI         ：段位数字的默认框（没上传说时这里是空白），
#     同样可以校准（ui_config.json 的 post_game_rank_roi）。
# 推进结算界面点哪儿：**中间那两个点**（`click.ERROR_REPORT_POINTS` =
# (1100,820) + (960,650)，原来用于错误弹窗/断线提示），每轮各点一次；
# 第三个点（「开始」按钮正中心）只在检测到「开始」之后的收尾里点。右边那些
# 辅助点不用（会被右上角日志浮窗吃掉/可能误点到下一屏），屏幕正中 (960,540)
# 会落到下一局「选择卡组」界面的卡组上，也不用它。
_POST_GAME_START_BUTTON_ROI = (1325, 865, 1475, 935)
_POST_GAME_START_FALLBACK_ROI = (1250, 845, 1550, 945)
_POST_GAME_START_DEFAULT_POINT = (1400, 900)
_POST_GAME_RANK_ROI = (1230, 180, 1385, 225)
_MAX_RANK_NUMBER = 100000
# 小字号放大后再送 OCR（与 AI胜率浮动条同一套做法）。
_POST_GAME_OCR_SCALE = 2.0
# 收尾总轮数上限：每轮 = 点一下待命点 + 等 _POST_GAME_CLICK_WAIT 秒 +（可选）OCR。
# 40 轮约 2~3 分钟；超时走原 FSM_ERROR 自愈路径（重启炉石），与改动前一致。
_POST_GAME_MAX_CYCLES = 40
_POST_GAME_CLICK_WAIT = 1.2
# 检测「开始」的尝试间隔：每轮点完前两个点就检查一次，读不到就下一轮再查。
# 段位的读取时机见 QuittingBattle / ChoosingHeroAction（点「开始」之前先读一次，
# 收尾后再兜底读一次，排队点「开始匹配」之前再读一次）。
_POST_GAME_RANK_READ_ATTEMPTS = 3
_POST_GAME_RANK_RETRY_WAIT = 1.5
# 收尾前预读的复核间隔：预读读到数字时，隔这么久再读一次，两次都读到才算数
# （那会儿屏幕上还是结算界面，不能把过渡画面里的误识别当成名次）。
_POST_GAME_RANK_CONFIRM_WAIT = 0.6
# 「选择套牌/选人」界面（点「开始匹配」之前）的名次检测：名次是**点完「开始」
# 之后那一屏**才画出来的，实测要 ~3 秒，所以这里多试几次；读到名次就不点
# 「开始匹配」，直接停在本局之后。
# 重试间隔从 1.2s 缩到 0.6s：卡面还没画出来时照样能重读到，但不会让「点开始匹配」
# 白等（用户反馈按开始后要等好一会鼠标才动）。
_CHOOSING_HERO_RANK_READ_ATTEMPTS = 4
_CHOOSING_HERO_RANK_RETRY_WAIT = 0.6
# 「开始」按钮的 OCR 关键词与最低置信度（繁体/英文一并兜住）。
_POST_GAME_START_KEYWORDS = ("开始", "開始", "Play")
_POST_GAME_MIN_CONFIDENCE = 0.5
# -------------------------------------------------------------- 控分（action=keep）
# 读到"名次比目标更靠前"时：点开始匹配 → 等进入对局 → 立刻投降 → 回到选卡组界面，
# 再由主循环重新读名次（每次 dispatch 只投降一局，主循环持续做存活检测）。
#   _KEEP_SCORE_ENTER_ATTEMPTS/W AIT：点开始后等"进入对局"的轮询次数与间隔；
#   _KEEP_SCORE_BACK_ATTEMPTS/WAIT  ：投降后等"回到选卡组界面"的轮询次数与间隔；
#   _KEEP_SCORE_MAX_PER_RUN          ：单次自动化最多投降多少局（防死循环/控分线打不到）。
_KEEP_SCORE_ENTER_ATTEMPTS = 20
_KEEP_SCORE_ENTER_WAIT = 2.0
_KEEP_SCORE_BACK_ATTEMPTS = 20
_KEEP_SCORE_BACK_WAIT = 2.0
# 进局后等「换牌阶段结束」的轮询：换牌要等玩家/对手确认，90s 足够覆盖正常
# 换牌超时；超时就不点认输（宁可这局不控分，也绝不能在换牌阶段投降）。
_KEEP_SCORE_MULLIGAN_ATTEMPTS = 90
_KEEP_SCORE_MULLIGAN_WAIT = 1.0
_KEEP_SCORE_MAX_PER_RUN = 40
# 段位区域「是否空白」判定：区域内足够亮的像素少于这么多，就当没有数字。
# 未上传说时该处是纯背景，直接跳过 OCR（省一次推理，也避免空图被误识别成数字）。
_RANK_MIN_BRIGHT_PIXELS = 8
_RANK_BRIGHT_THRESHOLD = 150.0
# 全角数字/空格 → 半角（OCR 有时会把全角数字读出来）。
_FULLWIDTH_DIGITS = str.maketrans(
    "０１２３４５６７８９　", "0123456789 ")


def _config_roi(key: str, default) -> tuple:
    """读用户校准过的区域（ui_config.json 顶层 [left,top,right,bottom]）。

    坐标是**客户区相对**口径（见 display_mapping），允许负值（盒子面板可以挂在
    炉石客户区之外）。非法/缺失时回退 default，保证自动化永远有一个可用的框。
    """
    try:
        box = getattr(recommendation_config, key, None)
        if box is not None:
            left, top, right, bottom = (int(v) for v in box)
            if left < right and top < bottom:
                return (left, top, right, bottom)
    except Exception:
        pass
    return tuple(int(v) for v in default)


def _grab_region(box, scale: float = 1.0):
    """截取屏幕区域并返回放大后的 BGR ndarray；依赖缺失/截图失败返回 None。

    box 是**客户区相对坐标**（基准 1920×1080）：这里先经 display_mapping 换算成
    屏幕坐标再截——炉石窗口不在屏幕原点时，不换算就会框到别的地方去。
    客户区比基准小时框内文字像素也会变小，按同一比例把放大倍数补水回去，
    保证送给 OCR 的字号与"客户区正好 1920×1080"时一致。
    **窗口移动的原子性**：抓图约 100ms，期间窗口被拖动就会截到别的位置。这里先
    读一次新鲜几何（窗口一动就失效，见 window_tracker）→ 抓 ROI → 复测；只有
    复测发现"真的移动过了"才退回 `capture_region` 重抓（少见路径）。
    """
    try:
        import cv2
        import numpy as np
        from PIL import ImageGrab
    except Exception:
        return None
    try:
        from display_mapping import box_to_screen, display_mapping
        import window_tracker as wt
        wt.ensure_started()

        mapping = display_mapping()
        screen_box = box_to_screen(box, mapping)
        if screen_box[2] - screen_box[0] < 2 or screen_box[3] - screen_box[1] < 2:
            # 被窗口边界夹没了：截出来也没有意义，直接当作失败。
            return None

        def _grab(region):
            return np.asarray(ImageGrab.grab(
                bbox=tuple(int(v) for v in region), all_screens=False))

        started = time.time()
        rgb = _grab(screen_box)
        if wt.moved_since(started):
            # 抓图期间窗口动过：这一帧与几何对不上了 → 走原子重抓（少见路径）。
            rgb = None
        if rgb is None:
            from display_mapping import capture_region
            rgb, _fresh = capture_region(
                grabber=_grab_region_client_grabber(), region=box,
                region_is_client=True)
            if rgb is None:
                return None

        if mapping is not None:
            smallest = min(mapping.scale_x, mapping.scale_y) or 1.0
            if smallest < 1.0:
                scale = float(scale) / smallest
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if scale and scale != 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_CUBIC)
        return img
    except Exception:
        return None


def _grab_region_client_grabber():
    """给 `capture_region` 用的抓图器：按屏幕框截一块。"""
    def grab(region):
        try:
            import numpy as np
            from PIL import ImageGrab
            return np.asarray(ImageGrab.grab(
                bbox=tuple(int(v) for v in region), all_screens=False))
        except Exception:
            return None
    return grab


def _ocr_lines(box, scale: float, tag: str):
    """对屏幕区域做一次 OCR，返回 OcrLine 列表；不可用/异常返回 None。

    复用换牌/胜率同一个 OCR backend（mulligan_reader 跨局复用），所以结算
    阶段不会额外加载引擎；引擎没就绪时返回 None，由调用方回退。
    """
    reader = mulligan_reader
    img = _grab_region(box, scale)
    if img is None or reader is None:
        return None
    try:
        evidence = reader.backend.recognize(
            img, f"{tag}-{time.time():.3f}", tag)
    except Exception:
        return None
    return list(evidence.lines)


def _region_is_blank(img) -> bool:
    """段位区域是不是「没有数字」的纯背景（未上传说）。

    未上传说时段位数字的位置是空的，靠“亮像素太少”判定，直接跳过 OCR。
    判不准（依赖异常）时返回 False = 当作有内容，交给 OCR 去读；
    img 为 None（截图失败）也算“空”，但调用方会把它当成**还没读到**去重试。
    """
    if img is None:
        return True
    try:
        import numpy as np
        gray = np.asarray(img)
        if gray.ndim == 3:
            gray = gray[:, :, 0] * 0.114 + gray[:, :, 1] * 0.587 \
                + gray[:, :, 2] * 0.299
        return int((gray > _RANK_BRIGHT_THRESHOLD).sum()) < _RANK_MIN_BRIGHT_PIXELS
    except Exception:
        return False


def _rank_bright_pixels(img) -> int:
    """段位区域里「够亮」的像素个数（只用于日志/页面的失败原因，判不出来返回 -1）。"""
    if img is None:
        return -1
    try:
        import numpy as np
        gray = np.asarray(img)
        if gray.ndim == 3:
            gray = gray[:, :, 0] * 0.114 + gray[:, :, 1] * 0.587 \
                + gray[:, :, 2] * 0.299
        return int((gray > _RANK_BRIGHT_THRESHOLD).sum())
    except Exception:
        return -1


def parse_rank_text(text: str) -> dict:
    """把结算界面段位区域的 OCR 文本解析成 {number, legend, raw, empty}。

    判定只看数字（用户口径）：
      * 这段区域在**未上传说时是空白的**，上了传说才会显示名次数字；
      * 所以「读得到数字」＝已上传说，数字就是名次；「读不到数字」＝没上传说。
    不做任何文字匹配（不再认「传说/傳奇/Legend」），只统一全角数字再取第一个
    1.._MAX_RANK_NUMBER 的整数；文本里的其它字（如果有）不影响判定。
    """
    result = {"number": None, "legend": False, "raw": text, "empty": False}
    if not text or not str(text).strip():
        return result
    normalized = str(text).translate(_FULLWIDTH_DIGITS)
    compact = re.sub(r"\s+", "", normalized)
    match = re.search(r"\d{1,6}", compact)
    if match:
        value = int(match.group(0))
        if 1 <= value <= _MAX_RANK_NUMBER:
            result["number"] = value
    # 有数字就是传说（名次）；没数字就是没上传说。
    result["legend"] = result["number"] is not None
    return result


def is_pure_rank_text(text: str) -> bool:
    """整行是不是「只有名次数字」（允许全角数字和可选的前导「#」）。

    给需要更严的检测用（例如必须在某一步之前就拦住点击时）：屏幕上别的
    内容被 OCR 随口读出一个数字，不算「已上传说」。
    """
    compact = re.sub(r"\s+", "", str(text or "").translate(_FULLWIDTH_DIGITS))
    return bool(re.fullmatch(r"#?\d{1,6}", compact))


def read_rank_region(strict: bool = False, min_confidence: float = 0.0) -> dict:
    """读一次段位区域的数字，返回 {number, legend, raw, empty, blank, bright}。

    * 读到数字 → number/legend 有值；
    * 区域是空白 → empty=True（**可能只是动画还没把名次画出来**，调用方会重试）；
    * 截图失败 → blank=True（这次不算数，读不出来）；
    * OCR 不可用/读不到 → raw=None。
    strict          ：只认「整行就是数字」（见 is_pure_rank_text）；
    min_confidence  ：低于这个置信度的 OCR 行直接丢掉。
    bright 是这个区域里「够亮」的像素个数（-1 = 判不出来），只用于日志与页面显示。
    """
    result = {"number": None, "legend": False, "raw": None,
              "empty": False, "blank": False, "bright": -1}
    box = _post_game_rank_box()
    img = _grab_region(box, _POST_GAME_OCR_SCALE)
    blank = _region_is_blank(img)
    result["blank"] = blank
    result["bright"] = _rank_bright_pixels(img)
    if blank:
        # 截图失败也算空，但要标成“还没读到”（blank=True），由调用方重试；
        # 真正截到图且亮像素太少时只是 empty，同样交给调用方决定重试几次。
        result["empty"] = img is not None
        return result
    lines = _ocr_lines(box, _POST_GAME_OCR_SCALE, "rank")
    if lines is None:
        return result
    for line in lines:
        text = str(getattr(line, "text", "") or "").strip()
        confidence = float(getattr(line, "confidence", 0.0) or 0.0)
        if min_confidence and confidence < float(min_confidence):
            continue
        if not text or not any(ch.isdigit() for ch in text):
            # 没有数字的行（文字标签之类）一律忽略：判定只看数字。
            continue
        if strict and not is_pure_rank_text(text):
            continue
        parsed = parse_rank_text(text)
        if parsed["number"] is not None:
            parsed["blank"] = False
            parsed["bright"] = result["bright"]
            return parsed
    return result


def _rank_target_number(cfg) -> int:
    """配置里的目标名次（收敛到合法范围；写坏时退 1）。"""
    try:
        target = int(cfg.get("legend_number", 1))
    except (TypeError, ValueError):
        target = 1
    return max(1, min(_MAX_RANK_NUMBER, target))


def _rank_action_for(reading, cfg) -> str:
    """读到的名次该做什么：返回 "stop" / "keep" / ""（什么都不做）。

    * 没读到数字（未上传说 / 截图为空）→ ""（不动）；
    * action="stop"（上分停止）：
        名次数值 <= 目标 → "stop"（本局结束后停止自动化）；
    * action="keep"（控分，用户 2026-10-11 口径）：
        名次数值 < 目标 → "keep"（太靠前了，本局开局即投降把它压后），
        名次数值 >= 目标 → ""（已在目标之内，正常打）。

    方向提醒：炉石传说名次**数值越小 = 排名越高**，所以「控分」= 太小就投降。
    """
    if not reading or not cfg.get("enabled"):
        return ""
    if not reading.get("legend"):
        return ""  # 没上传说、或这段位置没数字 —— 没有名次可判，不动。
    number = reading.get("number")
    if not isinstance(number, int):
        return ""
    target = _rank_target_number(cfg)
    if cfg.get("action") == "keep":
        return "keep" if number < target else ""
    # 默认按「上分停止」处理。
    if cfg.get("mode") != "legend_number":
        return "stop"  # mode=legend：读到名次就说明已上传说 → 停。
    return "stop" if number <= target else ""


def _rank_condition_met(reading, cfg) -> bool:
    """读数是否命中「上分停止」目标（保持旧语义，供测试与兼容调用）。

    mode="legend"        ：上传说即命中；
    mode="legend_number" ：上了传说且名次 <= 目标（第 N 名或更好）。
    注意：这个函数只回答 stop 语义，不看 action；控分请用 _rank_action_for。
    """
    return _rank_action_for(reading, {**cfg, "action": "stop"}) == "stop"


def _read_rank_with(attempts: int, wait: float, strict: bool = False,
                    min_confidence: float = 0.0):
    """按次数重试读段位，返回最后一次读数（读不到也返回，由调用方判断）。"""
    total = max(1, int(attempts))
    reading = None
    for attempt in range(1, total + 1):
        reading = read_rank_region(strict=strict,
                                   min_confidence=min_confidence)
        # 读到了能解析的数字、或读到了带数字的文本，就没必要再读。
        if reading.get("number") is not None or reading.get("raw") is not None:
            return reading
        if attempt < total:
            time.sleep(wait)
    return reading


def _read_rank_target(attempts=None, wait=None, label="选择套牌界面",
                      strict=False, min_confidence=0.0, confirm=False) -> str:
    """读一次段位，按用户设定的「段位目标」返回要做的动作。

    返回值：``"stop"``（命中上分停止）/ ``"keep"``（控分：太靠前，要投降压低）/
    ``""``（什么都不做）。判定见 :func:`_rank_action_for`。

    label         ：这一步叫什么（写进日志与页面，回答"到底检测了没有、在哪一步读的"）；
    attempts/wait ：重试次数与间隔（空白/截图失败/OCR 没读到都算"还没读到"，
                    会重试——这是实测踩过的坑：动画还没把名次画出来时第一次读
                    会得出"未上传说"的错误结论）；
    strict        ：只认「整行就是数字」（见 is_pure_rank_text）；
    min_confidence：丢掉低于该置信度的 OCR 行；
    confirm       ：读数会触发动作（stop/keep）时，隔 _POST_GAME_RANK_CONFIRM_WAIT
                    再读一次，两次都读到数字才算数（防止把过渡画面里的误识别当真）。
                    不会触发动作的读数不复核——复核没有意义，还会拖慢「点开始匹配」。
    """
    global _rank_stop_triggered, _rank_stop_stop_reason, _rank_last_read
    global _rank_last_phase
    if _rank_stop_triggered:
        return ""
    cfg = rank_stop_settings()
    if not cfg.get("enabled"):
        return ""
    total = _POST_GAME_RANK_READ_ATTEMPTS if attempts is None else int(attempts)
    interval = _POST_GAME_RANK_RETRY_WAIT if wait is None else float(wait)
    reading = _read_rank_with(total, interval, strict, min_confidence)
    if confirm and reading.get("number") is not None \
            and _rank_action_for(reading, cfg):
        # 只有这次读数**会触发动作**（上分停止 / 控分投降）才二次复核：复核的
        # 目的就是「一次误识别不能停/投降」；不会触发动作的读数复核没有意义，
        # 还会让「点开始匹配」白等 0.6s + 一次 OCR（用户反馈启动慢）。
        time.sleep(max(interval, _POST_GAME_RANK_CONFIRM_WAIT))
        again = read_rank_region(strict=strict,
                                 min_confidence=min_confidence)
        if again.get("number") is None:
            # 复核失败：当作"还没读到"，绝不因为一次误识别就停止或投降。
            reading = again
    _rank_last_read = reading
    _rank_last_phase = label
    number = reading.get("number")
    if number is None:
        if reading.get("raw") is not None:
            reason = f"读到的内容里没有名次数字（「{reading['raw']}」）"
        elif reading.get("empty") \
                and 0 <= reading.get("bright", -1) < _RANK_MIN_BRIGHT_PIXELS:
            reason = (f"段位位置还是空白（亮像素 {reading['bright']} 个，"
                      f"不够 {_RANK_MIN_BRIGHT_PIXELS} 个）")
        else:
            reason = f"没读到数字（截图/OCR 未读到，已试 {total} 次）"
        manual_controller.output(
            f"[SYS] 段位目标检测（{label}）：{reason} —— 按「还没上传说」处理。")
        return ""
    action = _rank_action_for(reading, cfg)
    target = _rank_target_number(cfg)
    if action == "keep":
        manual_controller.output(
            f"[SYS] 控分检测（{label}）：当前名次 {number} 比目标 {target} 更靠前，"
            f"本局将开局即投降把名次压后。")
        return "keep"
    if action == "stop":
        _rank_stop_triggered = True
        _rank_stop_stop_reason = f"已上传说到传说 {number} 名，自动化停止（本局结束后生效）。"
        manual_controller.output(
            f"[SYS] 上分目标已达成（{label}）：{_rank_stop_stop_reason}")
        request_stop_after_game()
        return "stop"
    manual_controller.output(
        f"[SYS] 段位目标检测（{label}）：当前名次 {number}（目标 {target}），"
        f"未触发动作。")
    return ""


def _check_rank_stop(attempts=None, wait=None, label="结算界面",
                     strict=False, min_confidence=0.0, confirm=False) -> bool:
    """兼容旧调用：读一次段位，命中「上分停止」就请求本局结束后停止。

    返回值只表示"是否命中停止"。控分（action=keep）请用 _read_rank_target，
    它会额外返回 "keep" 并**不**请求停止。
    """
    return _read_rank_target(attempts=attempts, wait=wait, label=label,
                             strict=strict, min_confidence=min_confidence,
                             confirm=confirm) == "stop"


def rank_stop_detection_state() -> dict:
    """段位目标（上分停止 / 控分）的当前状态，供 Web 控制台与浮窗显示。"""
    cfg = rank_stop_settings()
    reading = _rank_last_read or {}
    return {
        "enabled": bool(cfg["enabled"]),
        "mode": cfg["mode"],
        "action": cfg.get("action", "stop"),
        "legend_number": int(cfg["legend_number"]),
        "last_number": reading.get("number"),
        "last_legend": reading.get("legend"),
        "last_raw": reading.get("raw"),
        "last_empty": bool(reading.get("empty")),
        "last_phase": _rank_last_phase,
        "triggered": bool(_rank_stop_triggered),
        "stop_reason": _rank_stop_stop_reason,
        "keep_count": int(_keep_score_count),
    }


def _post_game_start_box() -> tuple:
    """结算「开始」按钮的检测框：优先用用户校准的 post_game_start_roi。

    recommendation_config 每次对局都会重建，所以校准完重开一局即生效。
    配置缺失/非法时回退代码默认值（_POST_GAME_START_BUTTON_ROI）。
    """
    return _config_roi("post_game_start_roi", _POST_GAME_START_BUTTON_ROI)


def _post_game_rank_box() -> tuple:
    """段位数字的读取框：优先用用户校准的 post_game_rank_roi。

    段位数字的位置随分辨率/窗口位置变化（且不同界面布局下会偏），所以和其它
    区域一样允许在校准窗口里拖（`screen_regions` 的 "rank" 目标）。
    配置缺失/非法时回退代码默认值（_POST_GAME_RANK_ROI）。
    """
    return _config_roi("post_game_rank_roi", _POST_GAME_RANK_ROI)


def post_game_start_point() -> tuple:
    """点「开始」按钮用的坐标：校准框的正中心（默认正好 (1400,900)）。

    框是用户在校准窗口里拖的，中心就是实际点击位置；配置异常时回退默认中心。
    """
    try:
        left, top, right, bottom = _post_game_start_box()
        return ((int(left) + int(right)) // 2, (int(top) + int(bottom)) // 2)
    except Exception:
        return _POST_GAME_START_DEFAULT_POINT


def click_post_game_start():
    """点一下结算界面的「开始」按钮正中心（坐标可校准）。"""
    x, y = post_game_start_point()
    info_print(f"已点击结算界面「开始」按钮的中心：({x}, {y})")
    click.left_click(x, y)


def start_button_present() -> bool:
    """结算界面的「开始」按钮是否已经出现（出现即收尾完成）。

    先读用户校准的框；读不到时再用固定兜底区域读一次（框被拖小/拖偏也能认出）。
    OCR 不可用/异常一律返回 False：检测不到就继续点左上角待命点，绝不提前
    跑掉（宁可多等一会儿，也不能在结算界面上空点着回到主界面）。
    """
    for box in (_post_game_start_box(), _POST_GAME_START_FALLBACK_ROI):
        lines = _ocr_lines(box, _POST_GAME_OCR_SCALE, "start")
        if lines is None:
            continue
        for line in lines:
            text = str(getattr(line, "text", "") or "")
            confidence = float(getattr(line, "confidence", 0.0) or 0.0)
            if confidence < _POST_GAME_MIN_CONFIDENCE:
                continue
            if any(keyword in text for keyword in _POST_GAME_START_KEYWORDS):
                return True
    return False


def click_post_game_point():
    """点掉结算界面：**屏幕中间那两个点**（`click.ERROR_REPORT_POINTS`）。

    这两下是原来就用来应对错误弹窗/断线提示的位置：`(1100, 820)`（奇怪的错误提示）
    → `(960, 650)`（断线时取消）。用户口径：回合结束推结算界面点**中间**这两下，
    **不点右边**（右边那些辅助点会被右上角日志浮窗吃掉，也可能误点到下一屏）。
    每轮先点这两下把结算界面推过去，再由调用方 `QuittingBattle` 检测「开始」
    按钮——检测到了才收尾，收尾里才点第三个点（「开始」按钮正中心）。
    """
    click.commit_error_report()


def confirm_button_present() -> bool:
    """换牌“确认”按钮是否仍在屏幕中间（提交后应消失）。

    用于换牌点击后的二次校验：确认按钮还在 → 换牌未提交成功，需重试；
    确认按钮消失 → 已提交，等待对局开始。用 OCR 读按钮区域的“确认”二字。
    """
    try:
        import cv2
        import numpy as np
        from PIL import ImageGrab
    except Exception:
        return False
    try:
        from display_mapping import box_to_screen, display_mapping
        box = recommendation_config.mulligan_confirm_roi
        screen_box = box_to_screen(box, display_mapping())
        rgb = np.asarray(ImageGrab.grab(
            bbox=tuple(int(v) for v in screen_box), all_screens=False))
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        evidence = mulligan_reader.backend.recognize(
            img, f"confirm-{time.time():.3f}", "confirm")
    except Exception:
        return False
    return any(
        "确认" in line.text and line.confidence >= 0.5
        for line in evidence.lines)


# ---------------------------------------------------------------- 自动投降检测
# 盒子浮动条“AI胜率 X%”的截图区域（1920x1080 实测）：主区域 + 放宽的兜底区域。
# 这是代码默认值；用户用校准工具改过之后走 ui_config.json 的
# ai_win_rate_roi / ai_win_rate_wide_roi（见 _ai_win_rate_regions）。
_AI_WIN_RATE_REGIONS = ((110, 8, 270, 48), (95, 0, 300, 60))
# 浮动条字号很小，放大后再送 OCR，识别率明显更高。
_AI_WIN_RATE_SCALE = 2.0
# 同一回合内 OCR 读不到时的重试次数与间隔：盒子浮动条常常要等面板画好才出现，
# 只读一次就丢掉整个回合会表现为“有时候根本没在检测”。
_CONCEDE_MAX_ATTEMPTS = 3
_CONCEDE_RETRY_WAIT = 0.6


def _ai_win_rate_regions() -> tuple:
    """AI胜率截图区域 (主区域, 兜底区域)：优先用用户校准值，否则用默认值。

    recommendation_config 在 initialize_recommendation_automation() 里每次
    对局都会重建，因此校准完重开对局即生效，不用重启脚本。
    """
    config = recommendation_config
    if config is None:
        return _AI_WIN_RATE_REGIONS
    main = getattr(config, "ai_win_rate_roi", None)
    wide = getattr(config, "ai_win_rate_wide_roi", None)
    if main is None and wide is None:
        return _AI_WIN_RATE_REGIONS
    try:
        regions = list(_AI_WIN_RATE_REGIONS)
        if main is not None:
            regions[0] = tuple(int(v) for v in main)
        if wide is not None:
            regions[1] = tuple(int(v) for v in wide)
        return tuple(regions)
    except (TypeError, ValueError):
        return _AI_WIN_RATE_REGIONS


def concede_detection_state() -> dict:
    """自动投降检测的当前状态（供 Web 控制台 / 日志浮窗显示）。

    rate=None 表示本回合没读到（或本局还没检测过）；checked_turn 是最近一次
    检测发生在第几回合，用来直观确认“到底有没有在检测”。
    """
    cfg = _load_concede_config()
    return {
        "enabled": bool(cfg["enabled"]),
        "threshold": float(cfg["threshold"]),
        "rounds": int(cfg["rounds"]),
        "rate": _concede_last_rate,
        "streak": int(_concede_streak),
        "checked_turn": _concede_last_check,
        "triggered": bool(_concede_triggered),
    }


def _load_concede_config():
    """读取自动投降配置（ui_config.json 的 auto_concede 段）。"""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent / "ui_config.json"
        ac = json.loads(p.read_text(encoding="utf-8")).get("auto_concede") or {}
        return {
            "enabled": bool(ac.get(
                "enabled", DEFAULT_AUTO_CONCEDE["enabled"])),
            "threshold": float(ac.get(
                "threshold", DEFAULT_AUTO_CONCEDE["threshold"])),
            "rounds": max(1, int(ac.get(
                "rounds", DEFAULT_AUTO_CONCEDE["rounds"]))),
        }
    except Exception:
        return dict(DEFAULT_AUTO_CONCEDE)


# ---------------------------------------------------------------- 卡组识别投降
# 敌我判反时"暂停判定"的提示，每局只打一次。
_NAME_MISMATCH_SURRENDER_WARNED = None


def _load_surrender_config():
    """读取卡组识别投降配置（ui_config.json 的 auto_surrender 段）。

    每次都读文件：网页保存后立即生效，不用重启自动化。
    """
    try:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent / "ui_config.json"
        raw = json.loads(path.read_text(encoding="utf-8")).get("auto_surrender")
        if not isinstance(raw, dict):
            return dict(DEFAULT_AUTO_SURRENDER)
        return raw
    except Exception:
        return dict(DEFAULT_AUTO_SURRENDER)


def _maybe_surrender_by_rules():
    """卡组识别投降：命中返回命中信息（调用方再去点认输），否则 None。

    必须**在 `is_my_turn` 之前**调用。用户的需求是"对方在前四回合打出某卡
    就投降"，而对手打牌发生在他自己的回合；只在己方回合判定就永远错过。
    """
    global _NAME_MISMATCH_SURRENDER_WARNED
    if quitting_flag or _concede_triggered:
        return None
    cfg = _load_surrender_config()
    if not cfg.get("enabled"):
        return None

    # 敌我可能判反（配置的用户 ID 与日志昵称对不上）：一律不判定。
    # 这时"对方的卡"有可能其实是我方的卡，宁可错过也不误投降。
    info = _name_match_result
    if isinstance(info, dict) and info.get("matched") is False:
        key = (log_state.game_generation, "name_mismatch")
        if _NAME_MISMATCH_SURRENDER_WARNED != key:
            _NAME_MISMATCH_SURRENDER_WARNED = key
            manual_controller.output(
                "[SYS] 卡组识别投降：用户 ID 与日志玩家名不匹配，"
                "无法可靠区分敌我，本局暂停判定（胜率投降不受影响）。")
        return None

    hit = surrender_rules.engine.check(cfg, log_state)
    if hit is None:
        return None
    details = "；".join(hit.get("evidence") or [])
    manual_controller.output(
        f"[SYS] 识别为对方卡组「{hit['name']}」→ 投降"
        + (f"（{details}）" if details else ""))
    return hit


def surrender_rule_state():
    """给网页/浮层：当前卡组识别配置 + 实时进度（哪套卡组满足到第几项）。"""
    cfg = _load_surrender_config()
    try:
        state = surrender_rules.engine.status(log_state)
    except Exception as exc:
        state = {"error": str(exc), "decks": []}
    return {"config": cfg, "state": state}


def read_ai_win_rate():
    """OCR 左上角盒子“AI胜率 X%”，返回百分数值；读不到返回 None。

    浮动条字号很小，先放大再识别；主区域读不到时用放宽的兜底区域再试一次，
    避免因为浮动条位置/宽度略有差异而整回合读不到。

    区域是客户区相对坐标，交给 _grab_region 统一换算到屏幕（含窗口偏移），
    所以与 _grab_region 的放大补偿共用一套逻辑。
    """
    for index, box in enumerate(_ai_win_rate_regions()):
        img = _grab_region(box, _AI_WIN_RATE_SCALE)
        if img is None:
            continue
        try:
            evidence = mulligan_reader.backend.recognize(
                img, f"winrate-{index}-{time.time():.3f}", "winrate")
        except Exception:
            continue
        for line in evidence.lines:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", line.text)
            if m:
                value = float(m.group(1))
                if 0.0 <= value <= 100.0:
                    return value
    return None


def _maybe_concede(snapshot):
    """每回合检测左上角 AI 胜率；连续低于阈值达到设定回合数则返回 True。

    同一回合内 OCR 读不到会重试若干次：盒子浮动条往往要等面板画好才出现，
    原先“一次读不到就丢掉整个回合”，会表现为“有时候根本没在检测”。
    """
    global _concede_streak, _concede_last_turn, _concede_triggered
    global _concede_last_rate, _concede_last_check
    if _concede_triggered:
        return False
    cfg = _load_concede_config()
    if not cfg["enabled"]:
        return False
    turn = getattr(snapshot, "game_num_turns_in_play", 0)
    if turn == _concede_last_turn:
        return False  # 本回合已检测过
    rate = None
    for attempt in range(1, _CONCEDE_MAX_ATTEMPTS + 1):
        rate = read_ai_win_rate()
        if rate is not None:
            break
        if attempt < _CONCEDE_MAX_ATTEMPTS:
            manual_controller.output(
                f"[SYS] 自动投降检测：AI胜率没读到（第 {attempt}/"
                f"{_CONCEDE_MAX_ATTEMPTS} 次），{_CONCEDE_RETRY_WAIT:.1f}s 后重试……")
            time.sleep(_CONCEDE_RETRY_WAIT)
    _concede_last_turn = turn  # 无论成败，本回合不再重复检测
    _concede_last_rate = rate  # 供浮窗/网页显示（None = 本回合没读到）
    _concede_last_check = turn
    if rate is None:
        # 读不到胜率（面板未就绪/OCR失败）：不激进投降，重置连续计数。
        # 这里始终打日志，方便区分“没检测”与“检测了但没读到”。
        manual_controller.output(
            f"[SYS] 自动投降检测：本回合（第 {turn} 回合）AI胜率读取失败，"
            f"已重试 {_CONCEDE_MAX_ATTEMPTS} 次，跳过本次检测。")
        if _concede_streak:
            manual_controller.output(
                "[SYS] 自动投降检测：连续计数清零。")
        _concede_streak = 0
        return False
    if rate < cfg["threshold"]:
        # 先 +1 再提示，第一次低于阈值就显示“连续低于 1 回合”。
        _concede_streak += 1
        manual_controller.output(
            f"[SYS] 自动投降检测：AI胜率 {rate:.1f}%（阈值 "
            f"{cfg['threshold']:.0f}%，连续低于 {_concede_streak} 回合）")
        if _concede_streak >= cfg["rounds"]:
            _concede_triggered = True
            return True
    else:
        _concede_streak = 0
        manual_controller.output(
            f"[SYS] 自动投降检测：AI胜率 {rate:.1f}%（阈值 "
            f"{cfg['threshold']:.0f}%，未低于阈值，连续计数清零）")
    return False


def _do_concede(reason=None):
    """点击右下角齿轮 → 等菜单弹出 → 点中间红色“认输”。

    `reason` 只影响提示文案（默认沿用原来的「持续低胜率」），
    卡组识别投降会传「识别为对方卡组「X」」。
    """
    global concede_count
    concede_count += 1
    manual_controller.output(f"[SYS] {reason or '持续低胜率'}，开始自动认输……")
    try:
        with click.hearthstone_action_session():
            click.click_setting()      # 齿轮 (1895, 1060)
            time.sleep(1.0)            # 等游戏菜单弹出
            click.click_concede()      # 认输 (960, 380)
        time.sleep(1.0)
    except Exception as exc:
        manual_controller.output(f"[SYS] 自动认输点击失败：{exc}")


def _wait_until(predicate, attempts, wait, on_tick=None) -> bool:
    """轮询等待某个条件成立；predicate() 返回真即成功。

    仅在本函数内轮询（不 sleep 更长），且尝试次数固定，不会无限等。
    on_tick(attempt) 可选：每轮打一次进度日志，方便用户从日志看到"在等什么"。
    """
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        try:
            if predicate():
                return True
        except Exception:
            pass
        if on_tick is not None:
            try:
                on_tick(attempt)
            except Exception:
                pass
        if attempt < total:
            time.sleep(wait)
    return False


def _in_match_state() -> bool:
    """当前是否已经进入对局（换牌 / 战斗）——用来判断"点开始后进没进局"。

    **主判据 = 界面状态**：只有界面确实停在换牌 / 战斗，才算进局。
    日志兜底（Power.log 已消费到本局、且回合数 > 0）只作补充——不能用
    `log_state.game_entity_id != 0` 这种"库里有残留"的弱证据，否则点开始前
    上一局的日志残留会让"还没进局"被误判成"已进局"，控分就会朝一个并不
    存在的对局乱点齿轮。
    """
    try:
        if get_screen.get_state() in (FSM_CHOOSING_CARD, FSM_BATTLING):
            return True
    except Exception:
        pass
    try:
        if update_log_state() and log_state.game_num_turns_in_play > 0:
            return True
    except Exception:
        pass
    return False


def _at_deck_select() -> bool:
    """当前是否回到了"选卡组/选人"界面（投降收尾完成的判据）。"""
    try:
        return get_screen.get_state() == FSM_CHOOSING_HERO
    except Exception:
        return False


def _game_ended() -> bool:
    """对局是否已经结束（COMPLETE）。日志不可用/异常返回 False。"""
    try:
        return bool(update_log_state() and log_state.is_end)
    except Exception:
        return False


def _mulligan_finished(baseline_generation=None) -> bool:
    """换牌阶段是否已经结束（第一回合已开始）。

    用 `game_num_turns_in_play > 0` 作为判据（与 `ChoosingCardAction` /
    `wait_until_battle_starts` 同一口径）。`baseline_generation` 是点「开始匹配」
    前的日志 generation：只有 generation 变了、并且回合数 > 0，才说明是**这一局**
    的换牌真的结束了——否则上一局日志残留的回合数会让控分在换牌阶段就投降。
    """
    try:
        if not update_log_state():
            return False
        if log_state.is_end:
            return False
        if (baseline_generation is not None
                and log_state.game_generation == baseline_generation):
            return False
        return int(log_state.game_num_turns_in_play or 0) > 0
    except Exception:
        return False


def _confirm_mulligan_if_ready() -> bool:
    """换牌确认按钮在场时提交空换牌（不替换任何牌）。

    控分局的目标是尽快输掉，没必要按留牌推荐换牌；但也不能在换牌界面
    直接投降。先确认按钮在场再点，避免换牌还没画好就盲点。
    """
    try:
        if not confirm_button_present():
            return False
        click.run_hearthstone_action(click.commit_choose_card)
        info_print("[SYS] 控分：已提交换牌（不替换手牌），等待换牌阶段结束……")
        return True
    except Exception as exc:
        warn_print(f"[SYS] 控分：提交换牌失败：{exc}")
        return False


def _wait_for_mulligan_end(baseline_generation=None) -> bool:
    """等换牌阶段真正结束；期间尝试提交一次空换牌。

    返回 True 才允许认输。收到停止请求、对局提前结束或超时都返回 False，
    调用方据此放弃本次投降，绝不把「换牌中」当成「换牌结束」。
    """
    confirmed = False
    total = max(1, int(_KEEP_SCORE_MULLIGAN_ATTEMPTS))
    for attempt in range(1, total + 1):
        if quitting_flag or stop_after_current_game:
            warn_print("[SYS] 控分：收到停止请求，中止换牌等待，不点认输。")
            return False
        if _mulligan_finished(baseline_generation):
            return True
        if _game_ended():
            warn_print("[SYS] 控分：对局在换牌阶段就结束了，不点认输。")
            return False
        if not confirmed:
            confirmed = _confirm_mulligan_if_ready()
        if attempt % 5 == 0:
            info_print(
                f"[SYS] 控分：等待换牌阶段结束……（{attempt}/{total}）")
        time.sleep(_KEEP_SCORE_MULLIGAN_WAIT)
    return False


def _keep_score_once() -> bool:
    """控分：投一局把名次往后压。成功返回 True（已回到选卡组界面）。

    流程（用户 2026-10-23 口径）：
      ① 点「开始匹配」进对局 → ② 等到真的进了局 → ③ **等换牌阶段结束**
      （确认按钮在场时提交空换牌）→ ④ 才认输 → ⑤ 等回到选卡组界面。
      换牌阶段内绝不点投降；等待失败/对局提前结束都不投降。

    **每次只投一局**，然后交还主循环：这样存活检测照常跑，也不会在这里长时间阻塞。
    任何一步超时都返回 False（不硬撑、不空转），由调用方决定后续。
    """
    global _keep_score_count
    _keep_score_count += 1
    manual_controller.output(
        f"[SYS] 控分：本局名次比目标更靠前，开始「开局即投降」"
        f"（本次运行第 {_keep_score_count} 局）。")
    try:
        baseline_generation = log_state.game_generation
    except Exception:
        baseline_generation = None
    # ① 点「开始匹配」（与正常开局同一个动作，保证语义一致）。
    try:
        click.run_hearthstone_action(click.match_opponent)
    except Exception as exc:
        warn_print(f"[SYS] 控分：点「开始匹配」失败：{exc}")
        return False
    # ② 等进入对局：没进局就不投降（避免在非对局界面乱点）。
    entered = _wait_until(
        _in_match_state, _KEEP_SCORE_ENTER_ATTEMPTS, _KEEP_SCORE_ENTER_WAIT,
        on_tick=lambda n: info_print(
            f"[SYS] 控分：等待进入对局……（{n}/{_KEEP_SCORE_ENTER_ATTEMPTS}）"))
    if not entered:
        warn_print("[SYS] 控分：等了很久也没进对局，放弃本次投降（下局再试）。")
        return False
    # ③ 等换牌阶段结束：这是用户明确要求的投降时机。
    manual_controller.output(
        "[SYS] 控分：已进入对局，等待换牌阶段结束（不替换手牌）……")
    if not _wait_for_mulligan_end(baseline_generation):
        warn_print("[SYS] 控分：换牌阶段未结束或对局已结束，本次不点认输（下局再试）。")
        return False
    if _game_ended():
        warn_print("[SYS] 控分：对局已结束，跳过认输。")
        return False
    manual_controller.output("[SYS] 控分：换牌阶段已结束，开始认输。")
    try:
        click.run_hearthstone_action(_do_concede)
    except Exception as exc:
        warn_print(f"[SYS] 控分：认输点击失败：{exc}")
        return False
    # ④ 等回到选卡组界面（投降 → 结算画面 → 回选卡组）。
    back = _wait_until(
        _at_deck_select, _KEEP_SCORE_BACK_ATTEMPTS, _KEEP_SCORE_BACK_WAIT,
        on_tick=lambda n: info_print(
            f"[SYS] 控分：等待回到选择套牌界面……（{n}/{_KEEP_SCORE_BACK_ATTEMPTS}）"))
    if not back:
        warn_print("[SYS] 控分：投降后没等到选卡组界面，交由状态机自己找路。")
        return False
    time.sleep(1.5)  # 留一点时间让界面稳定，方便下一步读名次。
    manual_controller.output(
        f"[SYS] 控分：已投降 {_keep_score_count} 局，重新检查名次。")
    return True


def run_automatic_battle_step():
    """Observe opponent turns; execute one newly validated player action."""
    global player_turn_delay_key, last_automation_diagnostic, _ocr_fail_streak
    global _concede_triggered

    snapshot = refresh_snapshot()
    if snapshot is None:
        _report_automation_diagnostic(
            "power_log_unavailable", "Power.log 暂不可用，继续重试。")
        return None
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if _concede_triggered:
        # 已触发自动认输：等待日志确认对局真正结束（COMPLETE）后再走结算计数，
        # 避免点完认输立刻计数导致“完成对局”时机不准。
        return None
    # 卡组识别投降：**在 is_my_turn 之前**判定 —— 特征卡往往是对手在他自己
    # 回合打出的，等到我方回合再看就已经晚了。命中就立刻点认输。
    surrender_hit = _maybe_surrender_by_rules()
    if surrender_hit is not None:
        _concede_triggered = True
        _do_concede(reason=f"识别为对方卡组「{surrender_hit['name']}」")
        return None
    if not snapshot.is_my_turn:
        # 活人感：对手回合里出现新随从就移上去悬停（只移动不点击）；
        # 这里本来就在空转等对手，不会拖慢自己的操作。
        _human_like_opponent_minions(snapshot)
        _report_automation_diagnostic("opponent_turn", "等待对手操作。")
        # 对方回合清空延迟标记：每次切回我方回合必延时一次，
        # 同回合内多次出牌不再重复延时（不依赖可能失真的回合号）。
        player_turn_delay_key = None
        return None
    # 自动投降：只在我方回合检测（对手回合不计入“连续回合”），
    # 连续低于阈值达到设定回合数则主动认输。
    if _maybe_concede(snapshot):
        _do_concede()
        # 认输后不立即返回结算，等上面 is_end 分支（日志 COMPLETE）再计数。
        return None
    _report_automation_diagnostic("my_turn", "轮到己方操作：开始读取推荐……")
    turn = snapshot.game_num_turns_in_play
    if player_turn_delay_key != turn:
        # 每个新回合开始只延时一次（给盒子更新推荐留时间），
        # 同回合内的多次出牌操作之间不重复延时。
        player_turn_delay_key = turn
        delay = recommendation_config.pre_action_delay_seconds
        label = f"回合 {turn} 延时"
        # 第一回合额外延时：开局生效的全局卡（如黑暗主教本尼迪塔斯）要跑
        # 效果动画，盒子推荐更新更晚。这里在 pre_action 基础上按张数追加：
        #   每张生效卡 × per_card_delay（无固定基础额外）。
        if turn == 1:
            card_count = getattr(snapshot, "start_of_game_card_count", 0) or 0
            extra = (card_count
                     * recommendation_config.first_turn_per_card_delay_seconds)
            if extra > 0:
                delay += extra
                label = f"第一回合延时（{card_count} 张开局生效卡）"
        _sleep_with_delay(delay, label)
        manual_controller.output(
            f"[SYS] 回合 {turn} 延时结束，开始本轮推荐读取。")
    if recommendation_flow is None:
        return run_manual_battle_step()

    result = recommendation_flow.run_player_turn_step()
    if result.status == FlowStepStatus.RETRY:
        # 连续读取失败计数：存活检测告警里会带上它（issue 建议的“辅以 OCR
        # 连续失败计数”，便于区分“炉石没了”与“盒子没面板”）。
        _ocr_fail_streak += 1
        if result.diagnostics == "discover_choice_still_open":
            message = "发现选择仍在，准备重新点击。"
        else:
            message = (
                "当前推荐暂不可执行，继续重试："
                f"{result.diagnostics}")
        _report_automation_diagnostic(
            f"retry:{result.diagnostics}", message)
    elif result.status == FlowStepStatus.OBSERVE:
        observe_messages = {
            "opponent_turn": "等待对手操作。",
            "waiting_recommendation_update": "等待盒子更新推荐。",
            "stale_mulligan_recommendation": "等待盒子刷新对局推荐。",
        }
        message = observe_messages.get(
            result.diagnostics,
            f"自动流程观察中：{result.diagnostics}")
        _report_automation_diagnostic(
            f"observe:{result.diagnostics}", message)
    else:
        # 成功执行/观察都算“本条链路是通的”，清空连续失败计数。
        _ocr_fail_streak = 0
        last_automation_diagnostic = None
    return None


def Battling():
    global win_count, game_count

    print_out()
    while True:
        if quitting_flag:
            sys.exit(0)
        # 对局中每轮都做存活检测：炉石闪退/卡死时这里会自动停止，
        # 不会再对着失效画面一直重试 OCR。
        check_hearthstone_liveness()
        if quitting_flag:
            sys.exit(0)
        next_state = run_automatic_battle_step()
        if next_state == FSM_QUITTING_BATTLE:
            # 对局真正结束才计数：game_count=已完成场数，win_count=胜场。
            game_count += 1
            if log_state.my_entity.query_tag("PLAYSTATE") == "WON":
                win_count += 1
                info_print("你赢得了这场对战")
            else:
                info_print("你输了")
            return next_state
        if next_state == FSM_ERROR:
            return next_state
        time.sleep(0.2)


def QuittingBattle():
    """对局结束收尾：点掉结算界面 → 等「开始」→ 点「开始」。

    结算界面（胜负横幅 / 段位升降级动画 / 奖励面板）要点好几下才会出现底部
    「开始」按钮。顺序是用户定的：

    1. 每轮点**中间那两个点**（`click_post_game_point()` → `(1100,820)` +
       `(960,650)`，原来就是用来应对错误弹窗/断线提示的位置）把结算界面推过去；
    2. 每轮用 OCR 检测「开始」按钮（读的是可校准的 post_game_start_roi，
       读不到再用兜底区域）——**没检测到就继续点，不做别的**；
    3. 检测到「开始」就点它（`click_post_game_start()`，按钮正中心）收尾；
    4. 返回 FSM_CHOOSING_HERO 继续下一局。

    段位读取**只在选择套牌界面做**（用户 2026-10-11 口径）：结算界面的名次
    画得晚、又容易被胜负动画干扰，读它既不准也没必要——回到选卡组界面时
    右上角就是当前名次，那里的读数才是权威。
    """
    print_out()

    time.sleep(5)

    cycle = 0
    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        state = get_screen.get_state()
        if state in [FSM_CHOOSING_HERO, FSM_LEAVE_HS]:
            return state

        if start_button_present():
            info_print("检测到结算界面「开始」按钮：点掉它，回到选卡组界面。")
            # 只点「开始」按钮正中心（坐标可以在校准窗口里拖）；段位留到
            # 选卡组界面再读，这里不做任何停止/投降判断。
            click.run_hearthstone_action(click_post_game_start)
            info_print("结算界面已出现「开始」按钮，收尾完成。")
            return FSM_CHOOSING_HERO

        click_post_game_point()
        cycle += 1

        if cycle >= _POST_GAME_MAX_CYCLES:
            warn_print(
                f"结算界面超过 {_POST_GAME_MAX_CYCLES} 轮仍未出现「开始」按钮，"
                "进入错误处理。")
            return FSM_ERROR

        time.sleep(_POST_GAME_CLICK_WAIT + random.random())


def GoBackHSAction():
    global FSM_state

    print_out()
    time.sleep(3)

    while not get_screen.test_hs_available():
        if quitting_flag or stop_after_current_game:
            sys.exit(0)
        click.enter_HS()
        time.sleep(10)

    # 有时候炉石进程会直接重写Power.log, 这时应该重新创建文件操作句柄
    init()

    return FSM_WAIT_MAIN_MENU


def MainMenuAction():
    """主界面 → 认出「传统对战」→ 点它 → 等到「开始」（= 已进选择套牌界面）。

    识别交给 battle_entry：**认不出来只点画面中心**（关掉挡住弹窗/公告），到超时
    就走错误处理 —— 不再像以前那样每 5 秒盲点一次「传统对战」（那会在别的界面上
    乱点）。点「开始」仍然由 ChoosingHeroAction 负责，因为**段位目标**那道闸门
    必须在点「开始」之前跑，顺序反了就会在到了目标段位后照样排队。
    """
    print_out()

    # 只留一帧的 UI 稳定时间：能走到这里说明 _initial_fsm_state 已经认出主界面，
    # enter_battle 自己会「识别 → 点中心 → 再识别」，原来固定等 3 秒纯属白等
    # （用户反馈：点开始后鼠标要等好一会才动）。
    time.sleep(0.2)

    import battle_entry
    result = battle_entry.enter_battle(
        click_start=False,
        should_abort=lambda: bool(quitting_flag or stop_after_current_game),
        logger=lambda text: sys_print(f"[SYS] {text}"))
    if result.get("ok"):
        _start_milestone("已点「传统对战」并认出选套牌界面")
        return FSM_CHOOSING_HERO
    if result.get("stage") == "aborted":
        sys.exit(0)

    # 进不去：仍然按老逻辑看一眼"是不是其实已经在对局里"（断线重连之类）。
    warn_print(f"进入对战未完成：{result.get('message')}")
    state = get_screen.get_state()
    if state == FSM_BATTLING:
        ok = update_log_state()
        if ok and log_state.available:
            return FSM_BATTLING
    if state == FSM_CHOOSING_HERO:
        return FSM_CHOOSING_HERO
    return FSM_ERROR


def WaitMainMenu():
    """等到主界面（认出「传统对战」）为止；认不出只点画面中心，绝不盲点按钮。

    进入本状态时炉石通常刚被拉起来（画面还在加载），所以这里等的是"主界面画出来
    了没有"，而不是"点得够不够多"。
    """
    print_out()

    import battle_entry
    result = battle_entry.wait_for_main_menu(
        should_abort=lambda: bool(quitting_flag or stop_after_current_game),
        logger=lambda text: sys_print(f"[SYS] {text}"))
    if result.get("ok"):
        return FSM_MAIN_MENU
    warn_print(f"等待主界面未完成：{result.get('message')}")
    # 等不到也继续走主界面流程：那一步自己会再识别一次，认不出来只会点中心。
    return FSM_MAIN_MENU


def HandleErrorAction():
    print_out()

    if not get_screen.test_hs_available():
        return FSM_LEAVE_HS
    manual_controller.output("状态暂不可确认，等待后重新检测。")
    time.sleep(STATE_CHECK_INTERVAL)
    state = get_screen.get_state()
    known_states = {
        FSM_LEAVE_HS, FSM_MAIN_MENU, FSM_CHOOSING_HERO, FSM_MATCHING,
        FSM_CHOOSING_CARD, FSM_BATTLING, FSM_QUITTING_BATTLE,
        FSM_WAIT_MAIN_MENU,
    }
    return state if state in known_states else FSM_ERROR


def FSM_dispatch(next_state):
    dispatch_dict = {
        FSM_LEAVE_HS: GoBackHSAction,
        FSM_MAIN_MENU: MainMenuAction,
        FSM_CHOOSING_HERO: ChoosingHeroAction,
        FSM_MATCHING: MatchingAction,
        FSM_CHOOSING_CARD: ChoosingCardAction,
        FSM_BATTLING: Battling,
        FSM_ERROR: HandleErrorAction,
        FSM_QUITTING_BATTLE: QuittingBattle,
        FSM_WAIT_MAIN_MENU: WaitMainMenu,
    }

    debug_print(f"当前状态为：+{next_state}")
    if next_state not in dispatch_dict:
        error_print("Unknown state!")
        return FSM_ERROR
    else:
        return dispatch_dict[next_state]()


def _initial_fsm_state():
    """启动/恢复时判断当前所处阶段，用于“立即接管”。

    屏幕像素(get_screen.get_state)在 BATTLING 时常不可靠，可能把对局误判成
    主菜单，导致恢复后要等下一回合才进入战斗。改用 Power.log 兜底：
    对局中(含对方回合)直接进入 Battling，换牌期进入 ChoosingCard；
    只有未对局时才退回屏幕检测。
    log_iter_func 每次开新 Power.log 会从头读到 EOF 一次性产出，
    因此一次 update_log_state() 即可把 log_state 快进到当前最新。
    """
    try:
        update_log_state()
    except Exception:
        pass
    if log_state.game_entity_id != 0 and not log_state.is_end:
        if log_state.game_num_turns_in_play > 0:
            _start_milestone("初始状态=对战中（Power.log）")
            return FSM_BATTLING
        _start_milestone("初始状态=换牌（Power.log）")
        return FSM_CHOOSING_CARD
    state = get_screen.get_state()
    _start_milestone(f"初始状态={state or FSM_MAIN_MENU}（屏幕识别）")
    return state if state else FSM_MAIN_MENU


def AutoHS_automata():
    global FSM_state, quitting_flag

    _reset_start_milestones()

    if get_screen.test_hs_available():
        hs_hwnd = get_screen.get_HS_hwnd()
        get_screen.move_window_foreground(hs_hwnd)
        time.sleep(0.5+random.random())

    # 出现这些状态时对局一定不在进行中，满足“打完本局再停止”的条件
    between_game_states = (
        FSM_MAIN_MENU, FSM_CHOOSING_HERO, FSM_MATCHING,
        FSM_WAIT_MAIN_MENU, FSM_LEAVE_HS, "",
    )

    while 1:
        if quitting_flag:
            sys.exit(0)
        # 每轮状态机分派前做一次存活检测（对局中还会检查 Power.log 是否停滞）：
        # 炉石进程消失/卡死时自动停止并醒目告警，不再空转到天亮。
        check_hearthstone_liveness()
        if quitting_flag:
            sys.exit(0)
        if stop_after_current_game and FSM_state in between_game_states:
            if _rank_stop_stop_reason:
                info_print(f"自动化停止：{_rank_stop_stop_reason}")
            else:
                info_print("已到计划停止时间，本局对战已经结束，自动化停止。")
            quitting_flag = True
            shutdown_event.set()
            sys.exit(0)
        if FSM_state == "":
            FSM_state = _initial_fsm_state()
        FSM_state = FSM_dispatch(FSM_state)





if __name__ == "__main__":
    keyboard.add_hotkey("ctrl+q", system_exit)

    init()
