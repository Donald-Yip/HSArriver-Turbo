import win32gui
import win32api
import win32con
import pywintypes
import time
from pynput.mouse import Button, Controller
import random
from contextlib import contextmanager
# import get_screen

from constants.constants import *
from print_info import *
from get_screen import *
from get_screen import _bitblt_region
from config import DEFAULT_HUMAN_LIKE, human_like_settings
from display_mapping import to_screen as _to_screen
from display_mapping import display_mapping as _display_mapping


#测试不同分辨率的点击效果，因为屏幕截取未支持不同分辨率，失败了
#炉石传说坐标
# hwnd_left,hwnd_top, hwnd_right, hwnd_bottom = 0,0,0,0
# hwnd_width  = 0
# hwnd_height = 0

#获取炉石传说的分辨率
#得到不同分辨率下的点击位置
# def hs_size():
#     hwnd = get_HS_hwnd()
#     global hwnd_left 
#     global hwnd_top 
#     global hwnd_right 
#     global hwnd_bottom 
#     hwnd_left,hwnd_top,hwnd_right,hwnd_bottom = win32gui.GetWindowRect(hwnd)
#     # print(left)
#     # print(top)
#     # print(right)
#     # print(bottom)
#     global hwnd_width 
#     global hwnd_height
#     hwnd_width = hwnd_right - hwnd_left -16
#     hwnd_height = hwnd_bottom - hwnd_top -39
#     print(hwnd_width)
#     print(hwnd_height)
#     # 1440 900 = 1456 939
#     # 16 39
#     # 1400 1050 = 1416 1089 
#     # 16 39

# def position_x(x):
#     global hwnd_width 
#     global hwnd_left 
#     if hwnd_width == 0:
#      return x
#     return x * (hwnd_width / 1920) + 16 + hwnd_left 

# def position_y(y):
#     global hwnd_height
#     global hwnd_top 
#     if hwnd_height == 0:
#      return y
#     return y * (hwnd_height / 1920) + 39 + hwnd_top 



def rand_sleep(interval):
    base_time = interval * 0.75
    rand_time = interval * 0.5 * random.random()  # avg = 0.25 * interval
    time.sleep(base_time + rand_time)


# 鼠标复位点（只移动、不点击的待命位）：屏幕左上角空白处。
# 历史：最初 (480, 540) 每局结束后会停在牌组选择界面第二行第一个卡组上，
# 复位后偶发误选中该卡组；用户最终指定 (70, 60) 作为待命点。
MOUSE_RESET_POS = (70, 60)

# 右侧辅助点候选：激活后台炉石、取消右键都用它。
# 历史 bug：这两个点原来写死 (1800,500)/(1700,400)，而右上角日志浮窗
# （292×628，屏幕矩形约 x1616..1908 / y12..640）正好把它们盖住 —— 浮窗不穿透
# 鼠标，点击被浮窗吃掉，炉石没被激活，紧接着真正要点的那个左键又被 Unity 当
# 作“激活窗口”吞掉，动作静默失败。所以改成运行时用 WindowFromPoint 校验，
# 首选点不落在炉石窗口上就换下一个候选。
SIDE_POINT_CANDIDATES = ((1800, 500), (1700, 400), (1700, 760), (1500, 520),
                         (1200, 800))


def side_point(hearthstone_hwnd=0, preferred=None,
               candidates=SIDE_POINT_CANDIDATES):
    """挑一个真正落在炉石窗口上的辅助点（避开右上角日志浮窗等置顶窗口）。

    * 按「首选点 → 候选列表」顺序，用 point_targets_hearthstone（WindowFromPoint）
      验证该位置最上层窗口属于炉石；
    * 全部不命中（炉石最小化/被完全遮挡）或拿不到 hwnd 时，退回首选点，
      保持与改动前完全一致的行为，不会退化成“什么都不点”。
    """
    ordered = []
    if preferred is not None:
        ordered.append(tuple(preferred))
    for point in candidates:
        point = tuple(point)
        if point not in ordered:
            ordered.append(point)
    if not ordered:
        return (0, 0)
    if not hearthstone_hwnd:
        return ordered[0]
    for point in ordered:
        try:
            if point_targets_hearthstone(point[0], point[1], hearthstone_hwnd):
                return point
        except Exception:
            break
    return ordered[0]

