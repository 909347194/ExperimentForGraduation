# -*- coding: utf-8 -*-
"""random_assign.py — 随机分配基线（性能下界对照）。

在**构造期硬可行**（航程，一任务一机）的前提下随机生成分配方案：

    - 任务顺序随机；每任务随机尝试（UAV, 插入位），航程可行即落位；
    - 多次尝试不可行则本周期暂缓（y_j = 0），不做解码端修复；
    - 终点机巢走 :meth:`ConstructionContext.finalize` 的贪心选择。

这是对照表中的下界方法：它与 greedy / vrptw 共享同一滚动外壳、同一
候选集与同一评价口径（``SelectiveMUASProblem.check_feasible``），
差别只在「怎么把任务串成航次」。无迭代：``cost_history=[]``、
``total_generations=0``。

约定：
    - 类名 ``RandomAssignSolver``，name == "random"
    - 继承 BaseOptimizer，返回 SolverResult（``extra["solution"]`` 为权威解）
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from task_allocation.baselines.construct import ConstructionContext, build_solver_result
from task_allocation.methods.muas.base import BaseOptimizer, SolverResult

__all__ = ["RandomAssignSolver"]


class RandomAssignSolver(BaseOptimizer):
    """随机可行分配（构造期航程门 + 随机落位）。

    Args:
        seed: 随机种子；None 时用熵种子（不同周期结果不同，不可复现）。
        max_tries: 每任务最多尝试的随机落位次数，全部失败则暂缓。
    """

    def __init__(self, seed: int | None = None, max_tries: int = 16) -> None:
        self._seed = seed
        self._max_tries = max(1, int(max_tries))

    @property
    def name(self) -> str:
        return "random"

    def solve(
        self,
        cost_matrix: Any,
        n_uavs: int,
        n_targets: int,
        **kwargs: Any,
    ) -> SolverResult:
        evaluator = kwargs.get("fitness_evaluator")
        if evaluator is None:
            raise ValueError("fitness_evaluator must be provided.")
        t0 = time.time()
        ctx = ConstructionContext(evaluator)
        rng = np.random.default_rng(self._seed)

        routes: dict[int, list[int]] = {u.id: [] for u in ctx.uavs}
        uav_ids = [u.id for u in ctx.uavs]
        order = [t.id for t in ctx.tasks]
        rng.shuffle(order)

        for tid in order:
            if not uav_ids:
                break
            # 航程可行即落位；多次尝试全部不可行 → 本周期暂缓（y_j = 0）
            for _ in range(self._max_tries):
                uid = int(rng.choice(uav_ids))
                uav = ctx.uav_by_id[uid]
                seq = routes[uid]
                pos = int(rng.integers(0, len(seq) + 1))
                trial = seq[:pos] + [tid] + seq[pos:]
                if ctx.range_ok(uav, trial):
                    routes[uid] = trial
                    break

        solution = ctx.finalize(routes)
        return build_solver_result(ctx, solution, self.name, time.time() - t0)
