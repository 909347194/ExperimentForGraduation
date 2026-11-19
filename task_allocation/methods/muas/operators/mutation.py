# -*- coding: utf-8 -*-
"""mutation.py — 混合变异策略。

职责：DMDE 进化中对种群执行变异，结合动态交叉率与混合差分策略，在探索（rand/1）与开发
（best/2）之间动态平衡。
边界：只做变异，不负责选择、评价与反映射。
相关：muas/operators/crossover.py、scale_factor.py、solvers/dmde_solver.py。"""

from __future__ import annotations

from .crossover import dynamic_crossover_rate, hybrid_differential_population
from .scale_factor import dynamic_scale_factor_batch


def mutate_population(
    cost_vectors: np.ndarray,
    best_idx: int,
    current_gen: int,
    total_gens: int,
    zeta: int = 3,
    rng=None,
) -> np.ndarray:
    """对整个种群执行混合变异。

    流程：
    1. 计算当前代的动态交叉率 CR (公式 3-9)。
    2. 为每个个体计算动态缩放因子 F (公式 3-11)。
    3. 执行混合差分策略 (公式 3-10)。

    Args:
        cost_vectors: 种群代价值矩阵。
        best_idx:     最优个体索引。
        current_gen:  当前代数。
        total_gens:   总代数。
        zeta:         曲率指数。
        rng:          随机数生成器。

    Returns:
        试验向量矩阵。
    """
    if rng is None:
        import numpy as np
        rng = np.random.default_rng()

    # 动态交叉率
    cr = dynamic_crossover_rate(current_gen, total_gens, zeta)

    # 动态缩放因子（每个个体独立）
    pop_size = cost_vectors.shape[0]
    f_values = dynamic_scale_factor_batch(cr, pop_size, rng)

    # 混合差分
    return hybrid_differential_population(
        cost_vectors, best_idx, f_values, cr, rng
    )
