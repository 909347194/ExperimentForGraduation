# -*- coding: utf-8 -*-
"""invalid_mutator.py — 规则 3.6：越界、重复及无效值随机变异修补

对应论文：
    规则 3.6：在按规则 3.4 和 3.5 匹配完合理的差分临时结果之后，
    对剩余的无效差分值（零值、负值、越界值）和未匹配的矩阵位置，
    利用局部随机变异的方法进行匹配。

    具体方法：对无效值，依次随机匹配一个未被映射到新个体中的代价值，
    按规则 3.5 修改矩阵，继续匹配其他无效值，直到全部任务匹配完。
"""

from __future__ import annotations

import random

import numpy as np

from .nearest_match import nearest_match


def repair_invalid(
    cost_matrix: np.ndarray,
    mask: np.ndarray,
    invalid_indices: list[int],
    model_type: str,
    n_uavs: int,
) -> list[tuple[int, int, float]]:
    """对无效差分值执行随机变异修补。

    对应规则 3.6。

    Args:
        cost_matrix:    代价矩阵。
        mask:           布尔掩码。
        invalid_indices: 无效值在基因序列中的位置索引列表。
        model_type:     分配模型类型。
        n_uavs:         UAV 数量。

    Returns:
        修补后的基因三元组列表 [(uav_id, target_id, cost), ...]。
    """
    from .unique_filter import (
        mask_balanced,
        mask_overloaded,
        mask_srp_upper,
    )

    repaired = []

    for _ in invalid_indices:
        # 在未匹配位置中随机选择一个
        available = np.argwhere(~mask)
        if len(available) == 0:
            break

        idx = random.randint(0, len(available) - 1)
        row, col = available[idx]
        cost = float(cost_matrix[row, col])

        if model_type == "srp" and row >= n_uavs:
            # SRP 模型下半部分：target-to-target
            repaired.append((-1, col, cost))
            mask[:, col] = True
        else:
            repaired.append((row, col, cost))
            # 按模型类型更新掩码
            if model_type == "balanced":
                mask_balanced(mask, row, col)
            elif model_type == "overloaded":
                mask_overloaded(mask, row, col)
            elif model_type == "srp":
                mask_srp_upper(cost_matrix, mask, row, col, n_uavs)

    return repaired