# ---------------------------------------------------------------- 活人感（可选）
# 只在【对局中识别盒子意见并执行完之后】生效：由 RecommendationFlow / MulliganFlow
# 在动作执行成功后经 FSM_action._human_like_post_action_pause() 调用本函数，
# 用 0.5~3s 的随机“思考”延时代替固定延时，期间把手牌区当普通人类一样随手悬停
# （每处约 1s），最后仍复位到 MOUSE_RESET_POS。
# 攻击类动作不经过这里（出手后还看手牌最像脚本，而且攻击常常连着来）：
# FSM 层会直接返回 False，让流程走正常的固定「操作后延时」去读下一条推荐。
# 匹配对手、选卡组、错误弹窗取消这类非推荐动作也不会触发。
# 开关与参数在 Web 控制台的「活人感」卡片里设置（ui_config.json 的 human_like 段）。
HAND_HOVER_Y = 1000          # 手牌卡面所在高度（与 choose_card 用的 y 一致）
HAND_HOVER_SIZES = (3, 8)    # 手牌张数未知，按常见张数取卡位


def center_mouse(mouse=None):
    """Move the pointer to the neutral reset point without clicking.

    MOUSE_RESET_POS 是客户区相对坐标（基准 1920×1080），这里换算成屏幕坐标；
    炉石没开、拿不到客户区映射时原样使用（与改造前一致）。
    """
    if mouse is None:
        mouse = Controller()
    mouse.position = _to_screen(MOUSE_RESET_POS[0], MOUSE_RESET_POS[1],
                                _display_mapping())


def _random_hand_hover_point(last=None):
    """在“看起来像手牌卡面”的位置里随机取一点（对齐 HAND_CARD_X 的卡位）。

    只移动不点击，所以即使落点处没有牌也无害；尽量避开刚停过的那个点。
    """
    for _ in range(8):
        size = random.randint(*HAND_HOVER_SIZES)
        point = (random.choice(HAND_CARD_X[size]), HAND_HOVER_Y)
        if point != last:
            return point
    return (960, HAND_HOVER_Y)


def human_like_pause(mouse=None, settings=None):
    """活人感延时：0.5~3s 随机等待，期间鼠标在手牌区随机悬停。

    每处悬停时长同样随机（默认 0.2~1s，见 config.DEFAULT_HUMAN_LIKE）。
    全程只移动、绝不点击；结束前复位到 MOUSE_RESET_POS。返回本次总延时（秒）。
    """
    cfg = settings or human_like_settings()
    if mouse is None:
        mouse = Controller()
    total = random.uniform(cfg["post_delay_min"], cfg["post_delay_max"])
    # 这句同时驱动浮窗底部的延时进度条（log_overlay 解析“延时 X s 后”取总时长），
    # 措辞保持“延时 X.Xs 后”，剩下的文字会作为进度条下方的说明显示。
    sys_print(f"[SYS] 活人感 延时 {total:.1f}s 后（手牌区随机悬停等待）")
    deadline = time.monotonic() + total
    last = None
    mapping = _display_mapping()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        point = _random_hand_hover_point(last)
        # 悬停点同样是客户区相对坐标，落到屏幕上要先换算。
        point = _to_screen(point[0], point[1], mapping)
        mouse.position = point
        last = point
        hover = random.uniform(cfg["hover_min"], cfg["hover_max"])
        time.sleep(min(hover, remaining))
    center_mouse(mouse)
    # 与固定延时一致的收尾标记：让浮窗进度条立刻清空、显示“延时：无”。
    sys_print("[SYS] 延时结束")
    return total


