# -*- coding: utf-8 -*-
"""适配器级联调：``RollingHorizonAdapter`` 的四段式与 ``LoopDriver`` 的回灌链咬合。

单测之间的空档在这里补上：``test_cycle_stages`` 只验上层三段式本身，
``test_in_cycle_resolve`` 只用假端口验环逻辑。本文件用**真的 RollingHorizon**
（合成实体、无 DEM）串起"下层报事实 → 翻译成上层键 → 写进代价 → 同快照重解 → 只提交一次"。

钉住两个最容易错位的点：

1. 角色翻译。exp01 里机巢 ``0/1/2`` 与任务 ``1..30`` **撞号**，靠"id 在不在机巢集"
   猜角色会把内部段 ``1 → 5`` 翻成 ``机巢1 → 任务5``——写到一条不存在的边上，静默失效。
   角色只能由航次结构给出（``CostUpdate.nest_ends`` 标注返航边）。
2. 寿命。事实只活在本周期，``commit`` 后必须清空，不留跨周期缓存。
"""

from __future__ import annotations

import pytest

from orchestration import LoopDriver
from orchestration.adapters.allocator_side import RollingHorizonAdapter
from orchestration.protocol import CostUpdate, PlannerReport, TourTicket
from path_planning.common.plan_types import LegCostRecord
from task_allocation.common.types import Nest, Task, UAV, UAVStatus
from task_allocation.methods.muas.problem import MUASProblemConfig
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig
from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

NEST_OFFSET = 1_000_000


def _horizon(n_tasks: int = 6, n_uavs: int = 3, n_nests: int = 3):
    nests = [Nest(id=i, x=float(i * 5000.0), y=0.0, z=0.0, capacity=4) for i in range(n_nests)]
    uavs = [
        UAV(id=i, x=nests[i % n_nests].x, y=0.0, z=0.0, remaining_range=120000.0,
            speed=15.0, nest_id=nests[i % n_nests].id, status=UAVStatus.IDLE)
        for i in range(n_uavs)
    ]
    # 任务 id 从 1 起：与机巢 1/2 **故意撞号**，用来暴露"按集合猜角色"的错误
    tasks = [
        Task(id=i + 1, x=2500.0 + 800.0 * i, y=0.0, z=0.0, reward=5.0,
             arrival_time=0.0, earliest=0.0, latest=100000.0)
        for i in range(n_tasks)
    ]
    horizon = RollingHorizon(
        TaskPool(current_time=0.0), UAVFleet(uavs, current_time=0.0), nests,
        dem=None, estimator=None,
        horizon_config=HorizonConfig(
            n_cycles=1, cycle_length=600.0, complete_after_cycle=True,
            recharge_at_nest=True, uav_max_range=120000.0,
        ),
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(prune_unprofitable=True, lamarckian=True),
        solver_config=DMDEConfig(pop_size=20, max_generations=40, seed=42),
        nest_offset=NEST_OFFSET,
    )
    return horizon, tasks


class _ScriptedPlanner:
    """第 1 次规划让最小机号伤在返航边上，第 2 次全部成功。"""

    def __init__(self, code: str = "RRT_NO_PATH", severity: str = "infeasible") -> None:
        self.code = code
        self.severity = severity
        self.calls = 0
        self.reset_calls: list[tuple[int, ...]] = []

    def plan_cycle(self, orders) -> PlannerReport:
        bad = self.calls == 0
        tickets = {}
        for o in orders:
            uid = int(o.uav_id)
            end_nest = int(o.stops[-1].id)
            if bad and uid == min(int(x.uav_id) for x in orders):
                # 断在返航边：起飞→首任务这段已经飞过，有实测长度；返航那段几何不可飞
                tickets[uid] = TourTicket(
                    uav_id=uid, ok=False, task_ids=o.task_ids, t0=float(o.t0),
                    time_source="failed", failure=self.code,
                    failure_severity=self.severity,
                    end_nest_id=end_nest,
                    leg_costs=(
                        LegCostRecord(
                            from_id=-(1 + uid), to_id=int(o.task_ids[0]),
                            length_m=2500.0, duration_s=170.0),
                        LegCostRecord(
                            from_id=int(o.task_ids[-1]), to_id=end_nest,
                            failure_code=self.code, severity=self.severity),
                    ),
                )
            else:
                tickets[uid] = TourTicket(
                    uav_id=uid, ok=True, task_ids=o.task_ids, t0=float(o.t0),
                    t_end=float(o.t0) + 300.0, duration_s=300.0, path_len_m=6000.0,
                    time_source="planned",
                    end_nest_id=end_nest,
                    leg_costs=(LegCostRecord(
                        from_id=int(o.task_ids[-1]), to_id=end_nest,
                        length_m=6000.0, duration_s=300.0),),
                )
        self.calls += 1
        return PlannerReport(
            tickets=tickets, unplanned=tuple(u for u, t in tickets.items() if not t.ok))

    def busy_until(self, uav_id: int) -> float:
        return 0.0

    def prune(self, now: float) -> tuple[int, ...]:
        return ()

    def reset_cycle(self, uav_ids) -> tuple[int, ...]:
        self.reset_calls.append(tuple(int(u) for u in uav_ids))
        return tuple(int(u) for u in uav_ids)


