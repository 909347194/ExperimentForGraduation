# -*- coding: utf-8 -*-
"""准入层测试：调用方填错 → 立刻 ValueError；航段飞不了 → 0 秒给码、不进 NLP。

两条纪律（与 ``docs/architecture.md`` §5 的"失败码分层"一致）：

1. **调用方填错**（重复停靠点 id、非有限坐标……）在构造请求时就抛 ``ValueError``，
   一毫秒也不进流水线——这类错修的是调用方，不是任务。
2. **这条航段飞不了**（下界就超预算）给业务码 ``ENDURANCE_EXCEEDED`` /
   ``RANGE_EXCEEDED``，且不进 NLP；声明了 ``allow_estimate_fallback`` 的航次仍按
   定速剖面交付（``time_source=estimate``），失败不被藏起来。
"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.flight_budget import Budget
from path_planning.common.grid_spec import GridSpec
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import PlanRequest, PlanStop
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.planning.admission import admit_leg, leg_lower_bounds
from path_planning.methods.planning.tour_planner import TourPlanningContext, plan_tour
from path_planning.methods.spatial.corridor_window import CorridorRule
from path_planning.methods.spatial.occupancy_sdf import build_occupancy, build_sdf
from path_planning.methods.spatial.world_field import WorldField

SPACING = (4.0, 4.0, 2.0)
GRID = GridSpec(origin=(-40.0, -80.0, 0.0), spacing=SPACING, shape=(41, 41, 16), yaw_rad=0.0)
ALT = (4.0, 28.0)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=5000.0, max_time_s=600.0,
    )


def _context(
    params: PlanParams | None = None, *, max_od_length_m: float | None = None
) -> TourPlanningContext:
    field = WorldField(
        grid=GRID,
        sdf=build_sdf(build_occupancy([], GRID), SPACING).transpose(2, 0, 1),
    )
    return TourPlanningContext(
        field=field, params=params or PlanParams(n_nodes=30, max_iter=200),
        corridor_rule=CorridorRule(half_width_m=20.0, margin_m=2.0),
        alt_range_m=ALT, seed=3, max_od_length_m=max_od_length_m,
    )


class TestCallerErrorsAreSlow(unittest.TestCase):
    """调用方填错必须在构造期拒（ValueError），不许走到规划里再失败。"""

    def test_duplicate_stop_ids_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PlanRequest(
                uav_id=1, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
                start_velocity=(0.0, 0.0, 0.0), t0=0.0,
                stops=(PlanStop(id=7, position=(20.0, 0.0, 10.0)),
                       PlanStop(id=7, position=(40.0, 0.0, 10.0))),
                budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
            )

    def test_task_and_nest_may_share_id(self) -> None:
        """任务 2 与终点机巢 2 撞号是合法输入：两者靠 kind 区分，不是同一点。"""
        stops = (
            PlanStop(id=7, position=(20.0, 0.0, 10.0)),
            PlanStop(id=2, position=(40.0, 0.0, 10.0)),                    # 任务 2
            PlanStop(id=2, position=(60.0, 0.0, 10.0), kind="nest"),        # 机巢 2
        )
        req = PlanRequest(
            uav_id=1, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
            start_velocity=(0.0, 0.0, 0.0), t0=0.0, stops=stops,
            budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
        )
        self.assertEqual(len(req.stops), 3)

    def test_duplicate_same_role_still_rejected(self) -> None:
        """同角色同 id（两个任务 2）仍是重复/漏填，必须拒。"""
        with self.assertRaises(ValueError):
            PlanRequest(
                uav_id=1, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
                start_velocity=(0.0, 0.0, 0.0), t0=0.0,
                stops=(PlanStop(id=2, position=(20.0, 0.0, 10.0)),
                       PlanStop(id=2, position=(40.0, 0.0, 10.0))),
                budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
            )

    def test_non_finite_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PlanRequest(
                uav_id=1, vehicle=_vehicle(), start_position=(float("nan"), 0.0, 10.0),
                start_velocity=(0.0, 0.0, 0.0), t0=0.0,
                stops=(PlanStop(id=1, position=(20.0, 0.0, 10.0)),),
                budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
            )


class TestFieldContractAdmission(unittest.TestCase):
    """建场契约：超长 OD 在请求期给结果码，不让底层抛 WindowOutsideField。"""

    def test_od_over_contract_rejected_before_nlp(self) -> None:
        req = PlanRequest(
            uav_id=1, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
            start_velocity=(0.0, 0.0, 0.0), t0=0.0,
            stops=(PlanStop(id=1, position=(200.0, 0.0, 10.0)),),
            budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
        )
        result = plan_tour(req, _context(max_od_length_m=100.0))
        self.assertEqual(result.failure.value, "OD_TOO_LONG")
        self.assertIn("第 1 段", result.detail)
        self.assertEqual(result.diagnostics["max_od_length_m"], 100.0)
        self.assertNotIn("legs", result.diagnostics, "绝对不能进流水线")

    def test_no_contract_keeps_legacy_behavior(self) -> None:
        """未声明契约的实验不受影响（默认 None = 不判）。"""
        self.assertIsNone(_context().max_od_length_m)

    def test_negative_contract_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _context(max_od_length_m=-1.0)


class TestLegLowerBounds(unittest.TestCase):
    """下界口径：水平与垂直必须同时完成，取较大者；航程取三维直线。"""

    def test_bounds_are_per_axis(self) -> None:
        lb = leg_lower_bounds(_vehicle(), np.array([0.0, 0.0, 0.0]),
                              np.array([30.0, 40.0, 10.0]))
        self.assertAlmostEqual(lb["dh_m"], 50.0, places=6)
        self.assertAlmostEqual(lb["l_lb_m"], np.hypot(50.0, 10.0), places=6)
        self.assertAlmostEqual(lb["t_lb_s"], 50.0 / 15.0, places=6)

    def test_vertical_leg_uses_climb_rate(self) -> None:
        lb = leg_lower_bounds(_vehicle(), np.array([0.0, 0.0, 0.0]),
                              np.array([0.0, 0.0, 12.0]))
        self.assertAlmostEqual(lb["t_lb_s"], 2.0, places=6)


class TestAdmissionRejectsFast(unittest.TestCase):
    """下界已超预算的航段：0 次 NLP、给预算类失败码。"""

    def test_short_endurance_gives_endurance_exceeded(self) -> None:
        code, diag = admit_leg(
            _vehicle(), np.array([-650.0, -259.8, 60.0]), np.array([-606.6, -506.0, 60.0]),
            remaining_s=6.0, remaining_m=300.0,
        )
        self.assertEqual(code.value, "ENDURANCE_EXCEEDED")
        self.assertGreater(diag["t_ratio"], 1.0)

    def test_short_range_gives_range_exceeded(self) -> None:
        code, _ = admit_leg(
            _vehicle(), np.array([0.0, 0.0, 10.0]), np.array([300.0, 0.0, 10.0]),
            remaining_s=500.0, remaining_m=100.0,
        )
        self.assertEqual(code.value, "RANGE_EXCEEDED")

    def test_roomy_budget_passes(self) -> None:
        code, _ = admit_leg(
            _vehicle(), np.array([0.0, 0.0, 10.0]), np.array([80.0, 0.0, 10.0]),
            remaining_s=500.0, remaining_m=4000.0,
        )
        self.assertIsNone(code)

    def test_planner_skips_nlp_and_still_delivers_estimate(self) -> None:
        """与 exp01_smoke 机7 同形：直线 80 m 需 ≥5.33 s，续航只给 4 s → 降级交付，NLP 不跑。"""
        req = PlanRequest(
            uav_id=7, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
            start_velocity=(0.0, 0.0, 0.0), t0=0.0,
            stops=(PlanStop(id=1, position=(80.0, 0.0, 10.0)),),
            budget=Budget(endurance_remaining_s=4.0, range_remaining_m=400.0),
            allow_estimate_fallback=True,
        )
        result = plan_tour(req, _context())
        self.assertTrue(result.deliverable)
        self.assertEqual(result.failure.value, "ENDURANCE_EXCEEDED")
        self.assertIsNotNone(result.trajectory)
        self.assertEqual(result.trajectory.time_source.value, "estimate")
        leg = result.diagnostics["legs"][0]
        self.assertEqual(leg.get("tried"), 0)
        self.assertEqual(leg.get("status"), "admission_rejected")
        self.assertIn("admission", leg)


if __name__ == "__main__":
    unittest.main()
