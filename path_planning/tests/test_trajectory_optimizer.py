# -*- coding: utf-8 -*-
"""时空联合优化测试：直连最短时间、绕墙、口径 B 速度复核、Hermite 自洽。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_params import PlanParams
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.spatial.corridor_window import CorridorRule, corridor_window
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.timeline.splines import segment_velocity
from path_planning.methods.timeline.trajectory_optimizer import (
    NlpConfig,
    optimize_trajectory,
)

SPACING = (2.0, 2.0, 2.0)
ALT = (4.0, 24.0)
START, GOAL = (0.0, 0.0, 10.0), (40.0, 0.0, 10.0)

FAR_PILLAR = ObstaclePrism(
    np.array([[0.0, -13.0], [2.0, -13.0], [2.0, -11.0], [0.0, -11.0]]), 0.0, 10.0
)
WALL = ObstaclePrism(
    np.array([[19.0, -14.0], [21.0, -14.0], [21.0, 14.0], [19.0, 14.0]]), 0.0, 14.0
)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=50000.0, max_time_s=3000.0,
    )


def _window_field(prisms):
    window = corridor_window(
        START, GOAL, CorridorRule(half_width_m=10.0, margin_m=2.0), SPACING, *ALT
    )
    occ = build_occupancy(prisms, window.grid)
    return SdfField(window.grid, build_sdf(occ, SPACING)), window


def _cfg(field_window, params, **over):
    _, window = field_window
    kw = dict(
        clearance_m=2.0,
        half_width_m=window.half_width_m,
        corridor_normal_xy=tuple(float(c) for c in window.normal),
        corridor_pivot_xy=window.start_xy,
    )
    kw.update(over)
    return NlpConfig.from_params(params, _vehicle(), **kw)


class TestNlpConfig(unittest.TestCase):
    def test_from_params(self) -> None:
        cfg = NlpConfig.from_params(PlanParams(), _vehicle(), clearance_m=6.0)
        self.assertEqual(cfg.n_nodes, PlanParams().n_nodes)
        self.assertAlmostEqual(cfg.v_max_plan_mps, 13.5, places=6)
        self.assertAlmostEqual(cfg.thrust_max_per_mass, 16.0, places=6)
        self.assertEqual(cfg.endpoint_exempt_segments, 1)


class TestOptimizeStraight(unittest.TestCase):
    def test_straight_free(self) -> None:
        fw = _window_field([FAR_PILLAR])
        field, _ = fw
        params = PlanParams(n_nodes=20, max_iter=300)
        cfg = _cfg(fw, params)
        path = np.array([START, GOAL], dtype=float)
        res = optimize_trajectory(path, field, cfg, uav_id=1, t0=100.0)
        self.assertIsNotNone(res.trajectory, f"求解失败：{res.solver_status} {res.message}")
        traj = res.trajectory
        traj.validate()  # 时间/位置连续
        self.assertEqual(traj.uav_id, 1)
        self.assertAlmostEqual(traj.t0, 100.0, places=6)
        np.testing.assert_allclose(traj.start_position, START, atol=1e-3)
        np.testing.assert_allclose(traj.end_position, GOAL, atol=1e-3)
        # 端点悬停
        np.testing.assert_allclose(traj.segments[0].v0, 0, atol=1e-3)
        np.testing.assert_allclose(traj.segments[-1].v1, 0, atol=1e-3)
        self.assertGreater(traj.duration, 0.0)
        # 直线：弧长≈弦长 40
        self.assertAlmostEqual(traj.path_len_m, 40.0, delta=1.0)

    def test_speed_limits_obeyed(self) -> None:
        fw = _window_field([FAR_PILLAR])
        field, _ = fw
        cfg = _cfg(fw, PlanParams(n_nodes=20, max_iter=300))
        res = optimize_trajectory(np.array([START, GOAL], dtype=float), field, cfg)
        self.assertIsNotNone(res.trajectory)
        veh = _vehicle()
        for seg in res.trajectory.segments:
            for s in np.linspace(0, 1, 5):
                v = segment_velocity(seg, s)
                self.assertLessEqual(np.hypot(v[0], v[1]), veh.v_max_plan_mps + 1e-2)
                self.assertLessEqual(v[2], veh.v_up_mps + 1e-2)
                self.assertGreaterEqual(v[2], -veh.v_dn_mps - 1e-2)
                self.assertLessEqual(np.linalg.norm(v), veh.v_max_mps + 1e-2)


class TestOptimizeWall(unittest.TestCase):
    def test_wall_routed_over(self) -> None:
        fw = _window_field([WALL])
        field, _ = fw
        params = PlanParams(n_nodes=30, max_iter=500, retreat_node_ladder=(20,))
        cfg = _cfg(fw, params)
        path = np.array([START, (20.0, 0.0, 18.0), GOAL], dtype=float)
        res = optimize_trajectory(path, field, cfg, uav_id=2)
        self.assertIsNotNone(res.trajectory, f"求解失败：{res.solver_status} {res.message}")
        traj = res.trajectory
        traj.validate()
        zmax = max(max(s.p0[2], s.p1[2]) for s in traj.segments)
        self.assertGreater(zmax, 15.0, "墙顶 14 + 净空 2，必须翻到 15 m 以上")
        np.testing.assert_allclose(traj.start_position, START, atol=1e-2)
        np.testing.assert_allclose(traj.end_position, GOAL, atol=1e-2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
