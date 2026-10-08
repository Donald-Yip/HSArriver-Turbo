# -*- coding: utf-8 -*-
"""脚本实际用到的「屏幕截图区域 / 状态判定点」清单，以及区域框预览图生成。

这是区域坐标的**唯一来源**：校准窗口（calibrate_roi.py）、屏幕叠加层
（region_overlay.py）、网页预览（GET /api/regions → build_region_preview()）
以及自动投降读 AI胜率，全都从这里取框，避免「画的框和实际截的不是同一个」。

* ``CALIBRATION_TARGETS`` / ``calibration_targets()`` —— 用户能拖、能保存到
  ui_config.json 的区域（推荐面板 / 换牌确认按钮 / AI胜率浮动条）；
* ``screenshot_regions()`` —— 屏幕上会画出来的**全部**区域（含兜底框，只读）；
* ``build_region_preview()`` —— 截一张当前屏幕、画上所有框，给浏览器看。

用户据此一眼确认三件事：
    1. 分辨率 / 缩放对不对——框的整体位置和整屏大小是否和 1920×1080 吻合；
    2. 盒子 UI 有没有摆正——盒子的「打法参考A」面板是否正好落在绿框
       （推荐区域 recommendation_roi）里；
    3. 换牌「确认」按钮、AI胜率浮动条这些区域有没有对偏。
4. 每局结束用的两个区域（结算「开始」按钮 / 段位数字）位置是否对——它们不需要
   校准，只用来对照（见 POST_GAME_START_BUTTON_REGION / POST_GAME_RANK_REGION）。

整个过程只截屏，不点击、不移动鼠标，自动化运行中也能安全使用。
区域坐标全部来自 config.py（用户可在 ui_config.json 覆盖，校准窗口写入），
本模块不重复定义默认值以外的坐标。
"""
from __future__ import annotations

import base64
import io
import time
from datetime import datetime
from typing import Callable, Optional

from selfcheck import STATUS_FAIL, STATUS_OK, STATUS_WARN

# 盒子浮动条「AI胜率 X%」的截图区域，必须与 FSM_action._AI_WIN_RATE_REGIONS
# 一致（test_screen_regions.py 会导入 FSM_action 校验，防止两边改岔）。
# 用户用校准工具改过后走 ui_config.json 的 ai_win_rate_roi，这里是代码默认值。
AI_WIN_RATE_REGION = (110, 8, 270, 48)
# 兜底区域 = 主区域按「左/上/右/下」外扩，完全包住主区域。
# 注意：不要直接写死兜底坐标，否则改了主区域默认值两边就会悄悄不一致。
AI_WIN_RATE_WIDE_MARGIN = (15, 8, 30, 12)


def expand_box(box, margin) -> tuple[int, int, int, int]:
    """把区域按 (左, 上, 右, 下) 边距外扩一圈，左上角不越过屏幕原点。"""
    left, top, right, bottom = (int(v) for v in box)
    margin_left, margin_top, margin_right, margin_bottom = (int(v) for v in margin)
    return (max(0, left - margin_left), max(0, top - margin_top),
            right + margin_right, bottom + margin_bottom)


AI_WIN_RATE_WIDE_REGION = expand_box(AI_WIN_RATE_REGION, AI_WIN_RATE_WIDE_MARGIN)

# 每局结束（对局结算）阶段的区域，必须与 FSM_action._POST_GAME_START_BUTTON_ROI /
# _POST_GAME_RANK_ROI 一致（test_screen_regions.py 会导入 FSM_action 校验）：
#   * POST_GAME_START_BUTTON_REGION：结算界面的「开始」按钮——**可校准**（用户拖框，
#     点击点取框中心；这个默认框的中心正好是实测的 (1400,900)）；
#   * POST_GAME_RANK_REGION        ：段位数字（没上传说时这里是空白），只读；
#   * POST_GAME_START_FALLBACK_REGION：检测「开始」的兜底区域（用户把框拖小后仍能认出）。
POST_GAME_START_BUTTON_REGION = (1325, 865, 1475, 935)
POST_GAME_START_FALLBACK_REGION = (1250, 845, 1550, 945)
POST_GAME_RANK_REGION = (1230, 180, 1385, 225)

