# -*- coding: utf-8 -*-
"""事件驱动重规划（论文 §6）的聚焦测试。

覆盖两条事件→反应式调整链路：
  A. 紧急任务 / 故障释放任务 → 第一阶段选择强制纳入（绕过 Top-αK 预筛）
  B. 机巢不可用            → 第二阶段终点机巢候选集剔除（强制异巢终止）

直接 `python test_event_replan.py` 运行；全部 assert 通过即 PASS。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent  # 仓库根目录（task_allocation 的父目录）
EXP = ROOT / "experiments" / "exp01_smoke"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain  # noqa: E402
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig  # noqa: E402
from task_allocation.methods.rolling.event_trigger import (  # noqa: E402
    EventTriggerConfig,
    summarize_events,
    should_replan,
)
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    HorizonConfig,
    RollingHorizon,
)
from task_allocation.methods.selection.feasibility import FeasibilityConfig  # noqa: E402
from task_allocation.methods.selection.marginal_gain import MarginalConfig  # noqa: E402
from task_allocation.methods.selection.pipeline import (  # noqa: E402
    SelectionConfig,
    run as run_selection,
)
from task_allocation.methods.selection.priority import (  # noqa: E402
    PriorityConfig,
    PriorityWeights,
)
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


def _build_tasks(n: int, *, reward=10.0, at=(1000.0, 1000.0)) -> list[Task]:
    return [
        Task(id=i, x=at[0], y=at[1], z=0.0, reward=reward, arrival_time=0.0,
             earliest=0.0, latest=100000.0)
        for i in range(n)
    ]


def _build_uavs(n: int) -> list[UAV]:
    return [
        UAV(id=i, x=0.0, y=float(i * 10), z=0.0, remaining_range=1e9,
            speed=10.0, nest_id=None, status=UAVStatus.IDLE)
        for i in range(n)
    ]


def test_selection_forced_inclusion() -> None:
    """紧急 / 故障释放任务绕过 Top-αK 预筛，但不过可行性过滤。"""
    # 12 个任务，11 个高收益(10)，1 个低收益(0.1) → 低收益者优先级最低
    tasks = _build_tasks(11, reward=10.0) + [
        Task(id=99, x=1000.0, y=1000.0, z=0.0, reward=0.1,
             arrival_time=0.0, earliest=0.0, latest=100000.0)
    ]
    uavs = _build_uavs(2)  # K=2, alpha=1.0 → 预筛仅取 2 个
    weights = PriorityWeights(w_r=1.0, w_u=0.0, w_w=0.0, w_s=0.0, w_c=0.0)

    def make_cfg(forced=set()):
        return SelectionConfig(
            priority=PriorityConfig(alpha=1.0, weights=weights),
            feasibility=FeasibilityConfig(),
            marginal=MarginalConfig(),
            current_time=0.0,
            forced_task_ids=forced,
        )

    # 控制组：不强制 → 低收益任务被 Top-αK 预筛排除
    res_off = run_selection(tasks, uavs, config=make_cfg(),
                            cost_hint=None, pairwise_cost=None)
    assert 99 not in res_off.selected_ids, "控制组不应选中低收益任务"

    # 实验组：强制纳入 → 必须出现在 T_t^sel
    res_on = run_selection(tasks, uavs, config=make_cfg(forced={99}),
                           cost_hint=None, pairwise_cost=None)
    assert 99 in res_on.selected_ids, "强制纳入失效：低收益任务未进入选择集"
    assert res_on.diagnostics.get("n_forced_in", 0) >= 1, "未记录强制纳入计数"
    print("[A] selection forced-inclusion: PASS  "
          f"(control={res_off.selected_ids}, forced={res_on.selected_ids})")


def _make_horizon(n_tasks=10, n_uavs=24, n_nests=8, nest_capacity=4, event_replan=True):
    """用合成实体（不依赖 CSV/DEM）搭一个最小可用的 RollingHorizon。"""
    nests = [
        Nest(id=i, x=float(i * 5000.0), y=0.0, z=0.0,
             capacity=nest_capacity, available=True)
        for i in range(n_nests)
    ]
    uavs = [
        UAV(id=i, x=nests[i % n_nests].x, y=nests[i % n_nests].y,
            z=0.0, remaining_range=120000.0, speed=15.0,
            nest_id=nests[i % n_nests].id, status=UAVStatus.IDLE)
        for i in range(n_uavs)
    ]
    tasks = _build_tasks(n_tasks, reward=5.0,
                         at=(2500.0, 0.0))  # 均匀散布在机巢之间
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)

    stage_config = MUASStageConfig(
        prune_unprofitable=True, end_nest_in_encoding=True, lamarckian=True,
    )
    horizon_config = HorizonConfig(
        n_cycles=1, cycle_length=600.0, complete_after_cycle=True,
        recharge_at_nest=True, uav_max_range=120000.0,
        event_replan=event_replan,
    )
    event_config = EventTriggerConfig(
        reward_threshold=8.0, slack_threshold=900.0,
    )
    horizon = RollingHorizon(
        pool, fleet, nests, dem=None, estimator=None,
        horizon_config=horizon_config,
        problem_config=MUASProblemConfig(),
        stage_config=stage_config,
        solver_config=DMDEConfig(pop_size=20, max_generations=40, seed=42),
        event_config=event_config,
        nest_offset=1_000_000,
    )
    return horizon, tasks, nests


def test_urgent_task_replan() -> None:
    """突发高优先级（紧急）任务 → 本期触发重规划并强制纳入选择。"""
    horizon, tasks, _ = _make_horizon()
    # 注入一个紧急任务：reward 低（不靠收益排序），但 latest 贴近当前时刻
    # → slack <= slack_threshold，被 detect_events 判为 URGENT_TASK
    urgent = Task(id=999, x=2500.0, y=0.0, z=0.0, reward=1.0,
                  arrival_time=0.0, earliest=0.0, latest=650.0)  # t=600 时 slack=50
    tasks.append(urgent)
    # 取消超出机队承载的任务，放大「任务≫机」压力，让 Top-αK 真正成为瓶颈
    records = horizon.run(n_cycles=1, arrivals={1: tasks})

    rec = records[0]
    assert rec.replan is True, "紧急任务应触发本期重规划"
    ctx = rec.replan_context
    assert 999 in ctx.get("urgent_task_ids", []), "紧急任务未在事件上下文被识别"
    assert 999 in rec.selection.get("forced_task_ids", []), "紧急任务未进入强制纳入清单"
    in_plan = (999 in rec.executed_ids) or (999 in rec.deferred_ids)
    assert in_plan, "紧急任务未进入本周期计划（执行或保留）"
    print(f"[B] urgent-task replan: PASS  replan={rec.replan} "
          f"urgent={ctx.get('urgent_task_ids')} forced={rec.selection.get('forced_task_ids')}")


def test_urgent_task_no_replan_when_disabled() -> None:
    """event_replan=False 时，事件仅记录、不触发强制纳入。"""
    horizon, tasks, _ = _make_horizon(event_replan=False)
    urgent = Task(id=999, x=2500.0, y=0.0, z=0.0, reward=1.0,
                  arrival_time=0.0, earliest=0.0, latest=650.0)
    tasks.append(urgent)
    records = horizon.run(n_cycles=1, arrivals={1: tasks})
    rec = records[0]
    assert rec.replan is False, "event_replan=False 不应触发重规划"
    assert rec.replan_context.get("replan") is False
    assert 999 not in rec.selection.get("forced_task_ids", []), "关停后不应强制纳入"
    # 事件仍被识别并记入上下文（仅不行动）
    assert 999 in rec.replan_context.get("urgent_task_ids", [])
    print(f"[C] urgent no-replan when disabled: PASS  replan={rec.replan} "
          f"detected_urgent={rec.replan_context.get('urgent_task_ids')}")


def test_unavailable_nest_excluded() -> None:
    """机巢不可用 → 本期触发重规划，且任何航次终点都不落在该机巢。"""
    horizon, tasks, nests = _make_horizon()
    target = nests[0]
    # 构造后、运行前把 0 号机巢置为不可用（init 时 known_unavailable 不含它 → 触发事件）
    target.available = False
    records = horizon.run(n_cycles=1, arrivals={1: tasks})
    rec = records[0]

    assert rec.replan is True, "机巢不可用应触发本期重规划"
    assert target.id in rec.replan_context.get("unavailable_nest_ids", []), \
        "不可用机巢未在事件上下文被识别"
    assert target.id in rec.muas.get("excluded_nest_ids", []), \
        "不可用机巢未传入 MUAS 终点候选剔除"

    # 关键不变量：本周期任何被执行航次的终点都不可能是被排除的机巢
    for tid in rec.executed_ids:
        # 在执行反馈后，该 UAV 的归属机巢（下周期起点）不应是被排除巢
        pass
    # 通过 fleet 当前归属校验：所有 UAV 此刻都不归属于被排除的机巢
    occ = horizon.fleet.nest_occupancy(nests)
    assert occ.get(target.id, 0) == 0, "存在 UAV 仍归属于不可用机巢（未改降）"
    print(f"[D] unavailable-nest excluded: PASS  replan={rec.replan} "
          f"excluded={rec.muas.get('excluded_nest_ids')} "
          f"occ_after={occ.get(target.id, 0)}")


def test_unit_summarize_and_should_replan() -> None:
    """summarize_events / should_replan 解析正确。"""
    from task_allocation.methods.rolling.event_trigger import (
        EventReplanContext,
        EventType,
        ReplanEvent,
    )
    events = [
        ReplanEvent(EventType.URGENT_TASK, {"task_id": 7, "reward": 9.0}),
        ReplanEvent(EventType.NEST_UNAVAILABLE, {"nest_id": 3}),
        ReplanEvent(EventType.UAV_FAULT, {"uav_id": 12}),
    ]
    ctx = summarize_events(events)
    assert ctx.urgent_task_ids == {7}
    assert ctx.unavailable_nest_ids == {3}
    assert ctx.faulted_uav_ids == {12}
    assert ctx.replan is True
    assert should_replan(events) is True
    # enabled=False → 不触发
    cfg_off = EventTriggerConfig(enabled=False)
    assert should_replan(events, cfg_off) is False
    print("[E] summarize_events / should_replan: PASS")


if __name__ == "__main__":
    test_unit_summarize_and_should_replan()
    test_selection_forced_inclusion()
    test_urgent_task_replan()
    test_urgent_task_no_replan_when_disabled()
    test_unavailable_nest_excluded()
    print("\nALL TESTS PASSED")
