# -*- coding: utf-8 -*-
"""sort_de.py — 随机键排序解码 DE 基线（文献代表：Sort-DE / 标准实数 DE）。

文献中「实数 DE 直接求解组合问题」的经典路线：**随机键（random-key）编码 +
排序解码 + 标准 DE/rand/1/bin**。染色体是纯实数向量，组合结构由解码端
（argmin 归属 + 按键排序定序）导出，DE 算子与连续优化教科书逐字相同
——这使它同时充当「标准 DE」的文献对照。本实现与 DMDE 共享同一解码
修复链与交付后处理（engine swap，见 :mod:`task_allocation.baselines.evolution`），
预算同为 pop × gen 次评价。

染色体 ``x ∈ R^(N·K + N)``：

- 归属键 ``A[j·K + k]``：任务 j 归属 ``argmin_k A[j, k]``；
- 顺序键 ``O[j]``：各机任务按 ``O[j]`` 升序定序。

演化算子（每代 pop 次试验，贪婪一对一替换）：

- 变异：``v = x_r1 + F·(x_r2 − x_r3)``（DE/rand/1，r1≠r2≠r3≠i）；
- 交叉：二项交叉（CR，保证至少一位来自 v）；
- 替换：``child.fitness < target.fitness`` 才替换。
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from task_allocation.baselines.evolution import (
    EMPTY_FITNESS,
    evaluate_routes,
    routes_to_assignment,
)
from task_allocation.methods.muas.base import BaseOptimizer, SolverResult

__all__ = ["SortDESolver"]


class SortDESolver(BaseOptimizer):
    """Sort-DE（随机键编码 + 标准 DE/rand/1/bin + 贪婪替换）。

    Args:
        seed:            随机种子；None 用熵源。
        pop_size:        种群规模（默认 50）。
        max_generations: 代数（默认 100）。
        f:               差分缩放因子 F（文献默认 0.5）。
        cr:              二项交叉率（文献默认 0.9）。
    """

    def __init__(
        self,
        seed: int | None = None,
        pop_size: int = 50,
        max_generations: int = 100,
        f: float = 0.5,
        cr: float = 0.9,
    ) -> None:
        self._seed = seed
        self._pop = max(2, int(pop_size))
        self._gens = max(0, int(max_generations))
        self._f = float(f)
        self._cr = min(max(float(cr), 0.0), 1.0)

    @property
    def name(self) -> str:
        return "sort_de"

    # ---- 解码 ---------------------------------------------------------

    @staticmethod
    def _decode(
        x: np.ndarray, n_uavs: int, n_targets: int
    ) -> list[list[int]]:
        """随机键 → 航线（argmin 归属 + 顺序键排序）。"""
        k, n = n_uavs, n_targets
        a = x[: n * k].reshape(n, k)
        o = x[n * k : n * k + n]
        uav_of = np.argmin(a, axis=1)
        routes: list[list[int]] = [[] for _ in range(k)]
        for j in sorted(range(n), key=lambda j: float(o[j])):
            routes[int(uav_of[j])].append(int(j))
        return routes

    def solve(
        self, cost_matrix: np.ndarray, n_uavs: int, n_targets: int, **kwargs: Any
    ) -> SolverResult:
        evaluator = kwargs.get("fitness_evaluator")
        if evaluator is None:
            raise ValueError("fitness_evaluator must be provided.")
        t0 = time.time()
        rng = np.random.default_rng(self._seed)
        cm = np.asarray(cost_matrix, dtype=float)
        k = max(1, int(n_uavs))
        n = max(1, int(n_targets))
        dim = n * k + n

        pop = rng.standard_normal((self._pop, dim))
        fitnesses = [
            evaluate_routes(evaluator, self._decode(pop[i], k, n), cm, k)
            for i in range(self._pop)
        ]
        best_idx = int(np.argmin(fitnesses))
        best_x = pop[best_idx].copy()
        best_fitness = float(fitnesses[best_idx])
        cost_history = [best_fitness]

        for _gen in range(self._gens):
            for i in range(self._pop):
                others = [j for j in range(self._pop) if j != i]
                picks = rng.choice(
                    others, size=3, replace=(len(others) < 3)
                )
                r1, r2, r3 = (int(picks[0]), int(picks[1]), int(picks[2]))
                v = pop[r1] + self._f * (pop[r2] - pop[r3])
                # 二项交叉（保证至少一位来自 v）
                mask = rng.random(dim) < self._cr
                j_rand = int(rng.integers(0, dim))
                mask[j_rand] = True
                child = np.where(mask, v, pop[i])
                f = evaluate_routes(evaluator, self._decode(child, k, n), cm, k)
                if f < fitnesses[i]:  # DE 式贪婪替换
                    pop[i] = child
                    fitnesses[i] = f
                    if f < best_fitness:
                        best_fitness = f
                        best_x = child.copy()
            cost_history.append(best_fitness)

        best_routes = self._decode(best_x, k, n)
        return SolverResult(
            best_assignment=routes_to_assignment(best_routes),
            best_fitness=best_fitness,
            cost_history=cost_history,
            total_generations=self._gens,
            elapsed_seconds=time.time() - t0,
            solver_name=self.name,
            extra={"n_evals": self._pop * (self._gens + 1), "empty_fitness": EMPTY_FITNESS},
        )
