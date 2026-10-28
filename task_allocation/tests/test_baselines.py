# -*- coding: utf-8 -*-
"""构造式基线（random / greedy / vrptw）与求解器注入通道的常驻回归测试。

锁定的契约（exp05_baselines 的公平性前提）：

1. **SolverResult 口径**：三个基线均继承 BaseOptimizer、返回完整
   SolverResult，``extra["solution"]`` 挂权威 AllocationSolution；
2. **构造期硬可行**：交付解航次闭合代价 ≤ 剩余航程（与 _repair_range
   同口径），一任务一机，执行集 ⊆ 候选集；
3. **VRPTW 硬时间窗**：赶不到 latest 的任务不排班（与 greedy 的
   「迟到不是废飞」口径形成对照差异）；
4. **注入通道**：``run_selective_muas(solver=...)`` 走预构建解通道，
   ``solver=None`` 时 DMDE 主算法行为逐位不变。
"""

from __future__ import annotations

import math

import numpy as np

from task_allocation.baselines import (
    GreedySolver,
    RandomAssignSolver,
    VRPTWInsertionSolver,
)
from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.base import BaseOptimizer
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASAssignmentEvaluator,
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000
RANGE = 80_000.0


def _task(tid: int, x: float, y: float, *, latest: float | None = 1.0e9) -> Task:
    return Task(
        id=tid, x=x, y=y, z=0.0, reward=10.0,
        earliest=0.0, latest=latest, arrival_time=0.0,
    )


def _uav(uid: int, remaining_range: float = RANGE) -> UAV:
    return UAV(
        id=uid, x=0.0, y=0.0, z=0.0, remaining_range=remaining_range,
        speed=10.0, nest_id=0,
    )


def _pairwise(tasks, uavs, nests):
    pts = {t.id: (t.x, t.y, t.z) for t in tasks}
    pts.update({-(1 + u.id): (u.x, u.y, u.z) for u in uavs})
    pts.update({NEST_KEY + n.id: (n.x, n.y, n.z) for n in nests})
    keys = list(pts)
    out = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            out[(a, b)] = math.dist(pts[a], pts[b])
    return out


def _evaluator(tasks, uavs, nests) -> MUASAssignmentEvaluator:
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(),
    )
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=list(uavs),
        tasks=list(tasks),
        nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(),
    )


