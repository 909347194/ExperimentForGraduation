# -*- coding: utf-8 -*-
"""协调编排测试：双机交叉 → 只改机号大的一台 → 全对复检 → 冲突清零。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.grid_spec import GridSpec
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import HermiteSegment, Trajectory, TimeSource
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.conflict.conflict_detector import DetectionConfig
from path_planning.methods.coordination.global_recheck import coordinate_conflicts
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.timeline.trajectory_optimizer import NlpConfig

FAR_PILLAR = ObstaclePrism(
    np.array([[60.0, -2.0], [62.0, -2.0], [62.0, 2.0], [60.0, 2.0]]), 0.0, 20.0
)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=50000.0, max_time_s=3000.0,
    )


def _field() -> SdfField:
    grid = GridSpec(origin=(-20.0, -50.0, 0.0), spacing=(2.0, 2.0, 2.0),
                    shape=(46, 61, 26), yaw_rad=0.0)
    return SdfField(grid, build_sdf(build_occupancy([FAR_PILLAR], grid), grid.spacing))


def _line(uav_id, p0, p1, dur, t0=0.0) -> Trajectory:
    u = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
    v = u / dur
    seg = HermiteSegment(t0=t0, t1=t0 + dur, p0=p0, p1=p1, v0=tuple(v), v1=tuple(v))
    return Trajectory(uav_id=uav_id, segments=(seg,), path_len_m=float(np.linalg.norm(u)),
                      time_source=TimeSource.TRAJECTORY)


def _nlp(params: PlanParams) -> NlpConfig:
    return NlpConfig.from_params(params, _vehicle(), clearance_m=2.0)


class TestCoordinateConflicts(unittest.TestCase):
    def test_crossing_resolved_by_local_detour(self) -> None:
        """双机十字交叉：机 2 让路，一轮内冲突清零，端点与 t0 冻结。

        几何为什么是 90 m 长：让路域半径 100 m、椭球水平半轴 30 m，若被让路航线的
        端点离对方航线只剩 25 m（旧版 50 m 长的场景），那么**任何**冻结端点的局部
        位移都消不掉冲突——起点本身已在安全泡内。这不是"算法改不动"，而是场景无解。
        """
        params = PlanParams(n_nodes=30, max_iter=500, retreat_node_ladder=(20,))
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(2, (20.0, -45.0, 10.0), (20.0, 45.0, 10.0), 8.0)
        out = coordinate_conflicts(
            [a, b], field=_field(), params=params, vehicle=_vehicle(),
            detection=DetectionConfig.from_params(params, _vehicle()),
            nlp_for=lambda _i: _nlp(params), prisms=None,
        )
        self.assertTrue(out.ok, [r.round_index for r in out.rounds])
        self.assertEqual(len(out.rounds), 1)
        self.assertEqual(out.rounds[0].replanned, (2,))
        self.assertEqual(len(out.report.conflicts), 0)
        # 让路必须真的落到轨迹上（位置是自由变量，只给初值会被 min ΣΔt 抹平）
        p0 = np.array([20.0, -45.0, 10.0])
        dir_v = np.array([0.0, 90.0, 0.0])
        nodes = np.array([s.p0 for s in out.trajectories[2].segments], dtype=float)
        u = (nodes - p0) @ dir_v / float(dir_v @ dir_v)
        dev = float(np.max(np.linalg.norm(nodes - (p0 + u[:, None] * dir_v), axis=1)))
        self.assertGreater(dev, 6.0)  # 远大于引导管半径 2 m，确认位移真进了轨迹
        # 被让路的一台必须仍从原起点、到原终点（冻结的只有 t0）
        np.testing.assert_allclose(out.trajectories[2].start_position, (20.0, -45.0, 10.0),
                                   atol=1e-2)
        np.testing.assert_allclose(out.trajectories[2].end_position, (20.0, 45.0, 10.0),
                                   atol=1e-2)
        self.assertAlmostEqual(out.trajectories[2].t0, 0.0, places=6)

    def test_no_conflict_returns_immediately(self) -> None:
        params = PlanParams(n_nodes=20, max_iter=300)
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(2, (20.0, -25.0, 10.0), (20.0, 25.0, 10.0), 5.0, t0=200.0)
        out = coordinate_conflicts(
            [a, b], field=_field(), params=params, vehicle=_vehicle(),
            detection=DetectionConfig.from_params(params, _vehicle()),
            nlp_for=lambda _i: _nlp(params),
        )
        self.assertTrue(out.ok)
        self.assertEqual(out.rounds[0].replanned, ())
        self.assertEqual(out.detail["rounds_used"], 0)

    def test_duplicate_ids_raise(self) -> None:
        params = PlanParams()
        with self.assertRaises(ValueError):
            coordinate_conflicts(
                [_line(1, (0, 0, 10), (10, 0, 10), 1.0),
                 _line(1, (10, 0, 10), (0, 0, 10), 1.0)],
                field=_field(), params=params, vehicle=_vehicle(),
                detection=DetectionConfig.from_params(params, _vehicle()),
                nlp_for=lambda _i: _nlp(params),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