def test_edge_roles_decide_translation_not_id_membership() -> None:
    """返航边加 ``nest_offset``；内部段即便端点与机巢撞号也保持任务 id。"""
    horizon, tasks = _horizon()
    adapter = RollingHorizonAdapter(horizon)
    adapter.prepare(tasks)

    applied = adapter.apply_cost_update(CostUpdate(
        facts={(1, 5): 300.0},                       # (任务1→任务5) 的实测长度
        infeasible=frozenset({(5, 0)}),              # (任务5→机巢0) 几何不可飞
        nest_ends=frozenset({(5, 0)}),               # 只有后者是返航边
    ))
    assert applied is not None
    assert set(applied.facts) == {(1, 5)}, "内部段保持任务 id"
    assert applied.infeasible == frozenset({(5, NEST_OFFSET)}), "返航边翻成机巢键"
    assert horizon.cost_state()["n_infeasible"] == 1


def test_infeasible_edge_from_planner_flows_through_loop() -> None:
    horizon, tasks = _horizon()
    adapter = RollingHorizonAdapter(horizon)
    planner = _ScriptedPlanner()
    plan = LoopDriver(adapter, planner, prune_at_cycle_end=False,
                      max_in_cycle=1).step_one(tasks)

    assert plan.n_resolves == 1
    assert plan.cost_update is not None and len(plan.cost_update.infeasible) == 1
    # 台账一律下层 id：返航边就是（末任务, 机巢编号），不加 nest_offset——
    # 翻译只发生在适配器内部（见上一个用例），否则环里 pending ∩ leftover 会不同键空间。
    edge = next(iter(plan.cost_update.infeasible))
    assert edge[0] >= 1 and edge[1] < NEST_OFFSET, f"台账应记下层 id：{edge}"
    assert plan.cost_update.nest_ends == frozenset({edge}), "返航边必须被标注出来"
    assert plan.cost_version == "cycle:1"
    # 本场景上层只派了一台机，且它正是失败的那台 → 没有已发布航迹可撤
    assert planner.reset_calls == [()], planner.reset_calls
    assert plan.n_intermediate_failures == 1, "首版失败、终版成功 = 一条中间版失败"
    assert plan.infeasible_but_survived == ()
    assert plan.resolve_error == ""
    assert len(horizon.records) == 1, "任务池只能被写一次"


def test_measured_legs_are_written_as_costs_in_the_same_cycle() -> None:
    """已飞航段的实测长度必须以数值进代价表，且台账只记本周期这一批。"""
    horizon, tasks = _horizon()
    plan = LoopDriver(RollingHorizonAdapter(horizon), _ScriptedPlanner(),
                      prune_at_cycle_end=False, max_in_cycle=1).step_one(tasks)

    assert plan.cost_update is not None
    assert plan.cost_update.facts, "有已飞航段就必须有实测长度"
    for edge, length_m in plan.cost_update.facts.items():
        assert length_m > 0.0, f"实测长度必须是正数：{edge} → {length_m}"
        assert edge[1] < NEST_OFFSET, "台账保留下层 id"


def test_facts_are_cycle_scoped() -> None:
    """commit 之后事实必须清空：本周期量到的长度不许带到下一周期。"""
    horizon, tasks = _horizon()
    adapter = RollingHorizonAdapter(horizon)
    LoopDriver(adapter, _ScriptedPlanner(), prune_at_cycle_end=False,
               max_in_cycle=1).step_one(tasks)

    assert adapter.cost_state() == {"version": "base", "n_facts": 0, "n_infeasible": 0}


@pytest.mark.parametrize("code,severity", [
    ("RANGE_EXCEEDED", "infeasible"),   # 整条航次预算判定，写不可飞会误杀可行 OD
    ("TIME_NOT_OPTIMAL", "degraded"),   # 降级，不是几何事实
])
def test_non_geometric_failure_stays_in_pool_without_resolve(code, severity) -> None:
    """非几何失败不许写成不可飞：不开重解，任务按回池处理。"""
    horizon, tasks = _horizon()
    plan = LoopDriver(RollingHorizonAdapter(horizon),
                      _ScriptedPlanner(code=code, severity=severity),
                      prune_at_cycle_end=False, max_in_cycle=1).step_one(tasks)

    assert plan.n_resolves == 0 and plan.cost_update is None
    assert plan.rolled_back, "失败航次承载的任务必须进回池清单"