# 可用校准工具拖拽并保存到 ui_config.json 的区域（校准目标登记表）。
# key 与屏幕叠加层的区域 key 一致；config_key 是 ui_config.json 的顶层键，
# 同时也是 config.RecommendationConfig 的字段名。dict 的 "default" 是代码默认值。
# 新增一个「可校准区域」只需要在这里加一项：校准工具、网页与叠加层都自动跟上。
CALIBRATION_TARGETS = (
    {"key": "recommendation", "config_key": "recommendation_roi",
     "label": "盒子推荐面板", "short": "推荐面板", "color": "#63c76f",
     "default": (7, 200, 202, 500),
     "hint": "把盒子的「打法参考A」面板框进绿框"},
    {"key": "mulligan_confirm", "config_key": "mulligan_confirm_roi",
     "label": "换牌「确认」按钮", "short": "换牌确认", "color": "#5fa8e6",
     "default": (860, 810, 1060, 890),
     "hint": "框住换牌界面里的「确认」按钮"},
    {"key": "win_rate", "config_key": "ai_win_rate_roi",
     "label": "盒子「AI胜率」浮动条", "short": "AI胜率", "color": "#e2a84e",
     "default": AI_WIN_RATE_REGION,
     "hint": "框住左上角盒子的「AI胜率 X%」浮动条"},
    {"key": "post_game_start", "config_key": "post_game_start_roi",
     "label": "结算「开始」按钮", "short": "开始按钮", "color": "#7fd4c1",
     "default": POST_GAME_START_BUTTON_REGION,
     "hint": "框住每局结束时底部的「开始」按钮：检测它是否出现，并用框中心点击"},
)

# get_screen.get_state() 直接读这几个像素来判断当前阶段（屏幕坐标 x, y）。
# 它们不是区域而是单点，所以画成十字准星：位置偏了状态识别就会错。
STATE_PROBE_POINTS = (
    {"key": "probe_main", "label": "主界面/选英雄/匹配判定点",
     "point": (1090, 1070)},
    {"key": "probe_main_alt", "label": "主界面判定点（备用）",
     "point": (705, 305)},
    {"key": "probe_mulligan", "label": "选牌界面判定点",
     "point": (860, 960)},
)

_FONT_CANDIDATES = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
                    r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc")


def _default_config():
    try:
        from config import RecommendationConfig
        return RecommendationConfig()
    except Exception:
        return None


def _box(value, fallback):
    """把配置里的 ROI 规整成 (left, top, right, bottom)，非法就用兜底值。"""
    try:
        vals = tuple(int(v) for v in value)
        if len(vals) == 4 and vals[0] < vals[2] and vals[1] < vals[3]:
            return vals
    except Exception:
        pass
    return tuple(int(v) for v in fallback)


def ai_win_rate_boxes(config=None) -> tuple[tuple, tuple]:
    """AI胜率的主区域与兜底区域（都优先取用户校准值，非法则回代码默认值）。"""
    cfg = config if config is not None else _default_config()
    main = _box(getattr(cfg, "ai_win_rate_roi", None), AI_WIN_RATE_REGION)
    raw_wide = getattr(cfg, "ai_win_rate_wide_roi", None)
    if raw_wide is None:
        wide = expand_box(main, AI_WIN_RATE_WIDE_MARGIN)
    else:
        wide = _box(raw_wide, expand_box(main, AI_WIN_RATE_WIDE_MARGIN))
    return main, wide


