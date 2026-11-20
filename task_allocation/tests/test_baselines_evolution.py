# -*- coding: utf-8 -*-
"""演化类文献基线（genetic / pmx_de / sort_de）的常驻回归测试。

锁定的契约（exp12_literature_baselines 的公平性前提）：

1. **SolverResult 口径**：三个求解器均继承 BaseOptimizer、name 与
   registry key 一致、cost_history 非空且 ``cost_history[-1] == best_fitness``；
2. **染色体完整性**：``best_assignment`` 覆盖全部任务恰好一次
   （一任务一机），uav 下标在 ``[0, n_uavs)`` 内；
3. **预算口径**：``extra["n_evals"] == pop_size × (max_generations + 1)``
   ——与 DMDE 的评价预算同量（engine swap 的公平性前提）；
4. **可复现**：同 seed 两次 solve 的 best_assignment 逐位一致；
5. **注入通道**：``run_selective_muas(solver=...)`` 走 assignment 解码
   交付通道（与 DMDE 同链），交付解由 ``problem.evaluate`` 统一记账。
"""

from __future__ import annotations

import math

import numpy as np

from task_allocation.baselines import (
    GeneticSolver,
    PmxDESolver,
    SortDESolver,
    get_baseline,
)
from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.base import BaseOptimizer
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASAssignmentEvaluator,
    MUASStageConfig,
    run_selective_muas,
)
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs

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
    """巢 0 在原点；4 个任务散布；2 架机满电 80 km。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0), _uav(1)]
    tasks = [
        _task(0, 3000.0, 0.0),
        _task(1, 0.0, 5000.0),
        _task(2, -4000.0, 2000.0),
        _task(3, 2000.0, -3000.0),
    ]
    return tasks, uavs, nests


def _cost_matrix(tasks, uavs, nests):
    """DMDE 方案 A 布局的代价矩阵：行 = [UAV 起点 | 任务]，列 = 任务。"""
    pairs = _pairwise(tasks, uavs, nests)
    rows = [-(1 + u.id) for u in uavs] + [t.id for t in tasks]
    mat = np.zeros((len(uavs) + len(tasks), len(tasks)), dtype=float)
    for i, key in enumerate(rows):
        for j, t in enumerate(tasks):
            mat[i, j] = pairs.get((key, t.id), pairs.get((t.id, key), 0.0))
    return mat


SOLVERS = [GeneticSolver, PmxDESolver, SortDESolver]


def test_solver_result_contract():
    """三个求解器返回完整 SolverResult，染色体覆盖全部任务恰好一次。"""
    tasks, uavs, nests = _fixture()
    mat = _cost_matrix(tasks, uavs, nests)
    evaluator = _evaluator(tasks, uavs, nests)
    n_uavs, n_targets = len(uavs), len(tasks)
    for cls in SOLVERS:
        solver = cls(seed=7, pop_size=8, max_generations=3)
        assert isinstance(solver, BaseOptimizer)
        res = solver.solve(
            mat, n_uavs, n_targets, fitness_evaluator=evaluator
        )
        assert res.solver_name == solver.name
        assert res.cost_history, f"{cls.__name__}: cost_history 不得为空"
        assert res.cost_history[-1] == res.best_fitness
        assert res.total_generations == 3
        assert math.isfinite(res.best_fitness)
        # 染色体完整性：任务恰好覆盖一次、uav 下标合法
        tids = [t for _, t in res.best_assignment]
        assert sorted(tids) == list(range(n_targets)), (
            f"{cls.__name__}: 任务覆盖不完整 {sorted(tids)}"
        )
        for u, t in res.best_assignment:
            assert 0 <= u < n_uavs and 0 <= t < n_targets
        # 预算口径
        assert res.extra["n_evals"] == 8 * (3 + 1)


def test_solver_deterministic_with_seed():
    """同 seed 逐位复现；不同 seed 允许不同（不强求，只验不崩）。"""
    tasks, uavs, nests = _fixture()
    mat = _cost_matrix(tasks, uavs, nests)
    for cls in SOLVERS:
        e1 = _evaluator(tasks, uavs, nests)
        e2 = _evaluator(tasks, uavs, nests)
        r1 = cls(seed=11, pop_size=6, max_generations=2).solve(
            mat, len(uavs), len(tasks), fitness_evaluator=e1
        )
        r2 = cls(seed=11, pop_size=6, max_generations=2).solve(
            mat, len(uavs), len(tasks), fitness_evaluator=e2
        )
        assert r1.best_assignment == r2.best_assignment, (
            f"{cls.__name__}: 同 seed 不可复现"
        )
        assert r1.best_fitness == r2.best_fitness


def test_registry_keys_match_names():
    """registry key 与 name 属性一致（配置驱动入口约定）。"""
    for key, cls in [("genetic", GeneticSolver), ("pmx_de", PmxDESolver), ("sort_de", SortDESolver)]:
        assert get_baseline(key) is cls
        assert cls(seed=1, pop_size=4, max_generations=1).name == key


def test_injection_channel_assignment_decode():
    """注入通道：演化基线走 assignment 解码交付通道，交付解被 problem 记账。"""
    tasks, uavs, nests = _fixture()
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, nest_offset=NEST_KEY, store_details=False
    )
    stage = run_selective_muas(
        tasks, uavs, nests, cb,
        solver=GeneticSolver(seed=5, pop_size=6, max_generations=2),
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
    )
    sol = stage.solution
    executed = list(sol.executed_task_ids)
    assert len(executed) == len(set(executed)), "任务重复执行"
    assert set(executed) <= {t.id for t in tasks}
    assert sol.validate_unique_tasks()
    assert stage.cost_history, "搜索轨迹应随求解器透传"
