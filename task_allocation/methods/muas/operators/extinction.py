# -*- coding: utf-8 -*-
"""extinction.py — GMR 灭绝/灾变算子（公式 3-12）

职责：
    当种群陷入早熟收敛时，执行世代间灭绝操作，保留较优个体，
    重置其余个体，以增加种群多样性和搜索覆盖范围。

对应论文：
    公式 (3-12):
        GMR(t) = 0,           if CR(t) >= δ
        GMR(t) = 1 - CR(t),   if CR(t) < δ
    δ 为产生灭绝的动态变异率临界值。

    灭绝操作：保留当前种群中一定数量的较优个体（环境压力范围内），
    将其他普通个体全部重置。最优解始终保留。
"""

from __future__ import annotations

import numpy as np


def gmr_rate(cr: float, delta: float = 0.3) -> float:
    """计算代间变异率 GMR。

    对应公式 (3-12)。

    Args:
        cr:    当前动态交叉率。
        delta: 灭绝临界值 δ（默认 0.3）。

    Returns:
        GMR 值。0 表示不触发灭绝。
    """
    if cr >= delta:
        return 0.0
    return 1.0 - cr


def should_extinct(cr: float, delta: float = 0.3, rng=None) -> bool:
    """判断当前代是否触发灭绝。

    灭绝概率由 GMR 决定。

    Args:
        cr:    当前动态交叉率。
        delta: 灭绝临界值。
        rng:   随机数生成器。

    Returns:
        True 表示触发灭绝。
    """
    if rng is None:
        rng = np.random.default_rng()
    rate = gmr_rate(cr, delta)
    if rate <= 0:
        return False
    return rng.random() < rate


def apply_extinction(
    population_fitness: np.ndarray,
    cost_vectors: np.ndarray,
    best_idx: int,
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    model_type: str,
    env_pressure_max: float = 0.3,
    rng=None,
) -> tuple[np.ndarray, list[int]]:
    """执行灭绝操作。

    对应论文 3.4.2 节描述的灭绝策略：
    1. 对个体适应度归一化。
    2. 保留环境压力范围内的较优个体。
    3. 重置其余个体（用随机新个体替换）。
    4. 最优个体始终保留。

    Args:
        population_fitness: 种群适应度数组。
        cost_vectors:       种群代价值矩阵。
        best_idx:           最优个体索引。
        cost_matrix:        代价矩阵（用于生成新个体）。
        n_uavs:             UAV 数量。
        n_targets:          目标数量。
        model_type:         分配模型类型。
        env_pressure_max:   最大环境压力（0~0.3）。
        rng:                随机数生成器。

    Returns:
        (new_cost_vectors, survived_indices) 更新后的代价值矩阵和存活索引。
    """
    if rng is None:
        rng = np.random.default_rng()

    pop_size = cost_vectors.shape[0]

    # 适应度归一化到 [0, 1]
    f_min = population_fitness.min()
    f_max = population_fitness.max()
    if f_max - f_min < 1e-10:
        # 所有个体相同，随机保留一半
        n_survive = max(1, pop_size // 2)
        survived = rng.choice(pop_size, size=n_survive, replace=False).tolist()
        if best_idx not in survived:
            survived[0] = best_idx
    else:
        normalized = (population_fitness - f_min) / (f_max - f_min)
        # 环境压力：随机阈值
        env_pressure = rng.random() * env_pressure_max
        survived = [i for i in range(pop_size) if normalized[i] <= env_pressure]
        if best_idx not in survived:
            survived.append(best_idx)

    # 重置未存活的个体
    new_cost_vectors = cost_vectors.copy()
    for i in range(pop_size):
        if i not in survived:
            # 生成随机新个体的代价值向量
            new_cost_vectors[i] = _random_cost_vector(
                cost_matrix, n_uavs, n_targets, model_type, rng
            )

    return new_cost_vectors, survived


def _random_cost_vector(
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    model_type: str,
    rng,
) -> np.ndarray:
    """生成随机个体的代价值向量。"""
    from ..representation.encoder import PopulationEncoder

    encoder = PopulationEncoder(cost_matrix, n_uavs, n_targets)
    ind = encoder.generate(1)[0]
    return ind.cost_vector
