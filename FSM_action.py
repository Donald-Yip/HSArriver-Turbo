import _thread
import random
import re
import sys
import threading
import time

import keyboard

import click
import get_screen
from config import (
    DEFAULT_AUTO_CONCEDE, SNAPSHOT_WRITE_INTERVAL, human_like_settings,
    rank_stop_settings,
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
# ---------------------------------------------------------------- 上分停止条件
# 每局对战结束后读结算界面的段位数字，命中用户设定的目标就把「本局结束后停止」
# 置位（见 _check_rank_stop / DEFAULT_RANK_STOP）。
#   _rank_stop_triggered      : 本局已命中并已请求停止（每局由 reset_game_session 复位）。
#   _rank_stop_stop_reason    : 给网页横幅显示的中文停止原因。
_rank_stop_triggered = False
_rank_stop_stop_reason = None
# 最近一次段位读数（供 Web / 浮窗显示，None = 还没读过）与它发生在哪一步
# （"点「开始」前" / "点「开始」后" / "选择套牌界面"）——页面据此说明"到底检测了没有"。
_rank_last_read = None
_rank_last_phase = None


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
    global recommendation_config, recommendation_capture
    global recommendation_parser, recommendation_reader
    global mulligan_reader, recommendation_validator

    # 轻量：每次都重建，以便拾取校准后的最新 ROI / 尺寸配置。
    recommendation_config = RecommendationConfig()
    recommendation_capture = DesktopCapture(recommendation_config)

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
            lambda: recommendation_capture.capture(ocr_panel_ok=True),
            recommendation_capture.crop_recommendation)
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
    # 新一局重新允许“上分停止”检测：已触发的停止不再撤销，但本局标志要复位，
    # 否则下一局打完不会再检查段位。最近读数保留，方便网页继续显示。
    _rank_stop_triggered = False
    _rank_stop_stop_reason = None
    # 新一局重新校验昵称（换号提示按局给一次）。
    _name_match_result = None
    # 新一局重新建立“对手随从基线”，第一份快照只记录不悬停。
    _oppo_minion_ids = None
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
    # 上分停止条件：新一轮自动化从头开始（最近读数也清掉，避免显示上一次运行的旧值）。
    _rank_stop_triggered = False
    _rank_stop_stop_reason = None
    _rank_last_read = None
    # 存活检测按“本轮自动化”重新开始计数：本轮没见过的炉石进程不算“消失”，
    # 否则“启动脚本 → 脚本拉起炉石”的正常流程会被误判成闪退。
    try:
        hearthstone_liveness.reset()
    except Exception:
        pass
    shutdown_event.clear()
    initialize_recommendation_automation()
    click.center_mouse()


