# -*- coding: utf-8 -*-
"""降级巡航剖面测试：时长放大、位置贴折线、悬停计入时长但不计入飞行时长、入参校验。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_types import TimeSource
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.timeline.fallback import CONSERVATIVE_FACTOR, cruise_profile
from path_planning.methods.timeline.splines import position_at, segment_velocity

PATH = [(0.0, 0.0, 10.0), (30.0, 0.0, 10.0), (30.0, 40.0, 10.0)]  # 折线长 70 m


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=50000.0, max_time_s=3000.0,
    )


class TestCruiseProfile(unittest.TestCase):
    def test_conservative_duration_and_geometry(self) -> None:
        traj = cruise_profile(PATH, _vehicle(), uav_id=7, t0=120.0)
        veh = _vehicle()
        eff = veh.v_max_plan_mps / CONSERVATIVE_FACTOR
        # 时长 = 折线长 / (v_max_plan / κ)，保守系数只作用在时间上
        self.assertAlmostEqual(traj.flight_duration, 70.0 / eff, places=6)
        self.assertAlmostEqual(traj.path_len_m, 70.0, places=6)
        self.assertAlmostEqual(traj.t0, 120.0, places=6)
        self.assertEqual(traj.uav_id, 7)
        self.assertEqual(traj.time_source, TimeSource.ESTIMATE)
        self.assertAlmostEqual(traj.meta["conservative_factor"], CONSERVATIVE_FACTOR)

    def test_positions_stay_on_polyline(self) -> None:
        """定速 Hermite 必须退化为线性：折点处位置与折线**逐点重合**，不切角。"""
        traj = cruise_profile(PATH, _vehicle())
        # 折点处（第一段末端前一瞬）：应恰在 (30,0,10)
        np.testing.assert_allclose(
            position_at(traj, traj.segments[0].t1 - 1e-9), (30.0, 0.0, 10.0), atol=1e-4
        )
        mid_t = 0.5 * (traj.segments[0].t0 + traj.segments[0].t1)
        np.testing.assert_allclose(position_at(traj, mid_t), (15.0, 0.0, 10.0), atol=1e-6)
        # 段内速度恒定且模长 = 有效速度
        for seg in traj.segments:
            for s in (0.0, 0.5, 1.0):
                v = segment_velocity(seg, s)
                self.assertAlmostEqual(float(np.linalg.norm(v)),
                                       _vehicle().v_max_plan_mps / CONSERVATIVE_FACTOR, places=6)

    def test_service_hover_counts_in_duration_only(self) -> None:
        traj = cruise_profile(PATH, _vehicle(), service_time_s=30.0)
        self.assertTrue(traj.segments[-1].is_hover)
        self.assertAlmostEqual(traj.duration - traj.flight_duration, 30.0, places=6)
        self.assertAlmostEqual(traj.path_len_m, 70.0, places=6)  # 悬停不加弧长

    def test_degenerate_and_errors(self) -> None:
        one = cruise_profile([(1.0, 2.0, 3.0)], _vehicle())
        self.assertTrue(one.segments[0].is_hover)
        self.assertEqual(one.path_len_m, 0.0)

        with self.assertRaises(ValueError):
            cruise_profile([(0.0, 0.0, 0.0), (1.0, 0.0)], _vehicle())
        with self.assertRaises(ValueError):
            cruise_profile(PATH, _vehicle(), speed_mps=0.0)
        with self.assertRaises(ValueError):
            cruise_profile(PATH, _vehicle(), conservative=1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
