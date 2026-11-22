# -*- coding: utf-8 -*-
"""环驱动的单元测试：用假端口固定"每拍的顺序、超框判定、失败票、注册表清理"。

真场（广州数据 + 全域 SDF）的端到端不在这里跑——那是分钟级、要建场的场景级验证；
本文件只钉协议与驱动的语义，保证这两层不会因为改适配器而悄悄变形。
"""

from __future__ import annotations

import unittest

from closed_loop import (
    CycleSnapshot,
    LoopDriver,
    LoopTrace,
    PlannerReport,
    TourOrder,
    TourStop,
    TourTicket,
)


def _order(uid: int, t0: float, task_ids=(11,), nest: int = 0) -> TourOrder:
    stops = [TourStop(id=nest, kind="nest", position=(0.0, 0.0, 10.0))]
    for tid in task_ids:
        stops.append(TourStop(id=tid, kind="task", position=(100.0, 0.0, 20.0)))
    stops.append(TourStop(id=nest, kind="nest", position=(0.0, 0.0, 10.0)))
    return TourOrder(uav_id=uid, t0=t0, start_position=(0.0, 0.0, 10.0), stops=tuple(stops))


class _FakeAllocator:
    """每拍派一条航次；t_end 按 cycle_length 递增。"""

    def __init__(self, uavs=(1, 2), cycle_length: float = 100.0) -> None:
        self.uavs = uavs
        self.cycle_length = cycle_length
        self._now = 0.0
        self._cycle = 0
        self.calls: list[object] = []

    def step(self, new_tasks=None) -> CycleSnapshot:
        self.calls.append(new_tasks)
        self._cycle += 1
        self._now += self.cycle_length
        orders = tuple(
            _order(uid, self._now - self.cycle_length) for uid in self.uavs
        )
        return CycleSnapshot(
            cycle=self._cycle,
            t_start=self._now - self.cycle_length,
            t_end=self._now,
            orders=orders,
            executed_ids=tuple(self._cycle * 100 + uid for uid in self.uavs),
            est_cost={uid: 250.0 for uid in self.uavs},
        )


class _FakePlanner:
    """按 duration 决定票；duration > 周期长度即超框。"""

    def __init__(self, durations: dict[int, float] | None = None,
                 failures: dict[int, str] | None = None) -> None:
        self.durations = durations or {1: 80.0, 2: 120.0}
        self.failures = failures or {}
        self.plan_calls: list[tuple[TourOrder, ...]] = []
        self.pruned: list[float] = []
        self.busy_calls: list[int] = []

    def plan_cycle(self, orders) -> PlannerReport:
        self.plan_calls.append(tuple(orders))
        tickets = {}
        for order in orders:
            uid = order.uav_id
            if uid in self.failures:
                tickets[uid] = TourTicket(
                    uav_id=uid, ok=False, t0=order.t0,
                    time_source="failed", failure=self.failures[uid],
                )
                continue
            dur = float(self.durations.get(uid, 60.0))
            tickets[uid] = TourTicket(
                uav_id=uid, ok=True, t0=order.t0,
                t_end=order.t0 + dur, duration_s=dur, path_len_m=250.0,
            )
        return PlannerReport(tickets=tickets, moved={}, conflicts_before=0,
                             conflicts_after=0, elapsed_s=0.0)

    def busy_until(self, uav_id: int) -> float:
        self.busy_calls.append(uav_id)
        return 0.0

    def prune(self, now: float) -> tuple[int, ...]:
        self.pruned.append(now)
        return ()


