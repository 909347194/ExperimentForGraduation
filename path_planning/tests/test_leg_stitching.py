# -*- coding: utf-8 -*-
"""按航段拆/拼的单元测试（不跑 NLP，只验时间账与结构）。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_types import HermiteSegment, TimeSource, Trajectory
from path_planning.methods.coordination.leg_stitching import (
    leg_blocks,
    legs_of,
    stitch_leg,
)


def _seg(t0: float, t1: float, p0, p1) -> HermiteSegment:
    u = (np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)) / (t1 - t0)
    return HermiteSegment(t0=t0, t1=t1, p0=p0, p1=p1,
                          v0=tuple(u), v1=tuple(u))


def _hover(t0: float, t1: float, p) -> HermiteSegment:
    return HermiteSegment(t0=t0, t1=t1, p0=p, p1=p,
                          v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0), is_hover=True)


def _tour(uav_id: int = 1, dur2: float = 40.0) -> Trajectory:
    """起点 → A（停 20 s）→ B 的两航次。"""
    a, b, c = (0.0, 0.0, 10.0), (100.0, 0.0, 10.0), (200.0, 50.0, 10.0)
    segs = (
        _seg(0.0, 30.0, a, b),
        _hover(30.0, 50.0, b),
        _seg(50.0, 50.0 + dur2, b, c),
    )
    return Trajectory(uav_id=uav_id, segments=segs, path_len_m=250.0,
                      time_source=TimeSource.TRAJECTORY,
                      meta={"stops": (1, 2)})


class LegStitchingTest(unittest.TestCase):
    def test_legs_split_at_hover(self) -> None:
        traj = _tour()
        legs = legs_of(traj)
        self.assertEqual(len(legs), 2)
        self.assertAlmostEqual(legs[0].t0, 0.0)
        self.assertAlmostEqual(legs[0].t_end, 30.0)
        self.assertAlmostEqual(legs[0].service_after_s, 20.0)
        self.assertAlmostEqual(legs[1].t0, 50.0)
        self.assertAlmostEqual(legs[1].t_end, 90.0)
        self.assertAlmostEqual(legs[1].service_after_s, 0.0)
        # 折线端点必须与段端点一致（改几何时端点冻结）
        np.testing.assert_allclose(legs[1].path[0], (100.0, 0.0, 10.0), atol=1e-9)
        np.testing.assert_allclose(legs[1].path[-1], (200.0, 50.0, 10.0), atol=1e-9)

    def test_stitch_shifts_suffix_and_keeps_hover(self) -> None:
        traj = _tour()
        blocks = leg_blocks(traj)
        self.assertEqual(len(blocks), 2)
        # 第二段重解成 55 s（比原来长 15 s）：整条航次必须自洽，悬停仍 20 s
        b, c = (100.0, 0.0, 10.0), (200.0, 50.0, 10.0)
        new_leg = Trajectory(
            uav_id=1, segments=(_seg(50.0, 105.0, b, c),), path_len_m=260.0,
            time_source=TimeSource.TRAJECTORY,
        )
        out = stitch_leg(traj, blocks, 1, new_leg)
        out.validate()
        self.assertAlmostEqual(out.t_end, 105.0)
        self.assertAlmostEqual(out.hover_duration, 20.0)
        self.assertAlmostEqual(out.flight_duration, 85.0)
        self.assertEqual(out.meta["legs"], 2)
        self.assertEqual(out.meta["replanned_leg"], 1)

    def test_stitch_rejects_mismatched_endpoints(self) -> None:
        traj = _tour()
        b, other = (100.0, 0.0, 10.0), (210.0, 50.0, 10.0)
        new_leg = Trajectory(
            uav_id=1, segments=(_seg(50.0, 90.0, b, other),), path_len_m=260.0,
            time_source=TimeSource.TRAJECTORY,
        )
        with self.assertRaises(ValueError):
            stitch_leg(traj, leg_blocks(traj), 1, new_leg)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
