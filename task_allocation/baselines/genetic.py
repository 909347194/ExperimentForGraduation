# -*- coding: utf-8 -*-
"""genetic.py — 经典遗传算法基线（文献代表：GA 类智能优化）。

多无人机任务分配文献中最常见的演化基线：**遗传算法**（锦标赛选择 +
顺序保持交叉 + 变异 + 精英保留的世代模型）。本实现与 DMDE 共享同一
初始解构造（随机分组 + 最近邻排序）、同一解码修复链与同一交付后处理
（见 :mod:`task_allocation.baselines.evolution`）——公平性口径是
**engine swap**：只换搜索引擎，编码/解码/评价/交付不动，预算同为
pop × gen 次评价。

染色体（routes）：``list[list[int]]``，下标 = 参与机局部下标，组内顺序 =
访问顺序；全体任务恰好出现一次。遗传算子：

- **交叉**（顺序保持合并，``crossover_rate``）：子代每任务归属逐位取自
  双亲（均匀交叉归属维度）；同机任务序取「父 A 序过滤 + 父 B 序补尾」
  的保序合并（顺序维度），保证每任务恰出现一次；
- **变异**（``mutation_rate``，三选一）：relocate（跨机/机内迁移一任务）、
  swap（两机各出一任务交换归属与位点）、reverse（随机机内段逆序）；
- **选择/替换**：k 锦标赛选亲，世代模型 + 精英保留（``elitism``）。

返回 ``SolverResult``（``best_assignment`` 走 assignment 解码交付通道）。
无迭代含义：本求解器有代际，``cost_history`` 记逐代最优。
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
    tournament,
)
from task_allocation.methods.muas.base import BaseOptimizer, SolverResult

__all__ = ["GeneticSolver"]


class GeneticSolver(BaseOptimizer):
    """经典 GA（锦标赛 + 保序交叉 + 三类变异 + 精英保留）。

    Args:
        seed:               随机种子；None 用熵源（不可复现）。
        pop_size:           种群规模（默认 50，与 DMDE 生产口径一致）。
        max_generations:    进化代数（默认 100，同 DMDE 预算口径）。
        crossover_rate:     交叉概率（未交叉则子代 = 亲本 A 拷贝）。
        mutation_rate:      每个子代应用一次变异的概率。
        tournament_k:       锦标赛规模。
        elitism:            每代保留的最优个体数（世代替换）。
    """

    def __init__(
        self,
        seed: int | None = None,
        pop_size: int = 50,
        max_generations: int = 100,
        crossover_rate: float = 0.9,
        mutation_rate: float = 0.2,
        tournament_k: int = 2,
        elitism: int = 1,
    ) -> None:
        self._seed = seed
        self._pop = max(2, int(pop_size))
        self._gens = max(0, int(max_generations))
        self._pc = float(crossover_rate)
        self._pm = float(mutation_rate)
        self._k = int(tournament_k)
        self._elitism = max(0, min(int(elitism), self._pop))

    @property
    def name(self) -> str:
        return "genetic"

    # ---- 遗传算子 -----------------------------------------------------

    @staticmethod
    def _crossover(
        ra: list[list[int]], rb: list[list[int]], rng: np.random.Generator
    ) -> list[list[int]]:
        """归属维度均匀交叉 + 顺序维度保序合并（见模块 docstring）。"""
        n_uavs = len(ra)
        a_of = {t: u for u, seq in enumerate(ra) for t in seq}
        b_of = {t: u for u, seq in enumerate(rb) for t in seq}
        child_of: dict[int, int] = {
            t: (a_of[t] if rng.random() < 0.5 else b_of[t]) for t in a_of
        }
        a_flat = [t for seq in ra for t in seq]
        b_flat = [t for seq in rb for t in seq]
        child: list[list[int]] = [[] for _ in range(n_uavs)]
        seen: set[int] = set()
        for t in a_flat + b_flat:
            if t in seen:
                continue
            seen.add(t)
            child[child_of[t]].append(t)
        return child

    def _mutate(
        self, routes: list[list[int]], rng: np.random.Generator
    ) -> list[list[int]]:
        """relocate / swap / reverse 三选一（原地改拷贝）。"""
        out = [list(seq) for seq in routes]
        n_uavs = len(out)
        non_empty = [u for u in range(n_uavs) if out[u]]
        if not non_empty:
            return out
        move = int(rng.integers(0, 3))
        if move == 0:  # relocate：一任务迁到（同或异机）随机位点
            u = int(non_empty[rng.integers(0, len(non_empty))])
            i = int(rng.integers(0, len(out[u])))
            t = out[u].pop(i)
            v = int(rng.integers(0, n_uavs))
            pos = int(rng.integers(0, len(out[v]) + 1))
            out[v].insert(pos, t)
        elif move == 1:  # swap：两机各出一任务交换归属与位点
            u = int(non_empty[rng.integers(0, len(non_empty))])
            v = int(non_empty[rng.integers(0, len(non_empty))])
            if not out[u] or not out[v]:
                return out
            i = int(rng.integers(0, len(out[u])))
            j = int(rng.integers(0, len(out[v])))
            out[u][i], out[v][j] = out[v][j], out[u][i]
        else:  # reverse：随机机内段逆序
            u = int(non_empty[rng.integers(0, len(non_empty))])
            n = len(out[u])
            if n >= 2:
                i = int(rng.integers(0, n - 1))
                j = int(rng.integers(i + 1, n + 1))
                out[u][i:j] = reversed(out[u][i:j])
        return out

    # ---- 主循环 -------------------------------------------------------

    def solve(
        self, cost_matrix: np.ndarray, n_uavs: int, n_targets: int, **kwargs: Any
    ) -> SolverResult:
        evaluator = kwargs.get("fitness_evaluator")
        if evaluator is None:
            raise ValueError("fitness_evaluator must be provided.")
        t0 = time.time()
        rng = np.random.default_rng(self._seed)
        cm = np.asarray(cost_matrix, dtype=float)

        population = [
            random_routes(n_uavs, n_targets, rng, cost_matrix=cm)
            for _ in range(self._pop)
        ]
        fitnesses = [
            evaluate_routes(evaluator, r, cm, n_uavs) for r in population
        ]
        best_idx = int(np.argmin(fitnesses))
        best_routes = [list(seq) for seq in population[best_idx]]
        best_fitness = float(fitnesses[best_idx])
        cost_history = [best_fitness]

        for _gen in range(self._gens):
            new_pop: list[list[list[int]]] = []
            new_fit: list[float] = []
            # 精英保留
            elite_idx = np.argsort(fitnesses)[: self._elitism]
            for e in elite_idx:
                new_pop.append([list(seq) for seq in population[int(e)]])
                new_fit.append(float(fitnesses[int(e)]))
            while len(new_pop) < self._pop:
                ia = tournament(fitnesses, rng, self._k)
                ib = tournament(fitnesses, rng, self._k)
                pa, pb = population[ia], population[ib]
                if rng.random() < self._pc:
                    child = self._crossover(pa, pb, rng)
                else:
                    child = [list(seq) for seq in pa]
                if rng.random() < self._pm:
                    child = self._mutate(child, rng)
                f = evaluate_routes(evaluator, child, cm, n_uavs)
                new_pop.append(child)
                new_fit.append(f)
                if f < best_fitness:
                    best_fitness = f
                    best_routes = [list(seq) for seq in child]
            population, fitnesses = new_pop, new_fit
            cost_history.append(best_fitness)

        return SolverResult(
            best_assignment=routes_to_assignment(best_routes),
            best_fitness=best_fitness,
            cost_history=cost_history,
            total_generations=self._gens,
            elapsed_seconds=time.time() - t0,
            solver_name=self.name,
            extra={"n_evals": self._pop * (self._gens + 1), "empty_fitness": EMPTY_FITNESS},
        )