def calibration_targets(config=None) -> list[dict]:
    """可校准区域清单（校准工具/叠加层/AI胜率读取共用的唯一来源）。

    返回 [{key, config_key, label, short, color, box, hint}, ...]，
    box 是「当前生效」的 (left, top, right, bottom)：优先 ui_config.json
    里用户校准过的值，否则用 CALIBRATION_TARGETS 里的代码默认值。
    """
    cfg = config if config is not None else _default_config()
    targets = []
    for item in CALIBRATION_TARGETS:
        target = dict(item)
        target["box"] = _box(getattr(cfg, item["config_key"], None), item["default"])
        targets.append(target)
    return targets


def calibration_target(config=None, key: str = "") -> Optional[dict]:
    """按 key 取一个可校准区域；找不到返回 None。"""
    for target in calibration_targets(config):
        if target["key"] == key:
            return target
    return None


def screenshot_regions(config=None) -> list[dict]:
    """脚本所有截图区域（区域框预览与文档共同的唯一来源）。"""
    cfg = config if config is not None else _default_config()
    by_key = {target["key"]: target for target in calibration_targets(cfg)}
    win_rate, win_rate_wide = ai_win_rate_boxes(cfg)
    return [
        {"key": "recommendation", "color": "#63c76f",
         "label": "盒子推荐面板（OCR 识别来源）",
         "box": by_key["recommendation"]["box"],
         "config_key": "recommendation_roi",
         "note": "对战中把盒子的「打法参考A」面板拖进这个绿框，脚本就靠它读出牌建议"},
        {"key": "mulligan_confirm", "color": "#5fa8e6",
         "label": "换牌「确认」按钮（提交校验）",
         "box": by_key["mulligan_confirm"]["box"],
         "config_key": "mulligan_confirm_roi",
         "note": "换牌阶段用来确认按钮是否已经消失（还在=没提交成功，要重试）"},
        {"key": "win_rate", "color": "#e2a84e",
         "label": "盒子「AI胜率」浮动条",
         "box": win_rate,
         "config_key": "ai_win_rate_roi",
         "note": "开了自动投降才用得到：靠它读左上角 AI胜率"},
        {"key": "win_rate_wide", "color": "#e2705f",
         "label": "AI胜率兜底区域",
         "box": win_rate_wide,
         # 兜底区域完全包住主区域，所以画细一点、标签放框下面，避免和上面重叠。
         "width": 1, "label_below": True,
         "note": "主区域读不到时放宽再读一次，偏一点也能兜住"},
        # 下面两个只在「对局结算」阶段使用（打完一局后的界面）：
        # 「开始」按钮可校准（点击点取框中心），段位框只读、画出来供对照。
        {"key": "post_game_start", "color": "#7fd4c1",
         "label": "结算「开始」按钮（每局结束等它出现）",
         "box": by_key["post_game_start"]["box"],
         "config_key": "post_game_start_roi",
         "note": "检测它有没有出现，并用框中心点击它；按钮挪位置了就把这个框拖过去"},
        {"key": "rank", "color": "#c9a3e8",
         "label": "段位数字（上传说后读取，用于按段位停止）",
         "box": POST_GAME_RANK_REGION,
         "note": "每局结束读一次：有数字=已上传说（数字即名次），空白=还没上传说"},
    ]


def state_probe_points() -> list[dict]:
    """阶段判定的像素点（复制一份，调用方改动不影响模块常量）。"""
    return [{"key": p["key"], "label": p["label"], "point": tuple(p["point"])}
            for p in STATE_PROBE_POINTS]


# ---------------------------------------------------------------- 截屏 / 度量
def _default_grabber():
    """整屏截图（主显示器），返回 PIL.Image（RGB）。"""
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=False)


def _default_screen_metrics() -> tuple[int, int, int]:
    width = height = dpi = 0
    try:
        import ctypes
        user32 = ctypes.windll.user32
        width, height = int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        dpi = int(user32.GetDpiForSystem())
    except Exception:
        pass
    return width, height, dpi


