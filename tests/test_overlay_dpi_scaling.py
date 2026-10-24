"""浮窗 DPI 适配：几何按系统 DPI 缩放，100% 屏下与旧版逐像素一致。

背景：浮窗的宽高/行高/内边距都是按 96 DPI（100% 缩放）写死的设计值，字体却
按「磅」定义、Tk 会随 DPI 自动放大。4K + 150% 这类高缩放屏下字变大、窗口
不变 → 内容被压扁（用户反馈的「浮窗在不同分辨率下被压缩」）。
修法：开窗时按 Tk 报告的 DPI 算 UI_SCALE，几何一律经 px() 换算。
"""

import inspect
import unittest

import log_overlay


class _FakeRoot:
    """只实现 winfo_fpixels 的假 root（不依赖显示器）。"""

    def __init__(self, dpi):
        self._dpi = dpi

    def winfo_fpixels(self, _spec):
        if isinstance(self._dpi, Exception):
            raise self._dpi
        return self._dpi


class ComputeUiScaleTests(unittest.TestCase):
    def test_96_dpi_means_no_scaling(self):
        self.assertEqual(1.0, log_overlay.compute_ui_scale(_FakeRoot(96)))

    def test_144_dpi_means_one_and_a_half(self):
        self.assertEqual(1.5, log_overlay.compute_ui_scale(_FakeRoot(144)))

    def test_192_dpi_means_double(self):
        self.assertEqual(2.0, log_overlay.compute_ui_scale(_FakeRoot(192)))

    def test_below_96_is_clamped_to_one(self):
        # 72 DPI（老式低分屏）不缩小浮窗：窗口太小会挤不下内容。
        self.assertEqual(1.0, log_overlay.compute_ui_scale(_FakeRoot(72)))

    def test_extreme_high_dpi_is_clamped(self):
        self.assertEqual(3.0, log_overlay.compute_ui_scale(_FakeRoot(9600)))

    def test_zero_or_unreadable_dpi_falls_back_to_one(self):
        self.assertEqual(1.0, log_overlay.compute_ui_scale(_FakeRoot(0)))
        self.assertEqual(
            1.0, log_overlay.compute_ui_scale(_FakeRoot(RuntimeError("x")))
        )


class PxTests(unittest.TestCase):
    def setUp(self):
        self._original = log_overlay.UI_SCALE
        log_overlay.UI_SCALE = 1.0

    def tearDown(self):
        log_overlay.UI_SCALE = self._original

    def test_scale_one_is_identity(self):
        self.assertEqual(263, log_overlay.px(263))
        self.assertEqual(8, log_overlay.px(8))

    def test_scales_with_the_current_factor(self):
        self.assertEqual(395, log_overlay.px(263, 1.5))  # 263 * 1.5 = 394.5 → 395
        self.assertEqual(12, log_overlay.px(8, 1.5))
        self.assertEqual(526, log_overlay.px(263, 2.0))

    def test_uses_the_module_scale_when_unset(self):
        log_overlay.UI_SCALE = 1.5
        self.assertEqual(104, log_overlay.px(69))  # 103.5 → 104

    def test_never_returns_zero_or_negative(self):
        self.assertEqual(1, log_overlay.px(0, 1.5))  # 0px 设计值也至少占 1px
        self.assertEqual(1, log_overlay.px(1, 0.0))  # 极端缩放也不会塌成 0
        self.assertEqual(2, log_overlay.px(1, 1.5))  # 1.5px 半像素正常进位
        self.assertEqual(1, log_overlay.px(1, 1.0))  # 100% 下逐像素一致

    def test_garbage_input_falls_back_to_one(self):
        # 数值字符串能转就照常缩放；真转不了的（None 等）退回 1px，绝不炸开窗。
        self.assertEqual(11, log_overlay.px("7", 1.5))
        self.assertEqual(1, log_overlay.px(None, 1.5))


class MinimizedGeometryTests(unittest.TestCase):
    def test_collapsed_uses_the_passed_min_height(self):
        self.assertEqual(
            "395x51+8+9",
            log_overlay.minimized_geometry(
                395, 1040, 8, 9, collapsed=True, min_height=51
            ),
        )

    def test_expanded_ignores_the_min_height(self):
        self.assertEqual(
            "395x1040+8+9",
            log_overlay.minimized_geometry(
                395, 1040, 8, 9, collapsed=False, min_height=51
            ),
        )

    def test_default_stays_the_design_value(self):
        # 不传 min_height 时与旧行为一致（96 DPI 基准，测试锁的口径）。
        self.assertEqual(
            f"{log_overlay.WINDOW_WIDTH}x{log_overlay.MINIMIZED_HEIGHT}+8+9",
            log_overlay.minimized_geometry(
                log_overlay.WINDOW_WIDTH,
                log_overlay.WINDOW_HEIGHT,
                8,
                9,
                collapsed=True,
            ),
        )


class WindowWiringTests(unittest.TestCase):
    """开窗流程必须真的把 UI_SCALE 算出来、几何走 px()。"""

    def test_run_computes_the_scale_and_pins_tk_font_scaling(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("UI_SCALE = compute_ui_scale(root)", source)
        # 字体（磅）与几何（px）必须按同一个 DPI 缩放，否则又会压扁。
        self.assertIn('"tk", "scaling"', source)
        self.assertIn("UI_SCALE * 96.0 / 72.0", source)

    def test_run_sizes_the_window_through_px(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("W, H = px(WINDOW_WIDTH), px(WINDOW_HEIGHT)", source)
        self.assertIn("px(MINIMIZED_HEIGHT)", source)
        self.assertNotIn("W, H = WINDOW_WIDTH, WINDOW_HEIGHT", source)

    def test_detail_wrap_uses_the_scaled_window_width(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("detail_wrap_px(\n                px(WINDOW_WIDTH),", source)

    def test_base_constants_keep_the_design_values(self):
        # 263/802/34 是 100% 缩放下的设计值，测试与文档都按这个口径锁住。
        # （723 → 772：新增「炉石窗口宽度」滑条行；772 → 802：「装好没反应？」按钮行。）
        self.assertEqual(263, log_overlay.WINDOW_WIDTH)
        self.assertEqual(802, log_overlay.WINDOW_HEIGHT)
        self.assertEqual(34, log_overlay.MINIMIZED_HEIGHT)
        self.assertEqual(1.0, log_overlay.UI_SCALE)


if __name__ == "__main__":
    unittest.main()