OPPO_MINION_HOVER_Y = 400        # 对手随从卡面中心高度（与 choose_opponent_minion 一致）
OPPO_MINION_HOVER_JITTER = 25    # 落点随机偏移，避免每次都停在同一个像素上


def opponent_minion_hover_point(oppo_index, oppo_num, jitter=True):
    """对手第 oppo_index 个随从卡面上的一点（坐标口径同 choose_opponent_minion）。

    只用于「活人感」移动鼠标、绝不点击，所以落点略有偏差也无害；
    oppo_index/oppo_num 与 choose_opponent_minion 完全一致（按 zone_pos 排序后的序号）。
    """
    x = 960 - (oppo_num - 1) * 70 + oppo_index * 140
    y = OPPO_MINION_HOVER_Y
    if jitter:
        x += random.randint(-OPPO_MINION_HOVER_JITTER, OPPO_MINION_HOVER_JITTER)
        y += random.randint(-OPPO_MINION_HOVER_JITTER, OPPO_MINION_HOVER_JITTER)
    return (x, y)


def hover_opponent_minion(oppo_index, oppo_num, mouse=None, settings=None,
                          duration=None):
    """活人感：把鼠标移到对手新出现的随从上，随机悬停 0.2~1.5s（只移动、不点击）。

    悬停时长取自 ui_config.json 的 human_like.minion_hover_min/max；结束后仍复位到
    MOUSE_RESET_POS。日志措辞与其他延时一致：≥1s 的行只驱动浮窗底部进度条，
    <1s 的行留在正文日志里（见 log_overlay._update_delay_from_line）。
    返回本次悬停时长（秒）。
    """
    cfg = settings or human_like_settings()
    if mouse is None:
        mouse = Controller()
    if duration is None:
        duration = random.uniform(
            float(cfg.get("minion_hover_min", DEFAULT_HUMAN_LIKE["minion_hover_min"])),
            float(cfg.get("minion_hover_max", DEFAULT_HUMAN_LIKE["minion_hover_max"])))
    mouse.position = _to_screen(*opponent_minion_hover_point(oppo_index, oppo_num),
                                _display_mapping())
    sys_print(f"[SYS] 活人感 延时 {duration:.1f}s 后（悬停对手新随从）")
    time.sleep(max(0.0, duration))
    center_mouse(mouse)
    sys_print("[SYS] 延时结束")
    return duration


def park_mouse(mouse=None):
    """Finish an action: honor the minimum delay, then park without clicking.

    「活人感」的随机延时/手牌悬停**不在这里**：它只在对局中识别盒子意见并执行后
    由流程层调用（RecommendationFlow / MulliganFlow 的 post_action_pause），
    否则匹配对手、选卡组、错误弹窗取消这类非推荐动作也会被拖慢。
    """
    time.sleep(0.1)
    center_mouse(mouse)


@contextmanager
def hearthstone_action_session():
    """Park the pointer after every complete Hearthstone action."""
    try:
        yield
    finally:
        park_mouse()


def run_hearthstone_action(action):
    with hearthstone_action_session():
        return action()


def _send_physical_click(mouse, x, y, button):
    mouse.position = (x, y)
    rand_sleep(0.1)
    mouse.press(button)
    rand_sleep(0.1)
    mouse.release(button)


def click_button(x, y, button, require_hearthstone=True):
    """在**屏幕坐标** (x, y) 上按下/松开按钮。

    这是"屏幕坐标出口"：调用方传进来的必须已经是屏幕坐标。客户区相对坐标
    请走 left_click / right_click（它们负责换算），别在这里再换一次。
    """
    hearthstone_hwnd = get_HS_hwnd() if require_hearthstone else 0
    mouse = Controller()
    rand_sleep(0.1)
    if (require_hearthstone
            and hearthstone_hwnd != 0
            and win32gui.GetForegroundWindow() != hearthstone_hwnd):
        # The first physical click on a background Unity window can be
        # consumed solely to activate it. Activate via the same harmless
        # right-side point used by cancel_click, then send the real action.
        # 该点必须真的落在炉石上：右上角日志浮窗会盖住固定坐标（见 side_point）。
        activate_x, activate_y = side_point(hearthstone_hwnd,
                                            preferred=(1800, 500))
        _send_physical_click(
            mouse, activate_x, activate_y, Button.right)
        rand_sleep(0.25)
    _send_physical_click(mouse, x, y, button)


