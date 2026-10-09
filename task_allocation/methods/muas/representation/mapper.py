# -*- coding: utf-8 -*-
"""mapper.py — 离散到连续的正向空间映射 φ（公式 3-5）

职责：
    将离散个体的基因代价值提取为连续实值向量，使 DE 算子
    （变异、交叉）能在连续空间中正常运作。

对应论文：
    公式 (3-5): φ: {(U_i, T_j) ∈ Z^d} --C_cost--> R^c
    映射方法：直接提取基因中存储的航程代价值，组成连续向量。
    这是 DMDE 的核心创新——用航程代价作为离散→连续的映射媒介，
    使差分操作具有物理意义（定义 3.1 代价距离）。

数据流：
    Individual (离散三元组) → mapper.phi() → cost_vector (连续实值)
    cost_vector → DE 算子 → new_cost_vector → inverse_mapper → Individual
"""

from __future__ import annotations

import numpy as np

from .encoder import Individual


def phi(individual: Individual) -> np.ndarray:
    """正向映射：提取个体的代价值向量。

    对应公式 (3-5)：将离散的 (U_i, T_j) 对映射为连续的代价值空间。

    Args:
        individual: 进化个体。

    Returns:
        代价值向量，shape = (len(genes),)。
    """
    return individual.cost_vector


def phi_batch(population: list[Individual]) -> np.ndarray:
    """批量正向映射。

    Args:
        population: 种群个体列表。

    Returns:
        代价值矩阵，shape = (pop_size, gene_length)。
    """
    return np.array([phi(ind) for ind in population])
