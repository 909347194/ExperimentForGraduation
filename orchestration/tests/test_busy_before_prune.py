# -*- coding: utf-8 -*-
"""busy_until 必须在周期末 prune 之前采集，否则账上恒为 0、审计假 FAIL。"""

from __future__ import annotations

from dataclasses import replace

from orchestration import LoopDriver
from orchestration.protocol import (
    CostUpdate,
    CycleSnapshot,
    PlannerReport,
    TourOrder,
    TourStop,
    TourTicket,
)


class _Allocator:
    def __init__(self) -> None:
        stop = TourStop(id=1, kind="task", position=(0.0, 0.0, 12.0))
        nest = TourStop(id=0, kind="nest", position=(0.0, 0.0, 15.0))
        order = TourOrder(uav_id=4, t0=0.0, start_position=(0.0, 0.0, 15.0),
                          stops=(stop, nest), task_ids=(1,))
        self.snap = CycleSnapshot(cycle=1, t_start=0.0, t_end=1800.0,
                                  orders=(order,), executed_ids=(1,))

    def step(self, new_tasks=None):
        return self.snap

    def prepare(self, new_tasks=None):
        return self.snap

    def resolve(self):
        return replace(self.snap, cost_version="cycle:1")

    def commit(self):
        return self.snap

    def apply_cost_update(self, update: CostUpdate):
        return update

    def cost_state(self) -> dict:
        return {}


class _Planner:
    """prune 会清掉注册表；清理后 busy_until 只能返回 0。"""

    def __init__(self) -> None:
        self.busy: dict[int, float] = {}
        self.pruned = False

    def plan_cycle(self, orders) -> PlannerReport:
        tickets = {
            o.uav_id: TourTicket(uav_id=o.uav_id, ok=True, t0=o.t0,
                                 t_end=80.0, duration_s=80.0, path_len_m=100.0,
                                 task_ids=(1,))
            for o in orders
        }
        for uid in tickets:
            self.busy[uid] = 80.0
        return PlannerReport(tickets=tickets)

    def busy_until(self, uav_id: int) -> float:
        return float(self.busy.get(int(uav_id), 0.0))

    def prune(self, now: float):
        self.busy = {}
        self.pruned = True
        return (4,)

    def busy_until_before(self, uav_id: int) -> float:  # pragma: no cover
        raise AssertionError("不应调用")


def test_busy_survives_prune():
    planner = _Planner()
    trace = LoopDriver(_Allocator(), planner).run(1)
    assert planner.pruned
    assert trace.cycles[0].busy_until == {4: 80.0}
    assert trace.cycles[0].report.tickets[4].t_end == 80.0
