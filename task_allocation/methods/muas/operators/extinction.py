# -*- coding: utf-8 -*-
"""extinction.py — 世代灭绝/灾变算子。

职责：种群早熟时按灭绝概率保留较优个体、重置其余个体以提升多样性；概率由交叉率推导，
可选阈值把灭绝推迟到搜索后期。
边界：只产重置用的新个体与触发判定，不执行评价。
相关：muas/operators/crossover.py、representation/encoder.py、solvers/dmde_solver.py。"""

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

    随机流收口：随机性经 ``encoder.generate(rng=...)`` 取自调用方注入的
    种子 ``rng``（方案 B 单通道），不触碰全局 ``random`` / ``np.random``
    状态。历史缺陷：曾忽略 ``rng`` 直接走全局状态，灭绝重置随进程内
    其它随机消费者（基线求解器、诊断钩子、并行调用）漂移，固定 seed
    不能逐位复现。
    """
    from ..representation.encoder import PopulationEncoder

    encoder = PopulationEncoder(cost_matrix, n_uavs, n_targets)
    ind = encoder.generate(1, rng=rng)[0]
    return ind.cost_vector
