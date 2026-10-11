# -*- coding: utf-8 -*-
"""截图区域校准工具：屏幕上拖框，把脚本要截的每一块都对齐好。

三个入口打开的都是**同一个窗口**（同一个实现，不再有“只能看不能拖”的那种）：
  * 网页控制台「🎯 校准区域」卡片 → [📐 显示校准框（屏幕上）]
  * 日志浮窗右上角「校准」按钮
  * 源码运行：python calibrate_roi.py
  * 自检：python calibrate_roi.py --selftest —— 窗口打开约 1 秒自动退出
  * 只拖框不跑 OCR：python calibrate_roi.py --no-ocr

十二个可校准目标（按 Tab 切换，或直接点屏幕正中央的目标条）：

| # | 目标 | 写入 ui_config.json | 对齐方法 |
| --- | --- | --- | --- |
| 1 | 盒子推荐面板（绿） | `recommendation_roi` | 把盒子的「打法参考A」面板框进绿框 |
| 2 | 换牌「确认」按钮（蓝） | `mulligan_confirm_roi` | 框住换牌界面的确认按钮 |
| 3 | 盒子「AI胜率」浮动条（橙） | `ai_win_rate_roi` | 框住左上角 AI胜率浮动条（兜底区域自动跟着外扩） |
| 4 | 结算「开始」按钮（青绿） | `post_game_start_roi` | 框住每局结束底部的「开始」按钮：脚本用它判断结算界面点完没有，并点它的正中心 |
| 5 | 段位数字（淡紫） | `post_game_rank_roi` | 框住结算界面右上角的段位名次数字：有数字=已上传说，数字就是名次 |
| 6 | 主界面「传统对战」（金） | `main_menu_battle_roi` | **贴住那四个字**（不用框整条银条）：脚本在框 ±40px 内用这四个字的图案找它，认到就点认到的位置 |
| 7 | 选择套牌「选择套牌」标题（粉） | `deck_select_title_roi` | 框住顶部那四个字。**判界面靠它，绝不能用右下角「开始」**（没选中卡组时「开始」不出现） |
| 8 | 选择套牌 9 个卡组（天蓝） | `deck_grid_roi` | 框住 9 个卡组**整块**区域：脚本按 3×3 均分得到 9 个点击点，只有名牌上有字的格子才会被点 |
| 9-12 | 模式按钮 + 三个盾牌（红） | `mode_click` 段 | 选卡组界面的模式按钮、以及狂野/标准/休闲三个盾牌：**框中心=点击点** |

目标 6/7/8 是「界面识别」用的：拖它们的时候顶部提示条会实时显示判定结果
（`框内文字匹配 1.00（阈值 0.80），对准了` / `9 格里 9 格有卡组名牌文字`），
用的就是脚本运行时**同一个**判定函数，所以"这里说对、脚本说不对"不会发生。
目标 6/7 是文字框（传统对战 259×27、选择套牌标题 104×24），所以它们的缩放下限
单独放宽到 20px（见 `min_size`）。

**随时只有一个框在屏幕上**：只画当前目标那一个（切目标时框就换位置），
非当前目标一个都不画 —— 十几个框同时铺出来根本看不清要对齐哪个。
整体拖动抓**框正中央的四向箭头十字标**（它画在目标条之上，框落在屏幕正中也不会被盖住）；
框的四条边同样能拖整体，只是框一大就离得远、不好抓。改大小拖右下角手柄。
**目标条挡住了框就把它拖走**：抓卡片标题带整块挪（目标行/按钮仍照常点）。

**框跟着炉石窗口走**：所有框都以"客户区相对坐标"为准（基准 1920×1080），
每一帧按当前窗口位置/尺寸重新推导屏幕位置。所以：

  * 拖动/缩放炉石窗口时，所有框**实时跟着走**，不需要重新校准；
  * 保存时写入的是客户区相对坐标（`roi_frame="client"`），换个分辨率也不会偏；
  * 老配置（没有 `roi_frame` 标记）第一次打开时会自动判断每个框的口径并打印出来，
    按一次 **S** 保存即升级成新格式。

操作：
  * **屏幕上只画当前目标那一个框**（非当前目标不画）——十几个框同时铺在屏幕上
    会完全看不清要对齐哪个；切目标时框就换到新位置；
  * **框正中央有一个四向箭头十字标**：抓它就能整体拖动这个框。框的
    四条边同样可以拖整体，但框一大就离得太远、很难抓准，所以主力抓手是十字标；
  * 拖右下角手柄可调整大小；
  * **目标条本身也能拖**：卡片默认压在屏幕正中，正好挡住"正中那个框"时就抓
    标题带（✥ 图标那一行）整块挪开；目标行和两个按钮之外的空白都能拖。
    挪动**只活在这一个会话里**，不写进 `ui_config.json`，关掉窗口下次仍回正中；
  * 目标条里列出十二个目标（当前目标高亮 + 左侧竖条），点一行就能切过去；
  * 目标条底部两个按钮：**左「保存全部区域 (S)」、右「退出 (Esc)」**，
    都有悬停高亮；键盘 **S** 保存、**Esc** 退出同样有效；
  * 说明文字在卡片内自动换行（金色标题段 = 做什么，暗淡说明段 = 怎么做），
    永远不会穿出卡片；卡片高度跟着换行行数长高；
  * 画面空白处的鼠标是**穿透**的：只有框/十字标/手柄/目标条上才拦鼠标，其余点击
    照常落到炉石上，所以可以边看游戏画面边调；
  * 顶部提示条每 1.5s 重判一次「绿框里有没有盒子面板」，对齐成功会变绿。

**保存后什么时候生效**：ROI 不是独立的热开关，而是**在「开始对战」时读取一次**
（`fsm.init()` → `initialize_recommendation_automation()` 重建
`RecommendationConfig`）。校准窗口只能在自动化**没在跑**的时候打开，所以
"存完再点一次「开始对战」"就生效，不需要重启脚本进程、也不需要重开一局对战。

原理：盒子推荐面板顶部才是「打法参考A」红头标题；面板没被框进推荐区域时
OCR 读不到信标 → 程序判定面板不存在 → 回合内 recommendation_not_stable
重试——这正是「回合开始无法打牌」最常见的原因。校准 = 让截图区域与面板重合。

注意：无需管理员权限——本工具只画框+截图，不模拟鼠标/键盘。
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageColor, ImageDraw

from region_overlay import (
    LayeredWindow, MSG, PM_REMOVE, USER32, auto_mapping, label_font,
    paint_layer, panel_state,
)
from screen_regions import (
    AI_WIN_RATE_REGION, AI_WIN_RATE_WIDE_MARGIN, CALIBRATION_TARGETS,
    DECK_GRID_REGION, MAIN_MENU_BATTLE_REGION,
    POST_GAME_RANK_REGION, POST_GAME_START_BUTTON_REGION,
    calibration_targets, expand_box,
)

# ---------------------------------------------------------------- Win32
USER32.SetCapture.argtypes = [wintypes.HWND]
USER32.ReleaseCapture.argtypes = []
USER32.GetCursorPos.argtypes = [ctypes.c_void_p]
USER32.GetCursorPos.restype = wintypes.BOOL

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204

VK_ESCAPE = 0x1B
VK_S = 0x53
VK_TAB = 0x09

EDGE = 6           # 边框抓取厚度（画在截图区域外侧，捕捉画面保持干净）
HANDLE = 22        # 右下角缩放手柄边长
MIN_SIZE = 60      # 区域最小边长
CLASS_NAME = "HSLegendArriverCalibrate"
# 框正中央的「整体拖动」十字标：半边长 13 → 26×26 的方块，比 HANDLE 稍大、
# 好抓；画在框中心而不是边缘，是因为"拖边缘移动整体"很难抓准（用户反馈）。
MOVE_CROSS_HALF = 13
# 十字标只在框小到装不下时才缩（框短边的一半 - 4，最少 6px）。
MOVE_CROSS_MIN_HALF = 6

# 顶部提示条的重判间隔（与 region_overlay 保持一致）。
PANEL_REFRESH_SECONDS = 1.5
# OCR 预览的自动刷新间隔（秒）：只在校准「推荐面板」时跑，避免拖框时满核。
OCR_REFRESH_SECONDS = 2.5

# ---------------------------------------------------------------- 配色（RGBA）
CARD_BG = (24, 28, 36, 238)
CARD_BORDER = (232, 185, 59, 245)
CARD_ROW_BG = (44, 50, 62, 235)
CARD_ROW_ACTIVE = (92, 74, 30, 245)
CARD_ROW_HOVER = (56, 63, 78, 235)
TEXT_MAIN = (240, 240, 240, 255)
TEXT_DIM = (166, 172, 182, 255)
TEXT_OK = (99, 199, 111, 255)
TEXT_WARN = (226, 168, 78, 255)
TEXT_BAD = (240, 120, 110, 255)
BUTTON_BG = (196, 132, 40, 250)
BUTTON_HOVER_BG = (222, 156, 56, 250)
# 「退出」用中性灰：它不是危险操作（关掉的只是校准窗口），别和金色「保存」混淆。
BUTTON_EXIT_BG = (74, 83, 100, 250)
BUTTON_EXIT_HOVER_BG = (94, 105, 124, 250)
BUTTON_TEXT = (255, 255, 255, 255)
PANEL_BG = (18, 21, 27, 238)
PANEL_BORDER = (118, 124, 132, 245)

# ---------------------------------------------------------------- 目标条布局
# 目标条画在**屏幕正中央**：左上角正好压着盒子「AI胜率」浮动条
# （95,0,300,60，校准时要能看见它），顶部中间又是「请对齐相应UI」提示条，
# 屏幕正中才是空出来的地方（推荐面板在左、换牌确认在右下、判定点不在正中）。
CARD_W = 356
CARD_PAD = 12
ROW_H = 32
SAVE_BTN_H = 36
# 底部两个按钮（保存 / 退出）平分一行，中间留 10px。
BTN_GAP = 10
# 说明文字的行高 / 最多几行（超出截断加省略号）。
HINT_FONT_SIZE = 13
HINT_LINE_H = 17
HINT_MAX_LINES = 3
INFO_LINE_H = 18
# 基础高度：标题 + 目标行 + 5 行信息（当前目标 / 说明 / 屏幕位置 / 坐标 / 保存状态）
# + 按钮行。说明换行后会由 CalibrationSession._card_height() 按实际行数加高。
CARD_H = (CARD_PAD + 20 + 8          # 标题
          + len(CALIBRATION_TARGETS) * ROW_H + 6   # 目标行
          + 5 * INFO_LINE_H + 8      # 当前目标 / 说明 / 屏幕位置 / 坐标 / 保存状态
          + SAVE_BTN_H + CARD_PAD)


def _hint_parts(hint: str) -> tuple[str, str]:
    """把说明拆成「高亮标题段」+「说明段」（按第一个冒号）。

    目标 4 的说明是「框住……按钮：检测它是否出现，并用框中心点击」：冒号前是
    一句"做什么"，冒号后才是解释。前段用金色高亮，后段暗淡换行。
    """
    text = str(hint or "").strip()
    for sep in ("：", ":"):
        if sep in text:
            head, _sep, tail = text.partition(sep)
            return f"{head}{sep}", tail.strip()
    return "", text


def wrap_text(text, font, max_width: float,
              max_lines: int = HINT_MAX_LINES) -> list[str]:
    """按字符贪心换行（中文没空格，只能逐字量宽），返回每一行的文字。

    超过 max_lines 时把多余内容丢掉并在最后一行加省略号——说明必须留在
    卡片里（用户反馈过说明文字"穿出卡片"）。font 只要有 getlength() 即可，
    测试里可以传一个按字符数算宽度的假字体。
    """
    text = str(text or "").strip()
    if not text:
        return [""]

    def _width(value: str) -> float:
        try:
            return float(font.getlength(value))
        except Exception:
            return float(len(value) * 8)

    lines = [""]
    for char in text:
        if lines[-1] and _width(lines[-1] + char) > max_width:
            lines.append("")
        lines[-1] += char
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _width(last + "…") > max_width:
            last = last[:-1]
        lines[-1] = f"{last}…"
    return lines


def card_rect(width: int, height: int, card_h: int | None = None,
              offset: tuple[int, int] | None = None
              ) -> tuple[int, int, int, int]:
    """目标条的矩形：屏幕正中央 + 用户拖动偏移（屏幕比卡片还小时贴左上角）。

    offset 是用户把目标条拖开的那段位移（见 `CalibrationSession.card_offset`）。
    它**只活在这次会话里**，不写进 ui_config.json：那是 ROI 配置，不该混界面状态。
    拖动后仍会把整张卡片夹回屏幕内（只保证"看得见"，不改变卡片尺寸）。
    """
    card_h = CARD_H if card_h is None else int(card_h)
    dx, dy = (0, 0) if offset is None else (int(offset[0]), int(offset[1]))
    width, height = int(width), int(height)
    # 居中位置（屏幕比卡片还小时贴左上角 8px，不出现负数坐标）。
    home_left = max(8, (width - CARD_W) // 2)
    home_top = max(8, (height - card_h) // 2)
    if offset is None:
        return (home_left, home_top, home_left + CARD_W, home_top + card_h)
    # 拖动后夹回屏幕内。下限用 **0 而不是 8**：贴边是用户主动拖过去的，
    # 应该能真正贴到边上（否则右/下边缘永远差 8px 对不齐）。
    left = min(max(0, home_left + dx), max(0, width - CARD_W))
    top = min(max(0, home_top + dy), max(0, height - card_h))
    return (left, top, left + CARD_W, top + card_h)

# ---------------------------------------------------------------- 预览面板
PREVIEW_W, PREVIEW_H = 360, 430
PREVIEW_IMG_H = 240

# 每帧要画十几次字，按字号缓存字体（FreeType 每次重开 msyh.ttc 会拖慢拖框）。
_FONT_CACHE: dict[int, object] = {}


def _rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    red, green, blue = ImageColor.getrgb(hex_color)
    return (red, green, blue, alpha)


# 校准工具写盘时记录的坐标口径版本号。
#   * ROI_FRAME_CLIENT：客户区相对坐标（基准 1920×1080）——正常保存写这个；
#   * ROI_FRAME_SCREEN：屏幕坐标——只有在读不到客户区几何（炉石没开/最小化）
#     时才可能写出来，因为那时换算是恒等的，存下来的其实是屏幕坐标。
#     诚实地标成 screen（而不是谎报 client）是防止"框整体跑偏"的关键。
# 读取与解释这个标记由 config.RecommendationConfig + display_mapping 负责
# （见 display_mapping.interpret_roi_box / roi_frame_is_credible）；本模块只在
# 保存时写它，以及用 `_stored_roi_frame()` 诊断当前配置是什么口径。
# **ROI 键的白名单只有一份**：config._USER_BOX_KEYS（不要在这里再抄一份）。
ROI_FRAME_CLIENT = "client"
ROI_FRAME_SCREEN = "screen"


def _user_config_path() -> Path:
    """与 RecommendationConfig 读取位置一致：应用目录下的 ui_config.json。"""
    return Path(__file__).resolve().parent / "ui_config.json"


def _stored_roi_frame() -> str:
    """ui_config.json 里 ROI 用的坐标系（诊断/自检用）。

    * ``"client"``（校准工具正常保存后写入）：客户区相对坐标（基准 1920×1080）；
    * 缺省 / ``"screen"`` / 其它：ROI 是**屏幕坐标**（老配置的原语义，或"炉石
      没开时按了保存"留下的）。

    **口径解释不在这里做**（否则同一份数据会被解释两次、偏移算两遍）：
    真正把值解释成客户区相对坐标的是
    `config.RecommendationConfig` → `display_mapping.interpret_roi_box`，它还会
    验证这个标记可不可信（见 `display_mapping.roi_frame_is_credible`）。
    """
    try:
        data = json.loads(_user_config_path().read_text(encoding="utf-8"))
        value = str(data.get("roi_frame") or "").strip().lower()
        return ROI_FRAME_CLIENT if value == ROI_FRAME_CLIENT else ROI_FRAME_SCREEN
    except Exception:
        return ROI_FRAME_SCREEN


def _load_config():
    """尽力拿一个 RecommendationConfig（拿不到就返回 None，各方都走默认值）。"""
    try:
        from config import RecommendationConfig
        return RecommendationConfig()
    except Exception:
        return None


class CalibrationSession:
    """屏幕上的交互式校准窗口：每个登记的目标都能拖、都能存。"""

    def __init__(self, selftest: bool = False, ocr_enabled: bool = True,
                 start_key: str = "", mapping_provider=None,
                 config_provider=None):
        self.selftest = bool(selftest)
        self.ocr_enabled = bool(ocr_enabled) and not self.selftest
        self.running = True
        self.window: LayeredWindow | None = None
        self.width = 0
        self.height = 0
        # 客户区几何来源（供测试注入）；None = 用 region_overlay.auto_mapping 探测。
        self._mapping_provider = mapping_provider
        # 目标框来源（供测试注入）：None = 读 config.RecommendationConfig。
        self._config_provider = config_provider

        # 每个目标有两个口径（见 _sync_boxes）：
        #   * client_box：**唯一权威**，ui_config.json 里存的就是它（客户区相对，
        #     基准 1920×1080）；窗口移动/缩放时靠它重新推导屏幕位置；
        #   * box       ：本帧的屏幕坐标（描边/拖框/命中的口径），每帧由
        #     _sync_boxes() 从 client_box 推导，拖框时反向写回。
        # 口径解释（老配置的屏幕坐标、"谎报 client"的坏配置……）**全部由
        # config.RecommendationConfig 负责**（见 display_mapping.interpret_roi_box）：
        # 校准工具拿到的值已经是客户区相对坐标，**绝不能再解释一次**，否则同一个
        # 偏移会被算两遍（框会整体错位）。
        self.targets = self._read_targets()
        # 每帧用的屏幕框是否已经跟着最新几何推导过（见 _box_frame_sig）。
        self._box_frame_sig = None
        # 打开窗口时读不到客户区几何 → 那一刻配置里的老坐标没法换算；等几何可用
        # 时补读一次（用户还没动过任何框的前提下，见 _sync_boxes）。
        self._reload_pending = self._mapping() is None
        self._edited = False
        self.active_index = 0
        if start_key:
            for index, target in enumerate(self.targets):
                if target["key"] == start_key:
                    self.active_index = index
                    break

        self.panel_state = None
        # 当前目标自己的实时判定（只有"靠识别界面"的目标会填，见
        # _check_active_target）：显示在顶部提示条里，拖框时当场看准没准。
        self.target_status = None
        self.drag = None              # None | ("move", dx, dy) | ("resize",)
                                      #        | ("card", dx, dy)
        # 目标条被拖开的位移（相对"屏幕正中"；只活在本会话，不落盘）。
        # 默认停在正中：屏幕正中是唯一空出来的地方（推荐面板在左、换牌确认在
        # 右下、AI胜率浮动条贴左上），但正中偶尔会压住正在校准的那个框，所以
        # 让用户能把它拖开（用户反馈）。
        self.card_offset = [0, 0]
        # 鼠标在哪个按钮/目标行上（只为高亮，不影响其它逻辑）。
        self.hover = None             # None | ("save", None) | ("exit", None)
                                      #        | ("target", index) | ("card", None)
        # 按下时落在哪个按钮上：抬起也在同一按钮才触发（保存/退出都一样）。
        self.press_target = None      # None | ("save", None) | ("exit", None)
        self.saved_flash_until = 0.0
        self.dirty = True
        self._es_held = False
        self._s_held = False
        self._tab_held = False
        self._msg = MSG()
        # 目标条高度按"当前目标说明的换行行数"算：说明绝不能穿出卡片。
        self._card_h_cache: dict[str, int] = {}

        # OCR 预览
        self.last_crop = None
        self.crop_event = threading.Event()
        self.ocr_result = {"status": "loading", "lines": [], "beacon": None,
                           "msg": "正在加载 OCR 引擎……"}

    # ------------------------------------------------------------ 目标
    @property
    def active(self) -> dict:
        return self.targets[self.active_index]

    def select(self, index: int) -> None:
        if 0 <= index < len(self.targets) and index != self.active_index:
            self.active_index = index
            # 换了目标，上一个目标的判定结果就不再适用（下次定时检查会重算）。
            self.target_status = None
            self.dirty = True
            print(f"[校准] 当前目标：{self.active['label']}"
                  f"（{self.active['config_key']}）")

    def cycle(self, step: int = 1) -> None:
        self.select((self.active_index + step) % len(self.targets))

    def _active_min_size(self) -> int:
        """当前目标允许的最小边长（注册表没写就用全局 MIN_SIZE）。

        「传统对战」是个 259×27 的**文字**框：用统一的 MIN_SIZE(60) 收，右下角
        手柄根本拖不出这么薄的框（只剩改配置文件一条路）。所以注册表里允许给单个
        目标声明 `min_size` —— 和当初"AI 胜率浮动条只有 160×40"是同一类问题。
        """
        try:
            value = int(self.active.get("min_size") or MIN_SIZE)
        except Exception:
            return MIN_SIZE
        return max(4, value)

    # ------------------------------------------------------------ 实时判定
    # 活动目标是"靠识别界面"的那些时，用**与运行时同一个函数**算一次分显示出来。
    # 校准窗口说"对准了"、脚本却说不准，是最难查的一类问题 —— 所以这里绝不另写
    # 一份判定逻辑（判定只住在 battle_entry 里）。
    _TEXT_CHECK_KINDS = {"main_menu_battle": "traditional"}

    def _check_active_target(self) -> None:
        """重算当前目标的实时判定（只有识别类目标有内容，其它置 None）。"""
        key = self.active["key"]
        kind = self._TEXT_CHECK_KINDS.get(key)
        if kind is None and key != "deck_grid":
            self.target_status = None
            return
        try:
            import battle_entry
        except Exception as exc:
            self.target_status = {"text": f"界面识别不可用：{exc}", "ok": False}
            return
        try:
            if key == "deck_grid":
                self.target_status = self._check_deck_grid(battle_entry)
            else:
                self.target_status = self._check_text_target(battle_entry, kind)
        except Exception as exc:
            self.target_status = {"text": f"判定失败：{exc}", "ok": False}

    def _check_text_target(self, battle_entry, kind: str) -> dict:
        """文字目标：抓"框 ± 搜索半径"，用运行时的匹配函数算分。"""
        import cv2
        import numpy as np

        template, reason = battle_entry.load_template(kind)
        if template is None:
            text = battle_entry.REASON_TEXT.get(reason, reason or "模板不可用")
            return {"text": text, "ok": False}
        radius = int(battle_entry.SEARCH_RADIUS)
        left, top, right, bottom = (int(v) for v in self.active["box"])
        crop = self._grab_crop((left - radius, top - radius,
                                right + radius, bottom + radius))
        if crop is None:
            return {"text": "抓不到这块画面（框跑到屏幕外了？）", "ok": False}
        patch = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2GRAY)
        score, _offset = battle_entry.match_score(patch, template, radius)
        ok = score >= battle_entry.NCC_MIN
        return {"text": (f"框内文字匹配 {score:.2f}"
                         f"（阈值 {battle_entry.NCC_MIN:.2f}）"
                         + ("，对准了" if ok else "，还没对上：把框拖到字上")),
                "ok": ok}

    def _check_deck_grid(self, battle_entry) -> dict:
        """9 个格子里有几个像卡组（名牌有白字）——阈值与运行时同一套常量。"""
        import numpy as np

        left, top, right, bottom = (int(v) for v in self.active["box"])
        crop = self._grab_crop((left, top, right, bottom))
        if crop is None:
            return {"text": "抓不到这块画面（框跑到屏幕外了？）", "ok": False}
        pixels = np.asarray(crop).astype(np.int16)
        height, width = pixels.shape[:2]
        reference_h = (battle_entry.DECK_GRID_DEFAULT[3]
                       - battle_entry.DECK_GRID_DEFAULT[1]) or 1
        # 客户区不是基准尺寸时框内像素也跟着缩放，阈值按面积比例补回去。
        scale = (height / float(reference_h)) or 1.0
        half_band = max(4, int(battle_entry.DECK_PLATE_BAND * scale))
        threshold = battle_entry.DECK_PLATE_MIN_PIXELS * scale * scale
        found = 0
        for row in range(battle_entry.DECK_ROWS):
            for col in range(battle_entry.DECK_COLS):
                y0, y1 = int(height * row / 3), int(height * (row + 1) / 3)
                x0, x1 = int(width * col / 3), int(width * (col + 1) / 3)
                center_y = (y0 + y1) // 2
                strip = pixels[max(0, center_y - half_band):center_y + half_band,
                               x0:x1]
                if strip.size == 0:
                    continue
                if float(strip.mean()) > battle_entry.DECK_PLATE_MAX_MEAN:
                    continue        # 这条带是亮的：不是名牌（白屏/还没渲染）
                high = strip.max(axis=2)
                low = strip.min(axis=2)
                if int(np.count_nonzero((low > 185)
                                       & ((high - low) < 55))) >= threshold:
                    found += 1
        return {"text": (f"9 格里 {found} 格有卡组名牌文字"
                         f"（≥{battle_entry.DECK_MIN_SLOTS_FOUND} 才算卡组界面）"),
                "ok": found >= battle_entry.DECK_MIN_SLOTS_FOUND}

    # ------------------------------------------------------------ 布局/命中
    def _card_height(self) -> int:
        """目标条高度：说明按实际换行行数加高（每个目标算一次后缓存）。"""
        key = self.active["key"]
        cached = self._card_h_cache.get(key)
        if cached is None:
            lines = wrap_text(self.active["hint"], label_font(HINT_FONT_SIZE),
                              CARD_W - 2 * CARD_PAD)
            cached = CARD_H + (max(1, len(lines)) - 1) * HINT_LINE_H
            self._card_h_cache[key] = cached
        return cached

    def _card_rect(self, width: int, height: int):
        return card_rect(width, height, self._card_height(),
                         self.card_offset)

    def _chip_rects(self, width: int, height: int):
        left, top = self._card_rect(width, height)[:2]
        rects = []
        row_top = top + CARD_PAD + 20 + 8
        for index in range(len(self.targets)):
            rects.append(((left + CARD_PAD, row_top,
                           left + CARD_W - CARD_PAD, row_top + ROW_H - 4), index))
            row_top += ROW_H
        return rects

    def _card_drag_rect(self, width: int, height: int):
        """目标条的「拖动区」= 卡片减去目标行和按钮后剩下的部分。

        为什么要"减去"而不是整张卡片都能拖：卡片几乎被目标行铺满了，如果整张
        可拖，按下时到底是"选中那一行"还是"开始拖卡片"就分不清了 —— 拖完手指
        一松才发现在选目标。所以：目标行、两个按钮**优先**响应自己的点击，
        其余的空白（标题带、说明区、行与行之间的缝、状态文字附近）才是拖卡片。
        """
        left, top, right, bottom = self._card_rect(width, height)
        blocked = [rect for rect, _index in self._chip_rects(width, height)]
        blocked.extend(self._button_rects(width, height))
        return (left, top, right, bottom), blocked

    def _button_rects(self, width: int, height: int):
        """底部两个按钮：左「保存全部区域 (S)」、右「退出 (Esc)」。"""
        left, top = self._card_rect(width, height)[:2]
        bottom = top + self._card_height() - CARD_PAD
        inner_left = left + CARD_PAD
        inner_right = left + CARD_W - CARD_PAD
        button_w = (inner_right - inner_left - BTN_GAP) // 2
        save = (inner_left, bottom - SAVE_BTN_H,
                inner_left + button_w, bottom)
        exit_ = (inner_left + button_w + BTN_GAP, bottom - SAVE_BTN_H,
                 inner_right, bottom)
        return save, exit_

    def _save_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        return self._button_rects(width, height)[0]

    def _exit_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        return self._button_rects(width, height)[1]

    @staticmethod
    def _preview_rect(width: int) -> tuple[int, int, int, int]:
        left = max(8, width - PREVIEW_W - 16)
        return (left, 16, left + PREVIEW_W, 16 + PREVIEW_H)

    def _move_cross_rect(self) -> tuple[int, int, int, int]:
        """当前目标框正中央那个「整体拖动」十字标的方块（屏幕坐标）。

        框很小时（比如「传统对战」文字框只有 27px 高）十字标按短边缩到最小，
        但**永远留一块可点的区域**——它已经退化成很小，也需要能抓到。
        """
        left, top, right, bottom = (int(v) for v in self.active["box"])
        half = min(MOVE_CROSS_HALF,
                   max(MOVE_CROSS_MIN_HALF,
                       min(right - left, bottom - top) // 2 - 4))
        cx, cy = (left + right) // 2, (top + bottom) // 2
        return (cx - half, cy - half, cx + half, cy + half)

    def _hit_test(self, sx: int, sy: int, width: int, height: int):
        """返回 (类型, 目标序号)；不在可交互区域上返回 None。

        命中顺序（先到先得）：目标行/按钮 → **目标条空白区（拖卡片）** →
        右下角手柄 → 正中央十字标 → 框边。

        卡片排在框之前：卡片是画在最上层的不透明面板，点"穿过"卡片去命中的框
        与眼睛看到的不一致。卡片内部又先给目标行/按钮，只有它们之外的空白才算
        拖卡片（见 `_card_drag_rect`）。十字标排在框边前面，因为它在框内部，
        是"整体拖动"的主抓手（框一大，四条边离得远、要瞄得很准）。
        """
        for rect, index in self._chip_rects(width, height):
            if rect[0] <= sx <= rect[2] and rect[1] <= sy <= rect[3]:
                return ("target", index)
        for name, rect in (("save", self._save_rect(width, height)),
                           ("exit", self._exit_rect(width, height))):
            if rect[0] <= sx <= rect[2] and rect[1] <= sy <= rect[3]:
                return (name, None)
        card, blocked = self._card_drag_rect(width, height)
        if card[0] <= sx <= card[2] and card[1] <= sy <= card[3]:
            if not any(r[0] <= sx <= r[2] and r[1] <= sy <= r[3]
                       for r in blocked):
                return ("card", None)
        # 当前目标的框与鼠标事件同口径（都是屏幕上量出来的坐标），直接比较。
        left, top, right, bottom = (int(v) for v in self.active["box"])
        if (right + 2 <= sx <= right + 2 + HANDLE
                and bottom + 2 <= sy <= bottom + 2 + HANDLE):
            return ("corner", None)
        cross = self._move_cross_rect()
        if cross[0] <= sx <= cross[2] and cross[1] <= sy <= cross[3]:
            return ("cross", None)
        if (left - EDGE <= sx <= right + EDGE
                and top - EDGE <= sy <= bottom + EDGE):
            return ("edge", None)
        return None

    # ------------------------------------------------------------ 鼠标
    def _clamp(self, box) -> list[int]:
        """把框收进屏幕内（保存用，不改变大小）。

        这里**不**强制 MIN_SIZE：拖框时有最小尺寸保护，但「AI胜率」浮动条本身
        只有 160×40，比推荐面板小得多，用 MIN_SIZE 收会把用户框好的区域改掉。
        """
        width = self.width or 1920
        height = self.height or 1080
        left, top, right, bottom = (int(v) for v in box)
        box_width = min(max(1, right - left), width)
        box_height = min(max(1, bottom - top), height)
        left = min(max(0, left), max(0, width - box_width))
        top = min(max(0, top), max(0, height - box_height))
        return [left, top, left + box_width, top + box_height]

    def _on_mouse_down(self, sx: int, sy: int, width: int, height: int) -> None:
        kind = self._hit_test(sx, sy, width, height)
        if kind is None:
            return
        name, index = kind
        if name == "target":
            self.select(index)
        elif name in ("save", "exit"):
            # 按下时先记住，抬起时还在同一个按钮上才算点中（和普通按钮一致）。
            self.press_target = (name, None)
        elif name == "corner":
            self.drag = ("resize",)
        elif name in ("cross", "edge"):
            # 十字标与框边都是「整体移动」：记住按下点相对框左上的偏移即可。
            left, top = self.active["box"][0], self.active["box"][1]
            self.drag = ("move", sx - left, sy - top)
        elif name == "card":
            # 拖目标条：记住按下点相对卡片左上的偏移，移动时反推卡片左上。
            card = self._card_rect(width, height)
            self.drag = ("card", sx - card[0], sy - card[1])
        self.dirty = True
        if self.window is not None and self.window.hwnd is not None:
            USER32.SetCapture(self.window.hwnd)

    def _on_mouse_up(self, sx: int, sy: int, width: int, height: int) -> None:
        pressed, self.press_target = self.press_target, None
        if pressed is not None:
            if self._hit_test(sx, sy, width, height) == pressed:
                if pressed[0] == "save":
                    self.save(flash=True)
                else:
                    # 「退出」= 和 Esc 一样：关掉校准窗口（只关这个窗口）。
                    print("[校准] 已退出校准窗口。")
                    self.running = False
        self.drag = None
        USER32.ReleaseCapture()
        self.dirty = True

    def _on_mouse_move(self, sx: int, sy: int, width: int, height: int) -> None:
        if self.drag is None:
            # 不在拖框：记一下光标悬停在哪个按钮/目标行上，用于高亮。
            hover = self._hit_test(sx, sy, width, height)
            if hover != self.hover:
                self.hover = hover
                self.dirty = True
            return
        # 鼠标给的是屏幕坐标，而框本身也是屏幕上量出来的坐标：直接写入。
        if self.drag[0] == "card":
            # 拖目标条：drag 里存的是**按下点相对卡片左上**的偏移（grab），把
            # "想让卡片左上落在 (sx-grab)"夹进屏幕后反推 card_offset。
            #
            # 注意**不能**在按下的那一刻就把 grab 换算成"指针相对屏幕的绝对
            # 位移"再累加：一旦卡片被屏幕边缘夹住，指针继续往外走时累积量会
            # 一直涨，往回拖就得先空拖掉那段"隐形行程"，表现是卡片贴在边上
            # 不动（实测踩过）。存 grab、每帧用**指针当前位置**重算，就不存在
            # 这种累积误差。
            _, grab_x, grab_y = self.drag
            card_h = self._card_height()
            want_left = min(max(0, sx - grab_x), max(0, width - CARD_W))
            want_top = min(max(0, sy - grab_y), max(0, height - card_h))
            self.card_offset = [want_left - max(8, (width - CARD_W) // 2),
                                want_top - max(8, (height - card_h) // 2)]
            self.dirty = True
            return
        left, top, right, bottom = (int(v) for v in self.active["box"])
        if self.drag[0] == "move":
            _, dx, dy = self.drag
            box_width, box_height = right - left, bottom - top
            new_left = min(max(0, sx - dx), max(0, width - box_width))
            new_top = min(max(0, sy - dy), max(0, height - box_height))
            new_right = new_left + box_width
            new_bottom = new_top + box_height
        else:  # resize（右下角手柄）
            new_left, new_top = left, top
            new_right = min(width, max(left + self._active_min_size(), sx))
            new_bottom = min(height, max(top + self._active_min_size(), sy))
        self.active["box"] = [new_left, new_top, new_right, new_bottom]
        # 屏幕框是拖动时的即时口径，但它不是权威数据：每次改动都回写成
        # 客户区相对坐标，窗口移动后 _sync_boxes() 才能把它带回去。
        self._write_back_client(self.active)
        self.dirty = True

    # ------------------------------------------------------------ 保存
    # `_boxes_to_save()` / `save()` 见下面「坐标口径 / 实时跟随」一节：保存要先把
    # 各框按当前客户区几何同步（见 `_sync_boxes`），所以和它们放在一起。

    # ------------------------------------------------------------ OCR
    def _grab_crop(self, box):
        """抓一块屏幕，返回 PIL RGB 图（失败返回 None）。"""
        from PIL import ImageGrab
        left, top, right, bottom = (int(v) for v in box)
        if right - left < 4 or bottom - top < 4:
            return None
        try:
            return ImageGrab.grab(bbox=(left, top, right, bottom),
                                  all_screens=False).convert("RGB")
        except Exception:
            return None

    def _capture_for_ocr(self) -> None:
        target = next((t for t in self.targets
                       if t["key"] == "recommendation"), None)
        if target is None:
            return
        crop = self._grab_crop(target["box"])
        if crop is None:
            return
        self.last_crop = crop
        self.crop_event.set()

    def _ocr_worker(self) -> None:
        try:
            import numpy as np
            from src.ocr.paddle_adapter import PaddleOcrAdapter
            from src.ocr.preprocess import iter_preprocess_recommendation

            adapter = PaddleOcrAdapter()
            while self.running:
                self.crop_event.wait(timeout=5.0)
                self.crop_event.clear()
                if not self.running:
                    break
                time.sleep(0.2)     # 节流：拖框时事件密，别满速空转
                crop = self.last_crop
                if crop is None:
                    continue
                # 预处理链路要 BGR 的 ndarray（cv2 约定）。
                pixels = np.ascontiguousarray(np.asarray(crop)[:, :, ::-1])
                started = time.time()
                evidence = None
                for tag, candidate in iter_preprocess_recommendation(pixels):
                    if tag != "scaled_color_v1":
                        continue
                    evidence = adapter.recognize(
                        candidate, f"calib-{id(crop)}", "calib_scaled")
                    break
                if evidence is None:
                    continue
                lines = [line.text for line in evidence.lines if line.text]
                beacon = any("打法参考A" in text or "打法参考Ａ" in text
                             for text in lines)
                self.ocr_result = {
                    "status": "ok", "lines": lines[:6], "beacon": beacon,
                    "conf": evidence.confidence,
                    "msg": f"OCR {len(lines)} 行 置信 {evidence.confidence:.2f}"
                           f" 耗时 {time.time() - started:.0f}s",
                }
                self.dirty = True
        except Exception as exc:
            self.ocr_result = {"status": "error", "lines": [], "beacon": False,
                               "conf": 0.0,
                               "msg": f"OCR 未运行：{type(exc).__name__}: {exc}"}
            self.dirty = True

    def _start_ocr_thread(self) -> None:
        if not self.ocr_enabled:
            self.ocr_result = {"status": "off", "lines": [], "beacon": False,
                               "conf": 0.0, "msg": "预览模式（--no-ocr）"}
            return
        threading.Thread(target=self._ocr_worker, name="calib-ocr",
                         daemon=True).start()

    # ------------------------------------------------------------ 渲染
    def _mapping(self):
        """本帧使用的客户区几何（测试可注入；不传则自动探测炉石客户区）。"""
        if self._mapping_provider is not None:
            try:
                return self._mapping_provider()
            except Exception:
                return None
        # 实时跟踪：窗口一动，缓存立刻失效 → 拖动窗口时框实时跟着走。
        try:
            import window_tracker
            window_tracker.ensure_started()
        except Exception:
            pass
        return auto_mapping()

    # -------------------------------------------------- 坐标口径 / 实时跟随
    def _read_targets(self) -> list[dict]:
        """从配置读一遍目标框，统一成**客户区相对坐标 + 屏幕坐标**两份。

        `calibration_targets()` 给的值已经是客户区相对坐标 —— 口径解释（老配置的
        屏幕坐标、坏掉的 `roi_frame` 标记……）由 `config.RecommendationConfig`
        调 `display_mapping.interpret_roi_box` 完成，**这里不再解释**：同一份数据
        解释两次＝把窗口偏移算两遍，框会整体错位。
        """
        config = (self._config_provider() if self._config_provider is not None
                  else _load_config())
        targets = []
        for target in calibration_targets(config):
            target = dict(target)
            target["client_box"] = list(int(v) for v in target["box"])
            target["box"] = list(target["client_box"])
            targets.append(target)
        return targets

    def _reload_targets(self) -> None:
        """重新读一遍配置里的目标框（保留当前选中的目标）。

        只用于一种边界情况：**打开校准窗口时炉石还没显示**（读不到客户区几何，
        那一刻配置里的老坐标没法换算），之后炉石显示出来了 —— 这时补读一次，
        框才会落到正确位置。用户一旦动过框就不再补读，免得把他的调整冲掉。
        """
        active_key = self.active["key"] if self.targets else ""
        self.targets = self._read_targets()
        self.active_index = 0
        for index, target in enumerate(self.targets):
            if target["key"] == active_key:
                self.active_index = index
                break
        self._box_frame_sig = None
        self.dirty = True

    @staticmethod
    def _mapping_signature(mapping):
        """客户区几何的指纹（位置 + 尺寸）：变了就要重新推导屏幕框。"""
        if mapping is None:
            return None
        return (int(mapping.screen_left), int(mapping.screen_top),
                int(mapping.width), int(mapping.height))

    def _sync_boxes(self, mapping=None) -> None:
        """按当前客户区几何重算每个目标的**屏幕框**（每帧画之前调一次）。

        这是"框跟着炉石窗口实时移动"的关键：`client_box` 不动，屏幕框每帧跟着
        几何走。几何指纹没变时是空操作（一次元组比较，可以按帧调用）。
        """
        if self._reload_pending and mapping is not None and not self._edited:
            self._reload_pending = False
            self._reload_targets()
            print("[校准] 炉石客户区已可读：重新读取了一遍配置里的区域坐标。")
        sig = self._mapping_signature(mapping)
        if sig is None and self._box_frame_sig is not None:
            # 炉石最小化/关掉了：保持最后一次的位置，别让框塌到屏幕左上角。
            return
        if sig == self._box_frame_sig:
            return
        self._box_frame_sig = sig
        from display_mapping import box_to_screen
        for target in self.targets:
            screen_box = list(box_to_screen(target["client_box"], mapping))
            if screen_box != list(target["box"]):
                target["box"] = screen_box
                self.dirty = True

    def _write_back_client(self, target, mapping=None) -> None:
        """拖框/保存后把**屏幕框**回写成 `client_box`（权威口径）。

        拖动时以屏幕框为准（鼠标事件就是屏幕坐标），只在每次改动后回写一次；
        反过来"客户区 → 屏幕"在非整数缩放下有 floor/ceil 的 1px 漂移，反复
        往返会积累误差，所以方向固定为"屏幕 → 客户区"。
        `clamp=False`：框可以被拖到炉石窗口外面（负的客户区坐标是合法的），
        这样它跟着窗口走时仍然对得上。
        """
        from display_mapping import box_from_screen
        if mapping is None:
            mapping = self._mapping()
        target["client_box"] = list(
            box_from_screen(tuple(int(v) for v in target["box"]), mapping,
                            clamp=False))
        self._edited = True

    # -------------------------------------------------- 保存
    def _boxes_to_save(self, mapping=None) -> dict:
        boxes = {}
        mapping = self._mapping() if mapping is None else mapping
        self._sync_boxes(mapping)
        from display_mapping import box_from_screen
        for target in self.targets:
            key = target["config_key"]
            # 先把屏幕框收进屏幕内（不然存下来的是屏幕外的死框），再换算成
            # **客户区相对坐标** —— ui_config.json 统一存这个口径。
            screen_box = self._clamp(target["box"])
            target["box"] = list(screen_box)
            target["client_box"] = list(
                box_from_screen(screen_box, mapping, clamp=False))
            boxes[key] = list(target["client_box"])
            if key == "ai_win_rate_roi":
                # AI胜率兜底区域永远跟着主区域外扩，不给用户单独拖。
                boxes["ai_win_rate_wide_roi"] = list(
                    expand_box(boxes[key], AI_WIN_RATE_WIDE_MARGIN))
        return boxes

    def save(self, flash: bool = False) -> bool:
        mapping = self._mapping()
        boxes = self._boxes_to_save(mapping)
        path = _user_config_path()
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                cfg = {}
        except Exception:
            cfg = {}
        cfg.update(boxes)
        if mapping is None:
            # 没有客户区几何时换算退化成恒等：这次存下来的其实是**屏幕坐标**，
            # 绝不能标成 client（否则下次炉石一显示，所有框都会被整体偏移一次）。
            # 老用户踩过这个坑：标记是 client、值是屏幕坐标 → 框全跑偏还拖不动。
            print("[校准] 警告：读不到炉石客户区（未运行/最小化），这次按**屏幕坐标**"
                  "保存（不写 client 标记）。请开着炉石重新校准一次并按 S，"
                  "否则窗口移动后位置会偏。")
            cfg["roi_frame"] = ROI_FRAME_SCREEN
        else:
            # 记下坐标口径：保存后所有 ROI 都是客户区相对坐标，下次读取不用再猜。
            cfg["roi_frame"] = ROI_FRAME_CLIENT
        try:
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError as exc:
            print(f"[校准] 保存失败：{exc}")
            return False
        if mapping is None:
            # 没有客户区几何时换算退化成恒等，「客户区相对」其实等于屏幕坐标：
            # 现在能用，但窗口一挪就对不上，必须明确告诉用户。
            print("[校准] 警告：读不到炉石客户区（未运行/最小化），坐标按无偏移"
                  "保存。请开着炉石重新校准一次，否则窗口移动后位置会偏。")
        if flash:
            self.saved_flash_until = time.time() + 1.5
        self.dirty = True
        print(f"[校准] 已保存 {len(boxes)} 个区域 -> {path}")
        for key, box in boxes.items():
            print(f"[校准]   {key} = {tuple(box)}")
        print("[校准] 下次「开始对战」生效（不覆盖名字/日志目录/延时等其它配置）")
        return True

    def _preview_config(self, mapping=None):
        """把「正在拖的框」包成 config（**客户区相对口径**）。

        给 `panel_state`（截推荐区域判定盒子面板）等仍然按"客户区相对 + mapping"
        取值的消费者用。**直接用权威的 `client_box`**，不做屏幕→客户区的往返
        换算：绘制叠加层走 `overrides`（屏幕坐标），两条路径各自从同一份权威
        数据出发，就不会出现"切换目标时其他框跳位置"。
        """
        boxes = {target["config_key"]: tuple(target.get("client_box")
                                             or target["box"])
                 for target in self.targets}
        main = boxes.get("ai_win_rate_roi", AI_WIN_RATE_REGION)
        config = SimpleNamespace(
            desktop_size=(self.width or 1920, self.height or 1080),
            desktop_dpi=96,
            recommendation_roi=boxes.get("recommendation_roi", (7, 200, 202, 500)),
            mulligan_confirm_roi=boxes.get("mulligan_confirm_roi",
                                           (860, 810, 1060, 890)),
            ai_win_rate_roi=main,
            ai_win_rate_wide_roi=expand_box(main, AI_WIN_RATE_WIDE_MARGIN),
            post_game_start_roi=boxes.get("post_game_start_roi",
                                          POST_GAME_START_BUTTON_REGION),
            post_game_rank_roi=boxes.get("post_game_rank_roi",
                                         POST_GAME_RANK_REGION),
            main_menu_battle_roi=boxes.get("main_menu_battle_roi",
                                           MAIN_MENU_BATTLE_REGION),
            deck_grid_roi=boxes.get("deck_grid_roi", DECK_GRID_REGION),
        )
        # 绘制路径按**目标 key**取框（模式点击点的 config_key 是 `mode_click` 段里的
        # 键名，不是 config 字段，所以 getattr(config, config_key) 取不到）。
        config.hs_target_boxes = {
            target["key"]: tuple(target.get("client_box") or target["box"])
            for target in self.targets}
        return config

    def _screen_boxes(self):
        """当前所有目标的**屏幕坐标**框（绘制叠加层直接用）。"""
        return {target["key"]: tuple(int(v) for v in target["box"])
                for target in self.targets}

    def _region_keys_to_skip(self) -> set[str]:
        """叠加层里**一个框都不画**的 key 集合 —— 即"屏幕上所有会画出来的框"。

        `screenshot_regions()` 里除了可拖的目标，还有**推导出来**的区域：
        目前只有 `win_rate_wide`（AI胜率的兜底框，永远跟着主区域外扩）。它没有
        对应的目标行、也不能单独拖，但同样会在屏幕上多出一圈红框，所以必须
        一起跳过。这里**照着 regions 真实产出的 key 收**，而不是手抄一份名单：
        以后再加派生区域时不会漏。
        """
        from screen_regions import screenshot_regions
        cfg = self._preview_config()
        return {region["key"] for region in screenshot_regions(cfg)}

    def render(self, width: int, height: int):
        """画出一整屏 RGBA：**只有当前目标**一个框 + 提示条 + 目标条/预览。

        **屏幕上只画当前目标**：以前会把十几个框全画出来，屏幕全是框，根本看不
        清要对齐哪个（用户反馈）。现在非当前目标的框一个都不画——切目标时框就
        换到新位置，位置参考由屏幕正中央的目标条（含每个框的尺寸）负责。

        当前目标在这一层里被**整个跳过**，由 `_draw_active_target()` 单独画：
        否则同一个框会被画两次（两次描边差 1px，外圈偏暗），看起来像"一个区域
        有两个框"；它的标签也不画（坐标在屏幕正中央的目标条里已经有了，标签底
        又会紧贴框边）。

        **一帧只取一次几何**：先按当前客户区几何把所有目标框同步成屏幕坐标
        （`_sync_boxes`，窗口一动就重算 → 框实时跟着窗口走），然后两条绘制路径
        （`paint_layer` 的 overrides 与 `_draw_active_target`）共用同一批屏幕坐标。
        """
        active_key = self.active["key"]
        # 一帧只解析一次：两条绘制路径共用，绝不允许各自取。
        mapping = self._mapping()
        # 窗口移动/缩放后立刻重算屏幕框（帧内只算一次，之后是空操作）。
        self._sync_boxes(mapping)
        # 所有框都交给 skip_keys 不画（叠加层只留提示条 + 阶段判定点）；
        # 当前目标由 _draw_active_target 用同一批屏幕坐标画粗框。
        # overrides 仍然要给：draw_region_boxes 会用它回填 in_bounds/screen_box，
        # 网页预览图与本窗口共用同一份绘制逻辑，两边的框必须落在同一批像素上。
        layer = paint_layer(width, height, self.panel_state,
                            self._preview_config(mapping),
                            skip_keys=self._region_keys_to_skip(),
                            mapping=mapping,
                            overrides=self._screen_boxes(),
                            status=self.target_status)
        draw = ImageDraw.Draw(layer)
        self._draw_active_target(draw, width, height, mapping)
        self._draw_card(draw, width, height, mapping)
        # 十字标最后画：它是"整体拖动"的唯一抓手，被目标条压住就等于没了。
        # （框正好落在屏幕正中时，目标条会盖在框中心上——用户就是这么遇到的。）
        self._draw_move_cross(draw, _rgba(self.active["color"]))
        self._draw_preview(draw, width, layer)
        return layer

    def _draw_text(self, draw, xy, text: str, size: int, color) -> None:
        font = _FONT_CACHE.get(size) or label_font(size)
        _FONT_CACHE[size] = font
        if font is None:
            return
        draw.text(xy, text, fill=color, font=font)

    def _draw_active_target(self, draw, width: int, height: int,
                            mapping=None) -> None:
        """把**当前目标**画成粗框 + 右下角缩放手柄 + 正中央「整体拖动」十字标。

        只有当前目标会被画出来：屏幕上同时出现十几个框会完全看不清要对齐哪个
        （用户反馈）。非当前目标一个都不画，切目标时当前框换个位置就是了。

        坐标写在正中央的目标条里，不在框旁边飘字：左上角的
        「AI胜率」浮动条贴着屏幕顶边，飘字要么出屏、要么压住别的框。

        **不换算**：`self.active["box"]` 在 `render()` 里已经被 `_sync_boxes()`
        按当前几何换算成屏幕坐标，`paint_layer` 那条路径用的也是**同一批**屏幕
        坐标（overrides）—— 两条路径天然落在同一批像素上。这里若再换算一次就
        等于把窗口偏移算了两遍，表现就是"切换目标时其他框的位置变了"。
        """
        left, top, right, bottom = (int(v) for v in self.active["box"])
        color = _rgba(self.active["color"])
        draw.rectangle((left, top, right, bottom), outline=color, width=3)
        # 右下角缩放手柄
        hx, hy = min(width - 1, right + 2), min(height - 1, bottom + 2)
        hx1, hy1 = min(width, hx + HANDLE), min(height, hy + HANDLE)
        draw.rectangle((hx, hy, hx1, hy1), fill=color)
        for offset in range(6, HANDLE - 4, 7):
            x, y = hx + offset, hy + offset
            if x < hx1 and y < hy1:
                draw.line((x, y, x + 4, y + 4), fill=TEXT_MAIN, width=2)

    @staticmethod
    def _draw_move_glyph(draw, x: int, y: int, color) -> None:
        """16×16 的「四向箭头」小图标：标在目标条标题前，提示这里能整块拖。

        和框正中央的大十字标是同一个意象（四向箭头 = 整体移动），但这里只是
        **静态提示**：真正判定拖动靠 `_hit_test` 的 ("card", None)，不是靠这个
        图标——所以图标画小了也不影响好不好拖。
        """
        cx, cy = x + 7, y + 7
        arm = 6
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tip_x, tip_y = cx + dx * arm, cy + dy * arm
            draw.line((cx, cy, tip_x, tip_y), fill=color, width=2)
            back_x, back_y = tip_x - dx * 3, tip_y - dy * 3
            if dx:
                draw.line((tip_x, tip_y, back_x, back_y - 2), fill=color, width=2)
                draw.line((tip_x, tip_y, back_x, back_y + 2), fill=color, width=2)
            else:
                draw.line((tip_x, tip_y, back_x - 2, back_y), fill=color, width=2)
                draw.line((tip_x, tip_y, back_x + 2, back_y), fill=color, width=2)

    def _draw_move_cross(self, draw, color) -> None:
        """框正中央的「四个箭头十字标」= 整体拖动抓手。

        框边虽然也能拖整体，但框一大、四条边就离得很远，要瞄得很准才抓得到
        （用户反馈"拉框的边缘进行整体拖动非常不方便"）。十字标画在框中心，
        跟框一起走，抓它就能整体挪；框小的时候它会自动缩到框内。
        """
        rect = self._move_cross_rect()
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        half = min(rect[2] - cx, rect[3] - cy)
        if half < 3:
            return
        # 底衬：半透明深色方块，保证十字标在任何画面（亮/花）上都看得清。
        draw.rectangle(rect, fill=(20, 23, 30, 200), outline=color, width=2)
        # 四向箭头：每个方向的线段 + 两条箭头短线（不做旋转多边形，
        # 直线画法在 13px 尺度上更干净，也不会因为抗锯齿发虚）。
        arm = half - 3
        if arm < 2:
            return
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tip_x, tip_y = cx + dx * arm, cy + dy * arm
            draw.line((cx, cy, tip_x, tip_y), fill=TEXT_MAIN, width=2)
            # 箭头两撇：沿垂直方向各退一格，再朝尖端收拢。
            back_x, back_y = tip_x - dx * 4, tip_y - dy * 4
            if dx:
                draw.line((tip_x, tip_y, back_x, back_y - 3), fill=TEXT_MAIN,
                          width=2)
                draw.line((tip_x, tip_y, back_x, back_y + 3), fill=TEXT_MAIN,
                          width=2)
            else:
                draw.line((tip_x, tip_y, back_x - 3, back_y), fill=TEXT_MAIN,
                          width=2)
                draw.line((tip_x, tip_y, back_x + 3, back_y), fill=TEXT_MAIN,
                          width=2)

    def _draw_card(self, draw, width: int, height: int,
                   mapping=None) -> None:
        """目标条：目标列表 + 当前目标高亮 + 说明（自动换行）+ 保存/退出按钮。

        说明文字以前是整句一行画出去，句子一长就穿出卡片（用户反馈）。现在：
          * 说明按「：」拆成金色标题段 + 暗淡说明段；
          * 说明段按卡片内宽换行（最多 3 行），卡片高度跟着行数加高；
          * 「当前目标」「坐标」「保存状态」分行排版，当前目标用目标自己的颜色高亮；
          * 底部两个按钮：左「保存全部区域 (S)」、右「退出 (Esc)」，悬停会提亮；
          * 标题带是**拖动把手**（✥ + 高亮条），抓卡片空白处能整块挪开——卡片
            默认压在屏幕正中，正好挡住"正中那个框"时挪一下就能看见。
        """
        card_left, card_top, card_right, card_bottom = self._card_rect(width,
                                                                      height)
        draw.rectangle((card_left, card_top, card_right, card_bottom),
                       fill=CARD_BG, outline=CARD_BORDER, width=2)
        # 标题带 = 拖动把手：悬停时整条提亮，一眼看出"这里能拖"。
        title_h = CARD_PAD + 20 + 8
        dragging_card = self.drag is not None and self.drag[0] == "card"
        if dragging_card or self.hover == ("card", None):
            draw.rectangle((card_left + 2, card_top + 2, card_right - 2,
                            card_top + title_h - 4),
                           fill=CARD_ROW_HOVER)
        self._draw_move_glyph(draw, card_left + CARD_PAD, card_top + CARD_PAD,
                              CARD_BORDER)
        self._draw_text(draw, (card_left + CARD_PAD + 18, card_top + CARD_PAD),
                        "校准目标（Tab 切换 · 拖这里挪动）", 15, CARD_BORDER)
        for rect, index in self._chip_rects(width, height):
            target = self.targets[index]
            active = index == self.active_index
            hovered = self.hover == ("target", index)
            fill = (CARD_ROW_ACTIVE if active
                    else (CARD_ROW_HOVER if hovered else CARD_ROW_BG))
            draw.rectangle(rect, fill=fill)
            swatch = (rect[0] + 6, rect[1] + 7, rect[0] + 22, rect[1] + 21)
            draw.rectangle(swatch, fill=_rgba(target["color"]))
            if active:
                # 当前行再加一条左侧竖条，扫一眼就知道在拖哪个目标。
                draw.rectangle((rect[0], rect[1], rect[0] + 3, rect[3]),
                               fill=_rgba(target["color"]))
            text = f"{index + 1}. {target['label']}"
            self._draw_text(draw, (rect[0] + 30, rect[1] + 6), text, 15,
                            TEXT_MAIN if active else TEXT_DIM)
            box = target["box"]
            size_text = f"{box[2] - box[0]}×{box[3] - box[1]}"
            self._draw_text(draw, (rect[2] - 74, rect[1] + 7), size_text, 13,
                            _rgba(target["color"]) if active else TEXT_DIM)

        # ---- 当前目标 / 说明 / 坐标 / 保存状态 --------------------------
        inner_w = CARD_W - 2 * CARD_PAD
        line_y = card_top + CARD_PAD + 20 + 8 + len(self.targets) * ROW_H + 4
        target = self.active
        self._draw_text(draw, (card_left + CARD_PAD, line_y),
                        f"当前目标：{target['label']}", 13,
                        _rgba(target["color"]))
        line_y += INFO_LINE_H

        head, _body = _hint_parts(target["hint"])
        hint_font = _FONT_CACHE.get(HINT_FONT_SIZE)
        if hint_font is None:
            hint_font = label_font(HINT_FONT_SIZE)
            _FONT_CACHE[HINT_FONT_SIZE] = hint_font
        hint_lines = wrap_text(target["hint"], hint_font, inner_w)
        take_head = min(len(hint_lines[0]), len(head)) if head else 0
        for index, line in enumerate(hint_lines):
            x = card_left + CARD_PAD
            if index == 0 and take_head:
                self._draw_text(draw, (x, line_y), line[:take_head],
                                HINT_FONT_SIZE, CARD_BORDER)
                if hint_font is not None:
                    x += int(hint_font.getlength(line[:take_head]))
                self._draw_text(draw, (x, line_y), line[take_head:],
                                HINT_FONT_SIZE, TEXT_DIM)
            else:
                self._draw_text(draw, (x, line_y), line, HINT_FONT_SIZE,
                                TEXT_DIM)
            line_y += HINT_LINE_H
        # 说明行数不足 3 行时把底部信息顶到固定位置（卡片高度已经算过换行数）。
        line_y = (card_bottom - CARD_PAD - SAVE_BTN_H - 8
                  - 3 * INFO_LINE_H)

        box = target["box"]
        # 第一行 = 现在框在**屏幕上**的位置（跟着炉石窗口实时变）；
        # 第二行 = 权威的**客户区相对**坐标（存进 ui_config.json 的就是它，
        #          窗口移动/换分辨率后它不变，所以它才是"校对准了没有"的依据）。
        self._draw_text(draw, (card_left + CARD_PAD, line_y),
                        f"屏幕：{box[0]},{box[1]} → {box[2]},{box[3]}"
                        f"（{box[2] - box[0]}×{box[3] - box[1]}）", 13, TEXT_MAIN)
        self._draw_text(draw, (card_left + CARD_PAD, line_y + INFO_LINE_H),
                        self._client_box_line(target), 13, TEXT_DIM)
        if time.time() < self.saved_flash_until:
            self._draw_text(draw,
                            (card_left + CARD_PAD, line_y + 2 * INFO_LINE_H),
                            "已保存，下次「开始对战」生效", 13, TEXT_OK)
        else:
            self._draw_text(draw,
                            (card_left + CARD_PAD, line_y + 2 * INFO_LINE_H),
                            self._client_line(mapping), 13, TEXT_DIM)

        # ---- 底部两个按钮：保存（金） / 退出（灰） ----------------------
        for name, text, base, hover in (
                ("save", "保存全部区域 (S)", BUTTON_BG, BUTTON_HOVER_BG),
                ("exit", "退出 (Esc)", BUTTON_EXIT_BG, BUTTON_EXIT_HOVER_BG)):
            rect = (self._save_rect(width, height) if name == "save"
                    else self._exit_rect(width, height))
            bg = hover if self.hover == (name, None) else base
            draw.rectangle(rect, fill=bg, outline=CARD_BORDER, width=1)
            self._draw_text(draw, (rect[0] + 12, rect[1] + 8), text, 15,
                            BUTTON_TEXT)

    @staticmethod
    def _client_line(mapping=None) -> str:
        """一行客户区几何（给画在卡片底部的提示用）。"""
        if mapping is None:
            try:
                from display_mapping import display_mapping
                mapping = display_mapping()
            except Exception:
                mapping = None
        if mapping is None:
            return "客户区未知（炉石未运行）· S 保存 · Esc 退出"
        return (f"客户区 {mapping.width}×{mapping.height} @屏幕"
                f"({mapping.screen_left},{mapping.screen_top}) · S 保存 · Esc 退出")

    def _client_box_line(self, target, mapping=None) -> str:
        """一行"这个框的客户区相对坐标是多少"（存进 ui_config.json 的权威值）。

        直接用 `client_box`（而不是把屏幕框换算回去）：屏幕框每帧都在跟着窗口
        变，唯独客户区相对坐标是稳定的，用户校准时看的就是它。
        """
        client = target.get("client_box")
        if client is None:
            return "客户区：与上面相同"
        client = tuple(int(v) for v in client)
        screen = tuple(int(v) for v in target["box"])
        if client == screen:
            return "客户区：与上面相同"
        return (f"客户区：{client[0]},{client[1]} → "
                f"{client[2]},{client[3]}")

    def _draw_preview(self, draw, width: int, layer) -> None:
        left, top, right, bottom = self._preview_rect(width)
        draw.rectangle((left, top, right, bottom), fill=PANEL_BG,
                       outline=PANEL_BORDER, width=2)
        self._draw_text(draw, (left + 10, top + 8),
                        "推荐区域预览（OCR 实时识别）", 14, TEXT_MAIN)
        img_top = top + 32
        area_w = right - left - 20
        crop = self.last_crop
        if crop is not None and crop.size[0] > 0 and crop.size[1] > 0:
            scale = min(area_w / crop.size[0], PREVIEW_IMG_H / crop.size[1])
            target_size = (max(1, int(crop.size[0] * scale)),
                           max(1, int(crop.size[1] * scale)))
            resized = crop.resize(target_size, Image.LANCZOS).convert("RGBA")
            layer.alpha_composite(resized, dest=(left + 10, img_top))
        else:
            self._draw_text(draw, (left + 12, img_top + 8),
                            "等待画面……（拖动后自动刷新）", 12, TEXT_DIM)
        draw.rectangle((left + 10, img_top, left + 10 + area_w,
                        img_top + PREVIEW_IMG_H), outline=PANEL_BORDER)
        # OCR 结果
        result = self.ocr_result
        status = result.get("status")
        text_y = img_top + PREVIEW_IMG_H + 8
        if status == "ok":
            for index, line in enumerate((result.get("lines") or [])[:6]):
                color = (TEXT_OK if ("打法参考A" in line
                                     or "打法参考Ａ" in line) else TEXT_MAIN)
                self._draw_text(draw, (left + 12, text_y + index * 17),
                                line[:34], 13, color)
            msg_y = text_y + 6 * 17 + 2
            self._draw_text(draw, (left + 12, msg_y), result.get("msg", ""), 12,
                            TEXT_DIM)
            if result.get("beacon"):
                self._draw_text(draw, (left + 12, msg_y + 18),
                                "识别到『打法参考A』→ 对齐成功", 13, TEXT_OK)
            else:
                self._draw_text(draw, (left + 12, msg_y + 18),
                                "没读到『打法参考A』：把面板框进绿框", 13,
                                TEXT_WARN)
        else:
            color = TEXT_BAD if status == "error" else TEXT_DIM
            self._draw_text(draw, (left + 12, text_y), result.get("msg", ""), 13,
                            color)

    # ------------------------------------------------------------ 按键
    def _handle_keys(self) -> None:
        escape = bool(USER32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
        if escape and not self._es_held:
            self.running = False
        self._es_held = escape

        s_key = bool(USER32.GetAsyncKeyState(VK_S) & 0x8000)
        if s_key and not self._s_held:
            self.save(flash=True)
        self._s_held = s_key

        tab = bool(USER32.GetAsyncKeyState(VK_TAB) & 0x8000)
        if tab and not self._tab_held:
            self.cycle(1)
        self._tab_held = tab

    def _handle_message(self, width: int, height: int) -> None:
        msg = self._msg
        if msg.message in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE):
            sx = ctypes.c_short(msg.lParam & 0xFFFF).value
            sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            if msg.message == WM_LBUTTONDOWN:
                self._on_mouse_down(sx, sy, width, height)
            elif msg.message == WM_LBUTTONUP:
                self._on_mouse_up(sx, sy, width, height)
            else:
                self._on_mouse_move(sx, sy, width, height)
        elif msg.message == WM_RBUTTONDOWN:
            self.running = False
        else:
            USER32.TranslateMessage(ctypes.byref(msg))
            USER32.DispatchMessageW(ctypes.byref(msg))

    # ------------------------------------------------------------ 鼠标穿透
    def _sync_click_through(self, width: int, height: int) -> None:
        """只有光标压在框/手柄/按钮上时才拦鼠标，其余时候点得到炉石。"""
        window = self.window
        if window is None:
            return
        point = wintypes.POINT()
        if not USER32.GetCursorPos(ctypes.byref(point)):
            return
        interactive = (self.drag is not None
                       or self._hit_test(point.x, point.y, width, height)
                       is not None)
        window.set_click_through(not interactive)

    # ------------------------------------------------------------ 主循环
    def run(self) -> int:
        self.width = int(USER32.GetSystemMetrics(0))
        self.height = int(USER32.GetSystemMetrics(1))
        window = LayeredWindow("HSLegendArriver 校准", CLASS_NAME,
                               click_through=True)
        if not window.open(self.width, self.height):
            print("[校准] 创建窗口失败")
            return 1
        self.window = window
        print(f"[校准] 屏幕 {self.width}x{self.height}，可校准区域：")
        for index, target in enumerate(self.targets, start=1):
            print(f"[校准]   {index}. {target['label']:<16}"
                  f" {target['config_key']:<22} = {tuple(target['box'])}")
        print("[校准] Tab 切换目标 · 拖框/拖右下角手柄调整 · S 保存 · Esc 退出"
              "（空白处鼠标穿透，不影响你操作炉石）")
        self._start_ocr_thread()
        deadline = time.time() + 1.3 if self.selftest else None
        next_panel_check = 0.0
        next_ocr = 0.0
        last_paint = 0.0
        try:
            while self.running:
                while USER32.PeekMessageW(ctypes.byref(self._msg), None, 0, 0,
                                          PM_REMOVE):
                    self._handle_message(self.width, self.height)
                    if not self.running:
                        break
                if not self.running:
                    break
                self._handle_keys()
                if not self.running:
                    break

                now = time.time()
                if now >= next_panel_check:
                    next_panel_check = now + PANEL_REFRESH_SECONDS
                    state = panel_state(self._preview_config())
                    if state is not self.panel_state:
                        self.panel_state = state
                        self.dirty = True
                    # 当前目标自己的判定（只有识别类目标有）：拖框时当场看准没准。
                    before = self.target_status
                    self._check_active_target()
                    if self.target_status != before:
                        self.dirty = True
                # OCR 只在校准「推荐面板」时自动跑：拖别的框时别占满 CPU。
                if (self.ocr_enabled and self.active["key"] == "recommendation"
                        and now >= next_ocr):
                    next_ocr = now + OCR_REFRESH_SECONDS
                    self._capture_for_ocr()

                if self.dirty or now - last_paint >= 0.3:
                    window.blit(self.render(self.width, self.height))
                    self.dirty = False
                    last_paint = now
                self._sync_click_through(self.width, self.height)

                if deadline is not None and time.time() > deadline:
                    break
                time.sleep(0.03)
        finally:
            self.running = False
            window.close()
            self.window = None
        print("[校准] 已退出。")
        return 0


def main() -> int:
    args = sys.argv[1:]
    selftest = "--selftest" in args
    ocr_enabled = "--no-ocr" not in args
    start_key = ""
    if "--target" in args:
        index = args.index("--target")
        if len(args) > index + 1:
            start_key = args[index + 1]
    session = CalibrationSession(selftest=selftest, ocr_enabled=ocr_enabled,
                                 start_key=start_key)
    try:
        code = session.run()
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    if selftest:
        print("[校准] selftest OK")
    return code


if __name__ == "__main__":
    sys.exit(main())
