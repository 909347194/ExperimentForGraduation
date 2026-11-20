# -*- coding: utf-8 -*-
"""evolution.py — 演化类文献基线（genetic / pmx_de / sort_de）的共享构件。

只放**三个求解器共用**的编码口径与工具（与 ``construct.py`` 对构造式基线的
角色对称），不放任何求解策略：

- 航线染色体 ``routes``：``list[list[int]]``，下标 = 参与机局部下标
  （``MUASAssignmentEvaluator`` 的局部口径），元素 = 任务局部下标，
  组内顺序 = 访问顺序；全体任务恰好出现一次（一任务一机）。
- ``routes_to_assignment``：routes → ``[(uav_idx, task_idx), ...]``
  （``build_solution`` 输入格式；同机对按访问顺序连续排列）。
- ``nn_routes``：与 ``PopulationEncoder._generate_srp`` 同款初始化
  （随机分组 + 组内最近邻贪心排序）——保证文献基线与 DMDE 的
  初始解构造口径一致，消融差异只来自搜索引擎。
- ``evaluate_routes``：统一走 ``fitness_evaluator.evaluate(assignment)``
  ——**与 DMDE 同一条解码修复链**（覆盖装载 / 合并 / 终点巢 MCF /
  ``_best_order``），交付阶段同样经 assignment 解码通道重建 + polish。
  公平性设计：引擎换血、解码与后处理不动（engine swap）。

设计原则（与 ``baselines/README.md`` 一致）：评价端唯一事实来源是
``SelectiveMUASProblem``（经 evaluator），本模块不复制 DMDE 的
编码/映射实现，不做构造期可行性预判（修复链负责）。
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

__all__ = [
    "routes_to_assignment",
    "assignment_to_routes",
    "nn_routes",
    "random_routes",
    "evaluate_routes",
    "tournament",
    "EMPTY_FITNESS",
]

#: 空解码的哨兵适应度（与 DMDE 的空 assignment 口径一致）
EMPTY_FITNESS = 1e12


def routes_to_assignment(routes: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    """routes → assignment 三元组列表（同机对按访问顺序连续）。"""
    out: list[tuple[int, int]] = []
    for u, seq in enumerate(routes):
        for t in seq:
            out.append((int(u), int(t)))
    return out


def assignment_to_routes(
    assignment: Sequence[tuple[int, int]], n_uavs: int
) -> list[list[int]]:
    """assignment → routes（保序；每任务保留首次出现，口径同 build_solution）。"""
    routes: list[list[int]] = [[] for _ in range(n_uavs)]
    seen: set[int] = set()
    for u, t in assignment:
        u, t = int(u), int(t)
        if u < 0 or u >= n_uavs or t in seen:
            continue
        seen.add(t)
        routes[u].append(t)
    return routes


def _nn_order(
    cost_matrix: np.ndarray, n_uavs: int, uav_idx: int, tasks: Sequence[int]
) -> list[int]:
    """组内最近邻贪心排序（与 PopulationEncoder._order_targets_greedy 同款）。

    cost_matrix 布局（方案 A）：``[0, K)`` 行 = UAV 起点，``[K, K+N)`` 行 =
    任务行，列 = 任务；任务 j 的列下标 = j。
    """
    if len(tasks) <= 1:
        return list(tasks)
    remaining = set(int(t) for t in tasks)
    first = min(remaining, key=lambda t: float(cost_matrix[uav_idx, t]))
    ordered = [first]
    remaining.remove(first)
    while remaining:
        last = ordered[-1]
        nxt = min(remaining, key=lambda t: float(cost_matrix[n_uavs + last, t]))
        ordered.append(nxt)
        remaining.remove(nxt)
    return ordered


def random_routes(
    n_uavs: int,
    n_targets: int,
    rng: np.random.Generator,
    *,
    cost_matrix: np.ndarray | None = None,
    nn_order: bool = True,
) -> list[list[int]]:
    """初始化：随机分组（每机至少一任务，N≥K 时）+ 组内最近邻排序。

    与 ``PopulationEncoder._generate_srp`` 同构（同款初始化口径）；
    ``nn_order=False`` 时组内随机排序。
    """
    targets = list(range(n_targets))
    rng.shuffle(targets)
    groups: dict[int, list[int]] = {i: [] for i in range(n_uavs)}
    if n_targets >= n_uavs:
        for i in range(n_uavs):
            groups[i].append(targets[i])
        for t in targets[n_uavs:]:
            groups[int(rng.integers(0, n_uavs))].append(t)
    else:
        for t in targets:
            groups[int(rng.integers(0, n_uavs))].append(t)
    routes: list[list[int]] = []
    for u in range(n_uavs):
        seq = groups[u]
        if nn_order and cost_matrix is not None and len(seq) > 1:
            seq = _nn_order(cost_matrix, n_uavs, u, seq)
        else:
            seq = list(seq)
            rng.shuffle(seq)
        routes.append(seq)
    return routes


def evaluate_routes(
    evaluator: Any,
    routes: Sequence[Sequence[int]],
    cost_matrix: np.ndarray,
    n_uavs: int,
) -> float:
    """经统一评价器（解码修复链 + 问题标量 f）评一路线染色体。"""
    assignment = routes_to_assignment(routes)
    if not assignment:
        return EMPTY_FITNESS
    result = evaluator.evaluate(assignment, cost_matrix, n_uavs=n_uavs)
    fitness = getattr(result, "fitness", EMPTY_FITNESS)
    if fitness is None or not math.isfinite(float(fitness)):
        return EMPTY_FITNESS
    return float(fitness)


def tournament(
    fitnesses: Sequence[float], rng: np.random.Generator, k: int = 2
) -> int:
    """k 锦标赛（最小化），返回胜者下标。"""
    n = len(fitnesses)
    if n == 0:
        raise ValueError("empty population")
    k = max(1, min(int(k), n))
    idx = rng.choice(n, size=k, replace=False)
    best = int(idx[0])
    for i in idx[1:]:
        if fitnesses[int(i)] < fitnesses[best]:
            best = int(i)
    return best
