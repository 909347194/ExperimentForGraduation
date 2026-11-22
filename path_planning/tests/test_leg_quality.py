# -*- coding: utf-8 -*-
"""分段账测试：leg 切分、leg 切片、停留点边界判据。

这三件是多段航迹审计的地基：切错了，逐段净空和时间账都会算错段（还看不出来
——数值大小常常很接近）。所以这里用**合成的**航迹把边界情形钉住，不依赖场地。
"""

from __future__ import annotations

import unittest

from path_planning.common.plan_types import HermiteSegment, Trajectory
from path_planning.experiments.exp01_smoke.support.quality_report import _boundary_check
from path_planning.experiments.exp01_smoke.support.trajectory_margins import (
    leg_trajectory,
    split_legs,
    time_quality,
)


def _flight(p0, p1, t0: float, dt: float) -> HermiteSegment:
    return HermiteSegment(
        t0=t0, t1=t0 + dt, p0=tuple(p0), p1=tuple(p1),
        v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0),
    )


def _hover(p, t0: float, dt: float) -> HermiteSegment:
    return HermiteSegment(
        t0=t0, t1=t0 + dt, p0=tuple(p), p1=tuple(p),
        v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0), is_hover=True,
    )


def _tour() -> Trajectory:
    """巢 → A(20 s) → B(20 s) → 巢(30 s)：3 段飞行，2 个内部悬停 + 1 个末尾悬停。"""
    segs = [
        _flight((0, 0, 0), (100, 0, 0), 0.0, 10.0),
        _hover((100, 0, 0), 10.0, 20.0),
        _flight((100, 0, 0), (100, 50, 0), 30.0, 5.0),
        _hover((100, 50, 0), 35.0, 20.0),
        _flight((100, 50, 0), (0, 0, 0), 55.0, 12.0),
        _hover((0, 0, 0), 67.0, 30.0),
    ]
    chord = 100.0 + 50.0 + 111.80339887498948
    return Trajectory(uav_id=1, segments=segs, path_len_m=chord)


class TestSplitLegs(unittest.TestCase):
    def test_three_legs_with_service(self) -> None:
        legs = split_legs(_tour())
        self.assertEqual(len(legs), 3)
        self.assertEqual([leg.index for leg in legs], [0, 1, 2])
        self.assertEqual([leg.hover_s for leg in legs], [20.0, 20.0, 30.0])
        self.assertAlmostEqual(legs[0].flight_s, 10.0)
        self.assertAlmostEqual(legs[2].flight_s, 12.0)

    def test_leg_endpoints_are_stops(self) -> None:
        legs = split_legs(_tour())
        self.assertEqual(legs[0].start, (0.0, 0.0, 0.0))
        self.assertEqual(legs[0].end, (100.0, 0.0, 0.0))
        self.assertEqual(legs[1].end, (100.0, 50.0, 0.0))
        self.assertEqual(legs[2].end, (0.0, 0.0, 0.0))

    def test_adjacent_flights_merge_into_one_leg(self) -> None:
        """服务时长 0 的停留点在航迹里没有悬停段，两段飞行应合成一条 leg。"""
        segs = [
            _flight((0, 0, 0), (10, 0, 0), 0.0, 2.0),
            _flight((10, 0, 0), (20, 0, 0), 2.0, 2.0),
            _hover((20, 0, 0), 4.0, 10.0),
        ]
        legs = split_legs(Trajectory(uav_id=1, segments=segs, path_len_m=20.0))
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0].last_seg, 1)
        self.assertAlmostEqual(legs[0].flight_s, 4.0)
        self.assertAlmostEqual(legs[0].hover_s, 10.0)

    def test_all_hover_raises(self) -> None:
        traj = Trajectory(
            uav_id=1,
            segments=[_hover((0, 0, 0), 0.0, 5.0), _hover((0, 0, 0), 5.0, 5.0)],
            path_len_m=0.0,
        )
        with self.assertRaises(ValueError):
            split_legs(traj)

    def test_leg_flight_plus_hover_matches_whole(self) -> None:
        traj = _tour()
        legs = split_legs(traj)
        self.assertAlmostEqual(sum(leg.flight_s for leg in legs), traj.flight_duration)
        self.assertAlmostEqual(sum(leg.hover_s for leg in legs), traj.hover_duration)


class TestLegTrajectory(unittest.TestCase):
    def test_slice_reuses_time_account(self) -> None:
        """切片只含飞行段：停靠时长记在 ``LegSlice.hover_s``，不在切片里重复计。"""
        traj = _tour()
        leg = split_legs(traj)[1]
        sub = leg_trajectory(traj, leg)
        tq = time_quality(sub, vehicle=_vehicle())
        self.assertEqual(sub.meta["leg_index"], 1)
        self.assertEqual(len(sub.segments), leg.last_seg - leg.first_seg + 1)
        self.assertAlmostEqual(tq["flight_s"], 5.0)
        self.assertAlmostEqual(tq["hover_s"], 0.0)
        self.assertAlmostEqual(leg.hover_s, 20.0)

    def test_slice_does_not_mutate_source(self) -> None:
        traj = _tour()
        before = len(traj.segments)
        leg_trajectory(traj, split_legs(traj)[0])
        self.assertEqual(len(traj.segments), before)


class TestBoundaryCheck(unittest.TestCase):
    def test_matching_stops_pass(self) -> None:
        traj = _tour()
        stops = [((100.0, 0.0, 0.0), 20.0), ((100.0, 50.0, 0.0), 20.0),
                 ((0.0, 0.0, 0.0), 30.0)]
        check = _boundary_check(1, split_legs(traj), stops)
        self.assertTrue(check.ok)

    def test_wrong_leg_count_fails(self) -> None:
        traj = _tour()
        stops = [((100.0, 0.0, 0.0), 0.0), ((100.0, 50.0, 0.0), 20.0),
                 ((0.0, 0.0, 0.0), 30.0)]
        check = _boundary_check(1, split_legs(traj), stops)
        self.assertFalse(check.ok)
        self.assertIn("期望 2 段", check.detail)

    def test_endpoint_mismatch_fails(self) -> None:
        traj = _tour()
        stops = [((100.0, 0.0, 1.0), 20.0), ((100.0, 50.0, 0.0), 20.0),
                 ((0.0, 0.0, 0.0), 30.0)]
        check = _boundary_check(1, split_legs(traj), stops)
        self.assertFalse(check.ok)
        self.assertIn("偏离", check.detail)


def _vehicle():
    from path_planning.common.vehicle_spec import VehicleSpec

    return VehicleSpec(
        model="M30T", mass_kg=9.5, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=3.0,
        a_max_mps2=3.0, f_max_n=200.0, f_min_n=78.0,
        max_range_m=20000.0, max_time_s=1200.0,
    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
