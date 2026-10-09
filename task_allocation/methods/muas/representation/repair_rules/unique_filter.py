# -*- coding: utf-8 -*-
"""unique_filter.py — 规则 3.5：独占分配与掩码(Inf)行/列剔除

对应论文：
    规则 3.5a (N=M): 匹配后删除该基因所在行列（用 Inf 掩码）。
    规则 3.5b (N>M): 匹配后删除该基因对应行（UAV 不重复）。
    规则 3.5c (N<M): 匹配后删除目标列，用目标行替换 UAV 行。

    实际计算中用 Inf 重置行列，避免删除操作降低效率。
"""

from __future__ import annotations

import numpy as np

INF = 1e12


def mask_balanced(mask: np.ndarray, row: int, col: int) -> None:
    """规则 3.5a: N=M 时，行和列均置 Inf。

    保证 UAV 与目标点一一对应不重复。
    """
    mask[row, :] = True
    mask[:, col] = True


def mask_overloaded(mask: np.ndarray, row: int, col: int) -> None:
    """规则 3.5b: N>M 时，仅 UAV 所在行置 Inf。

    保证 UAV 不重复，但同一目标可被多个 UAV 执行。
    """
    mask[row, :] = True


def mask_srp_upper(
    cost_matrix: np.ndarray,
    mask: np.ndarray,
    row: int,
    col: int,
    n_uavs: int,
) -> None:
    """规则 3.5c: N<M 时，目标列置 Inf，UAV 行更新为巡游代价。

    匹配 UAV -> Target 后：
    1. 目标列置 Inf（该目标已被分配）。
    2. UAV 行替换为该目标到其他目标的巡游代价（下半部分矩阵）。
    """
    # 目标列置 Inf
    mask[:, col] = True

    # 用目标的巡游代价行替换 UAV 行
    target_row = n_uavs + col  # 下半部分矩阵中该目标的行
    cost_matrix[row, :] = cost_matrix[target_row, :]
    mask[row, :] = mask[target_row, :]
