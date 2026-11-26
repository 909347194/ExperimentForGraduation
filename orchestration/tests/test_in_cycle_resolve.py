# -*- coding: utf-8 -*-
"""事实回灌（v3）的聚焦测试：唯一触发条件 = 交付解里有几何不可行的边。

钉死五件事：
  1. 无不可飞边 ⇒ 不重解，残差再大也不触发；
  2. 有不可飞边 ⇒ 恰好一次回灌式重解，只提交最终版，前版航迹撤下；
  3. 非几何类 ``infeasible``（航程/续航/求解器偶发失败）不得写成不可飞边；
  4. 中间版失败只进 attempt，不进完成/不可行口径；
  5. 回灌后仍有不可飞边 ⇒ 如实记 ``unresolved_infeasible_edges``，不重试；
  6. 能力不全或重解抛异常 ⇒ 说出原因、照常提交一次，不许静默退化。
"""

from __future__ import annotations

from typing import Sequence

import pytest

from orchestration import LoopDriver
from orchestration.protocol import (
    CostUpdate,
    CycleSnapshot,
    PlannerReport,
    TourOrder,
    TourStop,
    TourTicket,
)
from path_planning.common.plan_types import LegCostRecord

# (真实长度 m, 失败码, 严重性)
_Spec = tuple[float, str | None, str]


def _order(uid: int = 0) -> TourOrder:
    stops = (
        TourStop(id=0, kind="nest", position=(0.0, 0.0, 0.0)),
        TourStop(id=1, kind="task", position=(100.0, 0.0, 0.0), service_time_s=5.0),
        TourStop(id=0, kind="nest", position=(0.0, 0.0, 0.0)),
    )
    return TourOrder(
        uav_id=uid, t0=0.0, start_position=(0.0, 0.0, 0.0),
        stops=stops, meta={"cycle": 1}, task_ids=(1,),
    )


def _ticket(uid: int, spec: _Spec) -> TourTicket:
    real_m, code, severity = spec
    if code is None:
        legs = (LegCostRecord(from_id=0, to_id=1, length_m=real_m, duration_s=10.0),)
        return TourTicket(
            uav_id=uid, ok=True, task_ids=(1,), t0=0.0, t_end=100.0, duration_s=10.0,
            path_len_m=real_m, time_source="planned", leg_costs=legs, end_nest_id=0,
        )
    legs = (LegCostRecord(from_id=0, to_id=1, failure_code=code, severity=severity),)
    return TourTicket(
        uav_id=uid, ok=False, task_ids=(1,), t0=0.0, t_end=0.0, duration_s=0.0,
        path_len_m=0.0, time_source="failed", failure=code,
        failure_severity=severity, leg_costs=legs, end_nest_id=0,
    )


class _FakeAllocator:
    """三段式假端口：同一快照可重解，commit 只发生一次。"""

    def __init__(self, uavs: Sequence[int] = (0,), est: float = 100.0) -> None:
        self.uavs = tuple(int(u) for u in uavs)
        self.est = float(est)
        self.t = 0.0
        self.cycle = 0
        self.prepares = self.resolves = self.commits = self.steps = 0
        self.updates: list[CostUpdate] = []
        self._snap: CycleSnapshot | None = None

    def prepare(self, new_tasks=None) -> CycleSnapshot:
        self.prepares += 1
        self.cycle += 1
        self.t += 600.0
        self._snap = self._mk(attempt=0)
        return self._snap

    def apply_cost_update(self, update: CostUpdate) -> None:
        self.updates.append(update)

    def resolve(self) -> CycleSnapshot:
        self.resolves += 1
        self._snap = self._mk(attempt=self.resolves)
        return self._snap

    def commit(self) -> CycleSnapshot:
        self.commits += 1
        assert self._snap is not None
        return self._snap

    def step(self, new_tasks=None) -> CycleSnapshot:  # 旧路径能力
        self.steps += 1
        self.cycle += 1
        self.t += 600.0
        self._snap = self._mk(attempt=0)
        return self._snap

    def _mk(self, *, attempt: int) -> CycleSnapshot:
        version = "base" if attempt == 0 else "cycle:1"
        return CycleSnapshot(
            cycle=self.cycle, t_start=self.t - 600.0, t_end=self.t,
            orders=tuple(_order(u) for u in self.uavs),
            executed_ids=tuple(u + 1 for u in self.uavs),
            est_cost={u: self.est for u in self.uavs},
            snapshot_id=f"{self.cycle}:{version}",
            cost_version=version,
            attempt=attempt,
        )


