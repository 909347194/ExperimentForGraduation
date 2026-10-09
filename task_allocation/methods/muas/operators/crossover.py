# -*- coding: utf-8 -*-
"""crossover.py — 动态交叉率 CR 与混合差分策略（公式 3-9 / 3-10）

对应论文：
    公式 (3-9): 动态交叉率
        CR(x) = 1 - (log(x) / log(S))^ζ
        x = 1,2,...,S (当前代数), S 为总代数, ζ 为曲率指数。

    公式 (3-10): 混合差分策略产生新个体
        C'(i)_j(t) = C(r1)_j(t) + F·(C(r2)_j(t) - C(r3)_j(t)),
            if CR(t) >= rand[0,1]      (DE/rand/1 探索)
        C'(i)_j(t) = C(best)_j(t) + F·(C(r1)_j(t) + C(r2)_j(t)
                                         - C(r3)_j(t) - C(r4)_j(t)),
            if CR(t) < rand[0,1]       (DE/best/2 开发)

    当 CR 较大时偏向探索（rand/1），较小时偏向开发（best/2），
    实现搜索过程中探索与开发的动态平衡。
"""

from __future__ import annotations

import numpy as np


def dynamic_crossover_rate(
    current_gen: int,
    total_gens: int,
    zeta: int = 3,
) -> float:
    """计算动态交叉率 CR。

    对应公式 (3-9)。

    Args:
        current_gen: 当前代数（从 1 开始）。
        total_gens:  总迭代代数。
        zeta:        曲率变化指数 ζ（默认 3）。

    Returns:
        动态交叉率 CR。
    """
    if current_gen <= 1:
        return 1.0
    if current_gen >= total_gens:
        return 0.0
    return 1.0 - (np.log(current_gen) / np.log(total_gens)) ** zeta


def hybrid_differential_population(
    cost_vectors: np.ndarray,
    best_idx: int,
    f_values: np.ndarray,
    cr: float,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """对整个种群执行混合差分（每个个体独立 F 值）。

    Args:
        cost_vectors: 种群代价值矩阵。
        best_idx:     最优个体索引。
        f_values:     每个个体的缩放因子数组，shape = (pop_size,)。
        cr:           当前动态交叉率。
        rng:          随机数生成器。

    Returns:
        试验向量矩阵，shape = (pop_size, gene_len)。
    """
    if rng is None:
        rng = np.random.default_rng()

    pop_size, gene_len = cost_vectors.shape
    trials = np.empty_like(cost_vectors)

    for i in range(pop_size):
        # 选择 4 个不同的随机个体
        indices = rng.choice(pop_size, size=4, replace=False)
        while i in indices:
            indices = rng.choice(pop_size, size=4, replace=False)
        r1, r2, r3, r4 = indices

        f = f_values[i]
        best = cost_vectors[best_idx]
        x_r1 = cost_vectors[r1]
        x_r2 = cost_vectors[r2]
        x_r3 = cost_vectors[r3]
        x_r4 = cost_vectors[r4]

        # 每个基因位独立判断
        rand_vals = rng.random(gene_len)
        use_rand = rand_vals <= cr

        trial_rand = x_r1 + f * (x_r2 - x_r3)
        trial_best = best + f * (x_r1 + x_r2 - x_r3 - x_r4)

        trials[i] = np.where(use_rand, trial_rand, trial_best)

    return trials


def mutate_rand_1(
    cost_vectors: np.ndarray,
    best_idx: int,
    f_values: np.ndarray,
    cr: float,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """纯 DE/rand/1/bin 变异。

    trial_i = x_r1 + F * (x_r2 - x_r3)
    不依赖最优个体，纯探索。
    """
    if rng is None:
        rng = np.random.default_rng()

    pop_size, gene_len = cost_vectors.shape
    trials = np.empty_like(cost_vectors)

    for i in range(pop_size):
        indices = rng.choice(pop_size, size=3, replace=False)
        while i in indices:
            indices = rng.choice(pop_size, size=3, replace=False)
        r1, r2, r3 = indices

        f = f_values[i]
        mutant = cost_vectors[r1] + f * (cost_vectors[r2] - cost_vectors[r3])

        # 二项交叉
        rand_vals = rng.random(gene_len)
        j_rand = rng.integers(gene_len)
        mask = (rand_vals <= cr) | (np.arange(gene_len) == j_rand)
        trials[i] = np.where(mask, mutant, cost_vectors[i])

    return trials


def mutate_best_1(
    cost_vectors: np.ndarray,
    best_idx: int,
    f_values: np.ndarray,
    cr: float,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """纯 DE/best/1/bin 变异。

    trial_i = x_best + F * (x_r1 - x_r2)
    以最优个体为锚点，收敛快。
    """
    if rng is None:
        rng = np.random.default_rng()

    pop_size, gene_len = cost_vectors.shape
    trials = np.empty_like(cost_vectors)

    for i in range(pop_size):
        indices = rng.choice(pop_size, size=2, replace=False)
        while i in indices:
            indices = rng.choice(pop_size, size=2, replace=False)
        r1, r2 = indices

        f = f_values[i]
        best = cost_vectors[best_idx]
        mutant = best + f * (cost_vectors[r1] - cost_vectors[r2])

        # 二项交叉
        rand_vals = rng.random(gene_len)
        j_rand = rng.integers(gene_len)
        mask = (rand_vals <= cr) | (np.arange(gene_len) == j_rand)
        trials[i] = np.where(mask, mutant, cost_vectors[i])

    return trials


def mutate_with_strategy(
    cost_vectors: np.ndarray,
    best_idx: int,
    f_values: np.ndarray,
    cr: float,
    strategy: str = "mixed",
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """根据策略名称选择变异方式。

    Args:
        strategy: "rand/1" | "best/1" | "mixed"
    """
    if strategy == "rand/1":
        return mutate_rand_1(cost_vectors, best_idx, f_values, cr, rng)
    elif strategy == "best/1":
        return mutate_best_1(cost_vectors, best_idx, f_values, cr, rng)
    else:  # "mixed" or default
        return hybrid_differential_population(cost_vectors, best_idx, f_values, cr, rng)
