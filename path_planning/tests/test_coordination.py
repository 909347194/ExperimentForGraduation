# -*- coding: utf-8 -*-
"""协调层测试：局部域权重、候选次序与端点冻结、局部重规划端到端（含真几何闸）。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import HermiteSegment, Trajectory, TimeSource
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.coordination.detour_candidates import generate_detours
from path_planning.methods.coordination.local_region import (
    LocalRegion,
    region_from_conflict,
    transition_weight,
)
from path_planning.methods.coordination.local_replan import (
    LocalReplanRequest,
    replan_locally,
)
from path_planning.methods.spatial.corridor_window import CorridorRule, corridor_window
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.timeline.feasibility import VerificationLimits
from path_planning.methods.timeline.trajectory_optimizer import NlpConfig
from path_planning.methods.timeline.splines import position_at

SPACING = (2.0, 2.0, 2.0)
ALT = (4.0, 24.0)
START, GOAL = (0.0, 0.0, 10.0), (40.0, 0.0, 10.0)

WALL = ObstaclePrism(
    np.array([[19.0, -14.0], [21.0, -14.0], [21.0, 14.0], [19.0, 14.0]]), 0.0, 14.0
)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=50000.0, max_time_s=3000.0,
    )


class TestLocalRegion(unittest.TestCase):
    def test_weight_is_bell(self) -> None:
        self.assertAlmostEqual(transition_weight(0.0, 10.0), 1.0)
        self.assertAlmostEqual(transition_weight(5.0, 10.0), 0.5)
        self.assertEqual(transition_weight(10.0, 10.0), 0.0)
        self.assertEqual(transition_weight(25.0, 10.0), 0.0)

    def test_region_from_conflict_takes_midpoint(self) -> None:
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(2, (0.0, 20.0, 10.0), (40.0, 20.0, 10.0), 4.0)
        r = region_from_conflict(a, b, 2.0, margin_m=10.0)
        self.assertAlmostEqual(r.center_xy[0], 20.0, places=6)
        self.assertAlmostEqual(r.center_xy[1], 10.0, places=6)  # 两机 y 的中点
        self.assertEqual(r.time_window, (2.0 - 10.0, 2.0 + 10.0))

    def test_invalid_radius(self) -> None:
        with self.assertRaises(ValueError):
            LocalRegion(center_xy=(0.0, 0.0), radius_m=0.0, t_cpa=1.0)


class TestDetourCandidates(unittest.TestCase):
    def _path(self) -> np.ndarray:
        return np.array([[0.0, 0.0, 10.0], [10.0, 0.0, 10.0],
                         [30.0, 0.0, 10.0], [40.0, 0.0, 10.0]])

    def test_endpoints_frozen_and_priority(self) -> None:
        region = LocalRegion(center_xy=(20.0, 0.0), radius_m=12.0, t_cpa=2.0)
        cands = generate_detours(self._path(), region,
                                 vertical_steps_m=(8.0, 16.0),
                                 lateral_steps_m=(12.0,))
        self.assertEqual(len(cands), 6)  # 2 竖直档 ×2 方向 + 1 水平档 ×2
        self.assertEqual(cands[0].label, "vertical_up_8m")
        self.assertEqual(cands[1].label, "vertical_down_8m")
        for c in cands:
            np.testing.assert_allclose(c.path[0], self._path()[0])
            np.testing.assert_allclose(c.path[-1], self._path()[-1])
            self.assertGreater(c.moved_nodes, 0)

    def test_vertical_up_shift_sign(self) -> None:
        region = LocalRegion(center_xy=(20.0, 0.0), radius_m=12.0, t_cpa=2.0)
        cands = generate_detours(self._path(), region,
                                 vertical_steps_m=(8.0,), lateral_steps_m=())
        up = cands[0]
        # 中心节点（x=20 处插值）位移 = 8 m；这里用最近节点 x=30 的权重核对方向
        self.assertTrue(np.all(up.path[:, 2] >= self._path()[:, 2]))
        self.assertAlmostEqual(float(up.path[2, 2] - 10.0), 8.0 * region.weight((30.0, 0.0)))

    def test_outside_region_gives_nothing(self) -> None:
        region = LocalRegion(center_xy=(200.0, 0.0), radius_m=5.0, t_cpa=2.0)
        cands = generate_detours(self._path(), region,
                                 vertical_steps_m=(8.0,), lateral_steps_m=(12.0,))
        self.assertEqual(cands, [])


class TestLocalReplan(unittest.TestCase):
    def test_routes_over_wall_and_passes_true_geometry(self) -> None:
        window = corridor_window(START, GOAL, CorridorRule(half_width_m=10.0, margin_m=2.0),
                                 SPACING, *ALT)
        field = SdfField(window.grid, build_sdf(build_occupancy([WALL], window.grid), SPACING))
        params = PlanParams(n_nodes=30, max_iter=500, retreat_node_ladder=(20,))
        path = np.array([START, (19.0, 0.0, 10.0), (21.0, 0.0, 10.0), GOAL], dtype=float)
        nlp = NlpConfig.from_params(
            params, _vehicle(), clearance_m=2.0, half_width_m=window.half_width_m,
            corridor_normal_xy=tuple(float(c) for c in window.normal),
            corridor_pivot_xy=window.start_xy,
        )
        limits = VerificationLimits(
            clearance_m=2.0, half_width_m=window.half_width_m,
            corridor_normal_xy=tuple(float(c) for c in window.normal),
            corridor_pivot_xy=window.start_xy, endpoint_exempt_segments=1,
            sample_step_m=params.clearance_sample_step_m,
            expected_start=START, expected_end=GOAL,
        )
        req = LocalReplanRequest(
            path=path, uav_id=1, t0=0.0, nlp=nlp, limits=limits,
            region=LocalRegion(center_xy=(20.0, 0.0), radius_m=18.0, t_cpa=2.0),
            params=params, vehicle=_vehicle(), clearance_fix_m=2.0,
            prisms=PrismClearance([WALL], search_radius_m=30.0),
        )
        out = replan_locally(req, field)
        self.assertTrue(out.ok, [t.reason for t in out.trials])
        traj = out.trajectory
        traj.validate()
        self.assertGreater(max(s.p0[2] for s in traj.segments), 14.0,
                           "墙顶 14 m + 净空 2 m，必须翻过去")
        clear, _ = req.prisms.worst(
            np.array([position_at(traj, t) for t in np.linspace(traj.t0, traj.t_end, 60)]),
            params.clearance_sample_step_m,
        )
        self.assertGreaterEqual(clear, 2.0 - 1e-6)

    def test_no_movable_nodes_reports_reason(self) -> None:
        window = corridor_window(START, GOAL, CorridorRule(half_width_m=10.0, margin_m=2.0),
                                 SPACING, *ALT)
        field = SdfField(window.grid, build_sdf(build_occupancy([], window.grid), SPACING))
        params = PlanParams(n_nodes=20, max_iter=300)
        nlp = NlpConfig.from_params(params, _vehicle(), clearance_m=2.0)
        limits = VerificationLimits(clearance_m=2.0, endpoint_exempt_segments=1)
        req = LocalReplanRequest(
            path=np.array([START, GOAL], dtype=float), uav_id=1, t0=0.0, nlp=nlp,
            limits=limits,
            region=LocalRegion(center_xy=(500.0, 0.0), radius_m=5.0, t_cpa=2.0),
            params=params, vehicle=_vehicle(), clearance_fix_m=2.0, prisms=None,
        )
        out = replan_locally(req, field)
        self.assertFalse(out.ok)
        self.assertEqual(out.reason, FailureCode.NO_LOCAL_SOLUTION)
        self.assertIn("没有可移动", out.details["cause"])


def _line(uav_id, p0, p1, dur, t0=0.0) -> Trajectory:
    u = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
    v = u / dur
    seg = HermiteSegment(t0=t0, t1=t0 + dur, p0=p0, p1=p1, v0=tuple(v), v1=tuple(v))
    return Trajectory(uav_id=uav_id, segments=(seg,), path_len_m=float(np.linalg.norm(u)),
                      time_source=TimeSource.TRAJECTORY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