class _FakePlanner:
    """第 i 次规划按 ``versions[i]`` 给出每台机的航次（缺省的 uid 沿用最后一条）。"""

    def __init__(self, versions: Sequence[dict[int, _Spec]]) -> None:
        self.versions = [dict(v) for v in versions]
        self.calls = 0
        self.reset_calls: list[tuple[int, ...]] = []

    def plan_cycle(self, orders) -> PlannerReport:
        specs = self.versions[min(self.calls, len(self.versions) - 1)]
        fallback = next(iter(specs.values()))
        tickets = {}
        for o in orders:
            uid = int(o.uav_id)
            tickets[uid] = _ticket(uid, specs.get(uid, fallback))
        self.calls += 1
        return PlannerReport(
            tickets=tickets,
            unplanned=tuple(uid for uid, t in tickets.items() if not t.ok),
        )

    def busy_until(self, uav_id: int) -> float:
        return 0.0

    def prune(self, now: float) -> tuple[int, ...]:
        return ()

    def reset_cycle(self, uav_ids) -> tuple[int, ...]:
        self.reset_calls.append(tuple(int(u) for u in uav_ids))
        return tuple(int(u) for u in uav_ids)


def _driver(alloc, planner, max_in_cycle: int = 1) -> LoopDriver:
    return LoopDriver(alloc, planner, prune_at_cycle_end=False, max_in_cycle=max_in_cycle)


def test_no_infeasible_edge_means_no_resolve() -> None:
    """估计偏 2 倍也没有不可飞边 ⇒ 不重解，残差只进诊断。"""
    alloc = _FakeAllocator(est=100.0)
    planner = _FakePlanner([{0: (200.0, None, "")}])
    plan = _driver(alloc, planner, max_in_cycle=3).step_one()

    assert alloc.prepares == 1 and alloc.resolves == 0 and alloc.commits == 1
    assert len(plan.attempts) == 1 and plan.cost_update is None
    assert plan.attempts[0].residuals == {0: pytest.approx(100.0)}


def test_infeasible_edge_triggers_exactly_one_resolve() -> None:
    """一台机断在不可飞边上、另一台已发布航迹 ⇒ 剔除后重解一次，且前版航迹撤下。"""
    alloc = _FakeAllocator(uavs=(0, 1))
    planner = _FakePlanner([
        {0: (0.0, "RRT_NO_PATH", "infeasible"), 1: (200.0, None, "")},
        {0: (120.0, None, ""), 1: (120.0, None, "")},
    ])
    plan = _driver(alloc, planner, max_in_cycle=3).step_one()

    assert alloc.resolves == 1, "回灌只做一次，max_in_cycle 是上限不是轮询次数"
    assert len(plan.attempts) == 2 and plan.n_resolves == 1
    assert plan.n_intermediate_failures == 1
    assert plan.cost_update is not None
    assert plan.cost_update.infeasible == frozenset({(0, 1)})
    assert plan.cost_version == "cycle:1"
    assert planner.reset_calls == [(1,)], "只撤下真的发布过航迹那台机"
    assert all(t.ok for t in plan.report.tickets.values())
    assert plan.rolled_back == ()


@pytest.mark.parametrize(
    "code", ["RANGE_EXCEEDED", "ENDURANCE_EXCEEDED", "OPT_FAILED"]
)
def test_non_geometric_infeasible_is_not_an_infeasible_edge(code: str) -> None:
    """航程/续航是整条航次的预算判定，OPT_FAILED 是求解器偶发失败——都不许写成不可飞边。"""
    alloc = _FakeAllocator()
    planner = _FakePlanner([{0: (0.0, code, "infeasible")}])
    plan = _driver(alloc, planner).step_one()

    assert alloc.resolves == 0 and plan.cost_update is None
    assert plan.report.tickets[0].ok is False
    assert plan.rolled_back == (1,), "失败航次的任务应回池，交给下一周期"


def test_degraded_failure_is_not_an_infeasible_edge() -> None:
    alloc = _FakeAllocator()
    planner = _FakePlanner([{0: (0.0, "TIME_EXCEEDS_BUDGET", "degraded")}])
    plan = _driver(alloc, planner).step_one()
    assert alloc.resolves == 0 and plan.cost_update is None


