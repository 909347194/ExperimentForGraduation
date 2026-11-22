# -*- coding: utf-8 -*-
"""弦化与 CPA 几何测试：解析解对拍、三支裁剪、相对静止、时间窗容差、弦化误差与覆盖。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_types import HermiteSegment, Trajectory, TimeSource
from path_planning.methods.conflict.chord_sampling import (
    MotionChord,
    chordify,
    max_initial_chord_s,
)
from path_planning.methods.conflict.cpa_geometry import (
    chord_pair_closest,
    chord_pair_distance,
    closest_approach_linear,
    overlap_window,
)
from path_planning.methods.timeline.splines import position_at


def _chord(t0, t1, p0, p1, err=0.0, seg=0) -> MotionChord:
    return MotionChord(t0, t1, p0, p1, err, seg)


class TestClosestApproachAnalytic(unittest.TestCase):
    def test_perpendicular_crossing_hits_zero(self) -> None:
        """A 沿 +x 走、B 沿 +y 走，同一时刻交于原点：d_min == 0，u* == 2 s。"""
        ca = closest_approach_linear(
            p_a=(-2.0, 0.0, 0.0), v_a=(1.0, 0.0, 0.0),
            p_b=(0.0, -2.0, 0.0), v_b=(0.0, 1.0, 0.0),
            dt=4.0,
        )
        self.assertAlmostEqual(ca.distance, 0.0, places=12)
        self.assertAlmostEqual(ca.time_offset, 2.0, places=12)

    def test_parallel_same_speed_is_relative_static(self) -> None:
        """同速同向：a≈0 分支，距离恒为初始间距（不是退化，是真实情况）。"""
        ca = closest_approach_linear(
            p_a=(0.0, 0.0, 0.0), v_a=(5.0, 0.0, 0.0),
            p_b=(0.0, 7.0, 0.0), v_b=(5.0, 0.0, 0.0),
            dt=10.0,
        )
        self.assertTrue(ca.is_static_relative)
        self.assertAlmostEqual(ca.distance, 7.0, places=12)
        self.assertEqual(ca.time_offset, 0.0)

    def test_clip_future_minimum(self) -> None:
        """极小点在窗口之后：取窗口末端（``u=dt`` 支）。"""
        ca = closest_approach_linear(
            p_a=(0.0, 0.0, 0.0), v_a=(1.0, 0.0, 0.0),
            p_b=(10.0, 0.0, 0.0), v_b=(0.0, 0.0, 0.0),
            dt=2.0,
        )
        self.assertAlmostEqual(ca.time_offset, 2.0, places=12)
        self.assertAlmostEqual(ca.distance, 8.0, places=12)  # 10 - 2

    def test_clip_past_minimum(self) -> None:
        """极小点在窗口之前：取起点（``u=0`` 支），距离正在变大。"""
        ca = closest_approach_linear(
            p_a=(0.0, 0.0, 0.0), v_a=(1.0, 0.0, 0.0),
            p_b=(-10.0, 0.0, 0.0), v_b=(0.0, 0.0, 0.0),
            dt=2.0,
        )
        self.assertEqual(ca.time_offset, 0.0)
        self.assertAlmostEqual(ca.distance, 10.0, places=12)

    def test_zero_window_is_start_only(self) -> None:
        ca = closest_approach_linear((0.0, 0.0, 0.0), (3.0, 0.0, 0.0),
                                     (3.0, 4.0, 0.0), (0.0, 0.0, 0.0), dt=0.0)
        self.assertAlmostEqual(ca.distance, 5.0, places=12)


class TestOverlapWindow(unittest.TestCase):
    def test_plain_and_touching(self) -> None:
        a = _chord(0.0, 2.0, (0, 0, 0), (1, 0, 0))
        b = _chord(1.0, 3.0, (0, 1, 0), (1, 1, 0))
        self.assertEqual(overlap_window(a, b), (1.0, 2.0))
        c = _chord(2.0, 3.0, (0, 1, 0), (1, 1, 0))
        self.assertEqual(overlap_window(a, c), (2.0, 2.0))  # 相接不算分离

    def test_gap_within_eps_only(self) -> None:
        a = _chord(0.0, 2.0, (0, 0, 0), (1, 0, 0))
        d = _chord(2.4, 3.0, (0, 1, 0), (1, 1, 0))
        self.assertIsNone(overlap_window(a, d, eps_s=0.1))
        win = overlap_window(a, d, eps_s=0.5)
        self.assertIsNotNone(win)
        self.assertAlmostEqual(win[0], win[1], places=12)  # 零长窗口

    def test_no_overlap_means_safe_infinity(self) -> None:
        a = _chord(0.0, 1.0, (0, 0, 0), (1, 0, 0))
        b = _chord(5.0, 6.0, (0, 0, 0), (1, 0, 0))
        self.assertEqual(chord_pair_distance(a, b), float("inf"))
        self.assertIsNone(chord_pair_closest(a, b))


class TestChordPairDistance(unittest.TestCase):
    def test_crossing_within_overlap(self) -> None:
        a = _chord(0.0, 4.0, (-2.0, 0.0, 0.0), (2.0, 0.0, 0.0), seg=0)
        b = _chord(0.0, 4.0, (0.0, -2.0, 0.0), (0.0, 2.0, 0.0), seg=0)
        self.assertAlmostEqual(chord_pair_distance(a, b), 0.0, places=12)

    def test_partial_overlap_uses_local_geometry(self) -> None:
        """重叠窗只是前半段时，CPA 必须在窗内取，不能拿全程参数算。"""
        a = _chord(0.0, 4.0, (0.0, 0.0, 0.0), (4.0, 0.0, 0.0))
        b = _chord(2.0, 6.0, (2.0, 3.0, 0.0), (6.0, 3.0, 0.0))
        ca = chord_pair_closest(a, b)
        self.assertIsNotNone(ca)
        self.assertAlmostEqual(ca.distance, 3.0, places=12)  # 平行同速，间距 3


class TestChordify(unittest.TestCase):
    def test_max_initial_chord_formula(self) -> None:
        # 2*sqrt(8*0.05/3) ≈ 0.7303
        self.assertAlmostEqual(max_initial_chord_s(0.05, 3.0), 0.7303, places=4)

    def _straight_traj(self, dur=10.0, p0=(0, 0, 0), p1=(30, 0, 0)) -> Trajectory:
        """速度与位移匹配的匀速直线：弦化偏差应恰为 0。"""
        u = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
        v = u / dur
        seg = HermiteSegment(t0=0.0, t1=dur, p0=p0, p1=p1,
                             v0=tuple(v), v1=tuple(v))
        return Trajectory(uav_id=1, segments=(seg,), path_len_m=float(np.linalg.norm(u)),
                          time_source=TimeSource.TRAJECTORY)

    def test_straight_line_chords_are_short_because_of_time_cap(self) -> None:
        traj = self._straight_traj(dur=2.0)
        chords = chordify(traj, eps_a_m=0.05, max_chord_s=0.73)
        self.assertGreaterEqual(len(chords), 3)  # 2.0/0.73 → 至少 3 段
        for c in chords:
            self.assertLessEqual(c.duration, 0.73 + 1e-12)
            self.assertLessEqual(c.error_m, 1e-9)  # 匀速直线 Hermite = 线性参数化

    def test_nonuniform_speed_still_bounded_by_eps(self) -> None:
        """几何是直线但速度剖面不均匀：弦化偏差必须仍被 eps 压住（靠细分）。"""
        seg = HermiteSegment(t0=0.0, t1=2.0, p0=(0.0, 0.0, 0.0), p1=(30.0, 0.0, 0.0),
                             v0=(3.0, 0.0, 0.0), v1=(3.0, 0.0, 0.0))
        traj = Trajectory(uav_id=1, segments=(seg,), path_len_m=30.0,
                          time_source=TimeSource.TRAJECTORY)
        chords = chordify(traj, eps_a_m=0.05, max_chord_s=0.73)
        self.assertLessEqual(max(c.error_m for c in chords), 0.05 + 1e-9)

    def test_curved_segment_refines_and_records_error(self) -> None:
        """强弯段：弦化必须细分，且报告的最大实测偏差不得超过阈值太多。"""
        seg = HermiteSegment(
            t0=0.0, t1=6.0, p0=(0.0, 0.0, 0.0), p1=(30.0, 0.0, 0.0),
            v0=(0.0, 8.0, 0.0), v1=(0.0, -8.0, 0.0),
        )
        traj = Trajectory(uav_id=1, segments=(seg,), path_len_m=40.0,
                          time_source=TimeSource.TRAJECTORY)
        chords = chordify(traj, eps_a_m=0.05, max_chord_s=0.73)
        self.assertGreater(len(chords), 8)
        self.assertEqual(max(c.error_m for c in chords) <= 0.05 + 1e-9, True)

    def test_coverage_contiguous_and_on_curve(self) -> None:
        traj = self._straight_traj(dur=2.0)
        chords = chordify(traj, eps_a_m=0.05, max_chord_s=1.0)
        self.assertEqual(chords[0].t0, traj.segments[0].t0)
        self.assertAlmostEqual(chords[-1].t1, traj.segments[0].t1, places=12)
        for a, b in zip(chords[:-1], chords[1:]):
            self.assertAlmostEqual(a.t1, b.t0, places=12)
            np.testing.assert_allclose(a.p1, b.p0, atol=1e-12)
        for c in chords:
            np.testing.assert_allclose(position_at(traj, c.t0), c.p0, atol=1e-9)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
