# -*- coding: utf-8 -*-
"""单航次编排测试：走廊 → RRT → NLP → 复核 → 预算；含失败分支与 api 门面。"""

from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np

from path_planning.api import PathPlanner
from path_planning.common.failure_codes import FailureCode
from path_planning.common.flight_budget import Budget
from path_planning.common.grid_spec import GridSpec
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import (
    HermiteSegment, PlanRequest, PlanStop, TimeSource, Trajectory,
)
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.planning.tour_planner import TourPlanningContext, plan_tour
from path_planning.methods.spatial.corridor_window import CorridorRule
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.trajectory_registry import TrajectoryRegistry

SPACING = (4.0, 4.0, 2.0)
GRID = GridSpec(origin=(-40.0, -80.0, 0.0), spacing=SPACING, shape=(41, 51, 16), yaw_rad=0.0)
ALT = (4.0, 28.0)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=5000.0, max_time_s=600.0,
    )


def _field(prisms) -> WorldField:
    return WorldField(
        grid=GRID,
        sdf=build_sdf(build_occupancy(list(prisms), GRID), SPACING).transpose(2, 0, 1),
    )


def _context(field: WorldField, params: PlanParams) -> TourPlanningContext:
    return TourPlanningContext(
        field=field, params=params,
        corridor_rule=CorridorRule(half_width_m=20.0, margin_m=2.0),
        alt_range_m=ALT, seed=7,
    )


def _request(*, stops, t0=0.0, budget=None) -> PlanRequest:
    return PlanRequest(
        uav_id=3, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
        start_velocity=(0.0, 0.0, 0.0), t0=t0, stops=tuple(stops),
        budget=budget or Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
    )