def _default_panel_detector(crop) -> bool:
    """复用 DesktopCapture 的面板可见性判定（暗红顶栏 + 像素方差）。"""
    import numpy as np
    from src.capture.desktop_capture import DesktopCapture
    pixels = np.ascontiguousarray(np.asarray(crop.convert("RGB"))[:, :, ::-1])
    return bool(DesktopCapture._panel_is_visible(pixels))


_FONT_CACHE: dict[int, object] = {}


def _font(size: int):
    """按字号加载字体（带缓存）。

    渲染循环每帧都要画十几次字，FreeType 每次重新打开 msyh.ttc 会明显拖慢
    拖框手感，所以按字号缓存住。
    """
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    from PIL import ImageFont
    font = None
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    _FONT_CACHE[size] = font
    return font


def label_font(size: int):
    """公开的字体加载入口（区域框预览图与屏幕叠加层共用）。"""
    return _font(size)


def panel_visible(crop) -> bool:
    """公开入口：判断一块截图里有没有盒子面板（暗红标题栏 + 像素方差）。"""
    return _default_panel_detector(crop)


def _check(key: str, label: str, status: str, detail: str,
           hint: str = "", required: bool = True) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail,
            "hint": hint, "required": bool(required)}


def _expected(config) -> tuple[int, int, int]:
    if config is None:
        return (1920, 1080, 96)
    size = getattr(config, "desktop_size", (1920, 1080))
    try:
        width, height = int(size[0]), int(size[1])
    except Exception:
        width, height = 1920, 1080
    try:
        dpi = int(getattr(config, "desktop_dpi", 96))
    except Exception:
        dpi = 96
    return width, height, dpi


# ---------------------------------------------------------------- 预览图
def draw_region_boxes(image, config=None, scale: float = 1.0,
                      skip_labels=(), skip_keys=()) -> list[dict]:
    """在 PIL 图像上画出所有截图区域框 + 标签，并回填 in_bounds。

    网页里的区域框预览图与屏幕上叠加的「校准」框共用这一份绘制逻辑，
    保证两处画出来的框完全一致。scale 是图像相对屏幕的缩放倍数。

    skip_keys：这些 key 的区域**整个不画**。校准窗口用它让"当前正在拖的区域"
    由 `_draw_active_target()` 单独画一遍——否则同一个框会被画两次（差 1px、
    外圈偏暗），叠起来就像"一个区域两个框"。
    skip_labels：这些 key 的区域**只画框、不画标签**（标签底紧贴框边时也会被看
    成第二个框；当前目标的坐标在屏幕正中央的目标条里已经有了）。
    每个 region dict 会带回 label_drawn，方便调用方与测试判断。
    """
    from PIL import ImageColor, ImageDraw

    draw = ImageDraw.Draw(image)
    try:
        draw.fontmode = "1"  # 关掉抗锯齿，小字更清楚
    except Exception:
        pass
    font = _font(16)
    small = _font(13)
    width, height = image.size
    skip_labels = set(skip_labels or ())
    skip_keys = set(skip_keys or ())
    regions = screenshot_regions(config)
    for region in regions:
        region["label_drawn"] = False
        left, top, right, bottom = region["box"]
        in_bounds = (0 <= left < right <= width and 0 <= top < bottom <= height)
        region["in_bounds"] = in_bounds
        if not in_bounds:
            continue
        if region["key"] in skip_keys:
            continue
        color = ImageColor.getrgb(region["color"])
        x0, y0 = int(left * scale), int(top * scale)
        x1, y1 = int(right * scale), int(bottom * scale)
        draw.rectangle((x0, y0, x1, y1), outline=color,
                       width=int(region.get("width", 3)))
        if region["key"] in skip_labels:
            continue
        label = (f"{region['label']} {left},{top},{right},{bottom}"
                 if font is not None else
                 f"{region['key']} {left},{top},{right},{bottom}")
        text_draw = small if small is not None else font
        try:
            text_box = draw.textbbox((0, 0), label, font=text_draw)
            text_width = text_box[2] - text_box[0]
            text_height = text_box[3] - text_box[1]
        except Exception:
            text_width, text_height = len(label) * 7, 14
        label_x = min(max(0, x0), max(0, width - text_width - 8))
        if region.get("label_below"):
            label_y = min(y1 + 4, max(0, height - text_height - 4))
        else:
            label_y = y0 - text_height - 8
            if label_y < 0:
                # 框贴着屏幕顶边（例如左上角的「AI胜率」浮动条）：标签挪到框
                # 右边，别盖在框上——校准/对照时得看得见框里的东西。
                if x1 + 6 + text_width + 3 <= width:
                    label_x = x1 + 6
                    label_y = y0
                else:
                    label_y = min(y0 + 4, max(0, height - text_height - 4))
        draw.rectangle((label_x - 3, label_y - 2,
                        label_x + text_width + 3, label_y + text_height + 3),
                       fill=(0, 0, 0))
        draw.text((label_x, label_y), label, fill=color, font=text_draw)
        region["label_drawn"] = True
    return regions


