"""Read-only desktop capture for the visible HSAng recommendation panel."""

from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path
import time
import uuid
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import ImageGrab

from src.recommendation_config import RecommendationConfig
from src.recommendation_models import FrameEvidence


class CaptureEnvironmentError(RuntimeError):
    """The visible desktop cannot safely produce a current recommendation."""


def _default_screen_grabber() -> np.ndarray:
    """Full-desktop grabber, kept for injected/test compatibility."""
    rgb = np.asarray(ImageGrab.grab(all_screens=False))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _roi_screen_grabber(bbox) -> Callable[[], np.ndarray]:
    """Grab only the recommendation panel ROI instead of the whole desktop.

    Capturing the 271x938 panel directly skips transferring and converting
    the other ~88% of a 1920x1080 frame on every capture.

    bbox 可以是矩形，也可以是**返回矩形**的可调用对象——后者让抓图时按当前
    炉石客户区实时换算（窗口移动/缩放后不用重建 grabber 也跟着对）。
    """
    def grab() -> np.ndarray:
        box = bbox() if callable(bbox) else bbox
        rgb = np.asarray(ImageGrab.grab(bbox=tuple(int(v) for v in box),
                                        all_screens=False))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return grab


def _default_dpi_reader() -> int:
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return 0


def _default_process_checker() -> bool:
    try:
        import psutil
        names = {
            (process.info.get("name") or "").lower()
            for process in psutil.process_iter(["name"])
        }
        return "hearthstone.exe" in names and "hsang.exe" in names
    except Exception:
        return False


def _default_window_reader() -> tuple[int, bool, Optional[tuple[int, int]]]:
    """炉石窗口状态： (hwnd, 是否前台, 客户区尺寸)。

    客户区尺寸用来做**证据新鲜度**校验：这一帧是不是按当前客户区截的。它不再是
    "分辨率必须等于配置"那种门槛——分辨率/窗口位置由 display_mapping 换算处理。
    """
    try:
        import win32gui
        from get_screen import get_HS_hwnd, is_allowed_hearthstone_foreground
        hwnd = int(get_HS_hwnd())
        foreground = bool(hwnd and is_allowed_hearthstone_foreground(
            hwnd, win32gui.GetForegroundWindow()))
        client_size = None
        try:
            from display_mapping import display_mapping
            mapping = display_mapping(hwnd)
            if mapping is not None:
                client_size = (mapping.width, mapping.height)
        except Exception:
            client_size = None
        return hwnd, foreground, client_size
    except Exception:
        return 0, False, None


def _unpack_window_reader(result):
    """兼容 window_reader 的 2 元组（旧实现/测试）与 3 元组返回。"""
    try:
        values = tuple(result)
    except Exception:
        return 0, False, None
    if len(values) >= 3:
        return values[0], values[1], values[2]
    if len(values) == 2:
        return values[0], values[1], None
    if len(values) == 1:
        return values[0], False, None
    return 0, False, None


