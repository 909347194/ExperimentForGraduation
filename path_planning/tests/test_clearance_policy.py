# -*- coding: utf-8 -*-
"""净空三层纪律与走廊下限的单元测试（参数口径的回归护栏）。

三层纪律：交付/审计 = ``clearance_m``（真几何）；求解 = ``clearance_m + sdf_quant_tolerance_m``
（SDF 到体素中心的竖直量化最多乐观一格）；重解时再叠加 ``clearance_fix_m``。
走廊下限：半宽 ≤ 净空的走廊是几何空集，必须在建 RRT 前报 ``CORRIDOR_TOO_NARROW``。
"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.grid_spec import GridSpec
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import PlanRequest, PlanStop
from path_planning.common.flight_budget import Budget
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.planning.leg_planner import _nlp_config, plan_leg
from path_planning.methods.planning.tour_planner import TourPlanningContext
from path_planning.methods.spatial.corridor_window import CorridorRule, corridor_window
from path_planning.methods.spatial.occupancy_sdf import (
    build_occupancy,
    build_sdf,
    ObstaclePrism,
)
from path_planning.methods.spatial.world_field import WorldField

SPACING = (4.0, 4.0, 2.0)
GRID = GridSpec(origin=(-40.0, -80.0, 0.0), spacing=SPACING, shape=(41, 51, 16), yaw_rad=0.0)
ALT = (4.0, 28.0)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=5000.0, max_time_s=600.0,
    )


def _field() -> WorldField:
    return WorldField(
        grid=GRID,
        sdf=build_sdf(build_occupancy([], GRID), SPACING).transpose(2, 0, 1),
    )


def _ctx(*, half_width_m: float, tolerance_m: float, clearance_m: float) -> TourPlanningContext:
    return TourPlanningContext(
        field=_field(), params=PlanParams(clearance_m=clearance_m, n_nodes=30, max_iter=300),
        corridor_rule=CorridorRule(half_width_m=half_width_m, margin_m=2.0),
        alt_range_m=ALT, seed=7, sdf_quant_tolerance_m=tolerance_m,
    )


def _request() -> PlanRequest:
    return PlanRequest(
        uav_id=3, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
        start_velocity=(0.0, 0.0, 0.0), t0=0.0,
        stops=(PlanStop(id=1, position=(80.0, 0.0, 10.0)),),
        budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
    )


class TestClearancePolicy(unittest.TestCase):
    def test_solve_threshold_adds_vertical_tolerance(self) -> None:
        """求解净空 = 交付净空 + SDF 竖直容差；重解再加 fix。"""
        ctx = _ctx(half_width_m=20.0, tolerance_m=2.0, clearance_m=6.0)
        window = corridor_window(
            (0.0, 0.0, 10.0), (80.0, 0.0, 10.0), ctx.corridor_rule, SPACING, *ALT
        )
        cfg = _nlp_config(ctx, window, _vehicle(), max_time_s=30.0, extra_clearance=0.0)
        self.assertAlmostEqual(float(cfg.clearance_m), 8.0, places=12)
        cfg_fix = _nlp_config(ctx, window, _vehicle(), max_time_s=30.0, extra_clearance=2.0)
        self.assertAlmostEqual(float(cfg_fix.clearance_m), 10.0, places=12)

    def test_corridor_narrower_than_clearance_early_exits(self) -> None:
        """半宽 ≤ 净空：解必违反净空，必须在建 RRT 前报 CORRIDOR_TOO_NARROW。"""
        ctx = _ctx(half_width_m=5.0, tolerance_m=2.0, clearance_m=6.0)
        leg, failure, diag = plan_leg(_request(), ctx, np.array([0.0, 0.0, 10.0]),
                                      np.array([80.0, 0.0, 10.0]), 0.0, 30.0, 100.0)
        self.assertIsNone(leg)
        self.assertEqual(failure, FailureCode.CORRIDOR_TOO_NARROW)
        self.assertAlmostEqual(diag["window_half_width_m"], 5.0, places=12)
        self.assertAlmostEqual(diag["clearance_m"], 6.0, places=12)


if __name__ == "__main__":
    unittest.main()
