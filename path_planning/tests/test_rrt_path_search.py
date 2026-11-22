# -*- coding: utf-8 -*-
"""RRT 初始路径搜索测试：直连、绕障、确定性、真高带、端点豁免、多初值。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_params import PlanParams
from path_planning.methods.spatial.corridor_window import CorridorRule, corridor_window
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.spatial.rrt_path_search import RRT3D, RRTConfig

SPACING = (2.0, 2.0, 2.0)
ALT = (4.0, 24.0)

#: 横在走廊中段、低于带顶的墙：必须绕（从顶上过），直连必穿
WALL = ObstaclePrism(
    np.array([[19.0, -14.0], [21.0, -14.0], [21.0, 14.0], [19.0, 14.0]]), 0.0, 14.0
)
#: 同一面墙但封到带顶以上：走廊内无自由通路
BLOCK_WALL = ObstaclePrism(
    np.array([[19.0, -14.0], [21.0, -14.0], [21.0, 14.0], [19.0, 14.0]]), 0.0, 26.0
)
#: 窗边远处的小柱：只为让空场的 SDF 有限（全空场的 EDT 无零点、值为 inf）
FAR_PILLAR = ObstaclePrism(
    np.array([[0.0, -13.0], [2.0, -13.0], [2.0, -11.0], [0.0, -11.0]]), 0.0, 10.0
)


def _window_field(prisms, *, alt=ALT, half_width=10.0, margin=2.0):
    start, goal = (0.0, 0.0, 10.0), (40.0, 0.0, 10.0)
    window = corridor_window(
        start, goal, CorridorRule(half_width_m=half_width, margin_m=margin), SPACING, *alt
    )
    occ = build_occupancy(prisms, window.grid)
    return start, goal, SdfField(window.grid, build_sdf(occ, SPACING)), window


def _cfg(**over):
    kw = dict(
        step_m=6.0,
        min_step_m=2.0,
        sdf_step_scale=0.5,
        max_iters=4000,
        goal_sample_rate=0.15,
        clearance_m=2.0,
        seed=7,
        sample_step_m=1.0,
        shortcut_iters=100,
    )
    kw.update(over)
    return RRTConfig(**kw)


class TestRRTBasics(unittest.TestCase):
    def test_straight_when_free(self) -> None:
        start, goal, field, window = _window_field([FAR_PILLAR])
        rrt = RRT3D(field, window, ALT, _cfg())
        res = rrt.plan(start, goal)
        self.assertIsNotNone(res)
        self.assertEqual(len(res.path_enu), 2)
        self.assertEqual(res.iterations, 0)
        self.assertEqual(res.nodes, 0)
        self.assertEqual(res.path_points_before_shortcut, 2)

    def test_wall_is_routed_over(self) -> None:
        start, goal, field, window = _window_field([WALL])
        rrt = RRT3D(field, window, ALT, _cfg())
        res = rrt.plan(start, goal)
        self.assertIsNotNone(res, "墙低于带顶，应能从上方绕过")
        path = res.path_enu
        np.testing.assert_allclose(path[0], start, atol=1e-9)
        np.testing.assert_allclose(path[-1], goal, atol=1e-9)
        # 墙顶 14 m + 净空 2 m：必然出现高于 15 m 的点
        self.assertGreater(float(path[:, 2].max()), 15.0)
        # 每条边（捷径化后的）仍满足净空：捷径化绝不接未校验的边
        for a, b in zip(path[:-1], path[1:]):
            self.assertGreaterEqual(field.segment_min(a, b, 1.0), 2.0 - 1e-6)
        self.assertTrue(bool(window.inside_grid(path).all()))
        self.assertGreater(res.iterations, 0)

    def test_fully_blocked_returns_none(self) -> None:
        start, goal, field, window = _window_field([BLOCK_WALL])
        rrt = RRT3D(field, window, ALT, _cfg(max_iters=800))
        self.assertIsNone(rrt.plan(start, goal))

    def test_same_seed_is_reproducible(self) -> None:
        start, goal, field, window = _window_field([WALL])
        rrt = RRT3D(field, window, ALT, _cfg())
        r1 = rrt.plan(start, goal, seed=3)
        r2 = rrt.plan(start, goal, seed=3)
        self.assertIsNotNone(r1)
        np.testing.assert_array_equal(r1.path_enu, r2.path_enu)
        self.assertEqual(r1.iterations, r2.iterations)

    def test_plan_many_multi_seed(self) -> None:
        start, goal, field, window = _window_field([WALL])
        rrt = RRT3D(field, window, ALT, _cfg())
        results = rrt.plan_many(start, goal, initializations=4)
        self.assertGreaterEqual(len(results), 2, "4 个种子至少应有 2 条候选")
        for res in results:
            np.testing.assert_allclose(res.path_enu[0], start, atol=1e-9)
            np.testing.assert_allclose(res.path_enu[-1], goal, atol=1e-9)


class TestEndpointExemption(unittest.TestCase):
    """端点豁免：起点悬在"停机坪"障碍上方（SDF≈1 < 净空 2）时的行为。

    场景：起点 (0,0,9.5) 正下方是一块 z≤8 的坪（格点插值下 SDF≈1），
    走廊中段另有一面 z≤12 的墙迫使绕行。不豁免 → 死在起点自检；
    豁免 → 起飞翻墙，且豁免圈外仍按全程净空。
    """

    def _setup(self):
        start, goal = (0.0, 0.0, 9.5), (20.0, 0.0, 10.0)
        pad = ObstaclePrism(
            np.array([[-2.0, -2.0], [2.0, -2.0], [2.0, 2.0], [-2.0, 2.0]]), 0.0, 8.0
        )
        wall = ObstaclePrism(
            np.array([[9.0, -13.0], [11.0, -13.0], [11.0, 13.0], [9.0, 13.0]]), 0.0, 12.0
        )
        window = corridor_window(
            start, goal, CorridorRule(half_width_m=10.0, margin_m=2.0), SPACING, *ALT
        )
        occ = build_occupancy([pad, wall], window.grid)
        return start, goal, SdfField(window.grid, build_sdf(occ, SPACING)), window

    def test_without_exemption_start_rejected(self) -> None:
        start, goal, field, window = self._setup()
        rrt = RRT3D(field, window, ALT, _cfg())
        self.assertLess(field.at_one(*start), 2.0, "测试前提：起点 SDF 低于净空阈值")
        self.assertIsNone(rrt.plan(start, goal))

    def test_with_exemption_path_found(self) -> None:
        start, goal, field, window = self._setup()
        cfg = _cfg(endpoint_clearance_m=0.5, endpoint_exempt_radius_m=4.0)
        res = RRT3D(field, window, ALT, cfg).plan(start, goal)
        self.assertIsNotNone(res, "豁免圈内放宽阈值后应能起飞翻墙")
        self.assertGreater(res.iterations, 0, "墙迫使绕行，不应是直连")
        path = res.path_enu
        # 圈外部分仍按全程净空逐点复核；圈内的点按豁免阈值
        start_a, goal_a = np.asarray(start), np.asarray(goal)
        for a, b in zip(path[:-1], path[1:]):
            n = max(2, int(np.ceil(float(np.linalg.norm(b - a)) / 1.0)) + 1)
            ts = np.linspace(0.0, 1.0, n)[:, None]
            pts = a[None, :] + ts * (b - a)[None, :]
            near = np.minimum(
                np.linalg.norm(pts - start_a, axis=1),
                np.linalg.norm(pts - goal_a, axis=1),
            )
            outside = pts[near > 4.0]
            if len(outside):
                self.assertGreaterEqual(
                    float(field.at(outside).min()), 2.0 - 1e-6,
                    "豁免圈外必须仍满足全程净空",
                )


class TestAglBand(unittest.TestCase):
    def _flat_ground(self, xs, ys) -> np.ndarray:
        return np.full(np.asarray(xs).shape, 10.0)

    def test_band_violating_endpoint_rejected(self) -> None:
        start, goal, field, window = _window_field([FAR_PILLAR])
        # start z=10、地面 10 → 真高 0，低于带下沿 2
        rrt = RRT3D(
            field, window, ALT, _cfg(), ground_fn=self._flat_ground, agl_range_m=(2.0, 6.0)
        )
        self.assertIsNone(rrt.plan(start, goal))

    def test_band_ok_passes(self) -> None:
        start, goal = (0.0, 0.0, 14.0), (40.0, 0.0, 14.0)
        window = corridor_window(
            start, goal, CorridorRule(half_width_m=10.0, margin_m=2.0), SPACING, *ALT
        )
        occ = build_occupancy([FAR_PILLAR], window.grid)
        field = SdfField(window.grid, build_sdf(occ, SPACING))
        rrt = RRT3D(
            field, window, ALT, _cfg(), ground_fn=self._flat_ground, agl_range_m=(2.0, 6.0)
        )
        res = rrt.plan(start, goal)
        self.assertIsNotNone(res)
        self.assertTrue(
            bool(((res.path_enu[:, 2] >= 12.0) & (res.path_enu[:, 2] <= 16.0)).all())
        )


class TestRRTConfig(unittest.TestCase):
    def test_from_params(self) -> None:
        params = PlanParams()
        cfg = RRTConfig.from_params(params, clearance_m=6.0, seed=42)
        self.assertEqual(cfg.step_m, params.rrt_step_m)
        self.assertEqual(cfg.min_step_m, params.rrt_min_step_m)
        self.assertEqual(cfg.sample_step_m, params.clearance_sample_step_m)
        self.assertEqual(cfg.shortcut_iters, params.rrt_shortcut_iters)
        self.assertEqual(cfg.clearance_m, 6.0)
        self.assertEqual(cfg.seed, 42)
        self.assertEqual(cfg.effective_endpoint_clearance_m, 6.0)

    def test_from_params_with_exemption(self) -> None:
        cfg = RRTConfig.from_params(
            PlanParams(), clearance_m=6.0, seed=1,
            endpoint_clearance_m=0.5, endpoint_exempt_radius_m=5.0,
        )
        self.assertEqual(cfg.effective_endpoint_clearance_m, 0.5)
        self.assertEqual(cfg.endpoint_exempt_radius_m, 5.0)

    def test_invalid_config_raises(self) -> None:
        with self.assertRaises(ValueError):
            _cfg(step_m=2.0, min_step_m=2.0)
        with self.assertRaises(ValueError):
            _cfg(goal_sample_rate=1.0)
        with self.assertRaises(ValueError):
            _cfg(endpoint_clearance_m=-1.0, endpoint_exempt_radius_m=4.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
