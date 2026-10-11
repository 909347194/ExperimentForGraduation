# -*- coding: utf-8 -*-
"""约束开关单元测试：每类约束都应能在实验配置层独立开/关。

锁定四点：
    1. 缺省 = 全部启用（历史行为不变）；
    2. ``time_window=False`` → 时间窗违约既不判不可行、也不计入惩罚；
    3. ``end_nest_reachability=False`` → 末任务→机巢不可达不判违约（开启时判违约）；
    4. ``range=False`` → 航程违约不判违约（其余约束不受影响）。

对应论文 §5(4) 约束处理与实验口径取舍：关闭某约束即声明"本模型不纳入该项"。
"""

from __future__ import annotations

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
from task_allocation.methods.muas.problem import (
    ConstraintConfig,
    MUASProblemConfig,
    SelectiveMUASProblem,
    build_cost_provider,
)

NEST_OFFSET = 1_000_000


def _tour_and_solution(end_nest_id: int = 0) -> AllocationSolution:
    tour = UAVTour(
        uav_id=0,
        task_ids=[0],
        end_nest_id=end_nest_id,
        start_x=0.0,
        start_y=0.0,
        start_z=0.0,
    )
    return AllocationSolution(
        tours=[tour], candidate_task_ids=[0], executed_task_ids=[0]
    )


def _window_problem(cons: ConstraintConfig) -> SelectiveMUASProblem:
    """任务最迟时刻 10s，但 500m / 10m/s = 50s 才到 → 必违约 40s。"""
    t = Task(id=0, x=0.0, y=500.0, z=0.0, latest=10.0, reward=5.0)
    u = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=1e6, speed=10.0, nest_id=0)
    n = Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)
    return SelectiveMUASProblem.from_entities(
        [t], [u], [n],
        config=MUASProblemConfig(constraints=cons),
    )


def _unreachable_problem(cons: ConstraintConfig) -> SelectiveMUASProblem:
    """末任务 → 机巢代价注入 inf（地形遮挡 / 禁飞），航程关掉以隔离。"""
    t = Task(id=0, x=0.0, y=100.0, z=0.0, reward=5.0)
    u = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=1e9, speed=10.0, nest_id=0)
    n = Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)
    return SelectiveMUASProblem(
        tasks=[t],
        uavs=[u],
        nests=[n],
        costs=build_cost_provider(
            [t], [n], [u],
            pairwise={(0, NEST_OFFSET + 0): float("inf")},
            nest_offset=NEST_OFFSET,
        ),
        fleet_uavs=[u],
        config=MUASProblemConfig(constraints=cons),
    )


def test_defaults_all_enabled():
    c = ConstraintConfig()
    for name in (
        "duplicate_task", "require_all_tasks", "require_no_idle",
        "range", "endurance", "time_window", "sequence", "sync",
        "nest_capacity", "nest_availability", "end_nest_reachability",
    ):
        assert getattr(c, name) is True, f"{name} 缺省应为启用"


def test_time_window_switch():
    # 开启 → 判违约、不可行
    p = _window_problem(ConstraintConfig(time_window=True, endurance=False))
    ok, det = p.check_feasible(_tour_and_solution())
    assert ok is False and det["violations"] == ["tw_task_0"]

    # 关闭 → 不判违约、可行
    p = _window_problem(ConstraintConfig(time_window=False, endurance=False))
    ok, det = p.check_feasible(_tour_and_solution())
    assert ok is True and det["violations"] == []


def test_end_nest_reachability_switch():
    # 开启 → 末任务→机巢不可达判违约
    p = _unreachable_problem(
        ConstraintConfig(range=False, endurance=False, time_window=False,
                         end_nest_reachability=True)
    )
    ok, det = p.check_feasible(_tour_and_solution())
    assert ok is False and det["violations"] == ["end_nest_unreachable_0"]

    # 关闭 → 不判违约
    p = _unreachable_problem(
        ConstraintConfig(range=False, endurance=False, time_window=False,
                         end_nest_reachability=False)
    )
    ok, det = p.check_feasible(_tour_and_solution())
    assert ok is True and det["violations"] == []


def test_range_switch():
    # 航程 100m 但路径 150m → 开启时违约
    t = Task(id=0, x=0.0, y=150.0, z=0.0, reward=5.0)
    u = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=100.0, speed=10.0, nest_id=0)
    n = Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)

    p = SelectiveMUASProblem.from_entities(
        [t], [u], [n],
        config=MUASProblemConfig(constraints=ConstraintConfig(
            range=True, endurance=False, time_window=False,
        )),
    )
    ok, det = p.check_feasible(_tour_and_solution())
    assert ok is False and det["violations"] == ["range_uav_0"]

    p = SelectiveMUASProblem.from_entities(
        [t], [u], [n],
        config=MUASProblemConfig(constraints=ConstraintConfig(
            range=False, endurance=False, time_window=False,
        )),
    )
    ok, det = p.check_feasible(_tour_and_solution())
    assert ok is True and det["violations"] == []
