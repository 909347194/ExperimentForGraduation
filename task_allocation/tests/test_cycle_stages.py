# -*- coding: utf-8 -*-
"""周期三段式（P1）的聚焦测试：``prepare`` 冻结 / ``solve`` 可重入 / ``commit`` 只写一次。

钉死的纪律：
  1. ``resolve`` 不推进时间（``t_end`` 冻结在同一快照上）；
  2. 重解期间任务池/机队保持原样，只有 ``commit`` 落状态；
  3. 回灌只有"写数值"一条路：不可飞边写成 :data:`INFEASIBLE_COST`，判死交给
     任务分配内部的航程检查，不另设可行性闸门。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from task_allocation.common.types import (
    Nest,
    Task,
    TaskStatus,
    UAV,
    UAVStatus,
)
from task_allocation.methods.muas.cost.fact_cost_view import INFEASIBLE_COST
from task_allocation.methods.muas.problem import MUASProblemConfig
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig
from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet


_NEST_OFFSET = 1_000_000


def _make_horizon(n_tasks: int = 3, n_uavs: int = 2) -> tuple[RollingHorizon, list[Task]]:
    """合成实体搭最小可用滚动层（不碰 CSV/DEM/RRT）。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=8, available=True)]
    uavs = [
        UAV(id=i, x=0.0, y=float(i * 10.0), z=0.0, remaining_range=120000.0,
            speed=15.0, nest_id=0, status=UAVStatus.IDLE)
        for i in range(n_uavs)
    ]
    tasks = [
        Task(id=i, x=800.0 + 300.0 * i, y=200.0, z=0.0, reward=5.0,
             arrival_time=0.0, earliest=0.0, latest=100000.0)
        for i in range(n_tasks)
    ]
    horizon = RollingHorizon(
        TaskPool(current_time=0.0),
        UAVFleet(uavs, current_time=0.0),
        nests, dem=None, estimator=None,
        horizon_config=HorizonConfig(
            n_cycles=1, cycle_length=600.0, complete_after_cycle=True,
            recharge_at_nest=True, uav_max_range=120000.0, event_replan=False,
        ),
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(prune_unprofitable=True, lamarckian=True),
        solver_config=DMDEConfig(pop_size=16, max_generations=30, seed=7),
        nest_offset=_NEST_OFFSET,
    )
    return horizon, tasks


def _statuses(horizon: RollingHorizon) -> dict[int, TaskStatus]:
    return {t.id: t.status for t in horizon.pool.active_tasks()}


def _update(**kw) -> SimpleNamespace:
    base = {"facts": {}, "infeasible": frozenset(), "source_attempt": 1, "reason": "facts"}
    base.update(kw)
    return SimpleNamespace(**base)


def test_begin_cycle_does_not_write_pool_or_records() -> None:
    horizon, tasks = _make_horizon()
    record = horizon.begin_cycle(tasks)

    assert horizon._time == pytest.approx(600.0), "prepare 推进一拍（每周期一次）"
    assert horizon.records == [], "预览不得进入周期账"
    assert all(s is TaskStatus.PENDING for s in _statuses(horizon).values())
    assert all(u.status is UAVStatus.IDLE for u in horizon.fleet.all())
    assert record.n_resolves == 0 and record.cost_version == "base"
    assert record.tours and record.executed_ids, "预览应该已含一版解"


def test_resolve_keeps_time_and_pool_frozen() -> None:
    horizon, tasks = _make_horizon()
    record = horizon.begin_cycle(tasks)
    prep = horizon._prepared
    t_end, snapshot_id = prep.t_end, prep.snapshot_id
    first_tours = list(record.tours)

    again = horizon.resolve_with(None)

    assert horizon._time == pytest.approx(t_end), "重解不得推进时间"
    assert prep.t_end == pytest.approx(t_end) and prep.snapshot_id == snapshot_id
    assert again is record, "预览记录是同一份（只刷新版本字段）"
    assert record.n_resolves == 1
    assert horizon.records == []
    assert all(s is TaskStatus.PENDING for s in _statuses(horizon).values())
    assert again.tours and len(first_tours) == len(again.tours)


def test_commit_writes_once_and_clears_cycle_layer() -> None:
    horizon, tasks = _make_horizon()
    horizon.begin_cycle(tasks)
    horizon.resolve_with(_update(infeasible={(0, 1)}))
    committed = horizon.commit()

    assert horizon.records == [committed]
    assert horizon._prepared is None and horizon._solved is None
    assert committed.n_resolves == 1
    assert committed.cost_version == "cycle:1"
    assert committed.completed_ids, "complete_after_cycle=True 应判完成"
    assert all(
        s in (TaskStatus.DONE, TaskStatus.EXECUTING, TaskStatus.UNREACHABLE)
        for s in _statuses(horizon).values()
    )
    # 周期作用域的事实层已清；代价版本回到 base
    assert horizon._cycle_infeasible == set()
    assert horizon.cost_state() == {"version": "base", "n_facts": 0, "n_infeasible": 0}


def test_resolve_requires_begin_and_commit_requires_begin() -> None:
    horizon, _ = _make_horizon()
    with pytest.raises(RuntimeError):
        horizon.resolve_with(None)
    with pytest.raises(RuntimeError):
        horizon.commit()


def test_infeasible_edge_costs_more_than_any_tour_can_fly() -> None:
    """``0 → 机巢`` 写进极大代价 ⇒ 含该边的航次在内部航程检查里必然超限。

    上层不另设可行性闸门，判死由 ``muas`` 自己的 ``range`` 检查完成：任务 0 只能
    留到下期（``deferred_ids``），且这条边确实以数值进了代价视图。
    """
    horizon, tasks = _make_horizon()
    horizon.begin_cycle(tasks)
    nest_key = horizon.nest_offset + 0
    view = horizon.build_cost_view(tasks, horizon.fleet.all())
    base_cost = float(view[(0, nest_key)])

    horizon.resolve_with(_update(infeasible={(0, nest_key)}))
    view = horizon.build_cost_view(tasks, horizon.fleet.all())
    assert float(view[(0, nest_key)]) == pytest.approx(INFEASIBLE_COST)
    assert float(view[(0, nest_key)]) > base_cost
    record = horizon.commit()

    assert 0 not in record.executed_ids, "含极大代价边的航次不得执行"
    assert 0 in record.deferred_ids, "被拒任务必须原样留着下周期"
