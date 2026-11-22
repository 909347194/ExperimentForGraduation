# -*- coding: utf-8 -*-
"""``CyclicTaskAllocator``（环的上层端口实现）：投放、筛空闲机、最近机派活、回巢、留痕。"""

from __future__ import annotations

import unittest

from closed_loop.allocators import CyclicTaskAllocator, LoopTask


def _task(tid, xy, release, service=20.0, z=50.0) -> LoopTask:
    return LoopTask(id=tid, position=(float(xy[0]), float(xy[1]), z),
                    service_time_s=service, release_cycle=release)


HOMES = {1: (0.0, 1000.0, 20.0), 2: (0.0, -1000.0, 20.0)}
NESTS = {1: 0, 2: 1}


def _allocator(*, busy=None, tasks=None, cycles=2) -> CyclicTaskAllocator:
    return CyclicTaskAllocator(
        tasks or (_task(10, (0, 600), 1), _task(20, (0, -600), 1)),
        HOMES, NESTS,
        cycle_length_s=240.0, n_cycles=cycles,
        busy_of=(busy or (lambda _uid: 0.0)),
    )


class TestCycleAdvance(unittest.TestCase):
    def test_time_axis_advances_by_cycle_length(self) -> None:
        a = _allocator(tasks=(_task(10, (0, 600), 1), _task(11, (0, 500), 2)), cycles=2)
        s1 = a.step()
        s2 = a.step()
        self.assertEqual((s1.cycle, s2.cycle), (1, 2))
        self.assertEqual((s1.t_start, s1.t_end), (0.0, 240.0))
        self.assertEqual((s2.t_start, s2.t_end), (240.0, 480.0))
        self.assertEqual([o.t0 for o in s2.orders], [240.0])

    def test_tasks_are_released_only_on_their_cycle(self) -> None:
        a = _allocator(cycles=3)
        s1 = a.step()
        s2 = a.step()
        s3 = a.step()
        self.assertEqual(sorted(s1.executed_ids), [10, 20])
        self.assertEqual(s2.executed_ids, ())
        self.assertEqual(s3.executed_ids, ())
        self.assertEqual(s3.orders, ())

    def test_step_after_last_cycle_raises(self) -> None:
        a = _allocator(cycles=1)
        a.step()
        with self.assertRaises(ValueError):
            a.step()

    def test_order_carries_task_ids_for_rollback(self) -> None:
        """失败回池靠 ``task_ids`` 认身份，不从 stops 里反推（机巢也在里面）。"""
        order = _allocator(tasks=(_task(10, (0, 520), 1),), cycles=1).step().orders[0]
        self.assertEqual(order.task_ids, (10,))


class TestAssignment(unittest.TestCase):
    def test_nearest_free_uav_gets_the_task(self) -> None:
        a = _allocator(tasks=(_task(10, (0, 520), 1), _task(20, (0, -6400), 1)), cycles=1)
        snap = a.step()
        by_uid = {o.uav_id: [s.id for s in o.stops] for o in snap.orders}
        self.assertEqual(by_uid, {1: [0, 10, 0], 2: [1, 20, 1]})

    def test_busy_uav_is_skipped_not_double_booked(self) -> None:
        """机 1 还在飞（忙到 300 s）→ 本拍只剩机 2 可用，先派 id 小的任务，剩下的留痕。"""
        a = _allocator(
            tasks=(_task(10, (0, 520), 1), _task(20, (0, -520), 1)),
            busy=lambda uid: 300.0 if uid == 1 else 0.0,
        )
        snap = a.step()
        self.assertEqual([o.uav_id for o in snap.orders], [2])
        self.assertEqual(snap.executed_ids, (10,))
        self.assertEqual(a.unassigned[1], (20,))

    def test_unassigned_when_fleet_is_too_small(self) -> None:
        a = _allocator(
            tasks=(_task(10, (0, 520), 1), _task(20, (0, -520), 1), _task(30, (0, 0), 1)),
            cycles=1,
        )
        snap = a.step()
        self.assertEqual(len(snap.orders), 2)
        self.assertEqual(a.unassigned[1], (30,))

    def test_order_returns_to_its_own_nest(self) -> None:
        a = _allocator(tasks=(_task(10, (0, 520), 1),), cycles=1)
        order = a.step().orders[0]
        self.assertEqual(order.stops[0].kind, "nest")
        self.assertEqual(order.stops[-1].kind, "nest")
        self.assertEqual(order.stops[0].id, order.stops[-1].id)
        self.assertEqual(order.stops[0].position, HOMES[1])
        self.assertAlmostEqual(order.stops[1].service_time_s, 20.0)

    def test_est_cost_is_round_trip_distance(self) -> None:
        a = _allocator(tasks=(_task(10, (0, 600), 1, z=20.0),), cycles=1)
        snap = a.step()
        self.assertAlmostEqual(snap.est_cost[1], 2.0 * 400.0)

    def test_duplicate_task_ids_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _allocator(tasks=(_task(10, (0, 520), 1), _task(10, (0, -520), 1)))

    def test_missing_home_nest_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CyclicTaskAllocator([], HOMES, {1: 0}, cycle_length_s=240.0, n_cycles=1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
