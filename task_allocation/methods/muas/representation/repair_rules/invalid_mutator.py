# -*- coding: utf-8 -*-
"""invalid_mutator.py — 无效差分值的随机修补规则。

职责：对零值/负值/越界等无效差分值与未匹配位置，依次随机匹配未使用的代价值并更新矩阵，
直到全部任务匹配完成。
边界：只修补无效值，不做最近邻匹配或独占屏蔽。
相关：muas/representation/inverse_mapper.py、repair_rules/unique_filter.py、
repair_rules/nearest_match.py。"""

from __future__ import annotations

import numpy as np



def repair_invalid(
    cost_matrix: np.ndarray,
    mask: np.ndarray,
    invalid_indices: list[int],
    model_type: str,
    n_uavs: int,
    rng: np.random.Generator,
) -> list[tuple[int, int, float]]:
    """对无效差分值执行随机变异修补。

    对应规则 3.6。

    Args:
        cost_matrix:    代价矩阵。
        mask:           布尔掩码。
        invalid_indices: 无效值在基因序列中的位置索引列表。
        model_type:     分配模型类型。
        n_uavs:         UAV 数量。
        rng:            随机数生成器（必填，方案 B 单通道）：全部随机性
                        取自该生成器，不触碰全局 ``random`` 状态。

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
        # 在未匹配位置中随机选择一个。
        # balanced / overloaded 只产生 (uav, target) 分配，候选行必须是
        # UAV 行（row < n_uavs）；目标行属于 SRP 巡游、机巢列属于终止基因，
        # 都不是分配关系，否则会产出越界 uav_id / target_id。
        candidates = np.argwhere(~mask)
        if model_type != "srp":
            candidates = candidates[candidates[:, 0] < n_uavs]
        if len(candidates) == 0:
            break

        idx = int(rng.integers(0, len(candidates)))
        row, col = candidates[idx]
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