class DesktopCapture:
    def __init__(
        self,
        config: RecommendationConfig,
        screen_grabber: Optional[Callable[[], np.ndarray]] = None,
        dpi_reader: Callable[[], int] = _default_dpi_reader,
        process_checker: Callable[[], bool] = _default_process_checker,
        window_reader: Callable[[], tuple] = _default_window_reader,
        clock: Callable[[], float] = time.time,
        roi: Optional[tuple[int, int, int, int]] = None,
    ):
        self.config = config
        # 每路 OCR 可以用自己的 ROI：换牌推荐区域比回合推荐大，二者分开截，
        # 避免为了 4 张留牌把每回合的 OCR 截图一起放大。
        self.roi = tuple(int(v) for v in (
            roi if roi is not None else config.recommendation_roi))
        # ROI 抓图前先把客户区相对 ROI 换算成屏幕坐标：炉石窗口不在屏幕原点时，
        # 不换算就会截到别的地方（客户区相对坐标 ↔ 屏幕坐标见 display_mapping）。
        self.screen_grabber = (screen_grabber
                               or _roi_screen_grabber(
                                   lambda: self._screen_roi_box()))
        self.dpi_reader = dpi_reader
        self.process_checker = process_checker
        self.window_reader = window_reader
        self.clock = clock

    def _screen_roi_box(self) -> tuple[int, int, int, int]:
        """把本实例的 ROI 换算成屏幕坐标框（拿不到客户区时原样返回）。"""
        from display_mapping import box_to_screen, display_mapping
        return tuple(box_to_screen(self.roi, display_mapping()))

    @property
    def _roi_size(self) -> tuple[int, int]:
        left, top, right, bottom = self.roi
        return (bottom - top, right - left)

    def _is_roi_frame(self, width: int, height: int) -> bool:
        """这一帧是不是已经裁好的推荐面板 ROI（而非整帧）。

        ROI 抓图会按客户区缩放（`_screen_roi_box` → `box_to_screen`），所以客户区
        不是基准 1920×1080 时，ROI 帧的像素尺寸也≠基准尺寸。基准口径与**当前
        客户区缩放后**的口径都认：前者兼容离线帧/单测，后者是缩小窗口下的真实
        尺寸。只认基准会把缩放后的 ROI 误判成整帧，进而触发 client_size_mismatch
        （用户实测 1785×902 时换牌一直重试）。
        """
        roi_height, roi_width = self._roi_size
        if (width, height) == (roi_width, roi_height):
            return True
        left, top, right, bottom = self._screen_roi_box()
        return (width, height) == (right - left, bottom - top)

    def from_file(self, path: Path | str) -> FrameEvidence:
        data = np.fromfile(str(path), dtype=np.uint8)
        pixels = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if pixels is None:
            raise CaptureEnvironmentError("frame_decode_failed")
        return self.from_array(pixels, validate_environment=False)

    def capture(self, ocr_panel_ok: bool = False) -> FrameEvidence:
        if not self.process_checker():
            raise CaptureEnvironmentError("required_process_missing")
        hwnd, foreground, client_size = _unpack_window_reader(
            self.window_reader())
        if not hwnd:
            raise CaptureEnvironmentError("hearthstone_window_missing")
        if not foreground:
            raise CaptureEnvironmentError("hearthstone_not_foreground")
        pixels = self.screen_grabber()
        try:
            return self.from_array(
                pixels, validate_environment=True, dpi=self.dpi_reader(),
                window_handle=hwnd, foreground=foreground,
                client_size=client_size)
        except CaptureEnvironmentError as exc:
            if "recommendation_panel_missing" not in str(exc):
                raise
            if not ocr_panel_ok:
                raise
            # 换牌面板的红头判定并不可靠；交由 OCR 证据裁定：
            # 识别出"打法参考A"（stable_reader.required_headers）即确认
            # 面板在，否则由 reader 置零置信度拒绝，安全兜底。
            return self.from_array(
                pixels, validate_environment=False, dpi=self.dpi_reader(),
                window_handle=hwnd, foreground=foreground,
                client_size=client_size)


    def from_array(
        self,
        pixels: np.ndarray,
        *,
        validate_environment: bool,
        dpi: Optional[int] = None,
        window_handle: int = 0,
        foreground: bool = True,
        client_size: Optional[tuple[int, int]] = None,
    ) -> FrameEvidence:
        if pixels is None or pixels.ndim != 3 or pixels.shape[2] < 3:
            raise CaptureEnvironmentError("invalid_frame")
        height, width = pixels.shape[:2]
        roi_frame = self._is_roi_frame(width, height)
        if validate_environment:
            if roi_frame:
                # ROI grabber output: the desktop size is known from config.
                desktop_width, desktop_height = self.config.desktop_size
            else:
                desktop_width, desktop_height = width, height
        else:
            desktop_width, desktop_height = width, height
        # 证据新鲜度：整屏抓图必须与当前客户区一致；ROI 抓图不需要（尺寸本就
        # 由 config 的 ROI 决定）。分辨率不再是门槛 —— 客户区几何由
        # display_mapping 换算，任何桌面分辨率都能跑。
        if validate_environment and not roi_frame and client_size is not None:
            if tuple(client_size) != (width, height):
                raise CaptureEnvironmentError(
                    f"client_size_mismatch:{width}x{height}"
                    f"!={client_size[0]}x{client_size[1]}")
        # DPI 只**记录**、不再拦截：所有几何都按物理像素换算（display_mapping
        # 拿真实客户区做缩放），显示缩放是 100% 还是 150% 都不影响"截到的图"与
        # "要点的坐标"之间的对应关系。历史上这里会因 DPI≠config.desktop_dpi 直接
        # 判失败，但进程声明 DPI 感知后（display_mapping.dpi_aware），
        # GetDpiForSystem 返回的是真实值（150% → 144），那条门槛会把整条自动化
        # 卡死在 dpi_mismatch 上（换牌推荐反复重试到超时）。
        effective_dpi = self.config.desktop_dpi if dpi is None else int(dpi)

        roi = (pixels[:, :, :3] if roi_frame
               else self._crop_array(pixels))
        panel_visible = self._panel_is_visible(roi)
        if validate_environment and not panel_visible:
            raise CaptureEnvironmentError("recommendation_panel_missing")
        exact_hash = hashlib.sha256(roi.tobytes()).hexdigest()
        perceptual_hash = self._perceptual_hash(roi)
        return FrameEvidence(
            frame_id=f"frame-{uuid.uuid4()}", captured_at=self.clock(),
            desktop_size=(desktop_width, desktop_height), dpi=effective_dpi,
            window_handle=window_handle, foreground=foreground,
            recommendation_roi=self.roi,
            exact_hash=exact_hash, perceptual_hash=perceptual_hash,
            panel_visible=panel_visible, pixels=pixels,
            client_size=(tuple(client_size) if client_size is not None else None),
        )

    def crop_recommendation(self, frame: FrameEvidence) -> np.ndarray:
        height, width = frame.pixels.shape[:2]
        if self._is_roi_frame(width, height):
            return frame.pixels[:, :, :3].copy()
        return self._crop_array(frame.pixels).copy()

    def _crop_array(self, pixels: np.ndarray) -> np.ndarray:
        left, top, right, bottom = self.roi
        height, width = pixels.shape[:2]
        if left < 0 or top < 0 or right > width or bottom > height:
            raise CaptureEnvironmentError("recommendation_roi_out_of_bounds")
        return pixels[top:bottom, left:right, :3]

    @staticmethod
    def _panel_is_visible(roi: np.ndarray) -> bool:
        if roi.size == 0 or float(np.std(roi)) < 8.0:
            return False
        # HSAng uses a dark red header at the top of this fixed panel.
        header = roi[:45]
        b, g, r = cv2.split(header)
        red_dark = (r > g * 1.25) & (r > b * 1.15) & (r < 140)
        return float(np.mean(red_dark)) > 0.08

    @staticmethod
    def _perceptual_hash(roi: np.ndarray) -> str:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)
        bits = resized >= float(np.mean(resized))
        return "".join(f"{value:02x}" for value in np.packbits(bits))