def left_click(x, y, require_hearthstone=True, mapping=None):
    """点客户区相对坐标 (x, y)（基准 1920×1080）。

    抖动在**客户区相对坐标**里做：±2px 的拟人幅度不随窗口缩放变化。
    换算集中在 click_button（屏幕坐标出口），所以 click_button 的调用方
    传的必须是屏幕坐标。mapping 供测试注入，默认取实时客户区映射。
    """
    x += random.randint(-2, 3)
    y += random.randint(-2, 3)
    x, y = _to_screen(x, y, mapping if mapping is not None else _display_mapping())
    click_button(x, y, Button.left, require_hearthstone)


def right_click(x, y, require_hearthstone=True, mapping=None):
    x, y = _to_screen(x, y, mapping if mapping is not None else _display_mapping())
    click_button(x, y, Button.right, require_hearthstone)


def choose_my_board_entity(entity_index, entity_num):
    rand_sleep(OPERATE_INTERVAL)
    x = 960 - (entity_num - 1) * 70 + entity_index * 140
    y = 600
    left_click(x, y)


def choose_my_minion(mine_index, mine_num):
    choose_my_board_entity(mine_index, mine_num)


def choose_my_hero():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 850)


def choose_opponent_minion(oppo_index, oppo_num):
    rand_sleep(OPERATE_INTERVAL)
    x = 960 - (oppo_num - 1) * 70 + oppo_index * 140
    y = 400
    left_click(x, y)


def choose_oppo_hero():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 200)


def cancel_click():
    rand_sleep(TINY_OPERATE_INTERVAL)
    right_click(*side_point(get_HS_hwnd(), preferred=(1700, 400)))


def test_click():
    rand_sleep(TINY_OPERATE_INTERVAL)
    left_click(*side_point(get_HS_hwnd(), preferred=(1700, 400)))


HAND_CARD_X = [
    [],  # 0
    [885],  # 1
    [820, 980],  # 2
    [750, 890, 1040],  # 3
    [690, 820, 970, 1130],  # 4
    [680, 780, 890, 1010, 1130],  # 5
    [660, 750, 840, 930, 1020, 1110],  # 6
    [660, 733, 810, 885, 965, 1040, 1120],  # 7
    [650, 720, 785, 855, 925, 995, 1060, 1130],  # 8
    [650, 710, 765, 825, 880, 950, 1010, 1070, 1140],  # 9
    [647, 700, 750, 800, 860, 910, 970, 1020, 1070, 1120]  # 10
]


def choose_card(card_index, card_num):
    rand_sleep(OPERATE_INTERVAL)

    assert 0 <= card_index < card_num <= 10
    # x = START[card_num] + 65 + STEP[card_num] * card_index
    x = HAND_CARD_X[card_num][card_index]

    y = 1000
    left_click(x, y)


DISCOVER_CARD_X = {
    1: (960,),
    2: (760, 1160),
    3: (560, 960, 1360),
    # Four-option menus (e.g. BG31_BOB and CATA_190h), at 1920x1080.
    4: (400, 775, 1150, 1525),
}


def choose_discover_card(choice_index, choice_count):
    """Click a centered 1920x1080 discover choice for its layout size."""
    assert choice_count in DISCOVER_CARD_X
    positions = DISCOVER_CARD_X[choice_count]
    assert 0 <= choice_index < len(positions)
    rand_sleep(OPERATE_INTERVAL)
    y = 540 if choice_count == 1 else 500
    left_click(positions[choice_index], y)


