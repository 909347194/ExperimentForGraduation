# -*- coding: utf-8 -*-
"""机制探针：验证「进化贡献 0%」是结构性（lamarckian 修复已逐代优化个体）
还是 bug。

设计：同实例（40:2, d0, inst=6000, seed=11）跑 4 种配置，对比 path_cost：
  - repair_only      gens=0,  refine=off, lamarckian=True   (地板 C)
  - evo_lam_True     gens=80, refine=off, lamarckian=True    (当前 A)
  - evo_lam_False    gens=80, refine=off, lamarckian=False   (探针 A')
  - full             gens=80, refine=on,  lamarckian=True    (当前 B)

若 evo_lam_False << C  => lamarckian 把进化压制成了 0（修复逐代写回抹平了
搜索可探索的空间）；若 evo_lam_False ≈ C  => 进化本身在该表示/景观下确实榨
不出改进（修复+初始已近局部最优）。
"""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.problem import MUASProblemConfig, SelectiveMUASProblem  # noqa: E402
from task_allocation.methods.muas.solvers.stage_runner import (  # noqa: E402
    DMDEConfig, MUASStageConfig, run_selective_muas,
)
from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon  # noqa: E402
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig  # noqa: E402
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


def make_instance(seed, n_uavs, n_targets, area, deadline_s=0.0):
    rng = np.random.default_rng(seed)
    tasks = [
        Task(id=i, x=float(rng.uniform(0, area)), y=float(rng.uniform(0, area)), z=0.0,
             reward=5.0, earliest=0.0, latest=(1e6 if deadline_s <= 0 else deadline_s),
             arrival_time=0.0)
        for i in range(n_targets)
    ]
    uavs = [
        UAV(id=j, x=float(rng.uniform(0, area)), y=float(rng.uniform(0, area)), z=0.0,
            remaining_range=1e7, speed=10.0, max_time=1e7 / 10.0, nest_id=0,
            status=UAVStatus.IDLE)
        for j in range(n_uavs)
    ]
    nests = [Nest(id=0, x=area / 2.0, y=area / 2.0, z=0.0, capacity=10, available=True)]
    return tasks, uavs, nests


def run(tasks, uavs, nests, *, gens, refine, lamarckian, solver_seed, n_cycles, deadline_s):
    prob_cfg = MUASProblemConfig()
    if deadline_s > 0:
        prob_cfg = MUASProblemConfig(w_violation=0.01, late_reward_decay_seconds=300.0)
    tasks = copy.deepcopy(list(tasks))
    uavs = copy.deepcopy(list(uavs))
    nests = copy.deepcopy(list(nests))
    from task_allocation.methods.selection.pipeline import SelectionConfig
    from task_allocation.methods.selection.marginal_gain import MarginalConfig
    from task_allocation.methods.selection.capability import CapabilityScaleConfig
    sel_cfg = SelectionConfig(marginal=MarginalConfig(max_selected=len(tasks)),
                              capability=CapabilityScaleConfig(enabled=False))
    stage_cfg = MUASStageConfig(
        lamarckian=lamarckian,
        cost_polish_enabled=refine,
        cost_polish_relocate_enabled=refine,
        polish_enabled=refine,
        polish_in_loop=False,
    )
    solver_cfg = DMDEConfig(pop_size=20, max_generations=gens, seed=solver_seed,
                            verbose=False)
    max_range = max((float(u.remaining_range) for u in uavs), default=1e7) or 1e7
    horizon = RollingHorizon(
        TaskPool(current_time=0.0), UAVFleet(list(uavs), current_time=0.0),
        list(nests), dem=None, estimator=None,
        horizon_config=HorizonConfig(n_cycles=n_cycles, cycle_length=600.0,
                                     complete_after_cycle=True, recharge_at_nest=True,
                                     uav_max_range=max_range),
        problem_config=prob_cfg, stage_config=stage_cfg, solver_config=solver_cfg,
        selection_config=sel_cfg, event_config=EventTriggerConfig(),
        nest_offset=1_000_000,
    )
    rng = np.random.default_rng(6000)
    order = list(range(len(tasks)))
    rng.shuffle(order)
    per = [order[c::n_cycles] for c in range(n_cycles)]
    arrivals = {c + 1: [tasks[i] for i in per[c]] for c in range(n_cycles)}
    t0 = time.time()
    records = horizon.run(n_cycles=n_cycles, arrivals=arrivals)
    elapsed = time.time() - t0
    path_cost = 0.0
    late = 0.0
    n_hard = 0
    completed = set()
    for rec_ in records:
        completed.update(int(t) for t in rec_.completed_ids)
        late += float(sum(rec_.lateness_by_task.values()))
        n_hard += len(rec_.infeasible_ids)
        path_cost += float(sum(t.get("est_cost", 0.0) for t in rec_.tours))
    total = len(tasks)
    return dict(path_cost=path_cost, late=late, n_hard=n_hard,
                coverage=len(completed) / max(1, total), elapsed_s=elapsed)


def main():
    n_targets, n_cycles, area, deadline_s = 40, 2, 1000.0, 0.0
    tasks, uavs, nests = make_instance(6000, 3, n_targets, area, deadline_s)
    configs = [
        ("repair_only",    dict(gens=0,   refine=False, lamarckian=True)),
        ("evo_lam_True",   dict(gens=80,  refine=False, lamarckian=True)),
        ("evo_lam_False",  dict(gens=80,  refine=False, lamarckian=False)),
        ("full",           dict(gens=80,  refine=True,  lamarckian=True)),
    ]
    print(f"{'config':<14} {'gens':>5} {'lam':>5} {'refine':>7} {'path_cost':>12} "
          f"{'late':>8} {'n_hard':>7} {'cov':>6} {'sec':>8}")
    for label, kw in configs:
        r = run(tasks, uavs, nests, solver_seed=11, n_cycles=n_cycles,
                deadline_s=deadline_s, **kw)
        print(f"{label:<14} {kw['gens']:>5} {str(kw['lamarckian']):>5} "
              f"{str(kw['refine']):>7} {r['path_cost']:>12.3f} {r['late']:>8.2f} "
              f"{r['n_hard']:>7} {r['coverage']:>6.3f} {r['elapsed_s']:>8.2f}")


if __name__ == "__main__":
    main()
