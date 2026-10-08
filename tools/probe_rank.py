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


def probe(save: str | None = None) -> None:
    reading = FSM_action.read_rank_region()
    lines = FSM_action._ocr_lines(*[
        FSM_action._POST_GAME_RANK_ROI, FSM_action._POST_GAME_OCR_SCALE, "rank"])
    texts = [(str(getattr(line, "text", "")),
              round(float(getattr(line, "confidence", 0.0) or 0.0), 2))
             for line in (lines or [])]
    print(
        f"区域 {FSM_action._POST_GAME_RANK_ROI} | 亮像素 {reading['bright']} | "
        f"空白 {reading['empty']} | 截图失败 {reading['blank']} | "
        f"数字 {reading['number']} | OCR 行 {texts}")
    if save:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=tuple(FSM_action._POST_GAME_RANK_ROI),
                                 all_screens=False)
            img = img.resize((img.width * 4, img.height * 4))
            img.save(save)
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
