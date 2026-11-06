# -*- coding: utf-8 -*-
"""P2-e 测试：ε 调度实装 Eq.12（Fan 2018）状态机 + 求解器驱动。

契约（定稿 §9 第 2 项 / P2-e）：
1. **Eq.12 状态机**：``ε(0)=ϕ_θ``；``FEs<TC`` 且 ``rk<α`` 收紧、
   ``rk≥α`` 放松到 ``(1+τ)·ϕ_max``、``FEs≥TC`` 归 0；
2. **求解器驱动**：dmde_solver 在 gen0 reset、每代 tick（注入 maxFEs）；
3. **对照档 EconstOR**：epsilon 模式经 stage_runner 端到端可运行、确定性；
4. **默认档不受影响**：penalty 路径仍逐位等价（由他处测试覆盖）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from task_allocation.common.types import Nest, Task, UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.compare_spec import make_compare_spec  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig, DMDESolver  # noqa: E402
from task_allocation.methods.muas.solvers.evaluator import (  # noqa: E402
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000


def _scenario():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=2, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=2, available=True),
    ]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=15000.0, speed=12.0, nest_id=0),
        UAV(id=1, x=4000.0, y=0.0, z=0.0, remaining_range=15000.0, speed=11.0, nest_id=1),
    ]
    tasks = [
        Task(id=0, x=800.0, y=200.0, z=0.0, reward=5.0, earliest=0.0, latest=1500.0),
        Task(id=1, x=1600.0, y=-300.0, z=0.0, reward=4.0, earliest=0.0, latest=2000.0),
        Task(id=2, x=2400.0, y=400.0, z=0.0, reward=6.0, earliest=0.0, latest=1800.0),
        Task(id=3, x=3100.0, y=-200.0, z=0.0, reward=3.0, earliest=0.0, latest=2200.0),
    ]
    return tasks, uavs, nests


def _make_ev():
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests=nests, pairwise_cost=cb.provider.pairwise, config=MUASProblemConfig()
    )
    ev = MUASAssignmentEvaluator(
        problem=problem,
        participating=uavs,
        tasks=tasks,
        nests=nests,
        fleet_uavs=uavs,
        config=MUASStageConfig(),
    )
    return ev, cb, tasks, uavs, nests


def test_solver_drives_epsilon_schedule():
    """求解器在 gen0 reset、每代 tick；收敛后 ε 归 0。"""
    ev, cb, tasks, uavs, nests = _make_ev()
    cm = np.vstack([cb.uav_target, cb.task_task])
    spec = make_compare_spec(
        mode="epsilon",
        epsilon_gamma=0.5, epsilon_tc_frac=0.4, epsilon_cp=5.0,
        epsilon_tau=0.0, epsilon_alpha=0.5,
    )
    solver = DMDESolver(
        config=DMDEConfig(pop_size=10, max_generations=12, seed=1, compare_spec=spec)
    )
    res = solver.solve(cm, 2, 4, fitness_evaluator=ev)
    assert np.isfinite(res.best_fitness)
    # 调度被驱动：gen 1..12 各 tick 一次 ⇒ _eps_gen == 12
    assert spec._eps_gen == 12
    # 收敛：TC = 0.4·12·10 = 48 FEs；gen12 ⇒ fes=120 ≥ TC ⇒ ε=0
    assert spec.epsilon_at() == 0.0


def test_epsilon_arm_end_to_end_deterministic():
    """对照档 EconstOR（epsilon 模式）经 stage_runner 端到端可运行、确定性。"""

    def _run():
        tasks, uavs, nests = _scenario()
        cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
        stage = run_selective_muas(
            tasks, uavs, nests, cb, fleet_uavs=uavs,
            problem_config=MUASProblemConfig(),
            stage_config=MUASStageConfig(compare_mode="epsilon"),
            solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=3),
            nest_offset=NEST_KEY, current_time=0.0,
        )
        sol = stage.solution
        return (
            sorted(sol.executed_task_ids),
            round(float(stage.diagnostics["final_fitness"]), 9),
            [(t.uav_id, tuple(t.task_ids), t.end_nest_id) for t in sol.tours],
        )

    assert _run() == _run()
