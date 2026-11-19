# -*- coding: utf-8 -*-
"""unique_filter.py — 独占分配与代价矩阵行/列屏蔽规则。

职责：按机数与任务数关系，匹配后用 Inf 屏蔽代价矩阵相应行/列，避免资源被重复选中；用置
Inf 代替删除以保留矩阵形状。
边界：只做屏蔽，不修补剩余无效值。
相关：muas/representation/inverse_mapper.py、repair_rules/nearest_match.py、
repair_rules/invalid_mutator.py。"""

from __future__ import annotations

import numpy as np

INF = 1e12


def mask_balanced(mask: np.ndarray, row: int, col: int) -> None:
    """规则 3.5a: K=N 时，行和列均置 Inf。

    保证 UAV 与目标点一一对应不重复。
    """
    mask[row, :] = True
    mask[:, col] = True


def mask_overloaded(mask: np.ndarray, row: int, col: int) -> None:
    """规则 3.5b: K>N 时，仅 UAV 所在行置 Inf。

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
    """规则 3.5c: K<N 时，目标列置 Inf，UAV 行更新为巡游代价。

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