STARTING_CARD_X = {
    3: [600, 960, 1320],
    5: [600, 850, 1100, 1350],
}


def replace_starting_card(card_index, hand_card_num):
    assert hand_card_num in STARTING_CARD_X
    assert card_index < len(STARTING_CARD_X[hand_card_num])

    rand_sleep(OPERATE_INTERVAL)
    left_click(STARTING_CARD_X[hand_card_num][card_index], 500)


def click_middle():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 500)


def click_setting():
    rand_sleep(OPERATE_INTERVAL)
    left_click(1895, 1060)


def click_concede():
    """点击游戏菜单中央的红色“认输”按钮（1920x1080 实测坐标）。"""
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 380)


def choose_and_use_spell(card_index, card_num):
    choose_card(card_index, card_num)
    click_middle()


def drag_card_to_board_entity(card_index, card_num, entity_index, entity_num):
    """Drag a hand card just left of an existing friendly board entity."""
    assert 0 <= card_index < card_num <= 10
    assert 0 <= entity_index < entity_num <= 7

    mouse = Controller()
    mapping = _display_mapping()
    mouse.position = _to_screen(HAND_CARD_X[card_num][card_index], 1000, mapping)
    rand_sleep(0.1)
    mouse.press(Button.left)
    try:
        board_x = 960 - (entity_num - 1) * 70 + entity_index * 140
        mouse.position = _to_screen(board_x - 25, 600, mapping)
        rand_sleep(0.1)
    finally:
        mouse.release(Button.left)


# 第[i]个随从左边那个空隙记为第[i]个gap
def put_minion(gap_index, minion_num):
    rand_sleep(OPERATE_INTERVAL)

    if minion_num >= 7:
        warn_print(f"Try to put a minion but there has already been {minion_num} minions")

    x = 960 - (minion_num - 1) * 70 + 140 * gap_index - 70
    y = 600
    left_click(x, y)


def match_opponent():
    # 一些奇怪的错误提示
    commit_error_report()
    rand_sleep(OPERATE_INTERVAL)
    left_click(1400, 900)


def enter_battle_mode():
    # 一些奇怪的错误提示
    commit_error_report()
    rand_sleep(OPERATE_INTERVAL)
    left_click(950, 320)


def commit_choose_card():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 850)


def end_turn():
    rand_sleep(OPERATE_INTERVAL)
    left_click(1550, 500)


# HSAng 左下「时间线」提示按钮中心（1920x1080 实测，见用户截图）：
#   回溯(撤销) ≈ (350, 805)   维持(保留) ≈ (585, 805)
TIMELINE_UNDO_POS = (350, 805)
TIMELINE_KEEP_POS = (585, 805)

# 时间线的两个选项就是这两张「卡」：盒子的推荐可能只写「选择我方N号位卡牌」，
# 没有回溯/维持文字，所以按 Power.log 里的 cardId 认按钮。
TIMELINE_OPTION_POS = {
    "TIME_000tb": TIMELINE_UNDO_POS,   # 回溯
    "TIME_000ta": TIMELINE_KEEP_POS,   # 维持
}


def choose_timeline(card_id):
    """点时间线按钮（回溯 TIME_000tb / 维持 TIME_000ta）。

    坐标是**客户区相对**的；换算统一交给 left_click → click_button，避免
    这里再手工 GetClientRect/ClientToScreen 换算一次（重复实现会漏掉偏移）。
    """
    if card_id not in TIMELINE_OPTION_POS:
        raise ValueError("Unknown timeline option")
    if not get_HS_hwnd():
        raise ValueError("Hearthstone window unavailable")
    rand_sleep(OPERATE_INTERVAL)
    left_click(*TIMELINE_OPTION_POS[card_id])