def _fixture():
    """巢0 在原点；3 个任务在 3~10 km 内；2 架机满电 80 km。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0), _uav(1)]
    tasks = [_task(1, 3000.0, 0.0), _task(2, 6000.0, 2000.0), _task(3, 10000.0, 0.0)]
    return tasks, uavs, nests


def _assert_hard_sane(ctx_evaluator, solution):
    """交付解的结构与航程硬不变量（三个基线共用）。"""
    assert solution.validate_unique_tasks()
    executed = set(solution.executed_task_ids)
    assert executed <= {t.id for t in ctx_evaluator.tasks}
    uav_by_id = {u.id: u for u in ctx_evaluator.participating}
    problem = ctx_evaluator.problem
    for tour in solution.tours:
        assert tour.task_ids, "空航次不应出现在交付解中"
        assert tour.uav_id in uav_by_id
        # 构造期航程门 ⇒ 闭合航次 ≤ 剩余航程（与 _repair_range 同口径）
        cost = problem.tour_path_cost(tour)
        assert cost <= uav_by_id[tour.uav_id].remaining_range + 1e-6, (
            f"航次 {tour.uav_id} 超航程：{cost:.1f} > {uav_by_id[tour.uav_id].remaining_range}"
        )


# ---------------------------------------------------------------------------
# 1) SolverResult 口径
# ---------------------------------------------------------------------------


def test_solver_result_contract():
    tasks, uavs, nests = _fixture()
    for solver in (RandomAssignSolver(seed=7), GreedySolver(), VRPTWInsertionSolver()):
        assert isinstance(solver, BaseOptimizer)
        ev = _evaluator(tasks, uavs, nests)
        res = solver.solve(np.zeros((3, 3)), 2, 3, fitness_evaluator=ev)
        assert res.solver_name == solver.name
        assert res.cost_history == [] and res.total_generations == 0
        assert res.elapsed_seconds >= 0.0
        sol = res.extra["solution"]
        assert sol.executed_task_ids, f"{solver.name} 简单实例应至少排出一个任务"
        _assert_hard_sane(ev, sol)


# ---------------------------------------------------------------------------
# 2) 构造期硬可行 / 一任务一机
# ---------------------------------------------------------------------------


def test_greedy_constructs_feasible_tours():
    tasks, uavs, nests = _fixture()
    ev = _evaluator(tasks, uavs, nests)
    res = GreedySolver().solve(np.zeros((3, 3)), 2, 3, fitness_evaluator=ev)
    sol = res.extra["solution"]
    _assert_hard_sane(ev, sol)
    # 简单实例（全部任务近距离）贪心应全排上
    assert set(sol.executed_task_ids) == {1, 2, 3}


def test_random_respects_one_task_one_uav():
    tasks, uavs, nests = _fixture()
    for seed in (1, 2, 3):
        ev = _evaluator(tasks, uavs, nests)
        res = RandomAssignSolver(seed=seed).solve(
            np.zeros((3, 3)), 2, 3, fitness_evaluator=ev
        )
        _assert_hard_sane(ev, res.extra["solution"])


# ---------------------------------------------------------------------------
# 3) VRPTW 硬时间窗：赶不到 latest 的任务不排班
# ---------------------------------------------------------------------------


def test_vrptw_refuses_impossible_deadline():
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0)]
    # t1 距巢 30 km、speed=10 → 到达 ≥ 3000 s；latest=500 s 航程可行但必然迟到
    tasks = [_task(1, 30_000.0, 0.0, latest=500.0), _task(2, 3000.0, 0.0)]
    ev = _evaluator(tasks, uavs, nests)
    res = VRPTWInsertionSolver().solve(np.zeros((3, 3)), 1, 2, fitness_evaluator=ev)
    sol = res.extra["solution"]
    _assert_hard_sane(ev, sol)
    assert 1 not in set(sol.executed_task_ids), "硬时间窗下赶不到的任务必须不排班"
    assert 2 in set(sol.executed_task_ids)

    # 对照：greedy 口径「迟到不是废飞」，同一任务会被排出（迟到交付）
    ev2 = _evaluator(tasks, uavs, nests)
    sol2 = GreedySolver().solve(np.zeros((3, 3)), 1, 2, fitness_evaluator=ev2).extra["solution"]
    assert 1 in set(sol2.executed_task_ids)


# ---------------------------------------------------------------------------
# 4) run_selective_muas 注入通道
# ---------------------------------------------------------------------------


def _stage_inputs(tasks, uavs, nests):
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, nest_offset=NEST_KEY, store_details=False
    )
    return cb


def test_injected_solver_prebuilt_channel():
    tasks, uavs, nests = _fixture()
    cb = _stage_inputs(tasks, uavs, nests)
    stage = run_selective_muas(
        tasks, uavs, nests, cb,
        solver=GreedySolver(),
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
    )
    sol = stage.solution
    _assert_hard_sane(_evaluator(tasks, uavs, nests), sol)
    # 预构建解通道：诊断字段齐全（与 DMDE 通道同口径）
    for key in ("final_fitness", "final_path_m", "final_n_hard", "fitness_weights"):
        assert key in stage.diagnostics
    assert stage.solver_extra.get("solution") is sol
    assert stage.cost_history == []


def test_default_solver_channel_unchanged():
    """solver=None 走 DMDE 主算法通道（不回归：能出解、诊断齐全）。"""
    tasks, uavs, nests = _fixture()
    cb = _stage_inputs(tasks, uavs, nests)
    stage = run_selective_muas(
        tasks, uavs, nests, cb,
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=8, max_generations=5, seed=0),
    )
    assert stage.solution.validate_unique_tasks()
    assert "final_fitness" in stage.diagnostics
    assert stage.solver_extra.get("solution") is None
