# -*- coding: utf-8 -*-
"""走廊窗口测试：斜栅格朝向、半宽口径、窗内判定与建场外扩。

职责：验证 ``corridor_window`` 的几何换算与 ``required_pad_m`` 的对齐整除性。
边界：不建 SDF、不测路径搜索。
相关：``methods/spatial/corridor_window.py``、``common/grid_spec.py``。

覆盖三处"错了不报错"的地方：斜栅格朝向接反、半宽按 ratio 却漏了 margin、
外扩不是格距公倍数导致全域格点整体平移。
"""

from __future__ import annotations

import math
import unittest

import numpy as np

from path_planning.methods.spatial.corridor_window import (
    CorridorRule,
    corridor_window,
    required_pad_m,
    window_area_m2,
    z_envelope_from_ground,
)


class TestCorridorWindowGeometry(unittest.TestCase):
    def test_yaw_matches_bearing(self) -> None:
        w = corridor_window((0.0, 0.0, 10.0), (100.0, 0.0, 10.0), CorridorRule(width_ratio=0.2), (4.0, 4.0, 2.0), 0.0, 40.0)
        self.assertAlmostEqual(w.grid.yaw_rad, 0.0, places=12)
        w2 = corridor_window((0.0, 0.0, 10.0), (0.0, 100.0, 10.0), CorridorRule(width_ratio=0.2), (4.0, 4.0, 2.0), 0.0, 40.0)
        self.assertAlmostEqual(w2.grid.yaw_rad, math.pi / 2, places=12)

    def test_axes_are_u_and_normal(self) -> None:
        w = corridor_window((10.0, 20.0, 5.0), (110.0, 120.0, 5.0), CorridorRule(half_width_m=15.0), (4.0, 4.0, 2.0), 0.0, 40.0)
        a0, a1 = w.grid.axes_xy
        np.testing.assert_allclose(a0, w.u, atol=1e-12)
        np.testing.assert_allclose(a1, w.normal, atol=1e-12)

    def test_endpoints_inside_grid(self) -> None:
        """margin=0 时起点恰在格 0 上，浮点误差必须被容差吃掉。"""
        rule = CorridorRule(half_width_m=10.0, margin_m=0.0)
        w = corridor_window((0.0, 0.0, 10.0), (100.0, 0.0, 10.0), rule, (4.0, 4.0, 2.0), 0.0, 40.0)
        pts = np.array([[0.0, 0.0, 10.0], [100.0, 0.0, 10.0]])
        self.assertTrue(np.all(w.inside_grid(pts)))

    def test_point_outside_lateral_is_outside(self) -> None:
        rule = CorridorRule(half_width_m=10.0)
        w = corridor_window((0.0, 0.0, 10.0), (100.0, 0.0, 10.0), rule, (4.0, 4.0, 2.0), 0.0, 40.0)
        self.assertFalse(bool(w.inside_grid(np.array([[50.0, 30.0, 10.0]]))[0]))

    def test_half_width_ratio_semantics(self) -> None:
        """``width_ratio`` 是**总宽**比：半宽 = 0.5·ratio·L。"""
        rule = CorridorRule(width_ratio=0.2)
        w = corridor_window((0.0, 0.0, 0.0), (100.0, 0.0, 0.0), rule, (4.0, 4.0, 2.0), 0.0, 40.0)
        self.assertAlmostEqual(w.half_width_m, 10.0, places=12)

    def test_explicit_half_width_wins(self) -> None:
        rule = CorridorRule(width_ratio=0.2, half_width_m=25.0)
        self.assertAlmostEqual(rule.half_width_for(100.0), 25.0, places=12)

    def test_lateral_includes_margin(self) -> None:
        rule = CorridorRule(half_width_m=10.0, margin_m=5.0)
        w = corridor_window((0.0, 0.0, 0.0), (100.0, 0.0, 0.0), rule, (4.0, 4.0, 2.0), 0.0, 40.0)
        self.assertAlmostEqual(w.lateral_m, 15.0, places=12)

    def test_lateral_of_measures_normal_distance(self) -> None:
        w = corridor_window((0.0, 0.0, 0.0), (100.0, 0.0, 0.0), CorridorRule(half_width_m=10.0), (4.0, 4.0, 2.0), 0.0, 40.0)
        pts = np.array([[50.0, 0.0, 10.0], [50.0, 7.0, 10.0], [50.0, -9.0, 10.0]])
        np.testing.assert_allclose(w.lateral_of(pts), [0.0, 7.0, 9.0], atol=1e-12)

    def test_vertical_layers_use_ceil(self) -> None:
        """不整除时必须 ceil，否则带顶合法点被判窗外。"""
        w = corridor_window((0.0, 0.0, 10.0), (100.0, 0.0, 10.0), CorridorRule(half_width_m=10.0), (4.0, 4.0, 3.2), 10.0, 120.0)
        top = w.grid.origin[2] + (w.grid.shape[2] - 1) * 3.2
        self.assertGreaterEqual(top, 120.0, "窗口顶未覆盖飞行带天花板")

    def test_horizontal_coincidence_raises(self) -> None:
        with self.assertRaises(ValueError):
            corridor_window((0.0, 0.0, 10.0), (0.0, 0.0, 30.0), CorridorRule(half_width_m=10.0), (4.0, 4.0, 2.0), 0.0, 40.0)

    def test_degenerate_altitudes_raise(self) -> None:
        with self.assertRaises(ValueError):
            corridor_window((0.0, 0.0, 10.0), (100.0, 0.0, 10.0), CorridorRule(half_width_m=10.0), (4.0, 4.0, 2.0), 40.0, 40.0)

    def test_window_area_is_band_not_bbox(self) -> None:
        """斜向走廊下窗口面积应接近带面积，而不是 ENU 外接矩形。"""
        rule = CorridorRule(half_width_m=10.0)
        w = corridor_window((0.0, 0.0, 0.0), (200.0, 200.0, 0.0), rule, (4.0, 4.0, 2.0), 0.0, 40.0)
        band = w.horizontal_length_m * 2.0 * w.lateral_m
        self.assertLess(abs(window_area_m2(w) - band) / band, 0.25)

    def test_enu_bounds_is_superset(self) -> None:
        w = corridor_window((0.0, 0.0, 0.0), (200.0, 0.0, 0.0), CorridorRule(half_width_m=10.0), (4.0, 4.0, 2.0), 0.0, 40.0)
        x0, y0, x1, y1 = w.enu_bounds
        # margin=0：轴向从 0 到 L，横向从 -lateral 到 +lateral
        self.assertLessEqual(x0, 0.0 + 1e-9)
        self.assertLessEqual(y0, -10.0)
        self.assertGreaterEqual(x1, 200.0 - 1e-9)
        self.assertGreaterEqual(y1, 10.0)