def click_timeline_undo():
    """点 HSAng 左下「回溯」：撤销时间线里上一步操作。"""
    choose_timeline("TIME_000tb")


def click_timeline_keep():
    """点 HSAng 左下「维持」：保留当前操作、关掉时间线提示。"""
    choose_timeline("TIME_000ta")


# 星舰「发射」按钮：展开的组件面板右侧（Cancel 旁边）。参考截图是 1920x1080
# 客户区，按钮中心 ≈ (1070, 920)——属于客户区相对坐标，换算由 left_click 统一
# 处理（含窗口偏移与客户区缩放）。
STARSHIP_LAUNCH_REF_POS = (1070, 920)


def click_launch_starship():
    """点开组件面板里的「发射」按钮。

    找不到炉石窗口时直接报错，不做盲目点击。
    """
    if not get_HS_hwnd():
        raise ValueError("Hearthstone window unavailable")
    rand_sleep(OPERATE_INTERVAL)
    left_click(*STARSHIP_LAUNCH_REF_POS)


def drag_card_to_deck():
    """Drag the selected hand card to the friendly deck.

    到达牌库后必须按够 DECK_DROP_HOLD_INTERVAL 再松手：交易/锻造/预备都靠
    “牌库悬停高亮就绪→松开”触发，松太快会概率性失败(卡牌又弹回手牌)。
    """
    mouse = Controller()
    mouse.press(Button.left)
    try:
        rand_sleep(0.1)
        # 我方牌库中心点（客户区相对坐标，1920x1080 实测：1635,640）。
        mouse.position = _to_screen(1635, 640, _display_mapping())
        rand_sleep(DECK_DROP_HOLD_INTERVAL)
    finally:
        mouse.release(Button.left)


# 屏幕中间那两个「点掉遮挡」的位置：
#   (1100, 820) —— 一些奇怪的错误提示；
#   (960, 650)  —— 已断线时点这里取消。
# 每局结束推结算界面也复用这两下（用户口径：回合结束点中间这两个点，**不点右边**
# ——右边那些辅助点会被右上角日志浮窗吃掉/会误点到下一屏）。
ERROR_REPORT_POINTS = ((1100, 820), (960, 650))


def commit_error_report():
    for x, y in ERROR_REPORT_POINTS:
        left_click(x, y)


def emoj(target=None):
    emoj_list = [(800, 880), (800, 780), (800, 680), (1150, 680), (1150, 780)]
    right_click(960, 830)
    rand_sleep(OPERATE_INTERVAL)

    if target is None:
        x, y = emoj_list[random.randint(1, 4)]
    else:
        x, y = emoj_list[target]
    left_click(x, y)
    rand_sleep(OPERATE_INTERVAL)


def click_skill():
    rand_sleep(OPERATE_INTERVAL)
    left_click(1150, 850)


def use_skill_no_point():
    click_skill()
    cancel_click()


def use_skill_point_mine(my_index, my_num):
    click_skill()

    if my_index < 0:
        choose_my_hero()
    else:
        choose_my_minion(my_index, my_num)

    cancel_click()


def minion_beat_minion(mine_index, mine_number, oppo_index, oppo_num):
    _choose_my_attacker(mine_index, mine_number)
    _choose_attack_target(oppo_index, oppo_num)
    _finish_attack()


def minion_beat_hero(mine_index, mine_number):
    _choose_my_attacker(mine_index, mine_number)
    _choose_attack_target_hero()
    _finish_attack()


def hero_beat_minion(oppo_index, oppo_num):
    _choose_my_hero_attacker()
    _choose_attack_target(oppo_index, oppo_num)
    _finish_attack()


def hero_beat_hero():
    _choose_my_hero_attacker()
    _choose_attack_target_hero()
    _finish_attack()


def _choose_my_attacker(mine_index, mine_number):
    time.sleep(0.05)
    x = 960 - (mine_number - 1) * 70 + mine_index * 140
    left_click(x, 600)