def test_leftover_infeasible_edges_are_reported_not_retried() -> None:
    """重解后仍有别的不可飞边：不重试、如实记账，交给下一周期。"""
    alloc = _FakeAllocator()
    planner = _FakePlanner([
        {0: (0.0, "RRT_NO_PATH", "infeasible")},
        {0: (0.0, "CLEARANCE_VIOLATION", "infeasible")},
    ])
    plan = _driver(alloc, planner, max_in_cycle=5).step_one()

    assert alloc.resolves == 1, "额度再多也只剔一次"
    assert plan.unresolved_infeasible_edges == ((0, 1),)
    assert plan.rolled_back == (1,)


def test_ban_that_does_not_take_effect_is_recorded() -> None:
    """写进代价却没落地（这里假端口不落地回灌，终版仍飞同一条边）⇒ 必须留下可判据的证据。

    这是回灌链上唯一能真失败的地方：只核对"重解决了"是结构性的，查不出
    "禁了一条不存在的键"。所以环把"禁了却仍在终版里飞"的边如实记下来。
    """
    alloc = _FakeAllocator()
    planner = _FakePlanner([
        {0: (0.0, "RRT_NO_PATH", "infeasible")},
        {0: (0.0, "RRT_NO_PATH", "infeasible")},
    ])
    plan = _driver(alloc, planner, max_in_cycle=5).step_one()

    assert alloc.resolves == 1
    assert plan.infeasible_but_survived == ((0, 1),), "禁而不掉 ⇒ 必须落账"
    assert plan.unresolved_infeasible_edges == ((0, 1),)


def test_resolve_disabled_uses_legacy_step() -> None:
    alloc = _FakeAllocator()
    planner = _FakePlanner([{0: (120.0, None, "")}])
    plan = _driver(alloc, planner, max_in_cycle=0).step_one()
    assert alloc.steps == 1 and alloc.prepares == 0 and alloc.commits == 0
    assert plan.attempts == () and plan.n_resolves == 0


def test_blocker_says_which_allocator_step_is_missing() -> None:
    """缺一个三段式入口 = 能力不全：返回"缺什么"，并真的退回一拍完成。"""

    class _NoCommit(_FakeAllocator):
        commit = None  # type: ignore[assignment]

    alloc = _NoCommit()
    planner = _FakePlanner([{0: (120.0, None, "")}])
    driver = _driver(alloc, planner)
    blocker = driver.resolve_blocker()

    assert blocker is not None and "commit" in blocker, blocker
    assert driver.can_resolve() is False
    plan = driver.step_one()
    assert alloc.steps == 1 and alloc.prepares == 0
    assert plan.attempts == ()


def test_blocker_flags_planner_without_reset_cycle() -> None:
    """缺 ``reset_cycle`` 时重解会躲自己上一版航迹：必须判为不可用，而非静默重解。"""

    class _NoReset(_FakePlanner):
        reset_cycle = None  # type: ignore[assignment]

    alloc = _FakeAllocator(uavs=(0, 1))
    planner = _NoReset([
        {0: (0.0, "RRT_NO_PATH", "infeasible"), 1: (200.0, None, "")},
        {0: (120.0, None, ""), 1: (120.0, None, "")},
    ])
    driver = _driver(alloc, planner)

    blocker = driver.resolve_blocker()
    assert blocker is not None and "reset_cycle" in blocker, blocker
    plan = driver.step_one()
    assert alloc.resolves == 0, "能力不全时不许假装重解过"
    assert plan.n_resolves == 0 and plan.report.tickets[0].ok is False


def test_resolve_crash_falls_back_to_first_version_and_still_commits() -> None:
    """重解抛异常：本周期不得漏提交，账按首解记，异常原文落进 ``resolve_error``。"""

    class _CrashOnSecond(_FakePlanner):
        def plan_cycle(self, orders):
            if self.calls >= 1:
                raise RuntimeError("下层重解炸了")
            return super().plan_cycle(orders)

    alloc = _FakeAllocator()
    planner = _CrashOnSecond([{0: (0.0, "RRT_NO_PATH", "infeasible")}])
    plan = _driver(alloc, planner, max_in_cycle=3).step_one()

    assert alloc.resolves == 1 and alloc.commits == 1, "commit 必须且只能发生一次"
    assert plan.resolve_error.startswith("RuntimeError: 下层重解炸了")
    assert len(plan.attempts) == 1, "第二版没规划成，不进 attempts"
    assert plan.n_resolves == 0 and plan.cost_update is None
    assert plan.report.tickets[0].ok is False, "交付的是首解"
    assert plan.infeasible_but_survived == ()