def update_log_state():
    global active_game_generation
    global _last_snapshot_write
    log_container = next(log_iter)
    if log_container.log_type == LOG_CONTAINER_ERROR:
        return False

    previous_revision = log_state.revision
    for log_line_container in log_container.message_list:
        ok = update_state(log_state, log_line_container)
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

    time.sleep(2)
    # 排队前最后一道闸（用户口径「上传说就停止」）：这一段屏幕（选择套牌/选人）
    # 右上角就是当前段位名次，读到了就**不点「开始匹配」**，把自动化停在本局之后。
    # 实测漏过：打完一局后画面自己回到这个界面，结算里的段位检测没跑到，
    # 脚本照样排队进了下一局。
    try:
        if _check_rank_stop(
                attempts=_CHOOSING_HERO_RANK_READ_ATTEMPTS,
                wait=_CHOOSING_HERO_RANK_RETRY_WAIT,
                label="选择套牌界面",
                min_confidence=_POST_GAME_MIN_CONFIDENCE,
                confirm=True):
            info_print(
                "已到上分停止条件：不点「开始匹配」，本局结束后停止"
                "（要继续打请在页面关掉「🏁 上分停止条件」）。")
            return FSM_CHOOSING_HERO
    except Exception as exc:
        warn_print(f"选择套牌界面段位检测失败（忽略，继续匹配）：{exc}")
    click.run_hearthstone_action(click.match_opponent)
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
    """
    try:
        event = hearthstone_liveness.check(
            in_game=FSM_state in _LIVENESS_IN_GAME_STATES,
            detail=_liveness_detail())
    except Exception:
        return None
    if event is None:
        return None
    if event.get("fatal"):
        _alert_hearthstone_gone(event["message"])
    else:
        try:
            manual_controller.output(f"[SYS] {event['message']}")
        except Exception:
            pass
    return event


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
#   * _POST_GAME_RANK_ROI         ：段位数字（没上传说时这里是空白）。
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
_CHOOSING_HERO_RANK_READ_ATTEMPTS = 4
_CHOOSING_HERO_RANK_RETRY_WAIT = 1.2
# 「开始」按钮的 OCR 关键词与最低置信度（繁体/英文一并兜住）。
_POST_GAME_START_KEYWORDS = ("开始", "開始", "Play")
_POST_GAME_MIN_CONFIDENCE = 0.5
# 段位区域「是否空白」判定：区域内足够亮的像素少于这么多，就当没有数字。
# 未上传说时该处是纯背景，直接跳过 OCR（省一次推理，也避免空图被误识别成数字）。
_RANK_MIN_BRIGHT_PIXELS = 8
_RANK_BRIGHT_THRESHOLD = 150.0
# 全角数字/空格 → 半角（OCR 有时会把全角数字读出来）。
_FULLWIDTH_DIGITS = str.maketrans(
    "０１２３４５６７８９　", "0123456789 ")


def _config_roi(key: str, default) -> tuple:
    """读用户校准过的区域（ui_config.json 顶层 [left,top,right,bottom]）。

    非法/缺失时回退 default，保证自动化永远有一个可用的框。
    """
    try:
        box = getattr(recommendation_config, key, None)
        if box is not None:
            left, top, right, bottom = (int(v) for v in box)
            if 0 <= left < right and 0 <= top < bottom:
                return (left, top, right, bottom)
    except Exception:
        pass
    return tuple(int(v) for v in default)


def _grab_region(box, scale: float = 1.0):
    """截取屏幕区域并返回放大后的 BGR ndarray；依赖缺失/截图失败返回 None。"""
    try:
        import cv2
        import numpy as np
        from PIL import ImageGrab
    except Exception:
        return None
    try:
        rgb = np.asarray(ImageGrab.grab(bbox=tuple(int(v) for v in box),
                                        all_screens=False))
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if scale and scale != 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_CUBIC)
        return img
    except Exception:
        return None


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
    img = _grab_region(_POST_GAME_RANK_ROI, _POST_GAME_OCR_SCALE)
    blank = _region_is_blank(img)
    result["blank"] = blank
    result["bright"] = _rank_bright_pixels(img)
    if blank:
        # 截图失败也算空，但要标成“还没读到”（blank=True），由调用方重试；
        # 真正截到图且亮像素太少时只是 empty，同样交给调用方决定重试几次。
        result["empty"] = img is not None
        return result
    lines = _ocr_lines(_POST_GAME_RANK_ROI, _POST_GAME_OCR_SCALE, "rank")
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


def _rank_condition_met(reading, cfg) -> bool:
    """读数是否命中用户设定的上分目标（见 config.DEFAULT_RANK_STOP）。

    mode="legend"        ：上传说即命中；
    mode="legend_number" ：上了传说且名次 <= 目标（第 N 名或更好）。
    """
    if not reading or not cfg.get("enabled"):
        return False
    if not reading.get("legend"):
        return False
    if cfg.get("mode") != "legend_number":
        return True
    number = reading.get("number")
    try:
        target = int(cfg.get("legend_number", 1))
    except (TypeError, ValueError):
        target = 1
    return isinstance(number, int) and number <= target


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


def _check_rank_stop(attempts=None, wait=None, label="结算界面",
                     strict=False, min_confidence=0.0, confirm=False) -> bool:
    """读一次段位；命中用户设定的上分目标就请求「本局结束后停止」。

    label        ：这一步叫什么（写进日志与页面，回答"到底检测了没有、在哪一步读的"）；
    attempts/wait：重试次数与间隔（空白/截图失败/OCR 没读到都算"还没读到"，
                   会重试——这是实测踩过的坑：动画还没把名次画出来时第一次读
                   会得出"未上传说"的错误结论）；
    strict       ：只认「整行就是数字」（见 is_pure_rank_text）；
    min_confidence：丢掉低于该置信度的 OCR 行；
    confirm      ：读到数字后隔 _POST_GAME_RANK_CONFIRM_WAIT 再读一次，两次都
                   读到数字才算命中（防止把过渡画面里的误识别当名次）。
    返回值只表示“是否命中”。
    """
    global _rank_stop_triggered, _rank_stop_stop_reason, _rank_last_read
    global _rank_last_phase
    if _rank_stop_triggered:
        return False
    cfg = rank_stop_settings()
    if not cfg.get("enabled"):
        return False
    total = _POST_GAME_RANK_READ_ATTEMPTS if attempts is None else int(attempts)
    interval = _POST_GAME_RANK_RETRY_WAIT if wait is None else float(wait)
    reading = _read_rank_with(total, interval, strict, min_confidence)
    if confirm and reading.get("number") is not None:
        time.sleep(max(interval, _POST_GAME_RANK_CONFIRM_WAIT))
        again = read_rank_region(strict=strict,
                                 min_confidence=min_confidence)
        if again.get("number") is None:
            # 复核失败：当作"还没读到"，绝不因为一次误识别就停掉自动化。
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
            f"[SYS] 上分停止检测（{label}）：{reason} —— 按「还没上传说」处理。")
        return False
    if not _rank_condition_met(reading, cfg):
        manual_controller.output(
            f"[SYS] 上分停止检测（{label}）：当前段位读数「{reading['raw']}」，"
            f"未达到停止条件。")
        return False
    detail = f"传说 {number} 名"
    _rank_stop_triggered = True
    _rank_stop_stop_reason = f"已上传说到{detail}，自动化停止（本局结束后生效）。"
    manual_controller.output(
        f"[SYS] 上分目标已达成（{label}）：{_rank_stop_stop_reason}")
    request_stop_after_game()
    return True


def rank_stop_detection_state() -> dict:
    """上分停止条件的当前状态（供 Web 控制台 / 浮窗显示）。"""
    cfg = rank_stop_settings()
    reading = _rank_last_read or {}
    return {
        "enabled": bool(cfg["enabled"]),
        "mode": cfg["mode"],
        "legend_number": int(cfg["legend_number"]),
        "last_number": reading.get("number"),
        "last_legend": reading.get("legend"),
        "last_raw": reading.get("raw"),
        "last_empty": bool(reading.get("empty")),
        "last_phase": _rank_last_phase,
        "triggered": bool(_rank_stop_triggered),
        "stop_reason": _rank_stop_stop_reason,
    }


def _post_game_start_box() -> tuple:
    """结算「开始」按钮的检测框：优先用用户校准的 post_game_start_roi。

    recommendation_config 每次对局都会重建，所以校准完重开一局即生效。
    配置缺失/非法时回退代码默认值（_POST_GAME_START_BUTTON_ROI）。
    """
    return _config_roi("post_game_start_roi", _POST_GAME_START_BUTTON_ROI)


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
        left, top, right, bottom = recommendation_config.mulligan_confirm_roi
        rgb = np.asarray(ImageGrab.grab(
            bbox=(left, top, right, bottom), all_screens=False))
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


def read_ai_win_rate():
    """OCR 左上角盒子“AI胜率 X%”，返回百分数值；读不到返回 None。

    浮动条字号很小，先放大再识别；主区域读不到时用放宽的兜底区域再试一次，
    避免因为浮动条位置/宽度略有差异而整回合读不到。
    """
    try:
        import cv2
        import numpy as np
        from PIL import ImageGrab
    except Exception:
        return None
    for index, box in enumerate(_ai_win_rate_regions()):
        try:
            rgb = np.asarray(ImageGrab.grab(bbox=box, all_screens=False))
            img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            img = cv2.resize(img, None, fx=_AI_WIN_RATE_SCALE,
                             fy=_AI_WIN_RATE_SCALE,
                             interpolation=cv2.INTER_CUBIC)
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


def _do_concede():
    """点击右下角齿轮 → 等菜单弹出 → 点中间红色“认输”。"""
    global concede_count
    concede_count += 1
    manual_controller.output("[SYS] 持续低胜率，开始自动认输……")
    try:
        with click.hearthstone_action_session():
            click.click_setting()      # 齿轮 (1895, 1060)
            time.sleep(1.0)            # 等游戏菜单弹出
            click.click_concede()      # 认输 (960, 380)
        time.sleep(1.0)
    except Exception as exc:
        manual_controller.output(f"[SYS] 自动认输点击失败：{exc}")


def run_automatic_battle_step():
    """Observe opponent turns; execute one newly validated player action."""
    global player_turn_delay_key, last_automation_diagnostic, _ocr_fail_streak

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
    """对局结束收尾：点掉结算界面 → 等「开始」→ 读段位 → 决定要不要点「开始」。

    结算界面（胜负横幅 / 段位升降级动画 / 奖励面板）要点好几下才会出现底部
    「开始」按钮。顺序是用户定的：

    1. 每轮点**中间那两个点**（`click_post_game_point()` → `(1100,820)` +
       `(960,650)`，原来就是用来应对错误弹窗/断线提示的位置）把结算界面推过去；
    2. 每轮用 OCR 检测「开始」按钮（读的是可校准的 post_game_start_roi，
       读不到再用兜底区域）——**没检测到就继续点，不做别的**；
    3. 检测到「开始」之后**先读段位**（`_check_rank_stop()`，label="点「开始」前"）：
       读到名次 → 命中停止条件就直接停，**连「开始」都不点**；没读到名次才收尾；
    4. 收尾只点**第三个点**：`click_post_game_start()`（「开始」按钮正中心），
       然后再读一次段位（label="点「开始」后"）兜住名次画得晚的情况；
    5. 没命中就返回 FSM_CHOOSING_HERO 继续下一局；命中则主循环干净退出。
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
            info_print("检测到结算界面「开始」按钮：先读段位，再决定要不要点「开始」。")

            # ① 用户口径：**检测到「开始」以后先检测段位**。读一次读不到就重试
            #    （名次有时画得晚），读到数字还要复核一次。命中就**不点**「开始」，
            #    本局已经结束，主循环会在非对局状态直接退出——绝不会替用户开下一局。
            hit = False
            try:
                hit = _check_rank_stop(
                    attempts=_POST_GAME_RANK_READ_ATTEMPTS, wait=0.8,
                    label="点「开始」前",
                    min_confidence=_POST_GAME_MIN_CONFIDENCE,
                    confirm=True)
            except Exception as exc:
                warn_print(f"点「开始」前段位检测失败（忽略，继续收尾）：{exc}")
            if hit:
                info_print(
                    "段位已到停止条件：不点「开始」，本局结束后直接退出自动化。")
                return FSM_CHOOSING_HERO

            # ② 没读到名次才收尾：**只点第三个点**——「开始」按钮正中心
            #    （坐标可以在校准窗口里拖）。前两个点（中间那两个）每轮已经点过了，
            #    这里不再点右边任何位置。
            click.run_hearthstone_action(click_post_game_start)
            info_print("结算界面已出现「开始」按钮，收尾完成。")

            # ③ 收尾后再读一次兜底：命中「上分停止条件」时它会请求「本局结束后停止」。
            try:
                _check_rank_stop(label="点「开始」后")
            except Exception as exc:
                warn_print(f"上分停止检测失败（忽略，继续下一局）：{exc}")

            # ④ 没命中停止就继续下一局；命中了主循环会在非对局状态立刻退出。
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
    print_out()

    time.sleep(3)

    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        click.run_hearthstone_action(click.enter_battle_mode)
        time.sleep(5)

        state = get_screen.get_state()

        # 重新连接对战之类的
        if state == FSM_BATTLING:
            ok = update_log_state()
            if ok and log_state.available:
                return FSM_BATTLING
        if state == FSM_CHOOSING_HERO:
            return FSM_CHOOSING_HERO


def WaitMainMenu():
    print_out()
    wait_main_menu_count = 0
    while get_screen.get_state() != FSM_MAIN_MENU:
        click.run_hearthstone_action(click.enter_battle_mode)
        time.sleep(5)
        wait_main_menu_count += 1
        if wait_main_menu_count >= 5:
            break
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
            return FSM_BATTLING
        return FSM_CHOOSING_CARD
    state = get_screen.get_state()
    return state if state else FSM_MAIN_MENU


def AutoHS_automata():
    global FSM_state, quitting_flag

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
