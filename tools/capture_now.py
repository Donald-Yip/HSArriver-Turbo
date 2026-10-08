"""临时取证脚本：抓当前屏幕 + 段位 ROI，存成 PNG 便于人工核对。

只截图，不点击、不改任何游戏状态。
    python tools/capture_now.py
输出：
    tmp_screens/live_full.png      全屏缩略
    tmp_screens/live_rank_roi.png  段位 ROI (1230,180,1385,225) ×4
    tmp_screens/live_rank_ctx.png  段位 ROI 周边 (1080,120,1560,300) ×3
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import ImageGrab  # noqa: E402

OUT = os.path.join(ROOT, "tmp_screens")
os.makedirs(OUT, exist_ok=True)

RANK_ROI = (1230, 180, 1385, 225)
CTX_ROI = (1080, 120, 1560, 300)


def save(img, name, scale):
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale))
    path = os.path.join(OUT, name)
    img.save(path)
    print(f"{name}: {img.width}x{img.height} -> {path}")


def main():
    try:
        full = ImageGrab.grab(all_screens=False)
    except Exception as exc:  # pragma: no cover - 取证脚本
        print(f"全屏截图失败：{exc}")
        return 1
    save(full, "live_full.png", 1)

    for box, name, scale in ((RANK_ROI, "live_rank_roi.png", 4),
                             (CTX_ROI, "live_rank_ctx.png", 3)):
        try:
            crop = ImageGrab.grab(bbox=box, all_screens=False)
        except Exception as exc:  # pragma: no cover
            print(f"{name} 截图失败：{exc}")
            continue
        save(crop, name, scale)

    # 顺带用脚本自己的读数逻辑看一眼（纯读，不点击）
    try:
        import FSM_action
        reading = FSM_action.read_rank_region()
        print("read_rank_region ->", reading)
    except Exception as exc:
        print(f"read_rank_region 不可用（忽略）：{exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