class TestRequiredPad(unittest.TestCase):
    def test_pad_is_common_multiple(self) -> None:
        pad = required_pad_m(
            CorridorRule(width_ratio=0.2, margin_m=20.0),
            (6.0, 4.0, 2.0),
            3000.0,
            extra_m=50.0,
        )
        self.assertAlmostEqual(pad % 12.0, 0.0, places=6, msg="pad 必须是 lcm(6,4)=12 的倍数")

    def test_pad_monotone_in_length(self) -> None:
        rule = CorridorRule(width_ratio=0.2, margin_m=10.0)
        p1 = required_pad_m(rule, (4.0, 4.0, 2.0), 1000.0, extra_m=0.0)
        p2 = required_pad_m(rule, (4.0, 4.0, 2.0), 5000.0, extra_m=0.0)
        self.assertGreater(p2, p1)

    def test_pad_honours_min(self) -> None:
        pad = required_pad_m(CorridorRule(half_width_m=5.0), (4.0, 4.0, 2.0), 100.0, extra_m=0.0, min_pad_m=500.0)
        self.assertGreaterEqual(pad, 500.0)

    def test_incommensurable_spacing_raises(self) -> None:
        # limit_denominator 会把 π 近似成 355/113（仍可公度），所以这里用真正超限的间距
        with self.assertRaises(ValueError):
            required_pad_m(CorridorRule(half_width_m=5.0), (100000.0, 1.0, 2.0), 100.0, extra_m=0.0)


class TestZEnvelope(unittest.TestCase):
    def test_envelope(self) -> None:
        lo, hi = z_envelope_from_ground(12.0, 5.0, 120.0)
        self.assertAlmostEqual(lo, 17.0)
        self.assertAlmostEqual(hi, 132.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
