# -*- coding: utf-8 -*-
"""航迹注册表测试：注册/占用查询/同机重叠拒绝/原地替换/落地注销/时间平移。"""

from __future__ import annotations

import unittest

from path_planning.common.plan_types import HermiteSegment, Trajectory, TimeSource
from path_planning.methods.trajectory_registry import TrajectoryRegistry


def _traj(uid: int, t0: float, dur: float, *, y: float = 0.0) -> Trajectory:
    seg = HermiteSegment(
        t0=t0, t1=t0 + dur, p0=(0.0, y, 10.0), p1=(100.0, y, 10.0),
        v0=(100.0 / dur, 0.0, 0.0), v1=(100.0 / dur, 0.0, 0.0),
    )
    return Trajectory(
        uav_id=uid, segments=(seg,), path_len_m=100.0, time_source=TimeSource.TRAJECTORY
    )


class TestTrajectoryRegistry(unittest.TestCase):
    def test_register_and_query(self) -> None:
        reg = TrajectoryRegistry()
        reg.register(_traj(1, 0.0, 10.0))
        reg.register(_traj(2, 0.0, 20.0), source="legacy")
        self.assertEqual(reg.ids(), (1, 2))
        self.assertEqual(reg.source_of(2), "legacy")
        self.assertAlmostEqual(reg.busy_until(1), 10.0)
        self.assertAlmostEqual(reg.busy_until(9), 0.0)  # 未注册 == 0，与上层判空闲同口径
        self.assertEqual(len(reg.active_at(5.0)), 2)
        self.assertEqual(reg.active_at(15.0)[0].uav_id, 2)

    def test_overlapping_same_uav_raises(self) -> None:
        """同一机号不得有两条未结束航迹：位置会有两个答案。"""
        reg = TrajectoryRegistry()
        reg.register(_traj(1, 0.0, 10.0))
        with self.assertRaises(ValueError):
            reg.register(_traj(1, 5.0, 10.0))
        # 旧航迹已经结束时可以覆盖（落地结算后重申）
        reg.register(_traj(1, 10.0, 10.0))
        self.assertAlmostEqual(reg.busy_until(1), 20.0)

    def test_replace_allows_extension(self) -> None:
        """让路会拉长时长，替换不能拿自己的新 t_end 去挡自己。"""
        reg = TrajectoryRegistry()
        reg.register(_traj(1, 0.0, 10.0))
        reg.replace(_traj(1, 0.0, 14.0))
        self.assertAlmostEqual(reg.busy_until(1), 14.0)
        self.assertEqual(reg.source_of(1), "planned")  # 来源保持

    def test_prune_and_shift(self) -> None:
        reg = TrajectoryRegistry()
        reg.register(_traj(1, 0.0, 4.0))
        reg.register(_traj(2, 10.0, 4.0))
        self.assertEqual(reg.prune(5.0), (1,))
        self.assertEqual(reg.ids(), (2,))
        shifted = reg.with_shift(100.0)
        self.assertAlmostEqual(shifted[0].t0, 110.0)
        self.assertAlmostEqual(shifted[0].t_end, 114.0)

    def test_unregister(self) -> None:
        reg = TrajectoryRegistry()
        reg.register(_traj(1, 0.0, 4.0))
        self.assertIsNotNone(reg.unregister(1))
        self.assertIsNone(reg.unregister(1))
        self.assertEqual(len(reg), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
