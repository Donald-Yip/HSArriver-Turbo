# -*- coding: utf-8 -*-
"""炉石界面识别 + 从主界面走进对战（认「传统对战」/「开始」/ 9 个卡组）。

只认文字
------------------------------------------------------------------
判定用**灰度 NCC**（`cv2.matchTemplate` + `TM_CCOEFF_NORMED`）：它先把窗口与模板
各自减掉均值再求相关，所以平坦的背景（银条、木纹）**不参与打分**，打分的只有笔画。

先试过"整条银条 + `TM_SQDIFF_NORMED`"，不行的原因很具体：平坦银色会抬高归一化的
分母、把所有分数压向 0 —— 真值 0.000 与旁边同款银条的「酒馆战棋」0.131 只差 7 倍，
还得靠限制搜索半径去躲邻居。换成 NCC + 只框文字后是 1.000 vs 0.461，而且
**根本不在框外找**。

实测（1920×1080 客户区，取自用户的两张截图，窗口 = 框 ± `SEARCH_RADIUS`）：

| 模板 | 该在的界面 | 反例界面 | 最强同屏反例 | 模糊3×3 | 噪声±6 |
| --- | --- | --- | --- | --- | --- |
| 「传统对战」文字 259×27 | **1.0000** @(961,330) | 0.3105（选套牌界面） | 0.461（竞技模式） | 0.926 | 0.998 |
| 「开始」按钮 150×70 | **1.0000** @(1400,900) | 0.1625（主界面） | — | 0.954 | — |

另一条实测同样重要：**全平/全黑的画面，`TM_CCOEFF_NORMED` 给 0.0000**（OpenCV 对
零方差窗口返回 0）。所以"游戏还没画出来"的白屏/黑屏不会被误判成主界面 —— 这是
"判定通过就会去点按钮"所依赖的安全性质，回归测试里锁住了。

框的语义
------------------------------------------------------------------
**模板 = 那几个字的样子（固定，不认识框）；框 = 到哪儿去找它（框 ±
`SEARCH_RADIUS`，认到就点**认到的位置**，不是框中心）。**

这样分工是为了安全：模板是"身份"，所以框拖错地方只会**找不到**（不点），不会
"把酒馆战棋认成传统对战然后点进去"—— 试过"按框裁模板"的自洽写法，那会让用户在
「酒馆战棋」上随便框一下也被判成主界面，反而危险。

±40px 这个窗口是量出来的：既能容下"框拖得不太准"，也刚好把同屏隔壁的
「酒馆战棋」（在下方 77px）挡在窗口外；用户 `ui_config.json` 里已校准的
`post_game_start_roi`(1314,842,1483,928) 与模板裁切用的代码默认框差 23px，
±40 一样能认到 1.0000（±5 就只有 0.481）。

皮肤/补丁把字改了颜色或反色之后，分数会掉下来（校准窗口里直接能看到），
用 `learn_template()`（`hsctl ui --learn`）**按当前框**从当前画面重裁模板即可。

坐标口径：全部是**客户区相对坐标**（基准 1920×1080，见 `display_mapping`）。
点击统一走 `click.left_click`（换算由它负责）；识别前把帧归一到 1920×1080，
所以换分辨率/窗口位置都不改变判定值。

边界：本模块**不 import FSM_action / web_ui**（避免循环依赖），也不强制 import 任何
重模块；抓图、点击、sleep、时钟、拉起炉石全部可注入，方便离线单测。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent

# 所有硬编码坐标的口径：炉石客户区基准尺寸（见 display_mapping.REFERENCE_CLIENT）。
REFERENCE_CLIENT = (1920, 1080)

# ---------------------------------------------------------------- 可校准框的默认值
# 主界面「传统对战」**四个字**的位置（实测：字 (835,320)-(1088,341)，外扩 3px）。
MAIN_MENU_BATTLE_DEFAULT = (832, 317, 1091, 344)
# 选择套牌界面顶部「选择套牌」**四个字**的位置（实测 2026-10-11 截图：
# 文字 (674,126)-(772,144)，外扩 3px）。
# 它是"停在选卡组界面"的**主**判定依据：标题始终在，而右下角「开始」在没选中
# 卡组时不出现 —— 只认「开始」会让脚本一直认不出界面、反复点画面中心到超时。
DECK_SELECT_TITLE_CONFIG_KEY = "deck_select_title_roi"
DECK_SELECT_TITLE_DEFAULT = (671, 123, 775, 147)
# 选择套牌界面 9 个卡组整块（实测列 486/729/968、行 318/534/750，3×3 均分）。
DECK_GRID_DEFAULT = (366, 210, 1089, 858)
# 「开始」按钮复用已经可校准的 post_game_start_roi（选套牌与结算界面同一个按钮）。
# **不再单独用于界面判定**，只在点「开始」时定位（识别交给上面的标题）。
START_BOX_CONFIG_KEY = "post_game_start_roi"
START_BOX_DEFAULT = (1325, 865, 1475, 935)

# ---------------------------------------------------------------- 判定参数
# 匹配分阈值：实测真值 1.0000，反例界面 0.3105 / 0.1625，最强同屏反例（隔壁
# 「酒馆战棋」）0.461 —— 0.80 落在 2.6 倍余量处，两边都留足。
NCC_MIN = 0.80
# 框内局部搜索半径：吸收"框拖得不太准"与亚像素抖动。
# 实测：±40 能容下用户已校准框与默认框之间 23px 的差值（1.0000），
# 同时刚好把同屏隔壁的「酒馆战棋」（下方 77px）挡在窗口外。
SEARCH_RADIUS = 40
# 模板"太干净"就是没框到字（一片平坦）——这种模板永远匹配不上，直接报原因。
MIN_TEMPLATE_STD = 8.0
# 真界面上的模板一定含深色像素（字/描边）：实测自带模板 8.1% 的像素 <100，
# 而"炉石还没画出来"的白屏只有 0.00%~0.24%。重裁模板（--learn）前用它把关，
# 免得在没渲染的画面上把一个能用的模板覆盖成废图。
MIN_TEMPLATE_DARK_RATIO = 0.02

# 卡组网格
DECK_ROWS = 3
DECK_COLS = 3
# 名牌文字带：每格中心上下各这么多像素（实测名牌白字都落在这条带里）。
DECK_PLATE_BAND = 28
# 单格"像卡组"的墨迹下限：实测真卡组格 207~585 像素，主界面同位置只有 3/9 格达标。
DECK_PLATE_MIN_PIXELS = 150
# 名牌是**暗色**半透明条 + 白字：实测真实卡组格的名牌带平均亮度只有 51~86，
# 而"炉石还没画出来"的白屏是 255（std=0）。所以除了白字数量，还要求这条带本身是暗的
# —— 否则一整块白屏会被当成 9 个卡组（真机上实测到过这个假阳性）。
DECK_PLATE_MAX_MEAN = 170
# 至少这么多格像卡组，才算"卡组网格在"（用来提示可能是结算界面）。
DECK_MIN_SLOTS_FOUND = 6

# 没认出界面时点的那个"安全中心点"：**客户区相对坐标**。
# 刻意不用 click.click_middle() 的 (960,500)：那个点离「竞技模式」按钮底边只有 3px，
# 抖动一下就会误点进竞技场。这里距「其他模式」底边(y≈575) 75px、
# 距「开包/我的收藏」行顶边(y≈740) 90px，实测是纯木纹背景（浅灰占比 0.00）。
CENTER_DISMISS_POINT = (960, 650)

# ---------------------------------------------------------------- 时序
# 识别轮询间隔：0.5s（原来 3s）。界面画出来就立刻往下走，不用等满 3 秒；
# 抓一帧 + NCC 只有几十毫秒，0.5s 一次的 CPU 开销可以忽略。
CHECK_INTERVAL_SECONDS = 0.5
# 没认出界面时「点一下画面中心」的最小间隔：点中心是为了关掉挡住文字的弹窗/
# 公告，不是探测手段——0.5s 轮询不能变成连点，仍然保持「最多每 3 秒一下」。
CENTER_CLICK_INTERVAL_SECONDS = 3.0
MAIN_MENU_TIMEOUT_SECONDS = 180.0
DECK_SELECT_TIMEOUT_SECONDS = 90.0

# ---------------------------------------------------------------- 取消信号
# 手动流程（进入对战 / 重启后自动恢复）可能连着点几十秒到几分钟。用户按 Ctrl+C、
# 在网页点「停止」、或者 agent 说「取消」时，必须**立刻停手**，不能再点一下。
# 这里用一个进程级事件做「急停开关」：任何等待/点击循环每轮都问一次，置位就退出。
# 单次点击类动作（deck-click / mode）本身就是一遍过，不需要它。
_CANCEL = threading.Event()


def request_cancel() -> None:
    """置位急停开关：所有正在等待/点击的手动流程在下一轮（≤ interval）退出。"""
    _CANCEL.set()


def clear_cancel() -> None:
    """清除急停开关。**每个新手动流程开始前必须调**，否则上一次的取消会立刻生效。"""
    _CANCEL.clear()


def is_cancelled() -> bool:
    """当前是否处于「已请求取消」状态（也直接当 should_abort 回调用）。"""
    return _CANCEL.is_set()


# ---------------------------------------------------------------- 模板资源
TEMPLATE_DIR = ROOT / "assets" / "ui_templates"
TEMPLATE_FILE = {
    "traditional": "traditional_text.png",
    "deck_select_title": "deck_select_title_text.png",
    "start": "start_text.png",
}

# 失败原因 → 一句能直接照做的话（`hsctl ui` 与校准窗口都显示它）。
REASON_TEXT = {
    "template_missing": "还没有模板：站到对应界面跑一次 hsctl ui --learn",
    "template_flat": "框里没有文字（一片平坦）：把框拖到字上再学一次",
    "unknown_template": "不认识的模板类型",
    "box_out_of_frame": "框跑到画面外了",
    "low_score": "有模板但没匹配上（框偏了或界面变了）",
}

_TEMPLATE_CACHE: dict = {}


# ================================================================ 配置取值
def _default_config():
    """拿一份配置：优先自动化主循环**正在用的那一份**，没有才新建。

    只用 `sys.modules` 里已有的 `FSM_action`（不主动 import 它）——避免为了读一个
    ROI 就把键盘钩子/OCR 那堆重模块拉起来；`get_state()` 这条热路径也就不用每次
    重新读盘。
    """
    module = sys.modules.get("FSM_action")
    if module is not None:
        cfg = getattr(module, "recommendation_config", None)
        if cfg is not None:
            return cfg
    try:
        from config import RecommendationConfig
        return RecommendationConfig()
    except Exception:
        return None


def _roi(config, key: str, default) -> tuple:
    """读一个可校准框（用户校准优先 → 代码默认），非法值回退默认。"""
    cfg = config if config is not None else _default_config()
    value = getattr(cfg, key, None) if cfg is not None else None
    try:
        box = tuple(int(v) for v in value)
        if len(box) == 4 and box[0] < box[2] and box[1] < box[3]:
            return box
    except Exception:
        pass
    return tuple(int(v) for v in default)


def traditional_box(config=None) -> tuple:
    """主界面「传统对战」文字框（客户区相对坐标）。"""
    return _roi(config, "main_menu_battle_roi", MAIN_MENU_BATTLE_DEFAULT)


def deck_select_title_box(config=None) -> tuple:
    """选择套牌界面顶部「选择套牌」标题框（客户区相对坐标）。"""
    return _roi(config, DECK_SELECT_TITLE_CONFIG_KEY, DECK_SELECT_TITLE_DEFAULT)


def deck_grid_box(config=None) -> tuple:
    """选择套牌界面 9 个卡组的整块区域（客户区相对坐标）。"""
    return _roi(config, "deck_grid_roi", DECK_GRID_DEFAULT)


def start_box(config=None) -> tuple:
    """「开始」按钮框（复用已可校准的 post_game_start_roi）。"""
    return _roi(config, START_BOX_CONFIG_KEY, START_BOX_DEFAULT)


def battle_entry_settings() -> dict:
    """ui_config.json 的 battle_entry 段（读不到就用默认值）。"""
    try:
        from config import battle_entry_settings as _settings
        return _settings()
    except Exception:
        return {"deck_index": None,
                "check_interval_seconds": CHECK_INTERVAL_SECONDS,
                "main_menu_timeout_seconds": MAIN_MENU_TIMEOUT_SECONDS,
                "deck_select_timeout_seconds": DECK_SELECT_TIMEOUT_SECONDS}


# ================================================================ 卡组几何
def deck_bounds(config=None) -> list:
    """9 个卡组的矩形 `[(l,t,r,b) × 9]`，row-major（1,2,3 = 第一行）。"""
    left, top, right, bottom = deck_grid_box(config)
    cell_w = (right - left) / float(DECK_COLS)
    cell_h = (bottom - top) / float(DECK_ROWS)
    boxes = []
    for row in range(DECK_ROWS):
        for col in range(DECK_COLS):
            boxes.append((int(round(left + col * cell_w)),
                          int(round(top + row * cell_h)),
                          int(round(left + (col + 1) * cell_w)),
                          int(round(top + (row + 1) * cell_h))))
    return boxes


def deck_slot_point(index: int, config=None) -> tuple:
    """第 `index`（1-9）个卡组的点击点 = 该格中心（客户区相对坐标）。"""
    bounds = deck_bounds(config)
    try:
        value = int(index)
    except (TypeError, ValueError):
        raise ValueError(f"卡组序号必须是 1-{len(bounds)} 的整数")
    if not 1 <= value <= len(bounds):
        raise ValueError(f"卡组序号必须是 1-{len(bounds)}，收到 {index!r}")
    left, top, right, bottom = bounds[value - 1]
    return ((left + right) // 2, (top + bottom) // 2)


def deck_slot_points(config=None) -> list:
    return [deck_slot_point(i, config) for i in range(1, DECK_ROWS * DECK_COLS + 1)]


# ================================================================ 图像工具
def _gray(frame):
    """转灰度；兼容 BGRA（catch_screen 出的就是 4 通道）与 BGR。"""
    import cv2
    if frame is None:
        return None
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame[:, :, :3], cv2.COLOR_BGR2GRAY)
    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def to_reference(frame, mapping=None):
    """4 通道 → BGR，并把帧归一到基准 1920×1080（本来就对时是恒等）。"""
    import cv2
    if frame is None:
        return None
    if frame.ndim == 3 and frame.shape[2] == 4:
        frame = frame[:, :, :3]
    height, width = frame.shape[:2]
    if (int(width), int(height)) == REFERENCE_CLIENT:
        return frame
    # 放大时不能用 INTER_AREA（OpenCV 放大时它≈最近邻，会出块状，模板匹配
    # 分数明显掉）；INTER_AREA 只适合降采样。
    upscaling = REFERENCE_CLIENT[0] * REFERENCE_CLIENT[1] > width * height
    interpolation = cv2.INTER_CUBIC if upscaling else cv2.INTER_AREA
    return cv2.resize(frame, REFERENCE_CLIENT, interpolation=interpolation)


def load_template(kind: str = "traditional"):
    """返回 `(灰度模板, 原因)`；原因非 None 表示模板不可用。

    缓存住，避免每帧读盘。模板是**灰度** PNG（`learn_template` 也按灰度写），
    与 `_gray(帧)` 同口径。
    """
    if kind in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[kind]
    name = TEMPLATE_FILE.get(kind)
    result = (None, "unknown_template")
    if name:
        try:
            import cv2
            import numpy as np
            path = TEMPLATE_DIR / name
            data = np.fromfile(str(path), dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if data.size else None
            if image is None:
                result = (None, "template_missing")
            elif float(image.std()) < MIN_TEMPLATE_STD:
                result = (None, "template_flat")
            else:
                result = (image, None)
        except Exception:
            result = (None, "template_missing")
    _TEMPLATE_CACHE[kind] = result
    return result


def clear_template_cache(kind: Optional[str] = None) -> None:
    """丢掉模板缓存（重裁模板后必须调，否则还是旧的）。"""
    if kind is None:
        _TEMPLATE_CACHE.clear()
    else:
        _TEMPLATE_CACHE.pop(kind, None)


def match_score(patch_gray, template, radius: int = SEARCH_RADIUS) -> tuple:
    """在 patch 里找 template，返回 `(最大 NCC, 相对 patch 左上角的最佳偏移)`。

    `patch_gray` 必须是"框 ± radius"的灰度图（每边比模板大 radius）。
    零方差窗口 OpenCV 给 0（实测全平/全黑画面 = 0.0000），所以"还没画出来"的
    白屏/黑屏不会被判成命中。任何异常都回退成"没命中"，绝不打断主循环。
    """
    import cv2
    miss = (-1.0, (int(radius), int(radius)))
    if patch_gray is None or template is None:
        return miss
    if (patch_gray.shape[0] < template.shape[0]
            or patch_gray.shape[1] < template.shape[1]):
        return miss
    try:
        patch = patch_gray.astype("float32", copy=False)
        tmpl = template.astype("float32", copy=False)
        res = cv2.matchTemplate(patch, tmpl, cv2.TM_CCOEFF_NORMED)
        _min, maximum, _minloc, maxloc = cv2.minMaxLoc(res)
    except Exception:
        return miss
    if maximum != maximum:          # NaN：拿不准就当没命中
        return miss
    return (float(maximum), (int(maxloc[0]), int(maxloc[1])))


def _visible(gray, box, kind: str) -> dict:
    """在 `box ± SEARCH_RADIUS` 内找 `kind` 的模板，返回证据 dict。"""
    left, top, right, bottom = (int(v) for v in box)
    template, reason = load_template(kind)
    evidence = {
        "visible": False,
        "score": -1.0,
        "box": [left, top, right, bottom],
        "point": [(left + right) // 2, (top + bottom) // 2],
        "reason": reason,
    }
    if template is None or gray is None:
        return evidence
    height, width = gray.shape[:2]
    radius = SEARCH_RADIUS
    x0, y0 = max(0, left - radius), max(0, top - radius)
    x1, y1 = min(width, right + radius), min(height, bottom + radius)
    if (x1 - x0) < template.shape[1] or (y1 - y0) < template.shape[0]:
        evidence["reason"] = "box_out_of_frame"
        return evidence
    score, offset = match_score(gray[y0:y1, x0:x1], template, radius)
    evidence["score"] = round(float(score), 4)
    if score >= 0:
        evidence["point"] = [int(x0 + offset[0] + template.shape[1] // 2),
                            int(y0 + offset[1] + template.shape[0] // 2)]
    evidence["visible"] = bool(score >= NCC_MIN)
    if not evidence["visible"] and score >= NCC_MIN / 2:
        evidence["reason"] = "low_score"
    return evidence


def plate_pixels(frame_bgr, config=None) -> list:
    """每个卡组格里「名牌白字」的像素数（判断这一格到底是不是卡组）。

    名牌 = **暗色**半透明条 + 白字，所以两个条件都要：这条带是暗的
    （`DECK_PLATE_MAX_MEAN`，挡住"整块白屏"这种假阳性），且带里有足够白字。
    """
    if frame_bgr is None:
        return [0] * (DECK_ROWS * DECK_COLS)
    import numpy as np
    # 帧本身不定是基准尺寸（换分辨率时是客户区原生像素），这里按基准取带。
    frame = to_reference(frame_bgr)
    out = []
    for left, top, right, bottom in deck_bounds(config):
        center_y = (top + bottom) // 2
        y0 = max(0, center_y - DECK_PLATE_BAND)
        y1 = min(frame.shape[0], center_y + DECK_PLATE_BAND)
        x0, x1 = max(0, left), min(frame.shape[1], right)
        band = frame[y0:y1, x0:x1]
        if band.size == 0:
            out.append(0)
            continue
        band = band.astype(np.int16)
        if float(band.mean()) > DECK_PLATE_MAX_MEAN:
            out.append(0)           # 这条带是亮的：不是名牌（白屏 / 还没渲染出来）
            continue
        high = band.max(axis=2)
        low = band.min(axis=2)
        out.append(int(np.count_nonzero((low > 185) & ((high - low) < 55))))
    return out


def deck_slot_looks_like_deck(frame_bgr, index: int, config=None) -> bool:
    """第 `index` 格看起来像不像一个卡组（名牌有白字）。"""
    counts = plate_pixels(frame_bgr, config)
    try:
        value = int(index)
    except (TypeError, ValueError):
        return False
    if not 1 <= value <= len(counts):
        return False
    return counts[value - 1] >= DECK_PLATE_MIN_PIXELS


# ================================================================ 识别
def identify(frame_bgr, config=None) -> dict:
    """认一次界面：`main_menu` / `deck_select` / `unknown`（外加全部证据）。

    判定顺序：**先看「选择套牌」标题**（选卡组界面**独有**，且没选中卡组时也在），
    **再看「传统对战」**（主界面独有）。「开始」按钮只作为选卡组界面的**兜底**
    证据返回，不单独决定状态 —— 它在没选中卡组时不出现，只认它会让脚本一直
    认不出界面、反复点画面中心直到超时（用户 2026-10-11 反馈的死循环）。
    """
    result = {
        "state": "unknown",
        "traditional": None,
        "deck_title": None,
        "start": None,
        "deck": {"slots_found": 0,
                 "slot_points": deck_slot_points(config),
                 "plate_pixels": [0] * (DECK_ROWS * DECK_COLS)},
        "note": None,
    }
    frame = to_reference(frame_bgr)
    if frame is None:
        result["note"] = "抓不到炉石画面"
        return result
    gray = _gray(frame)
    traditional = _visible(gray, traditional_box(config), "traditional")
    deck_title = _visible(gray, deck_select_title_box(config), "deck_select_title")
    start = _visible(gray, start_box(config), "start")
    plate = plate_pixels(frame, config)
    slots_found = sum(1 for value in plate if value >= DECK_PLATE_MIN_PIXELS)
    result["traditional"] = traditional
    result["deck_title"] = deck_title
    result["start"] = start
    result["deck"] = {"slots_found": slots_found,
                      "slot_points": deck_slot_points(config),
                      "plate_pixels": plate}
    if deck_title["visible"] or start["visible"]:
        result["state"] = "deck_select"
        if not deck_title["visible"]:
            # 只有「开始」认出来：可能是结算界面，也可能是标题模板还没学。
            result["note"] = ("「选择套牌」标题没认出来（只认到「开始」）："
                              "可能是对局结算界面，或标题模板还没学"
                              "（站到选卡组界面跑 hsctl ui --learn）")
        elif slots_found < DECK_MIN_SLOTS_FOUND:
            result["note"] = (f"只找到 {slots_found}/9 个卡组格：可能是对局结算界面")
    elif traditional["visible"]:
        result["state"] = "main_menu"
    else:
        for item in (traditional, deck_title, start):
            reason = item.get("reason")
            if reason:
                result["note"] = f"{REASON_TEXT.get(reason, reason)}（score={item['score']}）"
                break
    return result


def identify_from_capture(frame, mapping) -> Optional[dict]:
    """给 `get_screen.get_state()` 用的守卫入口：不在客户区口径上就返回 None。

    拿不到客户区几何（炉石最小化/未运行）时 `catch_screen()` 会退回整屏抓图，
    那时这里的框（客户区相对坐标）套不上，硬判会认错；帧尺寸与客户区不一致时
    同理。任何异常都返回 None，让调用方走原来的像素判定。
    """
    if frame is None or mapping is None:
        return None
    try:
        height, width = frame.shape[:2]
        if (int(width), int(height)) != (int(mapping.width), int(mapping.height)):
            return None
    except Exception:
        return None
    try:
        return identify(frame)
    except Exception:
        return None


def capture_client_frame(config=None) -> tuple:
    """抓一帧炉石客户区：`(frame, mapping)`；抓不到返回 `(None, None)`。"""
    try:
        import window_tracker
        window_tracker.ensure_started()
    except Exception:
        pass
    hwnd = hearthstone_hwnd()
    if not hwnd:
        return (None, None)
    try:
        from display_mapping import display_mapping
        mapping = display_mapping(hwnd)
    except Exception:
        mapping = None
    if mapping is None:
        return (None, None)
    try:
        import get_screen
        frame = get_screen.catch_screen(region=mapping.client_box)
    except Exception:
        frame = None
    if frame is None:
        return (None, None)
    return (frame, mapping)


def hearthstone_hwnd() -> int:
    """炉石窗口句柄（没开/拿不到返回 0）。"""
    try:
        from get_screen import get_HS_hwnd
        return int(get_HS_hwnd() or 0)
    except Exception:
        return 0


# ================================================================ 点击出口
def click_at(x, y, *, logger: Optional[Callable[[str], None]] = None) -> None:
    """点一个**客户区相对坐标**（换算与"点完复位鼠标"由 click 负责）。"""
    import click
    click.run_hearthstone_action(lambda: click.left_click(int(x), int(y)))


def click_start_button(*, logger: Optional[Callable[[str], None]] = None) -> dict:
    """点「开始」：走现有 `click.match_opponent`（坐标与历史行为完全一致）。"""
    import click
    click.run_hearthstone_action(click.match_opponent)
    if logger:
        logger("已点击「开始」")
    return {"ok": True, "message": "已点击「开始」"}


def default_launcher(*, logger: Optional[Callable[[str], None]] = None) -> dict:
    """把炉石拉起来（**只启动，不杀进程、不清日志**）。

    复用 `hs_restart.launch_hearthstone`：优先走战网「开始游戏」，连战网都没开就
    先启动战网。与 web_ui 的「重启炉石」是同一套接线，区别只在这里不杀进程。
    """
    log = logger or (lambda text: None)
    try:
        import hs_restart
        from config import HEARTHSTONE_LOG_ROOT
    except Exception as exc:
        return {"ok": False, "error": f"启动炉石不可用：{exc}"}
    try:
        info = hs_restart.resolve_paths(HEARTHSTONE_LOG_ROOT)
    except Exception as exc:
        return {"ok": False, "error": f"解析炉石路径失败：{exc}"}
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error") or "炉石路径不可用"}

    def _window_ready():
        return hearthstone_hwnd() != 0

    def _battlenet_ready():
        try:
            from get_screen import get_battlenet_hwnd
            return bool(get_battlenet_hwnd())
        except Exception:
            return False

    def _via_battlenet():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        import click
        return click.enter_HS()

    return hs_restart.launch_hearthstone(
        info.get("exe"), battlenet_exe=info.get("battlenet_exe"),
        launcher=_via_battlenet, battlenet_ready=_battlenet_ready,
        window_ready=_window_ready, logger=log)


# ================================================================ 等界面
def _capture_frame(capture, config):
    try:
        frame, _mapping = capture(config)
    except Exception:
        return None
    return frame


def _identify_now(capture, config):
    """抓一帧认一下当前界面（不点击）。抓不到/认不出返回 None。"""
    frame = _capture_frame(capture, config)
    if frame is None:
        return None
    try:
        return identify(frame, config)
    except Exception:
        return None


def _wait_for_state(kind: str, *, timeout, interval, capture, click_at_fn, sleep,
                    clock, should_abort, logger, config) -> dict:
    """等某个界面出现：每 `interval` 秒识别一次；没认出且距上次点中心 ≥
    CENTER_CLICK_INTERVAL_SECONDS 时，才点一次画面中心（关掉挡字的弹窗/公告）。

    **安全性质**：只有识别到才返回成功；到超时就退出并说明原因 —— 全程不会
    盲点「传统对战」或卡组格；点中心也保持「最多每 3 秒一下」的原有节奏，
    不会因为轮询变快而连点。
    """
    label = ("主界面（传统对战）" if kind == "main_menu"
             else "选择套牌界面（选择套牌标题）")
    interval = max(0.05, float(interval))
    deadline = clock() + max(0.0, float(timeout))
    polls = 0
    last = None
    last_click = None
    while True:
        if should_abort():
            return {"ok": False, "reason": "aborted", "polls": polls, "ui": last,
                    "message": f"等待{label}时收到停止请求"}
        ui = None
        frame = _capture_frame(capture, config)
        if frame is not None:
            try:
                ui = identify(frame, config)
            except Exception:
                ui = None
        if ui is not None:
            last = ui
            if ui["state"] == kind:
                return {"ok": True, "reason": None, "polls": polls, "ui": ui,
                        "message": f"已识别到{label}"}
        if clock() >= deadline:
            detail = ""
            for item in ((last or {}).get("traditional"), (last or {}).get("start")):
                if item and item.get("score", -1) >= 0:
                    detail = f"（最近一次匹配分 {item['score']}）"
                    break
            return {"ok": False, "reason": f"timeout_{kind}", "polls": polls,
                    "ui": last, "message": f"等待{label}超时{detail}"}
        now = clock()
        if last_click is None or now - last_click >= CENTER_CLICK_INTERVAL_SECONDS:
            click_at_fn(*CENTER_DISMISS_POINT)
            last_click = now
            logger(f"还没识别到{label}，已点击画面中心一次，"
                   f"{interval:.1f}s 后重试……")
        polls += 1
        sleep(interval)


def wait_for_main_menu(*, timeout: float = MAIN_MENU_TIMEOUT_SECONDS,
                       interval: float = CHECK_INTERVAL_SECONDS,
                       capture: Optional[Callable] = None,
                       click_at_fn: Optional[Callable] = None,
                       sleep: Optional[Callable] = None,
                       clock: Optional[Callable] = None,
                       should_abort: Optional[Callable] = None,
                       logger: Optional[Callable] = None,
                       config=None) -> dict:
    """等到主界面（认出「传统对战」）为止；不是就点一次画面中心。

    `should_abort` 缺省用全局急停开关（`hsctl cancel` / 网页「停止」置位），
    所以**默认就是可取消的**。
    """
    return _wait_for_state(
        "main_menu", timeout=timeout, interval=interval,
        capture=capture or capture_client_frame, click_at_fn=click_at_fn or click_at,
        sleep=sleep or time.sleep, clock=clock or time.monotonic,
        should_abort=should_abort or is_cancelled,
        logger=logger or (lambda text: None), config=config)


def wait_for_deck_select(*, timeout: float = DECK_SELECT_TIMEOUT_SECONDS,
                         interval: float = CHECK_INTERVAL_SECONDS,
                         capture: Optional[Callable] = None,
                         click_at_fn: Optional[Callable] = None,
                         sleep: Optional[Callable] = None,
                         clock: Optional[Callable] = None,
                         should_abort: Optional[Callable] = None,
                         logger: Optional[Callable] = None,
                         config=None) -> dict:
    """等到选择套牌界面（认出「选择套牌」标题）为止。"""
    return _wait_for_state(
        "deck_select", timeout=timeout, interval=interval,
        capture=capture or capture_client_frame, click_at_fn=click_at_fn or click_at,
        sleep=sleep or time.sleep, clock=clock or time.monotonic,
        should_abort=should_abort or is_cancelled,
        logger=logger or (lambda text: None), config=config)


# ================================================================ 选卡组
def select_deck(index: int, *, capture: Optional[Callable] = None,
                click_at_fn: Optional[Callable] = None,
                logger: Optional[Callable] = None,
                should_abort: Optional[Callable] = None, config=None) -> dict:
    """点第 `index`（1-9）个卡组。

    **单格保护**：先抓一帧，确认这一格名牌上有白字（真的像个卡组）才点 ——
    否则一格都不点，直接回报原因。这样即使界面不是"选择套牌"（例如结算界面），
    也不会在别的地方乱点。

    抓图/判断之间用户可能已经喊停，所以**真正点下去之前再问一次**急停开关。
    """
    if (should_abort or is_cancelled)():
        return {"ok": False, "cancelled": True, "error": "收到取消请求，未点卡组"}
    try:
        point = deck_slot_point(index, config)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    frame = _capture_frame(capture or capture_client_frame, config)
    if frame is None:
        return {"ok": False, "error": "抓不到炉石客户区（未运行/最小化），不点卡组"}
    count = plate_pixels(frame, config)[int(index) - 1]
    if count < DECK_PLATE_MIN_PIXELS:
        return {"ok": False, "skipped": True, "plate_pixels": count,
                "error": (f"第 {index} 格不像卡组（名牌文字 {count} 像素 < "
                          f"{DECK_PLATE_MIN_PIXELS}），已跳过不点")}
    if (should_abort or is_cancelled)():
        return {"ok": False, "cancelled": True, "error": "收到取消请求，未点卡组"}
    (click_at_fn or click_at)(*point)
    if logger:
        logger(f"已选择第 {index} 个卡组 {point}")
    return {"ok": True, "at": list(point), "plate_pixels": count,
            "message": f"已点第 {index} 个卡组 {point}"}


def select_deck_if_configured(*, settings: Optional[dict] = None,
                              attempts: int = 3, wait: float = 0.3,
                              sleep: Optional[Callable] = None,
                              **kwargs) -> dict:
    """按显式传入的 `deck_index` 选卡组（**自动化已不再调用**）。

    用户 2026-10-23 口径：自动化不再每局自动点卡组，避免覆盖手选；
    选卡组只走遥控显式命令 `hsctl deck-click N` / `enter-battle --deck N`。
    本函数保留给显式调用者/单测使用。

    名牌还没画出来时（`skipped`）短重试几次：调用方原来靠「进界面后先等 2 秒」
    兜住动画，现在那 2 秒去掉了，这里用 3 × 0.3s 重抓代替。重试只重新抓图
    判断、不会点任何东西，硬件/界面不对时行为与以前一致（跳过 + 报原因）。
    """
    cfg = settings if settings is not None else battle_entry_settings()
    index = cfg.get("deck_index")
    if not index:
        return {"ok": True, "skipped": True, "message": "没有配置自动选卡组，跳过"}
    sleep = sleep or time.sleep
    total = max(1, int(attempts))
    result = None
    for attempt in range(total):
        result = select_deck(int(index), **kwargs)
        if result.get("ok") or not result.get("skipped"):
            return result
        if attempt < total - 1:
            sleep(max(0.0, float(wait)))
    return result


# ================================================================ 一键进入对战
def enter_battle(*, deck_index=None, click_start: bool = False,
                 interval: float = CHECK_INTERVAL_SECONDS,
                 main_menu_timeout: float = MAIN_MENU_TIMEOUT_SECONDS,
                 deck_select_timeout: float = DECK_SELECT_TIMEOUT_SECONDS,
                 capture: Optional[Callable] = None,
                 click_at_fn: Optional[Callable] = None,
                 start_clicker: Optional[Callable] = None,
                 sleep: Optional[Callable] = None,
                 clock: Optional[Callable] = None,
                 should_abort: Optional[Callable] = None,
                 logger: Optional[Callable] = None,
                 launcher: Optional[Callable] = None,
                 window_present: Optional[Callable] = None,
                 config=None) -> dict:
    """从"炉石没开 / 停在一个不确定的界面"一路走到**选择套牌界面**（可顺带点「开始」）。

    顺序（每一步都记进返回的 `steps`，含坐标，便于事后核对有没有乱点）::

        launch → wait_main_menu → click_traditional → wait_deck_select
               → select_deck（可选）→ click_start（可选）

    任何一步识别不出来都只点画面中心，到超时就退出并说明原因 —— **绝不盲点**。
    默认 `click_start=False`：点「开始」等于开始排队，交给调用方（FSM 的
    `ChoosingHeroAction` 会先做「段位目标」判定，顺序不能反）。

    **可随时取消**：`should_abort` 缺省读全局急停开关（`request_cancel()` /
    `hsctl cancel`），置位后下一次轮询（≤ interval）就停手，且不会再点任何
    坐标；返回 `stage="aborted"`。
    """
    capture = capture or capture_client_frame
    click_at_fn = click_at_fn or click_at
    start_clicker = start_clicker or click_start_button
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    abort = should_abort or is_cancelled
    log = logger or (lambda text: None)
    steps = []
    started = clock()

    def record(stage, at=None, detail=""):
        steps.append({"stage": stage,
                      "at": [int(v) for v in at] if at else None,
                      "detail": detail})

    def done(ok, stage, message):
        return {"ok": bool(ok), "stage": stage, "message": message,
                "steps": steps, "elapsed": round(clock() - started, 2)}

    # ① 炉石没开就先拉起来（只启动，不杀进程、不清日志）
    present = window_present or (lambda: hearthstone_hwnd() != 0)
    if not present():
        log("没有找到炉石窗口，先把炉石拉起来……")
        launch = launcher or default_launcher
        try:
            launched = launch(logger=log)
        except TypeError:
            launched = launch()
        except Exception as exc:
            launched = {"ok": False, "error": str(exc)}
        if not (launched or {}).get("ok"):
            error = (launched or {}).get("error") or "未知原因"
            record("launch", None, error)
            return done(False, "launch_failed", f"炉石没能启动：{error}")
        record("launch", None, launched.get("message") or "已发出启动命令")
    else:
        record("launch", None, "炉石已在运行")

    # ② 已经停在「选择套牌」界面就直接从选卡组接手
    #    这里必须**先认一眼当前界面**：原来一律先等主界面，如果人已经站在选套牌
    #    界面，就会白等 180 秒、期间还一遍遍点画面中心（用户 2026-10-17 反馈的
    #    「让它取消它还在点」也是这条路上的）。认到选套牌就直接跳过 ②③④。
    here = _identify_now(capture, config)
    if here is not None and here.get("state") == "deck_select":
        ui = here
        record("already_deck_select", ui["deck_title"]["point"],
               "当前已经在选择套牌界面，跳过「等主界面 → 点传统对战」")
        log("当前已在选择套牌界面，直接选卡组。")
    else:
        # ② 等主界面（认出「传统对战」）
        waited = wait_for_main_menu(timeout=main_menu_timeout, interval=interval,
                                    capture=capture, click_at_fn=click_at_fn,
                                    sleep=sleep, clock=clock,
                                    should_abort=abort, logger=log, config=config)
        if not waited["ok"]:
            record("wait_main_menu", None, waited["message"])
            stage = ("aborted" if waited["reason"] == "aborted"
                     else "timeout_main_menu")
            return done(False, stage, waited["message"])
        point = waited["ui"]["traditional"]["point"]
        record("wait_main_menu", point, waited["message"])

        # ③ 点「传统对战」
        if abort():
            return done(False, "aborted", "收到停止请求，未点击「传统对战」")
        click_at_fn(*point)
        record("click_traditional", point, "已点击「传统对战」（框内匹配到的位置）")
        log(f"已点击「传统对战」{tuple(point)}，等待选择套牌界面……")

        # ④ 等「开始」按钮出现 = 已进入选择套牌界面
        waited = wait_for_deck_select(timeout=deck_select_timeout,
                                      interval=interval,
                                      capture=capture, click_at_fn=click_at_fn,
                                      sleep=sleep, clock=clock,
                                      should_abort=abort, logger=log,
                                      config=config)
        if not waited["ok"]:
            record("wait_deck_select", None, waited["message"])
            stage = ("aborted" if waited["reason"] == "aborted"
                     else "timeout_deck_select")
            return done(False, stage, waited["message"])
        ui = waited["ui"]
        record("wait_deck_select", ui["deck_title"]["point"], waited["message"])

    # ⑤ 选卡组（可选，带单格保护）
    if deck_index:
        picked = select_deck(int(deck_index), capture=capture,
                             click_at_fn=click_at_fn, logger=log,
                             should_abort=abort, config=config)
        record("select_deck", picked.get("at"),
               picked.get("message") or picked.get("error") or "")
        if picked.get("cancelled"):
            return done(False, "aborted", "收到取消请求，已停在「选卡组」这一步")
        if not picked.get("ok"):
            log(f"卡组没有点：{picked.get('error')}")

    # ⑥ 点「开始」（可选）
    if click_start:
        if abort():
            return done(False, "aborted", "收到停止请求，未点击「开始」")
        started_click = start_clicker(logger=log)
        record("click_start", ui["start"]["point"],
               (started_click or {}).get("message") or "已点击「开始」")

    tail = "并点了「开始」" if click_start else "（未点「开始」）"
    return done(True, "done", f"已进入选择套牌界面{tail}")


# ================================================================ 学模板
def learn_template(kind: str = "all", *, frame=None, config=None) -> dict:
    """按当前（可校准）框从**当前画面**重裁模板并落盘。

    皮肤/补丁把字改了颜色或反色之后，匹配分会掉到 0.5 以下；站到对应界面跑一次
    本函数即可自愈，框坐标不用动。框里没有字（一片平坦）时拒绝学习并说明。
    """
    import cv2
    import numpy as np
    if frame is None:
        frame, _mapping = capture_client_frame(config)
    if frame is None:
        return {"ok": False, "error": "抓不到炉石客户区（未运行/最小化）"}
    gray = _gray(to_reference(frame))
    names = (["traditional", "deck_select_title", "start"]
             if kind in (None, "", "all") else [str(kind)])
    boxes = {"traditional": traditional_box(config),
             "deck_select_title": deck_select_title_box(config),
             "start": start_box(config)}
    learned, refused = {}, {}
    for name in names:
        box = boxes.get(name)
        if box is None or name not in TEMPLATE_FILE:
            refused[name] = "不认识的模板类型"
            continue
        left, top, right, bottom = box
        patch = np.ascontiguousarray(gray[top:bottom, left:right])
        if patch.size == 0:
            refused[name] = "框跑到画面外了"
            continue
        deviation = float(patch.std())
        if deviation < MIN_TEMPLATE_STD:
            refused[name] = (f"框里没有文字（std={deviation:.1f} < {MIN_TEMPLATE_STD}）："
                             f"先把框拖到字上")
            continue
        dark = float((patch < 100).mean())
        if dark < MIN_TEMPLATE_DARK_RATIO:
            # 一片亮白：游戏还没画出来 / 界面不对。**绝不覆盖**已有模板 ——
            # 真机实测过这种画面（白屏），它 std 不低、能骗过上面那道检查。
            refused[name] = (f"框里几乎没有深色像素（{dark * 100:.2f}% < "
                             f"{MIN_TEMPLATE_DARK_RATIO * 100:.0f}%）："
                             f"画面还没正常显示或界面不对，已保留原模板")
            continue
        try:
            TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
            ok, buffer = cv2.imencode(".png", patch)
            if not ok:
                refused[name] = "PNG 编码失败"
                continue
            (TEMPLATE_DIR / TEMPLATE_FILE[name]).write_bytes(buffer.tobytes())
        except Exception as exc:
            refused[name] = f"写盘失败：{exc}"
            continue
        clear_template_cache(name)
        learned[name] = {"size": [int(patch.shape[1]), int(patch.shape[0])],
                         "std": round(deviation, 1)}
    if learned:
        detail = "、".join(f"{name} {value['size'][0]}×{value['size'][1]}"
                           f"（std={value['std']}）" for name, value in learned.items())
        message = f"已重裁模板：{detail}"
    else:
        message = "没有学到任何模板：" + "；".join(
            f"{name}：{why}" for name, why in refused.items())
    return {"ok": bool(learned), "learned": learned, "refused": refused,
            "message": message}
