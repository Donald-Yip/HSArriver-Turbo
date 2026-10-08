# -*- coding: utf-8 -*-
"""截图区域校准工具：屏幕上拖框，把脚本要截的每一块都对齐好。

三个入口打开的都是**同一个窗口**（同一个实现，不再有“只能看不能拖”的那种）：
  * 网页控制台「🎯 校准区域」卡片 → [📐 显示校准框（屏幕上）]
  * 日志浮窗右上角「校准」按钮
  * 源码运行：python calibrate_roi.py
  * 自检：python calibrate_roi.py --selftest —— 窗口打开约 1 秒自动退出
  * 只拖框不跑 OCR：python calibrate_roi.py --no-ocr

四个可校准目标（按 Tab 切换，或直接点屏幕正中央的目标条）：

| # | 目标 | 写入 ui_config.json | 对齐方法 |
| --- | --- | --- | --- |
| 1 | 盒子推荐面板（绿） | `recommendation_roi` | 把盒子的「打法参考A」面板框进绿框 |
| 2 | 换牌「确认」按钮（蓝） | `mulligan_confirm_roi` | 框住换牌界面的确认按钮 |
| 3 | 盒子「AI胜率」浮动条（橙） | `ai_win_rate_roi` | 框住左上角 AI胜率浮动条（兜底区域自动跟着外扩） |
| 4 | 结算「开始」按钮（青绿） | `post_game_start_roi` | 框住每局结束底部的「开始」按钮：脚本用它判断结算界面点完没有，并点它的正中心 |

操作：
  * 拖框的边框可整体移动，拖右下角手柄可调整大小；
  * 目标条里列出四个目标（当前目标高亮 + 左侧竖条），点一行就能切过去；
  * 目标条底部两个按钮：**左「保存全部区域 (S)」、右「退出 (Esc)」**，
    都有悬停高亮；键盘 **S** 保存、**Esc** 退出同样有效；
  * 说明文字在卡片内自动换行（金色标题段 = 做什么，暗淡说明段 = 怎么做），
    永远不会穿出卡片；卡片高度跟着换行行数长高；
  * 画面空白处的鼠标是**穿透**的：只有框/手柄/目标条上才拦鼠标，其余点击照常落到
    炉石上，所以可以边看游戏画面边调；
  * 顶部提示条每 1.5s 重判一次「绿框里有没有盒子面板」，对齐成功会变绿。

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
    LayeredWindow, MSG, PM_REMOVE, USER32, label_font, paint_layer, panel_state,
)
from screen_regions import (
    AI_WIN_RATE_WIDE_MARGIN, CALIBRATION_TARGETS, calibration_targets,
    expand_box,
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
# 基础高度：标题 + 目标行 + 4 行信息（当前目标 / 说明 / 坐标 / 保存状态）
# + 按钮行。说明换行后会由 CalibrationSession._card_height() 按实际行数加高。
CARD_H = (CARD_PAD + 20 + 8          # 标题
          + len(CALIBRATION_TARGETS) * ROW_H + 6   # 目标行
          + 4 * INFO_LINE_H + 8      # 当前目标 / 说明 / 坐标 / 保存状态
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


def card_rect(width: int, height: int,
              card_h: int | None = None) -> tuple[int, int, int, int]:
    """目标条的矩形：屏幕正中央（屏幕比卡片还小时贴左上角）。"""
    card_h = CARD_H if card_h is None else int(card_h)
    left = max(8, (int(width) - CARD_W) // 2)
    top = max(8, (int(height) - card_h) // 2)
    return (left, top, left + CARD_W, top + card_h)

# ---------------------------------------------------------------- 预览面板
PREVIEW_W, PREVIEW_H = 360, 430
PREVIEW_IMG_H = 240

# 每帧要画十几次字，按字号缓存字体（FreeType 每次重开 msyh.ttc 会拖慢拖框）。
_FONT_CACHE: dict[int, object] = {}


def _rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    red, green, blue = ImageColor.getrgb(hex_color)
    return (red, green, blue, alpha)


def _user_config_path() -> Path:
    """与 RecommendationConfig 读取位置一致：应用目录下的 ui_config.json。"""
    return Path(__file__).resolve().parent / "ui_config.json"


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
                 start_key: str = ""):
        self.selftest = bool(selftest)
        self.ocr_enabled = bool(ocr_enabled) and not self.selftest
        self.running = True
        self.window: LayeredWindow | None = None
        self.width = 0
        self.height = 0

        config = _load_config()
        self.targets = [dict(target, box=list(target["box"]))
                        for target in calibration_targets(config)]
        self.active_index = 0
        if start_key:
            for index, target in enumerate(self.targets):
                if target["key"] == start_key:
                    self.active_index = index
                    break

        self.panel_state = None
        self.drag = None              # None | ("move", dx, dy) | ("resize",)
        # 鼠标在哪个按钮/目标行上（只为高亮，不影响其它逻辑）。
        self.hover = None             # None | ("save", None) | ("exit", None)
                                      #        | ("target", index)
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
            self.dirty = True
            print(f"[校准] 当前目标：{self.active['label']}"
                  f"（{self.active['config_key']}）")

    def cycle(self, step: int = 1) -> None:
        self.select((self.active_index + step) % len(self.targets))

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
        return card_rect(width, height, self._card_height())

    def _chip_rects(self, width: int, height: int):
        left, top = self._card_rect(width, height)[:2]
        rects = []
        row_top = top + CARD_PAD + 20 + 8
        for index in range(len(self.targets)):
            rects.append(((left + CARD_PAD, row_top,
                           left + CARD_W - CARD_PAD, row_top + ROW_H - 4), index))
            row_top += ROW_H
        return rects

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

    def _hit_test(self, sx: int, sy: int, width: int, height: int):
        """返回 (类型, 目标序号)；不在可交互区域上返回 None。"""
        for rect, index in self._chip_rects(width, height):
            if rect[0] <= sx <= rect[2] and rect[1] <= sy <= rect[3]:
                return ("target", index)
        for name, rect in (("save", self._save_rect(width, height)),
                           ("exit", self._exit_rect(width, height))):
            if rect[0] <= sx <= rect[2] and rect[1] <= sy <= rect[3]:
                return (name, None)
        left, top, right, bottom = self.active["box"]
        if (right + 2 <= sx <= right + 2 + HANDLE
                and bottom + 2 <= sy <= bottom + 2 + HANDLE):
            return ("corner", None)
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
        elif name == "edge":
            left, top = self.active["box"][0], self.active["box"][1]
            self.drag = ("move", sx - left, sy - top)
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
        left, top, right, bottom = self.active["box"]
        if self.drag[0] == "move":
            _, dx, dy = self.drag
            box_width, box_height = right - left, bottom - top
            new_left = min(max(0, sx - dx), max(0, width - box_width))
            new_top = min(max(0, sy - dy), max(0, height - box_height))
            new_right = new_left + box_width
            new_bottom = new_top + box_height
        else:  # resize（右下角手柄）
            new_left, new_top = left, top
            new_right = min(width, max(left + MIN_SIZE, sx))
            new_bottom = min(height, max(top + MIN_SIZE, sy))
        self.active["box"] = [new_left, new_top, new_right, new_bottom]
        self.dirty = True

    # ------------------------------------------------------------ 保存
    def _boxes_to_save(self) -> dict:
        boxes = {}
        for target in self.targets:
            key = target["config_key"]
            boxes[key] = self._clamp(target["box"])
            if key == "ai_win_rate_roi":
                # AI胜率兜底区域永远跟着主区域外扩，不给用户单独拖。
                boxes["ai_win_rate_wide_roi"] = list(
                    expand_box(boxes[key], AI_WIN_RATE_WIDE_MARGIN))
        return boxes

    def save(self, flash: bool = False) -> bool:
        boxes = self._boxes_to_save()
        path = _user_config_path()
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                cfg = {}
        except Exception:
            cfg = {}
        cfg.update(boxes)
        try:
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError as exc:
            print(f"[校准] 保存失败：{exc}")
            return False
        if flash:
            self.saved_flash_until = time.time() + 1.5
        self.dirty = True
        print(f"[校准] 已保存 {len(boxes)} 个区域 -> {path}")
        for key, box in boxes.items():
            print(f"[校准]   {key} = {tuple(box)}")
        print("[校准] 重开对局生效（不覆盖名字/日志目录/延时等其它配置）")
        return True

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
    def _preview_config(self):
        """把「正在拖的框」包成 config，交给 paint_layer 画成实时的叠加层。"""
        boxes = {target["config_key"]: tuple(target["box"])
                 for target in self.targets}
        main = boxes.get("ai_win_rate_roi", (110, 8, 270, 48))
        return SimpleNamespace(
            desktop_size=(self.width or 1920, self.height or 1080),
            desktop_dpi=96,
            recommendation_roi=boxes.get("recommendation_roi", (7, 200, 202, 500)),
            mulligan_confirm_roi=boxes.get("mulligan_confirm_roi",
                                           (860, 810, 1060, 890)),
            ai_win_rate_roi=main,
            ai_win_rate_wide_roi=expand_box(main, AI_WIN_RATE_WIDE_MARGIN),
        )

    def render(self, width: int, height: int):
        """画出一整屏 RGBA：所有区域框 + 提示条 + 当前目标高亮 + 目标条/预览。

        当前目标在这一层里被**整个跳过**，由 `_draw_active_target()` 单独画：
        否则同一个框会被画两次（两次描边差 1px，外圈偏暗），看起来像"一个区域
        有两个框"；它的标签也不画（坐标在屏幕正中央的目标条里已经有了，标签底
        又会紧贴框边）。
        """
        active_key = self.active["key"]
        layer = paint_layer(width, height, self.panel_state,
                            self._preview_config(),
                            skip_keys=(active_key,))
        draw = ImageDraw.Draw(layer)
        self._draw_active_target(draw, width, height)
        self._draw_card(draw, width, height)
        self._draw_preview(draw, width, layer)
        return layer

    def _draw_text(self, draw, xy, text: str, size: int, color) -> None:
        font = _FONT_CACHE.get(size) or label_font(size)
        _FONT_CACHE[size] = font
        if font is None:
            return
        draw.text(xy, text, fill=color, font=font)

    def _draw_active_target(self, draw, width: int, height: int) -> None:
        """把当前目标画成粗框 + 右下角手柄。

        坐标写在正中央的目标条里，不在框旁边飘字：左上角的
        「AI胜率」浮动条贴着屏幕顶边，飘字要么出屏、要么压住别的框。
        """
        left, top, right, bottom = self.active["box"]
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

    def _draw_card(self, draw, width: int, height: int) -> None:
        """目标条：目标列表 + 当前目标高亮 + 说明（自动换行）+ 保存/退出按钮。

        说明文字以前是整句一行画出去，句子一长就穿出卡片（用户反馈）。现在：
          * 说明按「：」拆成金色标题段 + 暗淡说明段；
          * 说明段按卡片内宽换行（最多 3 行），卡片高度跟着行数加高；
          * 「当前目标」「坐标」「保存状态」分行排版，当前目标用目标自己的颜色高亮；
          * 底部两个按钮：左「保存全部区域 (S)」、右「退出 (Esc)」，悬停会提亮。
        """
        card_left, card_top, card_right, card_bottom = self._card_rect(width,
                                                                      height)
        draw.rectangle((card_left, card_top, card_right, card_bottom),
                       fill=CARD_BG, outline=CARD_BORDER, width=2)
        self._draw_text(draw, (card_left + CARD_PAD, card_top + CARD_PAD),
                        "校准目标（Tab 切换）", 15, CARD_BORDER)
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
                  - 2 * INFO_LINE_H)

        box = target["box"]
        self._draw_text(draw, (card_left + CARD_PAD, line_y),
                        f"当前：{box[0]},{box[1]} → {box[2]},{box[3]}"
                        f"（{box[2] - box[0]}×{box[3] - box[1]}）", 13, TEXT_MAIN)
        if time.time() < self.saved_flash_until:
            self._draw_text(draw, (card_left + CARD_PAD, line_y + INFO_LINE_H),
                            "已保存，重开对局生效", 13, TEXT_OK)
        else:
            self._draw_text(draw, (card_left + CARD_PAD, line_y + INFO_LINE_H),
                            "S 保存 · Esc 退出 · 空白处鼠标可穿透", 13, TEXT_DIM)

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