class TestLoopDriver(unittest.TestCase):
    def test_one_cycle_order_flows_through(self) -> None:
        alloc, planner = _FakeAllocator(uavs=(1,)), _FakePlanner()
        trace = LoopDriver(alloc, planner).run(2)
        self.assertEqual([len(c) for c in planner.plan_calls], [1, 1])
        self.assertEqual([c.cycle for c in trace.cycles], [1, 2])
        self.assertEqual(trace.executed_ids, (101, 201))

    def test_overrun_is_reported_not_swallowed(self) -> None:
        """机 2 的 120 s 超过 100 s 周期：必须出现在 overrun_cycles 与 tickets 里。"""
        alloc, planner = _FakeAllocator(uavs=(1, 2)), _FakePlanner(
            durations={1: 80.0, 2: 120.0}
        )
        trace = LoopDriver(alloc, planner).run(1)
        self.assertEqual(trace.overrun_cycles, (1,))
        self.assertEqual(trace.cycles[0].overruns, (2,))
        self.assertAlmostEqual(trace.cycles[0].report.tickets[1].duration_s, 80.0)

    def test_overrun_tolerance_respected(self) -> None:
        planner = _FakePlanner(durations={1: 80.0, 2: 101.0})
        strict = LoopDriver(_FakeAllocator(uavs=(1, 2)), planner, overrun_tol_s=0.0)
        loose = LoopDriver(_FakeAllocator(uavs=(1, 2)), planner, overrun_tol_s=2.0)
        self.assertEqual(strict.run(1).overrun_cycles, (1,))
        self.assertEqual(loose.run(1).overrun_cycles, ())

    def test_failure_ticket_is_not_counted_as_flight(self) -> None:
        alloc = _FakeAllocator(uavs=(1, 2))
        planner = _FakePlanner(failures={2: "RRT_NO_PATH"})
        trace = LoopDriver(alloc, planner).run(1)
        self.assertEqual(dict(trace.failures), {2: "RRT_NO_PATH"})
        self.assertEqual(trace.overrun_cycles, ())
        # 失败票不带时长，不能壮吞吐/时长账
        self.assertAlmostEqual(trace.planned_duration_s, 80.0)

    def test_prune_runs_at_cycle_end_and_busy_is_read(self) -> None:
        alloc, planner = _FakeAllocator(uavs=(1,)), _FakePlanner()
        driver = LoopDriver(alloc, planner)
        driver.run(2)
        self.assertEqual(planner.pruned, [100.0, 200.0])
        self.assertEqual(planner.busy_calls, [1, 1])

    def test_prune_can_be_disabled(self) -> None:
        alloc, planner = _FakeAllocator(uavs=(1,)), _FakePlanner()
        LoopDriver(alloc, planner, prune_at_cycle_end=False).run(1)
        self.assertEqual(planner.pruned, [])

    def test_arrivals_are_forwarded_per_cycle(self) -> None:
        alloc, planner = _FakeAllocator(uavs=(1,)), _FakePlanner()
        LoopDriver(alloc, planner).run(3, arrivals=[[7], None, [8]])
        self.assertEqual(alloc.calls, [[7], None, [8]])

    def test_empty_cycle_gives_empty_trace(self) -> None:
        alloc, planner = _FakeAllocator(uavs=()), _FakePlanner()
        trace = LoopDriver(alloc, planner).run(1)
        self.assertEqual(trace.cycles[0].report.tickets, {})
        self.assertIsInstance(trace, LoopTrace)

    def test_rejects_non_positive_cycles_and_negative_tolerance(self) -> None:
        with self.assertRaises(ValueError):
            LoopDriver(_FakeAllocator(), _FakePlanner()).run(0)
        with self.assertRaises(ValueError):
            LoopDriver(_FakeAllocator(), _FakePlanner(), overrun_tol_s=-1.0)


class TestProtocol(unittest.TestCase):
    def test_tour_order_requires_nest_endpoint(self) -> None:
        stops = (TourStop(id=0, kind="nest", position=(0.0, 0.0, 0.0)),
                 TourStop(id=5, kind="task", position=(1.0, 0.0, 0.0)))
        with self.assertRaises(ValueError):
            TourOrder(uav_id=1, t0=0.0, start_position=(0.0, 0.0, 0.0), stops=stops)

    def test_success_ticket_cannot_carry_failure(self) -> None:
        with self.assertRaises(ValueError):
            TourTicket(uav_id=1, ok=True, t0=0.0, failure="RRT_NO_PATH")

    def test_failed_ticket_must_declare_failed_source(self) -> None:
        with self.assertRaises(ValueError):
            TourTicket(uav_id=1, ok=False, t0=0.0, time_source="planned")

    def test_stop_kind_is_closed(self) -> None:
        with self.assertRaises(ValueError):
            TourStop(id=1, kind="waypoint", position=(0.0, 0.0, 0.0))

    def test_format_table_is_human_readable(self) -> None:
        planner = _FakePlanner(durations={1: 80.0, 2: 60.0})
        trace = LoopDriver(_FakeAllocator(uavs=(1, 2)), planner).run(1)
        table = LoopDriver.format_table(trace)
        self.assertIn("超框", table)
        self.assertEqual(len(table.splitlines()), 3)  # 表头 + 2 行（无超框提示）


if __name__ == "__main__":
    unittest.main()
