# -*- coding: utf-8 -*-
"""cost_matrix.py — 构建任务/UAV/机巢节点间三维近似代价矩阵。

对接 problem.CostProvider：pairwise[(id_a,id_b)] = cost
UAV 起点虚拟键：-(1+uav_id)；机巢键：nest_offset + nest_id
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
from task_allocation.methods.muas.cost.vertical_section import (
    CostEstimationResult,
    VerticalSectionCostEstimator,
)
from task_allocation.methods.muas.problem import CostProvider, build_cost_provider


@dataclass
class CostMatrixBuildResult:
    provider: CostProvider
    uav_target: np.ndarray  # (n_uavs, n_tasks)
    task_task: np.ndarray  # (n_tasks, n_tasks)
    # 终点机巢代价（论文 §5(2)：终点作为航次终止节点进编码）
    task_nest: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))  # (n_tasks, n_nests)
    uav_nest: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))  # (n_uavs, n_nests)
    n_nests: int = 0
    details: dict[tuple[int, int], CostEstimationResult] = field(default_factory=dict)


def build_pairwise_costs(
    tasks: Sequence[Task],
    uavs: Sequence[UAV],
    nests: Sequence[Nest] | None = None,
    dem: DEMTerrain | None = None,
    estimator: VerticalSectionCostEstimator | None = None,
    nest_offset: int = 1_000_000,
    store_details: bool = False,
    task_weight_from_reward: bool = False,
) -> CostMatrixBuildResult:
    """用垂直切面估算填充 pairwise，并返回 CostProvider。"""
    nests = list(nests or [])
    avail = [u for u in uavs if u.is_available]
    est = estimator or VerticalSectionCostEstimator(dem_terrain=dem)

    pairwise: dict[tuple[int, int], float] = {}
    details: dict[tuple[int, int], CostEstimationResult] = {}

    n_t = len(tasks)
    n_u = len(avail)
    uav_target = np.zeros((n_u, n_t))
    task_task = np.zeros((n_t, n_t))

    def weight_of(task: Task) -> float:
        if task_weight_from_reward:
            return max(float(task.reward), 1e-6)
        return 1.0

    # UAV -> Task
    for i, u in enumerate(avail):
        u_key = -(1 + u.id)
        for j, t in enumerate(tasks):
            res = est.estimate(u.position, t.position, weight=weight_of(t))
            pairwise[(u_key, t.id)] = res.cost
            uav_target[i, j] = res.cost
            if store_details:
                details[(u_key, t.id)] = res

    # Task -> Task
    for i, ti in enumerate(tasks):
        for j, tj in enumerate(tasks):
            if i == j:
                task_task[i, j] = 0.0
                pairwise[(ti.id, tj.id)] = 0.0
                continue
            res = est.estimate(ti.position, tj.position, weight=weight_of(tj))
            pairwise[(ti.id, tj.id)] = res.cost
            task_task[i, j] = res.cost
            if store_details:
                details[(ti.id, tj.id)] = res

    # Task -> Nest / UAV start -> Nest（返航）
    # 同时落成矩阵，供终止基因在「末任务 → 机巢」代价行上做匹配
    n_b = len(nests)
    task_nest = np.zeros((n_t, n_b))
    uav_nest = np.zeros((n_u, n_b))

    for jn, n in enumerate(nests):
        n_key = nest_offset + n.id
        for i, t in enumerate(tasks):
            res = est.estimate(t.position, n.position, weight=1.0)
            pairwise[(t.id, n_key)] = res.cost
            task_nest[i, jn] = res.cost
            if store_details:
                details[(t.id, n_key)] = res
        for i, u in enumerate(avail):
            u_key = -(1 + u.id)
            res = est.estimate(u.position, n.position, weight=1.0)
            pairwise[(u_key, n_key)] = res.cost
            uav_nest[i, jn] = res.cost
            if store_details:
                details[(u_key, n_key)] = res

    provider = build_cost_provider(
        tasks, nests, avail, pairwise=pairwise, nest_offset=nest_offset
    )
    # 用估算 pairwise 覆盖
    provider.pairwise = pairwise
    return CostMatrixBuildResult(
        provider=provider,
        uav_target=uav_target,
        task_task=task_task,
        task_nest=task_nest,
        uav_nest=uav_nest,
        n_nests=n_b,
        details=details,
    )