def _choose_my_hero_attacker():
    time.sleep(0.05)
    left_click(960, 850)


def _choose_attack_target(oppo_index, oppo_num):
    time.sleep(0.05)
    x = 960 - (oppo_num - 1) * 70 + oppo_index * 140
    left_click(x, 400)


def _choose_attack_target_hero():
    time.sleep(0.05)
    left_click(960, 200)


def _finish_attack():
    time.sleep(0.05)
    right_click(*side_point(get_HS_hwnd(), preferred=(1700, 400)))


# 战网「开始游戏」按钮：位置随战网窗口大小/DPI 变化（用户随手一拖窗口，
# 写死的偏移就对不上了），所以优先在窗口左下角扫 Blizzard 蓝播放键，取最大
# 色块的中心；扫不到（按钮还没画好、被别的窗口挡住、或游戏正在运行时的
# 灰色「正在游戏中」态）再退回这套历史偏移（上游项目一直用的值）。
BATTLENET_PLAY_FALLBACK_DX = 180
BATTLENET_PLAY_FALLBACK_DY = 110
# 扫描区：左下角。横向到 45% 宽就够（再往右是主内容/好友列，蓝色元素多，
# 容易扫错）；纵向从 70% 高度起（播放键在底部约 10% 处）。
BATTLENET_PLAY_ZONE_X = 0.45
BATTLENET_PLAY_ZONE_Y = 0.70
# 色块最小面积（px²）：播放键是实心大蓝块，小于这个的多半是图标/链接。
BATTLENET_PLAY_MIN_AREA = 1500


