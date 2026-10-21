# -*- coding: utf-8 -*-
"""extinction.py — GMR 灭绝/灾变算子（公式 3-12）

职责：
    当种群陷入早熟收敛时，执行世代间灭绝操作，保留较优个体，
    重置其余个体，以增加种群多样性和搜索覆盖范围。

对应论文（Zhao Ming 等, 2017, 离散映射差分进化 DMDE）公式 (3-12)：
    GMR(t) = 1 - CR(t)

    即代间变异（灭绝）概率直接由当前交叉率 CR 决定：
    前期 CR 高 → GMR 低（种群稳定，不灭绝）；
    后期 CR 低 → GMR 高（种群大量重置，跳出局部最优）。

    灭绝操作：把当代个体按（最小化）适应度归一化，保留落在随机
    "环境压力"阈值内的较优个体以及当代最优解，其余个体全部用随机
    新个体替换。

    关于 δ：
        论文公式 (3-12) **没有** δ 门限，故 ``delta=None``（默认）即为原文行为。
        本实现额外保留一个可选门限 ``delta``：当 ``CR >= delta`` 时直接
        GMR=0，即把灭绝强制推迟到搜索后期。这属于可选的工程扩展，
        论文复现时保持默认 ``None`` 即可。
"""

from __future__ import annotations

import numpy as np


def gmr_rate(cr: float, delta: float | None = None) -> float:
    """计算代间变异率 GMR（公式 3-12）。

    Args:
        cr:    当前动态交叉率。
        delta: 可选的灭绝门限 δ。``None``（默认）为论文原式 ``GMR = 1 - CR``；
               给定数值时附加工程门限 ``CR >= δ → GMR = 0``，把灭绝推迟到后期。

    Returns:
        GMR 值（灭绝概率），范围 [0, 1]。0 表示不触发灭绝。
    """
    if delta is not None and cr >= delta:
        return 0.0
    return 1.0 - cr


def should_extinct(cr: float, delta: float | None = None, rng=None) -> bool:
    """判断当前代是否触发灭绝。

    灭绝概率由 GMR 决定（公式 3-12）。

    Args:
        cr:    当前动态交叉率。
        delta: 可选灭绝门限 δ（默认 ``None`` = 论文原式）。
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
        n_uavs:             UAV 数 K（实例机数，≤ 可用机数）。
        n_targets:          任务数 N。
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
    """生成随机个体的代价值向量。

    长度必须与当前种群一致：方案 A 下 SRP 个体的基因数恒为 ``M``
    （终点机巢不进编码，无终止基因）。
    """
    from ..representation.encoder import PopulationEncoder

    encoder = PopulationEncoder(cost_matrix, n_uavs, n_targets)
    ind = encoder.generate(1)[0]
    return ind.cost_vector
