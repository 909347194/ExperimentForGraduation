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


def _pick_distinct(
    pop_size: int,
    exclude: int,
    need: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """从 ``[0, pop_size)`` 中无放回抽 ``need`` 个互异下标，且排除 ``exclude``。

    标准 DE 要求参与差分的个体互异且不包含目标个体自身。本函数用「先剔除
    自身、再从候选池抽取」替代「抽取后重试直到不含自身」：后者在
    ``pop_size == need + 1`` 时命中概率为 1，重试条件恒真 → **死循环**
    （进程无提示挂起）；``pop_size < need`` 时 ``rng.choice`` 直接抛错。

    Returns:
        下标数组；可用候选不足时长度小于 ``need``（调用方负责补齐/降级）。
    """
    pool = np.delete(np.arange(pop_size), exclude)
    k = int(min(need, pool.size))
    if k <= 0:
        return np.array([], dtype=int)
    return rng.choice(pool, size=k, replace=False)


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
        # 选择 4 个互异个体，且必须排除自身 i（标准 DE 约束）。
        if pop_size >= 5:
            # 主流路径：保留原有**拒绝采样**口径（抽 4 个、不含 i 则重试）。
            # 与候选池直接抽取的分布相同，但 rng 消费序列不同 —— DE 是随机
            # 搜索算法，序列一变就落到不同局部最优（实测 exp01 完成率
            # 95% vs 75%，差异显著）。故主流规模下**不改动**，以守住既有
            # 实验基线的可复现性；仅对会死循环的规模启用安全路径。
            indices = rng.choice(pop_size, size=4, replace=False)
            while i in indices:
                indices = rng.choice(pop_size, size=4, replace=False)
            xs = [cost_vectors[int(k)] for k in indices]
        else:
            # 小种群（pop_size <= 4）：无放回抽全部必然命中 i，重试条件恒真
            # → **死循环**（进程无提示挂起）；pop_size < 4 时 choice 直接抛错。
            # 改用候选池抽取 + 差分退化。
            idxs = _pick_distinct(pop_size, i, 4, rng)
            if idxs.size == 0:
                # 种群退化为单个个体：无可差分对象，原样保留
                trials[i] = cost_vectors[i]
                continue
            xs = [cost_vectors[int(k)] for k in idxs]
        # 小种群（pop_size <= 4）下可用个体不足 4 个：缺失的差分项用首个
        # 可用向量补齐，使差分自然退化（如 x_r4=x_r1 时 best/2 → best/1），
        # 既不挂起也不抛错。
        while len(xs) < 4:
            xs.append(xs[0])
        x_r1, x_r2, x_r3, x_r4 = xs

        f = f_values[i]
        best = cost_vectors[best_idx]

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
        if pop_size >= 4:
            # 主流路径：保留原有拒绝采样口径（见 hybrid 同款说明），
            # 不改变 rng 消费序列与实验基线。
            indices = rng.choice(pop_size, size=3, replace=False)
            while i in indices:
                indices = rng.choice(pop_size, size=3, replace=False)
            r1, r2, r3 = indices
        else:
            # pop_size == 3 时拒绝采样必然命中 i → 死循环，改用候选池
            idxs = _pick_distinct(pop_size, i, 3, rng)
            if idxs.size < 3:
                pad = [idxs[0]] * (3 - idxs.size) if idxs.size else []
                idxs = np.concatenate([idxs, np.asarray(pad, dtype=int)])
            if idxs.size < 3:
                trials[i] = cost_vectors[i]
                continue
            r1, r2, r3 = idxs

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
        if pop_size >= 3:
            # 主流路径：保留原有拒绝采样口径（见 hybrid 同款说明）
            indices = rng.choice(pop_size, size=2, replace=False)
            while i in indices:
                indices = rng.choice(pop_size, size=2, replace=False)
            r1, r2 = indices
        else:
            # pop_size == 2 时拒绝采样必然命中 i → 死循环，改用候选池
            idxs = _pick_distinct(pop_size, i, 2, rng)
            if idxs.size < 2:
                if idxs.size == 0:
                    trials[i] = cost_vectors[i]
                    continue
                idxs = np.concatenate([idxs, idxs[:1]])
            r1, r2 = idxs

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
