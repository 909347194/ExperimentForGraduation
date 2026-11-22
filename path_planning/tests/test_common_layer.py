# -*- coding: utf-8 -*-
"""common 层单元测试：机体规格、区间代数、预算、航迹、失败码、参数校验。

覆盖重点是**反例**：这些约束的价值不在于默认值能过，而在于能拦住真实会犯的错
（轻载机 f_min > mg 无法悬停、续航宽于航程成空约束、走廊容不下净空、
决策步长区间退化、退让阶梯非递减）。

运行：``python -m unittest path_planning.tests.test_common_layer -v``
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from path_planning.common.flight_budget import Binding, Budget
from path_planning.common.failure_codes import FAILURES, FailureCode, Severity
from path_planning.common.time_intervals import Interval, IntervalSet
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import (
    HermiteSegment,
    PlanRequest,
    PlanResult,
    PlanStop,
    TimeSource,
    Trajectory,
)
from path_planning.common.vehicle_spec import PLAN_SPEED_RATIO, VehicleSpec


def _default_vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="default", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=6000.0, max_time_s=400.0,
    )


def _light_vehicle(**overrides) -> VehicleSpec:
    base = dict(
        model="light", mass_kg=0.8, v_max_mps=15.0, v_up_mps=5.0, v_dn_mps=1.5,
        a_max_mps2=2.6, f_max_n=14.0, f_min_n=7.0,
        max_range_m=5000.0, max_time_s=320.0,
    )
    base.update(overrides)
    return VehicleSpec(**base)


def _seg(t0=0.0, t1=10.0, p0=(0.0, 0.0, 0.0), p1=(100.0, 0.0, 0.0),
         v0=(10.0, 0.0, 0.0), v1=(10.0, 0.0, 0.0), hover=False) -> HermiteSegment:
    return HermiteSegment(t0=t0, t1=t1, p0=p0, p1=p1, v0=v0, v1=v1, is_hover=hover)


class TestVehicleSpec(unittest.TestCase):
    """机体规格：派生量数值与逐机校验。"""

    def test_derived_values_match_doc(self) -> None:
        v = _default_vehicle()
        v.validate()
        self.assertAlmostEqual(v.gravity_force_n, 9.81, places=6)
        self.assertAlmostEqual(v.a_z_max_mps2, 16.0 - 9.81, places=6)
        self.assertAlmostEqual(v.a_z_min_mps2, 9.0 - 9.81, places=6)
        self.assertAlmostEqual(v.a_horiz_max_level_mps2, (16.0 ** 2 - 9.81 ** 2) ** 0.5,
                               places=6)
        self.assertAlmostEqual(v.v_max_plan_mps, PLAN_SPEED_RATIO * 15.0, places=6)
        # a_max=3、v_plan=13.5 时 R_min = 60.75 m（不是 15 m/s 对应的 75 m）
        self.assertAlmostEqual(v.r_min_m(), 60.75, places=6)
        self.assertAlmostEqual(v.r_min_m(15.0), 75.0, places=6)

    def test_combined_speed_constraint_is_redundant_at_this_parameter(self) -> None:
        """(C3) 在本轮参数下对水平分量恒不绑定——但这只是巧合，约束仍须保留。"""
        v = _default_vehicle()
        # 爬升端最紧处 sqrt(15²-6²)=13.748 > v_max_plan=13.5
        self.assertGreater(v.v_h_max_at_climb_mps, v.v_max_plan_mps)
        self.assertAlmostEqual(v.v_h_limit_mps(v.v_up_mps), v.v_max_plan_mps, places=6)
        # 临界值：只要 v_max_plan 提到 13.748 以上，合速度约束就会开始绑定
        self.assertAlmostEqual((15.0 ** 2 - 6.0 ** 2) ** 0.5, 13.748, places=3)

    def test_light_can_hover(self) -> None:
        _light_vehicle().validate()

    def test_light_with_too_high_f_min_cannot_hover(self) -> None:
        """m=0.8 时 mg=7.848 N；f_min=8.0 则最小推力大于重力，无法悬停。

        这条错误在 defaults（m=1、f_min=9，恰好可行）上永远查不出来，
        只有逐机展开才暴露——所以 validate 必须逐机跑。
        """
        with self.assertRaises(ValueError) as ctx:
            _light_vehicle(f_min_n=8.0).validate()
        self.assertIn("无法悬停", str(ctx.exception))

    def test_endurance_wider_than_range_is_rejected(self) -> None:
        """max_time_s > max_range/v_max 时续航永不绑定，是空约束。"""
        with self.assertRaises(ValueError) as ctx:
            _light_vehicle(max_time_s=420.0).validate()  # 5000/15 = 333.3
        self.assertIn("空约束", str(ctx.exception))

    def test_amax_above_thrust_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _default_vehicle_flex(a_max_mps2=13.5).validate()
        self.assertIn("平飞可用水平加速度", str(ctx.exception))

    def test_negative_and_zero_inputs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _light_vehicle(mass_kg=0.0).validate()
        with self.assertRaises(ValueError):
            _light_vehicle(v_max_mps=0.0).validate()
        with self.assertRaises(ValueError):
            _light_vehicle(mass_kg=5.0).validate()  # mg=49 > f_max=14


def _default_vehicle_flex(**overrides) -> VehicleSpec:
    base = dict(
        model="default", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=6000.0, max_time_s=400.0,
    )
    base.update(overrides)
    return VehicleSpec(**base)


class TestIntervals(unittest.TestCase):
    """半开区间代数。"""

    def test_half_open_semantics(self) -> None:
        iv = Interval(0.0, 5.0)
        self.assertEqual(iv.duration, 5.0)
        self.assertTrue(iv.contains(0.0))
        self.assertTrue(iv.contains(4.999))
        self.assertFalse(iv.contains(5.0))
        self.assertTrue(bool(iv))

    def test_adjacent_merge_has_no_off_by_one(self) -> None:
        """[0,5)+[5,10)+[10,15) 应恰为 15 s，而不是 16。"""
        s = IntervalSet([Interval(0, 5), Interval(5, 10), Interval(10, 15)])
        self.assertEqual(len(s), 1)
        self.assertAlmostEqual(s.total_duration, 15.0, places=9)

    def test_intersect_and_complement(self) -> None:
        a = IntervalSet([Interval(0, 10), Interval(20, 30)])
        b = IntervalSet([Interval(5, 25)])
        inter = a.intersect(b)
        self.assertEqual(len(inter), 2)
        self.assertAlmostEqual(inter.total_duration, 10.0, places=9)
        # [0,30) 减去 a 只剩 [10,20)
        comp = a.complement(Interval(0, 30))
        self.assertAlmostEqual(comp.total_duration, 10.0, places=9)

    def test_covers_across_pieces(self) -> None:
        whole = IntervalSet([Interval(0, 30)])
        piecewise = IntervalSet([Interval(0, 10), Interval(10, 20)])
        self.assertTrue(whole.covers(piecewise))
        self.assertFalse(IntervalSet([Interval(0, 10)]).covers(
            IntervalSet([Interval(0, 20)])))

    def test_degenerate_and_reversed_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Interval(5.0, 3.0)
        # 退化区间可构造，但不进入规范化
        empty = IntervalSet([Interval(2.0, 2.0)])
        self.assertTrue(empty.is_empty)


class TestBudget(unittest.TestCase):
    """双口径预算判定。"""

    def test_within_budget(self) -> None:
        v = Budget(400.0, 6000.0).check(380.0, 5500.0)
        self.assertTrue(v.ok)
        self.assertIsNone(v.failure)
        self.assertAlmostEqual(v.headroom_s, 20.0, places=6)

    def test_endurance_overshoot(self) -> None:
        v = Budget(400.0, 6000.0).check(450.0, 1000.0)
        self.assertFalse(v.ok)
        self.assertIs(v.binding, Binding.ENDURANCE)
        self.assertIs(v.failure, FailureCode.ENDURANCE_EXCEEDED)

    def test_range_overshoot(self) -> None:
        v = Budget(400.0, 6000.0).check(100.0, 7000.0)
        self.assertFalse(v.ok)
        self.assertIs(v.binding, Binding.RANGE)
        self.assertIs(v.failure, FailureCode.RANGE_EXCEEDED)

    def test_both_overshoot_picks_worse(self) -> None:
        v = Budget(400.0, 6000.0).check(800.0, 9000.0)
        self.assertFalse(v.ok)
        # time_ratio=2.0 > range_ratio=1.5 -> 续航胜出
        self.assertIs(v.binding, Binding.ENDURANCE)

    def test_consume_returns_new_object(self) -> None:
        b = Budget(400.0, 6000.0)
        b2 = b.consume(100.0, 1500.0)
        self.assertEqual(b.endurance_remaining_s, 400.0)
        self.assertAlmostEqual(b2.endurance_remaining_s, 300.0, places=9)
        self.assertAlmostEqual(b2.range_remaining_m, 4500.0, places=9)


class TestTrajectory(unittest.TestCase):
    """航迹构造与自洽校验。"""

    def test_duration_split(self) -> None:
        t = Trajectory(
            uav_id=0,
            segments=(_seg(t0=0.0, t1=10.0),
                      _seg(t0=10.0, t1=40.0, p0=(100.0, 0.0, 0.0),
                           p1=(100.0, 0.0, 0.0), v0=(0.0, 0.0, 0.0),
                           v1=(0.0, 0.0, 0.0), hover=True)),
            path_len_m=100.0,
        )
        t.validate()
        self.assertEqual(t.duration, 40.0)
        self.assertEqual(t.flight_duration, 10.0)
        self.assertEqual(t.hover_duration, 30.0)
        self.assertEqual(t.weighted_duration, 40.0)
        self.assertEqual(t.end_position, (100.0, 0.0, 0.0))

    def test_time_gap_rejected(self) -> None:
        t = Trajectory(
            uav_id=0,
            segments=(_seg(t0=0.0, t1=10.0),
                      _seg(t0=11.0, t1=20.0, p0=(100.0, 0.0, 0.0),
                           p1=(200.0, 0.0, 0.0))),
            path_len_m=200.0,
        )
        with self.assertRaises(ValueError) as ctx:
            t.validate()
        self.assertIn("时间不连续", str(ctx.exception))

    def test_hover_with_moving_endpoint_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _seg(p0=(0.0, 0.0, 0.0), p1=(10.0, 0.0, 0.0),
                 v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0), hover=True)


class TestPlanResultSemantics(unittest.TestCase):
    """三态语义：ok / deliverable / severity 必须能表达"内部 bug"与"真不可行"。"""

    def _traj(self, source=TimeSource.ESTIMATE) -> Trajectory:
        return Trajectory(uav_id=7, segments=(_seg(),), path_len_m=100.0,
                          time_source=source)

    def test_success(self) -> None:
        r = PlanResult.success(self._traj())
        self.assertTrue(r.ok)
        self.assertTrue(r.deliverable)
        self.assertIsNone(r.severity)

    def test_infeasible_is_not_deliverable(self) -> None:
        r = PlanResult.failed(7, FailureCode.RRT_NO_PATH, "budget exhausted")
        self.assertFalse(r.ok)
        self.assertFalse(r.deliverable)
        self.assertIs(r.severity, Severity.INFEASIBLE)

    def test_degraded_is_deliverable(self) -> None:
        r = PlanResult.failed(7, FailureCode.TIME_EXCEEDS_BUDGET, "wall clock",
                              trajectory=self._traj())
        self.assertFalse(r.ok)
        self.assertTrue(r.deliverable)
        self.assertIs(r.severity, Severity.DEGRADED)

    def test_internal_bug_is_marked_internal(self) -> None:
        """内部 bug 不得被当成"任务不可行"上报，否则上层会删掉可飞的任务。"""
        for code in (FailureCode.KINEMATIC_INCONSISTENT,
                     FailureCode.CONFLICT_UNRESOLVED):
            self.assertIs(FAILURES[code].severity, Severity.INTERNAL)
            r = PlanResult.failed(7, code)
            self.assertIs(r.severity, Severity.INTERNAL)

    def test_request_requires_stops(self) -> None:
        with self.assertRaises(ValueError):
            PlanRequest(
                uav_id=0, vehicle=_default_vehicle(),
                start_position=(0.0, 0.0, 10.0), start_velocity=(0.0, 0.0, 0.0),
                t0=0.0, stops=(), budget=Budget.from_spec(400.0, 6000.0),
            )

    def test_stop_service_time_negative_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PlanStop(id=1, position=(0.0, 0.0, 0.0), service_time_s=-1.0)


class TestFailureCodes(unittest.TestCase):
    """码表完整性。"""

    def test_every_code_has_severity(self) -> None:
        for code in FailureCode:
            self.assertIn(code, FAILURES, f"{code} 未登记分级")

    def test_retreat_like_codes_are_degraded_not_infeasible(self) -> None:
        self.assertIs(FAILURES[FailureCode.NO_LOCAL_SOLUTION].severity,
                      Severity.DEGRADED)
        self.assertIs(FAILURES[FailureCode.TIME_NOT_OPTIMAL].severity,
                      Severity.DEGRADED)
        self.assertIs(FAILURES[FailureCode.CYCLE_MISALIGN].severity,
                      Severity.DEGRADED)

    def test_capability_code_is_not_silent_acceptance(self) -> None:
        """上层期望速度高于能力时不得静默按上限飞。"""
        self.assertIn("不得静默", FAILURES[FailureCode.CAPABILITY_BELOW_REQUEST].remedy)


class TestPlanParams(unittest.TestCase):
    """参数校验。"""

    def test_defaults_valid(self) -> None:
        PlanParams().validate()

    def test_detection_step_matches_doc(self) -> None:
        p = PlanParams()
        step = p.detection_step_s(3.0)
        self.assertAlmostEqual(step, 0.365, places=3)
        # 逐机：a_max 变小则弦长变长
        self.assertGreater(p.detection_step_s(2.6), step)
        self.assertLess(p.detection_step_s(4.0), step)

    def test_corridor_must_fit_clearance(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            replace(PlanParams(), clearance_m=25.0).validate()
        self.assertIn("容不下净空", str(ctx.exception))

    def test_degenerate_dt_interval_rejected(self) -> None:
        with self.assertRaises(ValueError):
            replace(PlanParams(), dt_min_s=20.0, dt_max_s=0.3).validate()

    def test_ellipsoid_z_axis_must_exceed_twice_eps(self) -> None:
        with self.assertRaises(ValueError):
            replace(PlanParams(), ellipsoid_semiaxes_m=(30.0, 30.0, 0.05)).validate()

    def test_retreat_ladder_must_be_strictly_decreasing(self) -> None:
        with self.assertRaises(ValueError):
            replace(PlanParams(), retreat_node_ladder=(30, 40)).validate()

    def test_rrt_step_bounds(self) -> None:
        with self.assertRaises(ValueError):
            replace(PlanParams(), rrt_step_m=5.0, rrt_min_step_m=10.0).validate()

    def test_rrt_sample_step_must_not_exceed_sdf_cell(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            replace(PlanParams(), clearance_sample_step_m=4.0).validate()
        self.assertIn("整格跳检", str(ctx.exception))

    def test_rrt_goal_rate_in_open_interval(self) -> None:
        with self.assertRaises(ValueError):
            replace(PlanParams(), rrt_goal_sample_rate=1.0).validate()

    def test_decisions_count(self) -> None:
        self.assertEqual(PlanParams().n_decisions, 600)


if __name__ == "__main__":
    unittest.main()
