# -*- coding: utf-8 -*-
"""pmx_de.py — 置换编码差分进化基线（文献代表：PMX-DE）。

文献中「DE 适配组合优化」的代表路线之一：**置换编码 + PMX 部分映射交叉 +
DE/rand/1 框架**（差分个体以置换混合近似、贪婪一对一替换）。本实现与
DMDE 共享同一初始解构造、同一解码修复链与交付后处理（engine swap，
见 :mod:`task_allocation.baselines.evolution`），预算同为 pop × gen 次评价。

染色体：``(perm, uav_of)``——``perm`` 为任务全排列（全局访问序），
``uav_of[j]`` 为任务 j 的归属机；航线由「perm 序 + 归属过滤」导出。

演化算子（每代 pop 次试验，贪婪替换）：

- 差分个体：``donor = PMX(r1, PMX(r2, r3))``，两段 PMX 混合的段长 ≈ ``F·N``
  ——置换空间中「r2 − r3 差分」的段混合近似；
- 交叉：``child_perm = PMX(target, donor)``，段长 ≈ ``CR·N``
  （CR 控制交换强度，对应二项交叉的供体占比语义）；
- 归属维度：整数差分 ``donor_uav = (u_r1 + F·(u_r2 − u_r3)) mod K``，
  再按 CR 逐位二项交叉；
- 替换：``child.fitness < target.fitness`` 才替换（DE 式贪婪）。

PMX 实现带环路护栏（映射链检测 + 未用元素兜底），保证任意输入下产出
合法全排列。
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from task_allocation.baselines.evolution import (
    EMPTY_FITNESS,
    evaluate_routes,
    random_routes,
    routes_to_assignment,
)
from task_allocation.methods.muas.base import BaseOptimizer, SolverResult

__all__ = ["PmxDESolver"]


def _pmx_cross(
    a: list[int], b: list[int], start: int, length: int
) -> list[int]:
    """PMX 部分映射交叉（带环路护栏）：段取 b 的顺序，段外保 a 的顺序。"""
    n = len(a)
    if n == 0:
        return []
    length = max(1, min(int(length), n - start))
    seg_a = a[start : start + length]
    seg_b = b[start : start + length]
    child: list[int] = [0] * n
    child[start : start + length] = seg_b
    mapping = dict(zip(seg_b, seg_a))
    seg_b_set = set(seg_b)
    used = set(seg_b)
    for i in list(range(0, start)) + list(range(start + length, n)):
        x = a[i]
        guard = 0
        while x in seg_b_set and guard <= n:
            x = mapping[x]
            guard += 1
        if x in seg_b_set or x in used:
            x = next((e for e in a if e not in used), x)
        child[i] = x
        used.add(x)
    return child


def _mix_perm(
    r1: list[int],
    r2: list[int],
    r3: list[int],
    f: float,
    rng: np.random.Generator,
) -> list[int]:
    """差分个体的置换近似：PMX(r1, PMX(r2, r3))，段长 ≈ f·N。"""
    n = len(r1)
    if n <= 1:
        return list(r1)
    seg = max(1, int(round(abs(f) * n)))
    s23 = int(rng.integers(0, n - 1))
    d1 = _pmx_cross(r2, r3, s23, seg)
    s1 = int(rng.integers(0, n - 1))
    return _pmx_cross(r1, d1, s1, seg)


class PmxDESolver(BaseOptimizer):
    """PMX-DE（置换编码 + PMX 交叉 + DE/rand/1 贪婪替换）。

    Args:
        seed:            随机种子；None 用熵源。
        pop_size:        种群规模（默认 50）。
        max_generations: 代数（默认 100）。
        f:               差分缩放因子 F（段长 ≈ F·N）。
        cr:              交叉率 CR（child 段长 ≈ CR·N、归属维度二项交叉概率）。
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
        return "pmx_de"

    # ---- 染色体 <-> 航线 ---------------------------------------------

    @staticmethod
    def _to_routes(
        perm: list[int], uav_of: list[int], n_uavs: int
    ) -> list[list[int]]:
        routes: list[list[int]] = [[] for _ in range(n_uavs)]
        for t in perm:
            routes[uav_of[t]].append(t)
        return routes

    @staticmethod
    def _from_routes(
        routes: list[list[int]],
    ) -> tuple[list[int], list[int]]:
        perm = [t for seq in routes for t in seq]
        uav_of = [0] * len(perm)
        for u, seq in enumerate(routes):
            for t in seq:
                uav_of[t] = u
        return perm, uav_of

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

        # 初始种群：与 DMDE 同款初始化（随机分组 + 最近邻排序）
        perms: list[list[int]] = []
        uavs_of: list[list[int]] = []
        fitnesses: list[float] = []
        for _ in range(self._pop):
            routes = random_routes(k, n, rng, cost_matrix=cm)
            perm, uav_of = self._from_routes(routes)
            perms.append(perm)
            uavs_of.append(uav_of)
            fitnesses.append(evaluate_routes(evaluator, routes, cm, k))

        best_idx = int(np.argmin(fitnesses))
        best_perm = list(perms[best_idx])
        best_uav = list(uavs_of[best_idx])
        best_fitness = float(fitnesses[best_idx])
        cost_history = [best_fitness]

        for _gen in range(self._gens):
            for i in range(self._pop):
                others = [j for j in range(self._pop) if j != i]
                picks = rng.choice(
                    others, size=3, replace=(len(others) < 3)
                )
                r1, r2, r3 = (int(picks[0]), int(picks[1]), int(picks[2]))
                # 差分个体（置换混合 + 归属整数差分）
                donor_perm = _mix_perm(
                    perms[r1], perms[r2], perms[r3], self._f, rng
                )
                donor_uav = [
                    int((uavs_of[r1][t] + self._f * (uavs_of[r2][t] - uavs_of[r3][t])) % k)
                    for t in range(n)
                ]
                # 交叉：perm 段 PMX（段长 ≈ CR·N）、归属二项
                n_seg = max(1, int(round(self._cr * n))) if n > 1 else 0
                if n > 1:
                    start = int(rng.integers(0, n - 1))
                    child_perm = _pmx_cross(perms[i], donor_perm, start, n_seg)
                else:
                    child_perm = list(perms[i])
                child_uav = [
                    donor_uav[t] if rng.random() < self._cr else uavs_of[i][t]
                    for t in range(n)
                ]
                child_routes = self._to_routes(child_perm, child_uav, k)
                f = evaluate_routes(evaluator, child_routes, cm, k)
                if f < fitnesses[i]:  # DE 式贪婪替换
                    perms[i] = child_perm
                    uavs_of[i] = child_uav
                    fitnesses[i] = f
                    if f < best_fitness:
                        best_fitness = f
                        best_perm = list(child_perm)
                        best_uav = list(child_uav)
            cost_history.append(best_fitness)

        best_routes = self._to_routes(best_perm, best_uav, k)
        return SolverResult(
            best_assignment=routes_to_assignment(best_routes),
            best_fitness=best_fitness,
            cost_history=cost_history,
            total_generations=self._gens,
            elapsed_seconds=time.time() - t0,
            solver_name=self.name,
            extra={"n_evals": self._pop * (self._gens + 1), "empty_fitness": EMPTY_FITNESS},
        )
