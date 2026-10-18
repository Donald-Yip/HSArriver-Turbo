"""Versioned image preprocessing candidates for HSAng's dark panel."""

import os

import cv2
import numpy as np

from src.recommendation_config import RecommendationConfig

# 缩放系数集中定义于 config（ocr_preprocess_scale），
# 环境变量 OCR_PREPROCESS_SCALE 可临时覆盖。
_SCALE = float(
    os.environ.get("OCR_PREPROCESS_SCALE")
    or RecommendationConfig().ocr_preprocess_scale
)


def iter_preprocess_recommendation(image: np.ndarray, scale: float = 1.0):
    """Generate OCR candidates lazily, stopping work after a successful one.

    ``scale`` 是"客户区缩放补偿"倍数：炉石客户区比基准 1920×1080 小时，
    ROI 抓到的面板像素也同比例变少，这里按同一比例把放大倍数补水回去，
    让送给 OCR 的字号与"客户区正好 1920×1080"时一致（调用方见
    stable_reader._client_upscale）。默认 1.0 = 不额外放大。
    """
    factor = _SCALE * float(scale or 1.0)
    # 放大用 LANCZOS4（比 CUBIC 更锐，利于细笔画），降采样用 CUBIC。
    interpolation = cv2.INTER_LANCZOS4 if factor > 1.0 else cv2.INTER_CUBIC
    scaled = cv2.resize(image, None, fx=factor, fy=factor, interpolation=interpolation)
    yield "scaled_color_v1", scaled
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    yield "gray_clahe_v1", clahe
    binary = cv2.inRange(scaled, (105, 105, 105), (255, 255, 255))
    yield "light_text_binary_v1", binary


def preprocess_recommendation(
    image: np.ndarray, scale: float = 1.0
) -> dict[str, np.ndarray]:
    """Compatibility wrapper for callers that require every candidate."""
    return dict(iter_preprocess_recommendation(image, scale))