class TestPlanTour(unittest.TestCase):
    def test_routes_around_wall_and_delivers_trajectory(self) -> None:
        """横墙只挡中间一段：必须绕过去，且时间来自真实优化（trajectory 口径）。"""
        wall = ObstaclePrism(
            np.array([[40.0, -8.0], [44.0, -8.0], [44.0, 8.0], [40.0, 8.0]]), 0.0, 24.0
        )
        params = PlanParams(n_nodes=40, max_iter=600)
        req = _request(stops=(PlanStop(id=1, position=(80.0, 0.0, 10.0)),))
        result = plan_tour(req, _context(_field([wall]), params))
        self.assertTrue(result.deliverable, result.detail)
        traj = result.trajectory
        assert traj is not None
        self.assertEqual(traj.time_source, TimeSource.TRAJECTORY)
        self.assertEqual(traj.uav_id, 3)
        self.assertAlmostEqual(traj.t0, 0.0, places=6)
        self.assertGreater(traj.path_len_m, 80.0)  # 绕行必然比直线长
        # 让开的幅度必须真实存在：有节点的 |y| 超过半米
        self.assertGreater(max(abs(seg.p0[1]) for seg in traj.segments), 1.0)
        self.assertAlmostEqual(traj.end_position[0], 80.0, places=2)

    def test_full_wall_reports_rrt_no_path(self) -> None:
        """整条走廊都被挡（横跨半宽、竖到带顶）：RRT 无解，必须给 RRT_NO_PATH。"""
        wall = ObstaclePrism(
            np.array([[40.0, -40.0], [44.0, -40.0], [44.0, 40.0], [40.0, 40.0]]), 0.0, 40.0
        )
        req = _request(stops=(PlanStop(id=1, position=(80.0, 0.0, 10.0)),))
        result = plan_tour(req, _context(_field([wall]), PlanParams(n_nodes=30, max_iter=300)))
        self.assertFalse(result.deliverable)
        self.assertIsNotNone(result.failure)
        self.assertEqual(result.failure.value, "RRT_NO_PATH")

    def test_range_budget_exceeded_still_delivers(self) -> None:
        """航程预算不足：航迹仍可交付，但必须带 RANGE_EXCEEDED（降级不是隐藏）。"""
        req = _request(
            stops=(PlanStop(id=1, position=(80.0, 0.0, 10.0)),),
            budget=Budget(endurance_remaining_s=500.0, range_remaining_m=20.0),
        )
        result = plan_tour(req, _context(_field([]), PlanParams(n_nodes=40, max_iter=600)))
        self.assertTrue(result.deliverable)
        self.assertIsNotNone(result.failure)
        self.assertEqual(result.failure.value, "RANGE_EXCEEDED")
        self.assertFalse(result.ok)

    def test_endpoint_band_uses_per_point_ground(self) -> None:
        """A4 端点准入：起点/停靠点的真高必须落在带内，真高按**逐点**局部地面算。"""
        params = PlanParams(n_nodes=30, max_iter=300)
        req = _request(stops=(PlanStop(id=1, position=(80.0, 0.0, 10.0)),))

        def ground(x, y):  # 80 m 外地面抬到 12 m：z=10 停靠点真高 = -2 m
            return np.where(np.asarray(x) > 40.0, 12.0, 0.0)

        ctx = replace(
            _context(_field([]), params), agl_range_m=(10.0, 120.0), ground_height=ground
        )
        result = plan_tour(req, ctx)
        self.assertFalse(result.deliverable)
        self.assertEqual(result.failure, FailureCode.ENDPOINT_OUT_OF_BAND)
        self.assertIn("停靠点 1", result.detail)
        self.assertIn("真高 -2.0 m", result.detail)

        # 同一请求、同一高度，地面贴 0 m 时就在带内 → 正常交付
        ctx_ok = replace(ctx, ground_height=lambda x, y: np.zeros_like(np.asarray(x)))
        self.assertTrue(plan_tour(req, ctx_ok).deliverable)

    def test_endpoint_band_rejects_start_below_min(self) -> None:
        """起点越下限就是"不是空中悬停点"：入口直接拒，不进 RRT/NLP（失败码可归因）。"""
        params = PlanParams(n_nodes=30, max_iter=300)
        req = _request(stops=(PlanStop(id=1, position=(80.0, 0.0, 14.0)),))
        ctx = replace(
            _context(_field([]), params),
            agl_range_m=(10.0, 120.0),
            ground_height=lambda x, y: np.full_like(np.asarray(x, dtype=float), 3.0),
        )
        result = plan_tour(req, ctx)
        self.assertFalse(result.deliverable)
        self.assertEqual(result.failure, FailureCode.ENDPOINT_OUT_OF_BAND)
        self.assertIn("起点", result.detail)
        self.assertEqual(result.diagnostics["agl_range_m"], (10.0, 120.0))

    def test_absolute_height_above_ceiling_rejected(self) -> None:
        """methods 只收合法三维点：绝对 z 越上界（如屋顶 115+10=125）入口即拒。"""
        params = PlanParams(n_nodes=30, max_iter=300)
        req = _request(stops=(PlanStop(id=1, position=(80.0, 0.0, 125.0)),))
        ctx = _context(_field([]), params)  # alt_range_m = (4.0, 28.0)
        result = plan_tour(req, ctx)
        self.assertFalse(result.deliverable)
        self.assertEqual(result.failure, FailureCode.ENDPOINT_OUT_OF_BAND)
        self.assertIn("绝对高度", result.detail)
        self.assertIn("停靠点 1", result.detail)

    def test_nan_and_bad_dimension_rejected_at_data_model(self) -> None:
        """第一道闸：数据模型层 check_vec3 就直接拒绝 NaN / 非三维坐标。"""
        with self.assertRaisesRegex(ValueError, "非有限"):
            PlanStop(id=1, position=(80.0, 0.0, float("nan")))
        with self.assertRaisesRegex(ValueError, "3 维"):
            PlanStop(id=1, position=(80.0, 0.0))


    def test_service_time_becomes_hover_segment(self) -> None:
        """停靠服务时长必须变成悬停段，计入 total 但不计入飞行时长。"""
        stop = PlanStop(id=1, position=(60.0, 0.0, 10.0), service_time_s=12.0)
        req = _request(stops=(stop,))
        result = plan_tour(req, _context(_field([]), PlanParams(n_nodes=30, max_iter=400)))
        self.assertTrue(result.deliverable, result.detail)
        traj = result.trajectory
        assert traj is not None
        self.assertGreaterEqual(traj.hover_duration, 12.0 - 1e-6)
        self.assertGreater(traj.duration, traj.flight_duration)


