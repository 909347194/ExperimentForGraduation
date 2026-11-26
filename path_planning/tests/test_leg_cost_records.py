# -*- coding: utf-8 -*-
"""逐段账契约（P0）的聚焦测试：只报事实，不猜、不处置。

三条纪律：
  1. 失败段的长度/时长只在**实测存在**时给，否则 ``None``（不写 0 冒充）；
  2. 失败码与严重性成对出现，严重性只来自 ``FailureCode`` 码表；
  3. 老结果没有 ``leg_costs`` 时返回空元组（调用方能容忍"下层没报"）。
"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.flight_budget import Budget
from path_planning.common.leg_cost_records import (
    as_dicts,
    failed_record,
    from_dicts,
    planned_record,
    severity_of,
    severity_rank,
)
from path_planning.common.plan_types import (
    LegCostRecord,
    PlanRequest,
    PlanResult,
    PlanStop,
    TimeSource,
)
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.planning.leg_recorder import (
    LegRecorder,
    origin_id_of,
    time_source_of,
)
from path_planning.methods.planning.tour_planner import TourPlanningContext, plan_tour
from path_planning.methods.spatial.corridor_window import CorridorRule
from path_planning.methods.spatial.occupancy_sdf import build_occupancy, build_sdf
from path_planning.methods.spatial.world_field import WorldField
from path_planning.common.grid_spec import GridSpec

GRID = GridSpec(origin=(-40.0, -80.0, 0.0), spacing=(4.0, 4.0, 2.0),
                shape=(41, 51, 16), yaw_rad=0.0)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=5000.0, max_time_s=600.0,
    )


def _empty_context() -> TourPlanningContext:
    from path_planning.common.plan_params import PlanParams

    field = WorldField(grid=GRID, sdf=build_sdf(
        build_occupancy([], GRID), (4.0, 4.0, 2.0)).transpose(2, 0, 1))
    return TourPlanningContext(
        field=field, params=PlanParams(n_nodes=30, max_iter=300),
        corridor_rule=CorridorRule(half_width_m=20.0, margin_m=2.0),
        alt_range_m=(4.0, 28.0), seed=3,
    )


class TestRecordShape(unittest.TestCase):
    def test_planned_round_trip(self) -> None:
        rec = planned_record(-7, 1, length_m=120.5, duration_s=30.0, time_source="estimate")
        self.assertEqual(rec.time_source, "estimate")
        self.assertTrue(rec.ok)
        back = from_dicts(as_dicts([rec]))[0]
        self.assertEqual(back, rec)

    def test_failed_record_carries_code_and_severity_from_table(self) -> None:
        rec = failed_record(1, -7, FailureCode.RRT_NO_PATH)
        self.assertEqual(rec.severity, severity_of(FailureCode.RRT_NO_PATH))
        self.assertIsNone(rec.length_m, "失败段不得用 0 冒充实测长度")
        self.assertEqual(rec.time_source, "failed")
        self.assertFalse(rec.ok)

    def test_unknown_code_is_rejected_loudly(self) -> None:
        with self.assertRaises(KeyError):
            failed_record(1, 2, "NOT_A_REAL_CODE")

    def test_validation_rules(self) -> None:
        with self.assertRaises(ValueError):
            LegCostRecord(from_id=1.0, to_id=2)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            LegCostRecord(from_id=1, to_id=2, length_m=-1.0)
        with self.assertRaises(ValueError):
            LegCostRecord(from_id=1, to_id=2, time_source="guessed")
        with self.assertRaises(ValueError):
            LegCostRecord(from_id=1, to_id=2, failure_code="RRT_NO_PATH")

    def test_severity_rank_orders_infeasible_first(self) -> None:
        self.assertLess(severity_rank("infeasible"), severity_rank("internal"))
        self.assertLess(severity_rank("internal"), severity_rank("degraded"))
        self.assertEqual(severity_rank(None), 9)


class TestLegRecorder(unittest.TestCase):
    def test_from_id_uses_origin_for_first_leg(self) -> None:
        stops = (
            PlanStop(id=1, position=(10.0, 0.0, 5.0)),
            PlanStop(id=-7, position=(0.0, 0.0, 0.0), kind="nest"),
        )
        rec = LegRecorder(origin_id=-7)
        self.assertEqual(rec.from_id(0, stops), -7)
        self.assertEqual(rec.from_id(1, stops), 1)

    def test_records_are_append_only_and_typed(self) -> None:
        rec = LegRecorder(origin_id=-1)

        class _Leg:
            path_len_m = 50.0
            duration = 10.0
            time_source = TimeSource.ESTIMATE

        rec.planned(-1, 1, _Leg())  # type: ignore[arg-type]
        rec.failed(1, -2, "TIME_EXCEEDS_BUDGET")
        self.assertEqual(len(rec), 2)
        self.assertEqual([r.time_source for r in rec.records()],
                         ["estimate", "failed"])
        self.assertEqual(rec.as_dicts()[1]["failure_code"], "TIME_EXCEEDS_BUDGET")

    def test_estimated_leg_marks_time_source(self) -> None:
        rec = LegRecorder(origin_id=0)

        class _Leg:
            path_len_m = 80.0
            duration = 20.0
            time_source = TimeSource.TRAJECTORY

        rec.estimated(0, 1, _Leg())  # type: ignore[arg-type]
        self.assertEqual(rec.records()[0].time_source, "estimate")

    def test_origin_id_of_and_time_source_of(self) -> None:
        self.assertEqual(origin_id_of(PlanRequest(
            uav_id=0, vehicle=_vehicle(), start_position=(0.0, 0.0, 0.0),
            start_velocity=(0.0, 0.0, 0.0), t0=0.0,
            stops=(PlanStop(id=1, position=(10.0, 0.0, 0.0)),),
            budget=Budget(endurance_remaining_s=100.0, range_remaining_m=1000.0),
            meta={"origin_id": -7},
        )), -7)

        class _Leg:
            time_source = TimeSource.ESTIMATE

        self.assertEqual(time_source_of(_Leg()), "estimate")  # type: ignore[arg-type]


class TestPlanResultView(unittest.TestCase):
    def test_leg_costs_parsed_from_diagnostics(self) -> None:
        raw = as_dicts([planned_record(-1, 1, length_m=100.0, duration_s=20.0,
                                      time_source="planned")])
        result = PlanResult(uav_id=3, diagnostics={"leg_costs": raw})
        self.assertEqual(len(result.leg_costs), 1)
        self.assertEqual(result.leg_costs[0].to_id, 1)
        self.assertIsNone(result.failure_severity)

    def test_old_result_without_leg_costs_is_empty(self) -> None:
        self.assertEqual(PlanResult(uav_id=3).leg_costs, ())

    def test_failure_severity_is_passed_through(self) -> None:
        result = PlanResult(uav_id=3, failure=FailureCode.RRT_NO_PATH)
        self.assertEqual(result.failure_severity, severity_of(FailureCode.RRT_NO_PATH))


class TestPlannerWiring(unittest.TestCase):
    """真实规划一版：``leg_costs`` 必须与已飞航段同数、同端点、同长度。"""

    def test_plan_tour_reports_leg_costs(self) -> None:
        req = PlanRequest(
            uav_id=3, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
            start_velocity=(0.0, 0.0, 0.0), t0=0.0,
            stops=(
                PlanStop(id=1, position=(40.0, 0.0, 10.0)),
                PlanStop(id=-7, position=(0.0, 0.0, 10.0), kind="nest"),
            ),
            budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
            meta={"origin_id": -7},
        )
        result = plan_tour(req, _empty_context())
        self.assertTrue(result.deliverable, result.detail)

        legs = result.leg_costs
        self.assertEqual(len(legs), 2)
        self.assertEqual((legs[0].from_id, legs[0].to_id), (-7, 1))
        self.assertEqual((legs[1].from_id, legs[1].to_id), (1, -7))
        total = sum(r.length_m for r in legs if r.length_m)
        self.assertAlmostEqual(total, result.trajectory.path_len_m, places=3)
        self.assertIn("legs", result.diagnostics, "旧字段必须原样保留")
        self.assertTrue(np.isfinite(result.trajectory.path_len_m))

    def test_global_gate_failure_keeps_leg_costs(self) -> None:
        """交付闸失败（CLEARANCE_VIOLATION）也必须带逐段账，不能抛 NameError。

        这是审计里失败票必然走到的分支：上层靠 leg_costs 定位断在哪一段、
        并把已飞段的实测长度回灌。此前该分支引用了未定义的 leg_records/as_dicts。
        """
        from unittest.mock import patch

        from path_planning.common.failure_codes import FailureCode
        import path_planning.methods.planning.tour_planner as tp

        req = PlanRequest(
            uav_id=3, vehicle=_vehicle(), start_position=(0.0, 0.0, 10.0),
            start_velocity=(0.0, 0.0, 0.0), t0=0.0,
            stops=(PlanStop(id=1, position=(40.0, 0.0, 10.0)),),
            budget=Budget(endurance_remaining_s=500.0, range_remaining_m=4000.0),
            meta={"origin_id": -(1 + 3)},
        )
        with patch.object(tp, "global_gate", return_value=(1.5, "测试注入的净空失败")):
            result = tp.plan_tour(req, _empty_context())
        self.assertEqual(result.failure, FailureCode.CLEARANCE_VIOLATION)
        self.assertEqual(len(result.leg_costs), 1)
        self.assertEqual((result.leg_costs[0].from_id, result.leg_costs[0].to_id), (-4, 1))
        self.assertIsNone(result.trajectory)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
