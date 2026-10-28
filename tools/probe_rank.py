# -*- coding: utf-8 -*-
"""读一次段位区域 (1230,180)-(1385,225)，把结果打印出来（只读，不点击）。

用法：
    python tools/probe_rank.py          # 现在就读一次
    python tools/probe_rank.py --loop   # 每 0.5s 读一次，直到 Ctrl+C

用途：换屏（结算界面 / 选套牌界面 / 主菜单）时各跑一次，就能确认段位数字到底
在哪一屏、什么时刻才出现；脚本的判定逻辑不依赖它。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import FSM_action  # noqa: E402
from src.recommendation_config import RecommendationConfig  # noqa: E402

# The production flow rebuilds this object before every game; the standalone
# probe must do the same so it reads the calibrated ui_config ROI.
FSM_action.recommendation_config = RecommendationConfig()


def probe(save: str | None = None) -> None:
    reading = FSM_action.read_rank_region()
    box = FSM_action._post_game_rank_box()
    lines = FSM_action._ocr_lines(
        box, FSM_action._POST_GAME_OCR_SCALE, "rank_probe")
    texts = [(str(getattr(line, "text", "")),
              round(float(getattr(line, "confidence", 0.0) or 0.0), 2))
             for line in (lines or [])]
    print(
        f"区域 {box} | 亮像素 {reading['bright']} | "
        f"空白 {reading['empty']} | 截图失败 {reading['blank']} | "
        f"数字 {reading['number']} | OCR 原文 {reading['raw']!r} | OCR 行 {texts}")
    if save:
        try:
            import cv2
            img = FSM_action._grab_region(box, FSM_action._POST_GAME_OCR_SCALE)
            if img is None:
                raise RuntimeError("截图失败")
            cv2.imwrite(save, img)
            print(f"  已存图：{save}")
        except Exception as exc:      # pragma: no cover - 取证脚本
            print(f"  存图失败：{exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="读一次段位区域")
    parser.add_argument("--loop", action="store_true", help="每 0.5s 读一次")
    parser.add_argument("--save", metavar="PNG", default=None,
                        help="把这一段区域的截图放大 4 倍存成 PNG（留证用）")
    args = parser.parse_args()
    if not args.loop:
        probe(args.save)
        return 0
    try:
        while True:
            print(time.strftime("%H:%M:%S"), end=" ")
            probe()
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
