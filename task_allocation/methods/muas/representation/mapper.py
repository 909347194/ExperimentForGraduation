# -*- coding: utf-8 -*-
"""mapper.py — 离散到连续的正向映射。

职责：把个体基因中存储的航程代价提取为连续实值向量，使差分算子能在连续空间运作；以代价
作为离散到连续的映射媒介，使差分操作具有物理意义。
边界：只提取代价向量，不改基因结构。
相关：muas/representation/encoder.py、inverse_mapper.py、muas/operators/。"""

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