def _find_blue_play_button(left, top, right, bottom, grabber=None):
    """在战网窗口左下角找「开始游戏」蓝键，返回**屏幕坐标**中心。

    找不到返回 None（调用方负责退回历史偏移）。grabber 供测试注入。
    """
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    zone = (left, top + int(height * BATTLENET_PLAY_ZONE_Y),
            left + int(width * BATTLENET_PLAY_ZONE_X), bottom)
    frame = (grabber or _bitblt_region)(zone)
    if frame is None:
        return None
    try:
        import numpy
        import cv2
    except Exception:
        return None
    img = numpy.asarray(frame)[:, :, :3]
    if img.size == 0:
        return None
    blue, green, red = (img[:, :, i].astype(int) for i in range(3))
    mask = ((blue > 140) & (blue - red > 50) & (blue - green > 30))
    mask = mask.astype("uint8")
    if not mask.any():
        return None
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    best = int(areas.argmax()) + 1
    if int(stats[best, cv2.CC_STAT_AREA]) < BATTLENET_PLAY_MIN_AREA:
        return None
    cx = int(stats[best, cv2.CC_STAT_LEFT] + stats[best, cv2.CC_STAT_WIDTH] // 2)
    cy = int(stats[best, cv2.CC_STAT_TOP] + stats[best, cv2.CC_STAT_HEIGHT] // 2)
    return (zone[0] + cx, zone[1] + cy)


def battlenet_play_point(left, top, right, bottom, grabber=None):
    """战网「开始游戏」的点击点（屏幕坐标）：优先蓝键中心，扫不到用历史偏移。"""
    try:
        found = _find_blue_play_button(left, top, right, bottom, grabber=grabber)
    except Exception:
        found = None
    if found is not None:
        return found
    return (left + BATTLENET_PLAY_FALLBACK_DX,
            bottom - BATTLENET_PLAY_FALLBACK_DY)


def _point_targets_window(x, y, hwnd):
    """这个屏幕坐标的点击会不会真的落到 hwnd 上（WindowFromPoint 会自动跳过
    点击穿透窗，比如炉石盒子的浮窗）。判不了时返回 True——照点，不拦。"""
    try:
        hit = win32gui.WindowFromPoint((int(x), int(y)))
        if not hit:
            return True
        return win32gui.GetAncestor(hit, win32con.GA_ROOT) == int(hwnd)
    except Exception:
        return True


def enter_HS():
    rand_sleep(1)

    if test_hs_available():
        move_window_foreground(get_HS_hwnd(), "炉石传说")
        return

    battlenet_hwnd = get_battlenet_hwnd()

    if battlenet_hwnd == 0:
        error_print("未找到应用战网")
        return False

    move_window_foreground(battlenet_hwnd, "战网")

    rand_sleep(1)

    left, top, right, bottom = win32gui.GetWindowRect(battlenet_hwnd)
    # GetWindowRect 给的是**屏幕坐标**，必须走 click_button（屏幕坐标出口）。
    # 早年走的是 left_click，坐标被 to_screen 按炉石客户区又换算一遍：炉石
    # 窗口不在屏幕原点时（窗口化/多屏）点击点整体偏移，落到战网窗外——按钮
    # 「点不到」就是这么来的（实机：想点 (1406,1614)，实际点 (2313,2089)）。
    x, y = battlenet_play_point(left, top, right, bottom)
    if not _point_targets_window(x, y, battlenet_hwnd):
        warn_print(f"战网「开始游戏」按钮位置 ({x}, {y}) 被其它窗口盖住，"
                   "这一下可能点空")
    click_button(x, y, Button.left, require_hearthstone=False)
    return True


# 「模式选择」菜单（选卡组界面）。2026-10 实机截图实测，1920x1080 客户区基准：
#   打开菜单按钮（右上角）≈ (1405, 55)
#   弹出菜单里三个盾牌：狂野（无限符号）≈ (617, 380)、标准（圣甲虫）≈ (955, 375)、
#                       休闲（太阳）≈ (1290, 380)
# 坐标的**唯一来源**是 screen_regions.MODE_POINTS（config.load_mode_click_points()
# 在它之上叠加 ui_config.json 顶层 `mode_click` 段），这里不再硬编码。
# 核对方式：`hsctl screenshot --points mode` 只截图、不点击，把四个点画在图上。
# 点开菜单后到三个选项可点之间的动画延迟（保守固定值，不做截图轮询；
# 1.2s 在低配机上偶尔点在下落动画中途，所以给到 1.8s）。
MODE_MENU_OPEN_WAIT_SECONDS = 1.8


def mode_click_points():
    """当前生效的四个模式点击点（客户区相对坐标）。

    惰性走 config.load_mode_click_points()：它在 ui_config.json 里读用户覆盖，
    所以改配置立即生效，不需要重启控制台。
    """
    from config import load_mode_click_points
    return load_mode_click_points()


def click_mode_option(mode, points=None):
    """打开模式选择菜单并点 wild/standard/casual 之一（同步、约 2-3 秒）。

    前提：当前画面停在「选择套牌」界面（右上角模式按钮可见）。
    points 可选，形如 {"menu_open": (x, y), "wild": (x, y), ...}，用于注入
    用户在 ui_config.json 里微调过的坐标；缺省用 config 的当前值。
    """
    current = mode_click_points()
    open_pos = tuple(current.get("menu_open") or (1405, 55))
    pts = {key: tuple(value) for key, value in current.items()
           if key != "menu_open"}
    if points:
        if points.get("menu_open"):
            open_pos = tuple(points["menu_open"])
        for key in pts:
            if points.get(key):
                pts[key] = tuple(points[key])

    if mode not in pts:
        raise ValueError(f"Unknown mode: {mode!r}")
    if not get_HS_hwnd():
        raise ValueError("Hearthstone window unavailable")

    def _action():
        rand_sleep(OPERATE_INTERVAL)
        left_click(*open_pos)                      # 打开模式选择菜单
        rand_sleep(MODE_MENU_OPEN_WAIT_SECONDS)    # 等弹出动画
        left_click(*pts[mode])                     # 点对应模式
        rand_sleep(OPERATE_INTERVAL)               # 等模式切换动画收尾

    run_hearthstone_action(_action)

