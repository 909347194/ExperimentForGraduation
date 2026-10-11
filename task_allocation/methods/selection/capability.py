# -*- coding: utf-8 -*-
"""capability.py — 机队能力预算与候选规模的能力驱动上界（论文 3.3）。

设计原则：**候选/选择规模跟着机队能力（剩余航程预算）走，而不是只跟机数走**。

旧规则只按机数定名额：

    候选数   k = ⌈α · K_avail⌉
    选择数   limit = ⌈max_ratio · K_avail⌉

在机队航程充裕时欠选（预算还剩很多却封顶）、航程紧张时过筛（先放进来再裁）。
本模块给出能力预算可承载的任务数上界：

    B(t)  = budget_scale · budget_factor · Σ_{u ∈ U_t^avail} remaining_range(u)
    c̃     = 粗代价的 quantile 分位数（默认中位数）
    cap(t) = max(min_cap, ⌊B(t) / c̃⌋)

最终规模取两者的交：

    候选数   k     = min(⌈α · K_avail⌉, cap(t))
    选择数   limit = min(⌈max_ratio · K_avail⌉, cap(t))

即"机数名额"与"能力承载数"同时约束，能力只在【规模】这一维生效；
单任务可达性（prefilter）与机群预算硬闸门（postcheck）仍由 feasibility 负责，
本模块不重复其职责。

成本口径与 ``priority.compute_priorities`` 的 C_i(t) 一致：
``cost_hint``（滚动层三维代价矩阵到最近可用 UAV 的代价）优先，
否则最近可用 UAV 的欧氏距离，保证与基础优先级、postcheck 同一语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from task_allocation.common.types import Task, UAV

__all__ = [
    "CapabilityScaleConfig",
    "coarse_task_costs",
    "fleet_range_budget",
    "capability_task_cap",
]


@dataclass
class CapabilityScaleConfig:
    """能力驱动规模的旋钮（yaml ``selection.capability:`` 段）。"""

    # 关闭后退回纯机数名额（⌈αK⌉ / ⌈max_ratio·K⌉），供消融对比
    enabled: bool = True
    # 预算的额外折扣：给未来紧急任务 / 执行中的不确定性留余量
    # （与 feasibility.budget_factor 相乘；postcheck 的硬闸门仍按 budget_factor）
    budget_scale: float = 1.0
    # 任务粗代价的分位数：0.5=中位数；调低→cap 更宽松，调高→更保守
    quantile: float = 0.5
    # cap 下限：至少保留的任务数（保持"总能评出一个候选"的退化行为）
    min_cap: int = 1


def coarse_task_costs(
    tasks: Sequence[Task],
    uavs: Sequence[UAV],
    cost_hint: dict[int, float] | None = None,
) -> list[float]:
    """每任务的粗执行代价（与基础优先级 C_i(t) 同口径）。

    ``cost_hint``（task_id -> 三维代价）优先；缺省时取最近可用 UAV 的欧氏距离。
    无可用 UAV 时返回 1e6 哨兵值（与 ``priority._coarse_exec_cost`` 一致）。
    """
    avail = [u for u in uavs if u.is_available]
    out: list[float] = []
    for t in tasks:
        if cost_hint and t.id in cost_hint:
            out.append(float(cost_hint[t.id]))
            continue
        if not avail:
            out.append(1e6)
            continue
        tx, ty, tz = t.position
        out.append(
            float(
                min(
                    ((u.x - tx) ** 2 + (u.y - ty) ** 2 + (u.z - tz) ** 2) ** 0.5
                    for u in avail
                )
            )
        )
    return out


def fleet_range_budget(uavs: Sequence[UAV], budget_factor: float = 1.0) -> float:
    """机群能力预算 B(t)：可用 UAV 剩余航程之和 × 预算系数。"""
    return float(
        budget_factor * sum(max(u.remaining_range, 0.0) for u in uavs if u.is_available)
    )


def capability_task_cap(
    costs: Sequence[float],
    budget: float,
    config: CapabilityScaleConfig | None = None,
) -> int:
    """能力预算可承载的任务数上界 cap(t)。

    Args:
        costs:  各任务粗执行代价（长度 = 候选池规模）。
        budget: ``fleet_range_budget`` 得到的 B(t)。
        config: 旋钮；缺省取中位数分位、下限 1。

    Returns:
        ``max(min_cap, ⌊B(t) / c̃⌋)``。``costs`` 为空时返回 ``min_cap``。
        全零代价时 c̃ 回退 1.0，避免除零。
    """
    cfg = config or CapabilityScaleConfig()
    if not costs:
        return int(cfg.min_cap)
    arr = np.asarray(costs, dtype=float)
    c_ref = float(np.quantile(arr, float(cfg.quantile)))
    if not np.isfinite(c_ref) or c_ref < 1e-9:
        c_ref = 1.0
    cap = int(np.floor(max(float(budget), 0.0) / c_ref))
    return max(int(cfg.min_cap), cap)
