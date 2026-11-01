# -*- coding: utf-8 -*-
"""进程内存观测：每局结束打一行 RSS，超过阈值时回收 OCR 引擎。

用户反馈「脚本运行久了占用的内存越来越多」。这里只做两件轻量的事：

1. 每局结束打一行 RSS（基线 / 峰值），让长跑趋势在日志里可见；
2. RSS 过高时给集成方一个回收回调（默认跳过），并打出回收后的读数。

纯 stdlib + 可选 psutil：psutil 不可用时所有读数返回 ``None``，调用方静默
降级，不影响对局流程。
"""

import print_info

try:
    import psutil
except Exception:  # pragma: no cover - psutil 是可选依赖，缺了照常跑
    psutil = None

_MB = 1024.0 * 1024.0

# 阈值（MB）：整局结束时 RSS 超过回收阈值就让集成方回收 OCR 引擎；
# 回收后仍超过告警阈值，说明回收也压不下来，建议重启控制台。
MEMORY_RECYCLE_MB = 900
MEMORY_WARN_MB = 1400

# 进程内观测状态：基线 = 第一次观测到的 RSS；峰值 = 至今观测到的最大 RSS。
_baseline = None
_peak = None


def _remember(rss):
    """把一次读数计入峰值（None 忽略）。"""
    global _peak
    if rss is None:
        return
    if _peak is None or rss > _peak:
        _peak = rss


def rss_mb():
    """当前进程 RSS（MB，float）；psutil 不可用或读取失败时返回 None。"""
    if psutil is None:
        return None
    try:
        return psutil.Process().memory_info().rss / _MB
    except Exception:
        return None


def baseline_mb():
    """基线 RSS（MB）：第一次调用时取当时的读数并记住，直到 ``reset()``。"""
    global _baseline
    if _baseline is None:
        current = rss_mb()
        if current is not None:
            _baseline = current
            _remember(current)
    return _baseline


def peak_mb():
    """至今观测到的最大 RSS（MB）；还没读到过任何读数时返回 None。"""
    return _peak


def reset():
    """清空基线 / 峰值（测试用；长跑进程不需要主动调用）。"""
    global _baseline, _peak
    _baseline = None
    _peak = None


def note_game_end(on_recycle=None, rss_reader=rss_mb):
    """每局结束调用：打一行 RSS；过高时先回收 OCR 引擎再诊断。

    - 总是打一行 ``[SYS] 内存 RSS … MB（较基线 … MB / 峰值 … MB）``；
    - RSS >= ``MEMORY_RECYCLE_MB``：调 ``on_recycle()``（None 则跳过），
      再打回收后的 RSS；
    - 回收后 RSS 仍 >= ``MEMORY_WARN_MB``：warn 建议重启控制台。

    ``rss_reader`` 可注入（默认 :func:`rss_mb`）便于测试；回收回调抛异常也
    不影响本局收尾。所有异常都在这里兜住，观测逻辑绝不能打断对局循环。
    """
    global _baseline
    try:
        try:
            rss = rss_reader()
        except Exception:
            return
        if rss is None:
            return
        _remember(rss)
        if _baseline is None:
            _baseline = rss
        baseline = _baseline
        peak = _peak if _peak is not None else rss
        print_info.info_print(
            f"[SYS] 内存 RSS {rss:.0f} MB（较基线 {rss - baseline:+.0f} MB / "
            f"峰值 {peak:.0f} MB）")
        if rss >= MEMORY_RECYCLE_MB:
            if on_recycle is not None:
                try:
                    on_recycle()
                except Exception:
                    pass
            try:
                after = rss_reader()
            except Exception:
                after = None
            if after is not None:
                _remember(after)
                print_info.info_print(
                    f"[SYS] 内存回收后：RSS {rss:.0f} → {after:.0f} MB")
                rss = after
        if rss >= MEMORY_WARN_MB:
            print_info.warn_print(
                f"[WARN] 内存占用偏高（{rss:.0f} MB），"
                f"建议重启控制台：hsctl serve restart")
    except Exception:
        # 观测 / 日志失败一律不外溢：每局收尾不能被内存观测打断。
        pass