def draw_state_probe_points(image, scale: float = 1.0) -> list[dict]:
    """把阶段判定点画成黄色十字准星（同样是预览图与屏幕叠加层共用）。"""
    from PIL import ImageColor, ImageDraw

    draw = ImageDraw.Draw(image)
    width, height = image.size
    color = ImageColor.getrgb("#f7d97e")
    probes = state_probe_points()
    for probe in probes:
        px, py = probe["point"]
        in_bounds = 0 <= px < width and 0 <= py < height
        probe["in_bounds"] = in_bounds
        if not in_bounds:
            continue
        cx, cy = int(px * scale), int(py * scale)
        draw.line((cx - 9, cy, cx + 9, cy), fill=color, width=2)
        draw.line((cx, cy - 9, cx, cy + 9), fill=color, width=2)
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), outline=color, width=2)
    return probes


def build_region_preview(config=None, grabber: Optional[Callable] = None,
                         screen_metrics: Optional[Callable] = None,
                         panel_detector: Optional[Callable] = None,
                         jpeg_quality: int = 82,
                         max_width: int = 1920) -> dict:
    """截一张整屏、画上所有截图区域框，返回可直接给前端 <img> 用的结果。

    返回的 checks 与「环境自检」用同一套 ok/warn/fail 结构，前端一套渲染逻辑。
    """
    from PIL import Image, ImageColor, ImageDraw

    grabber = grabber or _default_grabber
    screen_metrics = screen_metrics or _default_screen_metrics
    panel_detector = panel_detector or _default_panel_detector

    image = grabber()
    if image is None:
        raise RuntimeError("截屏失败：请确认脚本以管理员身份运行")
    image = image.convert("RGB")
    actual_width, actual_height = image.size

    expected_width, expected_height, expected_dpi = _expected(config)
    screen_width, screen_height, dpi = 0, 0, 0
    try:
        screen_width, screen_height, dpi = screen_metrics()
    except Exception:
        pass

    # 4K 屏的整屏 JPEG 太大，超宽时等比缩小后再画（框坐标同步缩放）。
    scale = min(1.0, float(max_width) / float(actual_width or 1))
    if scale < 1.0:
        image = image.resize((max(1, int(actual_width * scale)),
                              max(1, int(actual_height * scale))),
                             Image.LANCZOS)

    draw = ImageDraw.Draw(image)
    try:
        draw.fontmode = "1"  # 关掉抗锯齿，小字更清楚
    except Exception:
        pass
    checks: list[dict] = []

    # --- 分辨率 / 缩放
    if (actual_width, actual_height) == (expected_width, expected_height):
        checks.append(_check("resolution", "屏幕分辨率", STATUS_OK,
                             f"{actual_width}×{actual_height}"))
    else:
        checks.append(_check(
            "resolution", "屏幕分辨率", STATUS_FAIL,
            f"{actual_width}×{actual_height}（要求 {expected_width}×{expected_height}）",
            "脚本的点击坐标按 1920×1080 硬编码：请把 Windows 分辨率设为 "
            f"{expected_width}×{expected_height}，炉石用全屏模式（别用最大化窗口）。"))
    if dpi:
        if dpi == expected_dpi:
            checks.append(_check("dpi", "显示缩放（DPI）", STATUS_OK,
                                 f"{dpi}（{round(dpi / 96 * 100)}%）"))
        else:
            checks.append(_check(
                "dpi", "显示缩放（DPI）", STATUS_FAIL,
                f"{dpi}（{round(dpi / 96 * 100)}%，要求 100%）",
                "显示设置 → 缩放改成 100%：不是 100% 时截图和点击会整体偏移。"))

    # --- 逐个区域：是否在屏幕内 + 画框
    regions = draw_region_boxes(image, config, scale=scale)
    out_of_bounds = [r for r in regions if not r.get("in_bounds")]
    for region in out_of_bounds:
        left, top, right, bottom = region["box"]
        # 区域跑到屏幕外 = 截图会被裁掉、点击也点不到，属于致命问题。
        checks.append(_check(
            f"bounds-{region['key']}", f"区域范围：{region['label']}", STATUS_FAIL,
            f"[{left},{top},{right},{bottom}] 超出屏幕 {actual_width}×{actual_height}",
            "这个区域会被裁掉/点不到：请校正分辨率与缩放，或重新校准区域。"))

    # --- 状态判定点
    probes = draw_state_probe_points(image, scale=scale)

    # --- 绿框里到底有没有盒子面板（决定要不要「移动盒子 UI」）
    recommendation = next((r for r in regions if r["key"] == "recommendation"), None)
    if recommendation is not None and recommendation.get("in_bounds"):
        left, top, right, bottom = recommendation["box"]
        try:
            crop = image.crop((int(left * scale), int(top * scale),
                               int(right * scale), int(bottom * scale)))
            visible = bool(panel_detector(crop))
        except Exception as exc:
            visible = False
            checks.append(_check("panel", "绿框内盒子面板", STATUS_WARN,
                                 f"无法判定：{type(exc).__name__}: {exc}", "",
                                 required=False))
        else:
            if visible:
                checks.append(_check("panel", "绿框内盒子面板", STATUS_OK,
                                     "检测到盒子面板（暗红标题栏）"))
            else:
                checks.append(_check(
                    "panel", "绿框内盒子面板", STATUS_WARN,
                    "绿框里没有检测到盒子面板",
                    "对战中把盒子的「打法参考A」面板拖进绿框再看一次；"
                    "不在对局时看不到面板是正常的。", required=False))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(jpeg_quality), optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    failed = [c for c in checks if c["status"] == STATUS_FAIL and c["required"]]
    return {
        "ok": not failed,
        "image": f"data:image/jpeg;base64,{encoded}",
        "width": actual_width,
        "height": actual_height,
        "scaled": scale < 1.0,
        "dpi": dpi,
        "screen": {"width": screen_width or actual_width,
                   "height": screen_height or actual_height, "dpi": dpi},
        "expected": {"width": expected_width, "height": expected_height,
                     "dpi": expected_dpi},
        "regions": regions,
        "points": probes,
        "checks": checks,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_ts": time.time(),
    }


def main() -> int:
    """命令行直接跑：python screen_regions.py [输出文件]（默认 preview.jpg）。"""
    import sys
    from pathlib import Path
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("region_preview.jpg")
    result = build_region_preview()
    data = result["image"].split(",", 1)[1]
    out.write_bytes(base64.b64decode(data))
    print(f"区域框预览已保存：{out}（{result['width']}×{result['height']}）")
    for item in result["checks"]:
        print(f"  [{item['status']}] {item['label']}：{item['detail']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