def _line_traj(uav_id: int, p0, p1, dur: float, t0: float = 0.0) -> Trajectory:
    """合成一条匀速直线航迹（作为 Registry 里的"旧航迹"，不做 NLP）。"""
    v = tuple((b - a) / dur for a, b in zip(p0, p1))
    seg = HermiteSegment(t0=t0, t1=t0 + dur, p0=p0, p1=p1, v0=v, v1=v)
    return Trajectory(
        uav_id=uav_id, segments=(seg,), path_len_m=float(np.linalg.norm(np.subtract(p1, p0))),
        time_source=TimeSource.TRAJECTORY,
    )


class TestPathPlannerFacade(unittest.TestCase):
    def test_plan_registers_trajectory(self) -> None:
        """门面排完一条 tour 必须把它登记成"已发布时空障碍"。"""
        planner = PathPlanner(_context(_field([]), PlanParams(n_nodes=30, max_iter=400)))
        req = _request(stops=(PlanStop(id=1, position=(60.0, 0.0, 10.0)),))
        result = planner.plan(req)
        self.assertTrue(result.deliverable, result.detail)
        self.assertIn(3, planner.registry)
        self.assertGreater(planner.busy_until(3), 0.0)

    def test_plan_fleet_resolves_crossing_and_updates_registry(self) -> None:
        """端到端：两机十字交叉 → 后一台让路 → 冲突清零 → Registry 里是让路后的航迹。"""
        planner = PathPlanner(_context(_field([]), PlanParams(n_nodes=40, max_iter=600)))
        req_a = _request(stops=(PlanStop(id=1, position=(80.0, 0.0, 10.0)),))
        # 两机到交点等距（40 m），故同刻抵达；椭球收到 20/10 后仍必然冲突
        req_b = PlanRequest(
            uav_id=4, vehicle=_vehicle(), start_position=(40.0, -40.0, 10.0),
            start_velocity=(0.0, 0.0, 0.0), t0=0.0,
            stops=(PlanStop(id=2, position=(40.0, 40.0, 10.0)),),
            budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
        )
        plan = planner.plan_fleet([req_a, req_b])
        self.assertTrue(plan.ok, None if plan.outcome is None else plan.outcome.detail)
        self.assertEqual(len(plan.results), 2)
        self.assertIn(4, plan.changed)  # 机号大者让路
        self.assertEqual(len(plan.outcome.report.conflicts), 0)
        # Registry 里必须是协调后的航迹，不是初次规划那条
        self.assertEqual(plan.trajectory_of(4), planner.registry.get(4))
        self.assertGreater(planner.busy_until(4), planner.busy_until(1))  # 让路拉长了时长

    def test_plan_fleet_detects_new_old_conflict_and_freezes_registry(self) -> None:
        """门面接线：Registry 旧机机号更大也必须冻结，只让本批新机让路。"""
        registry = TrajectoryRegistry()
        old = _line_traj(9, (0.0, 0.0, 10.0), (80.0, 0.0, 10.0), 20.0, t0=0.0)
        registry.register(old, source="legacy")
        planner = PathPlanner(
            _context(_field([]), PlanParams(n_nodes=40, max_iter=600)),
            registry=registry,
        )
        req = PlanRequest(
            uav_id=3, vehicle=_vehicle(), start_position=(40.0, -72.0, 10.0),
            start_velocity=(0.0, 0.0, 0.0), t0=5.0,
            stops=(PlanStop(id=1, position=(40.0, 72.0, 10.0)),),
            budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
        )
        plan = planner.plan_fleet([req])
        self.assertTrue(plan.ok, None if plan.outcome is None else plan.outcome.detail)
        self.assertEqual(plan.frozen_ids, (9,))
        self.assertEqual(len(plan.existing), 1)
        self.assertIs(plan.existing[0], old)
        self.assertIn(3, plan.changed)
        self.assertNotIn(9, plan.changed)
        self.assertIs(planner.registry.get(9), old)  # Registry 里旧航迹对象逐位未变
        self.assertTrue(plan.pre_report is not None)
        self.assertTrue(
            any(set(p.ids) == {3, 9} for p in plan.pre_report.conflicts),
            [(p.ids, p.min_distance) for p in plan.pre_report.pairs],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
