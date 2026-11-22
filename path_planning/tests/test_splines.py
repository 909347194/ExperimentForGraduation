# -*- coding: utf-8 -*-
"""Hermite 样条测试：端点条件、C¹、弧长 Gauss、悬停、按时刻查询、节点还原。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_types import HermiteSegment, Trajectory
from path_planning.methods.timeline.splines import (
    position_at,
    segment_arc_length,
    segment_derivative,
    segment_position,
    segment_velocity,
    trajectory_arc_length,
    trajectory_nodes,
    velocity_at,
)


def _seg(p0, p1, v0=(0, 0, 0), v1=(0, 0, 0), dt=5.0, hover=False) -> HermiteSegment:
    return HermiteSegment(
        t0=0.0, t1=dt, p0=tuple(p0), p1=tuple(p1),
        v0=tuple(v0), v1=tuple(v1), is_hover=hover,
    )


class TestHermiteBasics(unittest.TestCase):
    def test_endpoint_conditions(self) -> None:
        seg = _seg((0, 0, 0), (10, 0, 0), v0=(2, 1, 0), v1=(3, -1, 0), dt=4.0)
        np.testing.assert_allclose(segment_position(seg, 0.0), (0, 0, 0), atol=1e-9)
        np.testing.assert_allclose(segment_position(seg, 1.0), (10, 0, 0), atol=1e-9)
        np.testing.assert_allclose(segment_velocity(seg, 0.0), (2, 1, 0), atol=1e-9)
        np.testing.assert_allclose(segment_velocity(seg, 1.0), (3, -1, 0), atol=1e-9)

    def test_derivative_vs_velocity_scaling(self) -> None:
        seg = _seg((0, 0, 0), (10, 0, 0), v0=(2, 0, 0), v1=(2, 0, 0), dt=5.0)
        d = segment_derivative(seg, 0.3)
        v = segment_velocity(seg, 0.3)
        np.testing.assert_allclose(d, v * 5.0, atol=1e-9)

    def test_vectorized_evaluation(self) -> None:
        seg = _seg((0, 0, 0), (10, 0, 0))
        s = np.linspace(0, 1, 7)
        pos = segment_position(seg, s)
        self.assertEqual(pos.shape, (7, 3))
        np.testing.assert_allclose(pos[0], (0, 0, 0), atol=1e-9)
        np.testing.assert_allclose(pos[-1], (10, 0, 0), atol=1e-9)


class TestArcLength(unittest.TestCase):
    def test_zero_velocity_arc_equals_chord(self) -> None:
        # v0=v1=0：p(s)=p0+(p1-p0)(3s²-2s³)，单调直线，弧长=弦长
        seg = _seg((0, 0, 0), (10, 0, 0), dt=5.0)
        self.assertAlmostEqual(segment_arc_length(seg), 10.0, places=6)

    def test_constant_velocity_arc(self) -> None:
        # 匀速直线：p(s)=v·Δt·s，弧长=|v|·Δt
        seg = _seg((0, 0, 0), (10, 0, 0), v0=(2, 0, 0), v1=(2, 0, 0), dt=5.0)
        self.assertAlmostEqual(segment_arc_length(seg), 10.0, places=6)

    def test_curved_arc_exceeds_chord(self) -> None:
        seg = _seg((0, 0, 0), (10, 0, 0), v0=(0, 8, 0), v1=(0, -8, 0), dt=2.0)
        self.assertGreater(segment_arc_length(seg), 10.0)
        self.assertGreater(segment_arc_length(seg), seg.chord_m)

    def test_gauss_convergence(self) -> None:
        seg = _seg((0, 0, 0), (10, 0, 0), v0=(0, 4, 0), v1=(0, -4, 0), dt=3.0)
        l8 = segment_arc_length(seg, subintervals=8)
        l16 = segment_arc_length(seg, subintervals=16)
        l32 = segment_arc_length(seg, subintervals=32)
        self.assertLess(abs(l16 - l8) / l8, 1e-3)
        self.assertLess(abs(l32 - l16) / l16, 1e-4)

    def test_hover_arc_zero(self) -> None:
        seg = _seg((5, 5, 20), (5, 5, 20), dt=30.0, hover=True)
        self.assertEqual(segment_arc_length(seg), 0.0)

    def test_invalid_gauss_points(self) -> None:
        seg = _seg((0, 0, 0), (10, 0, 0))
        with self.assertRaises(ValueError):
            segment_arc_length(seg, gauss_points=1)


class TestTrajectoryQueries(unittest.TestCase):
    def _traj(self) -> Trajectory:
        s1 = HermiteSegment(0.0, 4.0, (0, 0, 0), (10, 0, 0), (0, 0, 0), (2, 0, 0))
        s2 = HermiteSegment(4.0, 9.0, (10, 0, 0), (20, 0, 0), (2, 0, 0), (0, 0, 0))
        return Trajectory(uav_id=1, segments=(s1, s2), path_len_m=20.0)

    def test_position_at_boundaries(self) -> None:
        traj = self._traj()
        np.testing.assert_allclose(position_at(traj, 0.0), (0, 0, 0), atol=1e-9)
        np.testing.assert_allclose(position_at(traj, 4.0), (10, 0, 0), atol=1e-9)
        np.testing.assert_allclose(position_at(traj, 9.0), (20, 0, 0), atol=1e-9)

    def test_position_at_clamps_out_of_range(self) -> None:
        traj = self._traj()
        np.testing.assert_allclose(position_at(traj, -5.0), (0, 0, 0), atol=1e-9)
        np.testing.assert_allclose(position_at(traj, 100.0), (20, 0, 0), atol=1e-9)

    def test_velocity_continuous_at_junction(self) -> None:
        traj = self._traj()
        v_left = velocity_at(traj, 4.0 - 1e-6)
        v_right = velocity_at(traj, 4.0 + 1e-6)
        np.testing.assert_allclose(v_left, v_right, atol=1e-3)
        np.testing.assert_allclose(v_left, (2, 0, 0), atol=1e-3)

    def test_arc_length_sum(self) -> None:
        traj = self._traj()
        total = trajectory_arc_length(traj)
        self.assertAlmostEqual(
            total,
            segment_arc_length(traj.segments[0]) + segment_arc_length(traj.segments[1]),
            places=9,
        )
        self.assertGreater(total, 0.0)
        # 两段都是纯 x 向直线运动（速度方向不变），弧长 = 弦长和 = 20
        self.assertAlmostEqual(total, 20.0, places=6)


class TestTrajectoryNodes(unittest.TestCase):
    def test_roundtrip(self) -> None:
        s1 = HermiteSegment(0.0, 4.0, (0, 0, 0), (10, 0, 0), (0, 0, 0), (2, 0, 0))
        s2 = HermiteSegment(4.0, 9.0, (10, 0, 0), (20, 0, 0), (2, 0, 0), (0, 0, 0))
        traj = Trajectory(uav_id=1, segments=(s1, s2), path_len_m=20.0)
        p, v, dt = trajectory_nodes(traj)
        self.assertEqual(p.shape, (3, 3))
        self.assertEqual(v.shape, (3, 3))
        self.assertEqual(dt.shape, (3,))
        np.testing.assert_allclose(p, [[0, 0, 0], [10, 0, 0], [20, 0, 0]])
        np.testing.assert_allclose(v, [[0, 0, 0], [2, 0, 0], [0, 0, 0]])
        np.testing.assert_allclose(dt, [4.0, 5.0, 5.0])  # 末位镜像
        # 用还原节点重建段，弧长不变
        rebuilt = tuple(
            HermiteSegment(
                float(np.sum(dt[:i])), float(np.sum(dt[: i + 1])),
                tuple(p[i]), tuple(p[i + 1]), tuple(v[i]), tuple(v[i + 1]),
            )
            for i in range(len(p) - 1)
        )
        self.assertAlmostEqual(
            trajectory_arc_length(rebuilt), trajectory_arc_length(traj), places=9
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
