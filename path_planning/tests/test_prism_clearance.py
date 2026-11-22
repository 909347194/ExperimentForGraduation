# -*- coding: utf-8 -*-
"""真几何净空测试：竖直/水平/角部距离、端点命中的 worst 点、量程截断、与 SDF 的保守关系。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.grid_spec import GridSpec
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.spatial.prism_clearance import (
    DEFAULT_SEARCH_RADIUS_M,
    PrismClearance,
    densify_polyline,
)

WALL = ObstaclePrism(
    np.array([[10.0, -5.0], [12.0, -5.0], [12.0, 5.0], [10.0, 5.0]]),
    0.0, 10.0, kind="building",
)


class TestDensify(unittest.TestCase):
    def test_includes_both_ends(self) -> None:
        pts = densify_polyline(np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]), 2.0)
        self.assertEqual(pts.shape[1], 3)
        np.testing.assert_allclose(pts[0], (0.0, 0.0, 0.0))
        np.testing.assert_allclose(pts[-1], (10.0, 0.0, 0.0))
        self.assertEqual(pts.shape[0], 6)  # 0,2,...,10

    def test_single_point_passes_through(self) -> None:
        pts = densify_polyline(np.array([[1.0, 2.0, 3.0]]), 2.0)
        self.assertEqual(pts.shape, (1, 3))


class TestPrismClearance(unittest.TestCase):
    def test_vertical_distance_above_top(self) -> None:
        """正上方飞越：水平距离到 footprint 内为 0，净空 = z − 顶。"""
        pc = PrismClearance(prisms=[WALL], search_radius_m=20.0)
        path = np.array([[11.0, 0.0, 13.0], [11.0, 0.0, 13.0]])
        d, _ = pc.worst(path, 1.0)
        self.assertAlmostEqual(d, 3.0, places=9)

    def test_horizontal_distance_beside(self) -> None:
        pc = PrismClearance(prisms=[WALL], search_radius_m=20.0)
        path = np.array([[8.0, 0.0, 4.0], [8.0, 0.0, 4.0]])
        d, _ = pc.worst(path, 1.0)
        self.assertAlmostEqual(d, 2.0, places=9)  # x=8 到 10

    def test_inside_prism_is_zero(self) -> None:
        pc = PrismClearance(prisms=[WALL], search_radius_m=20.0)
        path = np.array([[11.0, 0.0, 5.0], [11.0, 0.0, 5.0]])
        self.assertAlmostEqual(pc.min_clearance(path, 1.0), 0.0, places=9)
        self.assertTrue(pc.contains((11.0, 0.0, 5.0)))
        self.assertFalse(pc.contains((11.0, 0.0, 12.0)))

    def test_worst_point_is_reported(self) -> None:
        """穿墙直线：最差点必须落在墙内那一小段，而不是起点。"""
        pc = PrismClearance(prisms=[WALL], search_radius_m=20.0)
        path = np.array([[0.0, 0.0, 6.0], [20.0, 0.0, 6.0]])
        d, wp = pc.worst(path, 1.0)
        self.assertAlmostEqual(d, 0.0, places=9)
        self.assertGreater(wp[0], 9.0)  # 落在墙附近而不是 x=0
        self.assertLess(wp[0], 13.0)

    def test_clipped_at_search_radius(self) -> None:
        pc = PrismClearance(prisms=[WALL], search_radius_m=5.0)
        path = np.array([[0.0, 0.0, 4.0], [0.0, 0.0, 4.0]])
        d, _ = pc.worst(path, 1.0)
        self.assertEqual(d, 5.0)  # 真实 10 m，超量程截到 5

    def test_empty_prisms_returns_radius(self) -> None:
        pc = PrismClearance(prisms=[], search_radius_m=DEFAULT_SEARCH_RADIUS_M)
        d, p = pc.worst(np.array([[0.0, 0.0, 5.0], [1.0, 0.0, 5.0]]), 1.0)
        self.assertEqual(d, DEFAULT_SEARCH_RADIUS_M)
        self.assertEqual(p, (0.0, 0.0, 5.0))

    def test_true_clearance_not_above_sdf(self) -> None:
        """保守膨胀后的 SDF 是**下界**：SDF 读数不得高于真几何（否则规划器被欺骗）。"""
        grid = GridSpec(origin=(0.0, -10.0, 0.0), spacing=(1.0, 1.0, 1.0),
                        shape=(25, 21, 16), yaw_rad=0.0)
        occ = build_occupancy([WALL], grid)
        field = SdfField(grid, build_sdf(occ, grid.spacing))
        pc = PrismClearance(prisms=[WALL], search_radius_m=20.0)
        rng = np.random.default_rng(7)
        xs = rng.uniform(1.0, 22.0, 40)
        ys = rng.uniform(-8.0, 8.0, 40)
        zs = rng.uniform(2.0, 14.0, 40)
        sdf_vals = field.at(np.column_stack([xs, ys, zs]))
        true_vals = np.array([pc.worst([[x, y, z], [x, y, z]], 1.0)[0]
                               for x, y, z in zip(xs, ys, zs)])
        # SDF 允许比真值低（保守）；但不得显著高于真值（乐观超半格对角线 + 容差）
        excess = sdf_vals - true_vals
        self.assertLess(np.max(excess), 2.0,
                        f"SDF 比真几何乐观超出预期：{np.max(excess):.2f} m")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
