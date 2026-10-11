# -*- coding: utf-8 -*-
"""现场自检：确认"窗口移动 → 几何立刻刷新 → 区域框跟着走"这条链路。

不需要真的拖动炉石：这里把底层 Win32 读数换成假的，但**时钟、缓存、跟踪器
事件回调全部走真实分支** —— 先取一次几何（写进缓存），再模拟跟踪器收到一次
移动事件（`note_move()`，等价于 WinEvent 回调触发），然后把假几何改掉，看
下一次 `display_mapping()` 是否立刻返回新几何。

**为什么要看这个**：这条链路曾经因为三处缺陷整条失效，用户看到的现象是
"校准窗口不跟着炉石移动"：

1. 跟踪器默认时钟用了 `time.monotonic`（开机秒数），而缓存时间戳用
   `time.time`（纪元秒），`moved_since()` 恒为 False → 缓存永不失效；
2. `region_overlay.auto_mapping()` 的默认值是 `None`，于是"不传参"拿到的是
   `None`（不换算）而不是自动探测 → 校准窗口永远没有客户区几何；
3. "钩子可用但收不到事件"时没有 TTL 兜底 → 几何冻结在第一次采样上。

用法：python tools/check_window_follow.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import display_mapping as dm  # noqa: E402
import window_tracker as wt  # noqa: E402


def main() -> int:
    state = {"origin": (960, 362)}
    reads = [0]

    def rect_reader(handle):
        reads[0] += 1
        return (0, 0, 1920, 1080)

    def origin_reader(handle, x, y):
        return state["origin"]

    print(f"跟踪器默认时钟 : {'time.time（与 display_mapping 同源）' if wt.default_clock is time.time else wt.default_clock}")

    tracker = wt.default_tracker()
    tracker.reset()
    dm.clear_mapping_cache()

    with patch.object(dm, "hwnd_of", return_value=1050772), \
            patch.object(dm, "read_client_rect", side_effect=rect_reader), \
            patch.object(dm, "client_to_screen", side_effect=origin_reader), \
            patch.object(dm, "_tracker", return_value=tracker):
        first = dm.display_mapping()
        print(f"首次采样       : origin={first.origin} 读数={reads[0]}")

        again = dm.display_mapping()
        print(f"TTL 内再取     : {'命中缓存' if again is first else '重新采样'}"
              f" 读数={reads[0]}")

        # 模拟"拖动窗口"：先收到移动事件，再改变几何。
        tracker.note_move()
        state["origin"] = (444, 691)
        fresh = dm.display_mapping()
        print(f"移动事件后     : origin={fresh.origin} 读数={reads[0]}"
              f" → {'立刻跟随 OK' if fresh.origin == (444, 691) else '没跟随 FAIL'}")

        # 静默钩子（收不到事件）也要由 TTL 兜底：不 note_move，直接改几何 + 等 TTL。
        state["origin"] = (100, 200)
        time.sleep(dm._MAPPING_TTL_SECONDS + 0.05)
        late = dm.display_mapping()
        print(f"静默事件+TTL后 : origin={late.origin}"
              f" → {'TTL 兜底 OK' if late.origin == (100, 200) else '没跟随 FAIL'}")

    # 真实跟踪器状态（本机有没有挂上事件钩子；挂钩是异步的，等一下再报）。
    wt.ensure_started()
    wt.wait_ready()
    print(f"真实钩子       : available={wt.available()} "
          f"{dm.tracker_stats()['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
