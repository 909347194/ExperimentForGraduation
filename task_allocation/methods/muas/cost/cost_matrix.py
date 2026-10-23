# -*- coding: utf-8 -*-
"""cost_matrix.py — 构建任务/UAV/机巢节点间三维近似代价矩阵。

对接 problem.CostProvider：pairwise[(id_a,id_b)] = cost
UAV 起点虚拟键：-(1+uav_id)；机巢键：nest_offset + nest_id

两种建表口径（2026-10-20）：

- **eager（默认）**：对传入的 tasks 全量建 O(n²) 表，适合第二阶段
  （候选集已小）与测试基线；
- **惰性源**（``pairwise_source=``）：pairwise 值从
  :class:`~task_allocation.methods.muas.cost.lazy_pairwise.LazyPairwiseCost`
  按需读/算并共享缓存，只对**传入的 tasks**（候选子集）建 4 个矩阵。
  滚动层据此把全量 O(n²) 建矩阵从第一阶段之前挪到第一阶段之后、
  且只覆盖候选集——exp02 实测 N=200 端到端因此 11391→~1700 ms。
  值与 eager 全量建表逐位一致（同估算器/同坐标/同方向）。
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import numpy as np

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
from task_allocation.methods.muas.cost.lazy_pairwise import LazyPairwiseCost
from task_allocation.methods.muas.cost.vertical_section import (
    CostEstimationResult,
    VerticalSectionCostEstimator,
    make_distance_fn,
)
from task_allocation.methods.muas.cost.provider import CostProvider, build_cost_provider


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
    # 统一距离口径：由同一个估算器派生的 DEM 三维航程（带记忆化）。
    # 评价端航迹时间 F3、时间窗 ETA、第一阶段 travel/插入代价都从它取长度，
    # 杜绝「代价矩阵走地形、ETA 走直线」的口径分裂（P1-1）。
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None


def build_pairwise_costs(
    tasks: Sequence[Task],
    uavs: Sequence[UAV],
    nests: Sequence[Nest] | None = None,
    dem: DEMTerrain | None = None,
    estimator: VerticalSectionCostEstimator | None = None,
    nest_offset: int = 1_000_000,
    store_details: bool = False,
    task_weight_from_reward: bool = False,
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
    pairwise_source: LazyPairwiseCost | None = None,
) -> CostMatrixBuildResult:
    """用垂直切面估算填充 pairwise，并返回 CostProvider。

    Args:
        distance_fn: 统一距离口径。缺省时由 ``estimator`` 派生（带记忆化），
            保证 pairwise 代价与 ETA / F3 同源；无 DEM 时退化为三维直线。
        pairwise_source: 惰性 pairwise 表（:func:`make_lazy_pairwise` 产物）。
            非 None 时 pairwise 值从该表按需读/算并共享缓存（一阶段已算过
            的边直接命中），返回的 ``provider.pairwise`` **就是**该活视图；
            与全量 eager 建表逐位一致。此时 ``store_details`` 被忽略
            （无 CostEstimationResult 对象可存；惰性口径不消费明细）。
            典型用法：滚动层一阶段前建表 → 选择 → 只对候选子集调本函数。
    """
    nests = list(nests or [])
    avail = [u for u in uavs if u.is_available]
    est = estimator or VerticalSectionCostEstimator(dem_terrain=dem)
    if distance_fn is None:
        distance_fn = make_distance_fn(est)
    details: dict[tuple[int, int], CostEstimationResult] = {}
    if pairwise_source is not None:
        # 惰性口径：pairwise 即活视图，值按需解析；不产明细
        store_details = False
        pairwise: MutableMapping[tuple[int, int], float] = pairwise_source
    else:
        pairwise = {}

    def weight_of(task: Task) -> float:
        if task_weight_from_reward:
            return max(float(task.reward), 1e-6)
        return 1.0

    def pair_cost(
        key: tuple[int, int],
        pa: Sequence[float],
        pb: Sequence[float],
        weight: float,
    ) -> float:
        """单条有序边代价：惰性源命中/现算，否则本地 est.estimate。"""
        if pairwise_source is not None:
            return float(pairwise_source[key])
        res = est.estimate(pa, pb, weight=weight)
        if store_details:
            details[key] = res
        return float(res.cost)

    n_t = len(tasks)
    n_u = len(avail)
    uav_target = np.zeros((n_u, n_t))
    task_task = np.zeros((n_t, n_t))

    # UAV -> Task
    for i, u in enumerate(avail):
        u_key = -(1 + u.id)
        for j, t in enumerate(tasks):
            c = pair_cost((u_key, t.id), u.position, t.position, weight_of(t))
            pairwise[(u_key, t.id)] = c
            uav_target[i, j] = c

    # Task -> Task
    for i, ti in enumerate(tasks):
        for j, tj in enumerate(tasks):
            if i == j:
                task_task[i, j] = 0.0
                pairwise[(ti.id, tj.id)] = 0.0
                continue
            c = pair_cost((ti.id, tj.id), ti.position, tj.position, weight_of(tj))
            pairwise[(ti.id, tj.id)] = c
            task_task[i, j] = c

    # Task -> Nest / UAV start -> Nest（返航）
    # 同时落成矩阵，供终止基因在「末任务 → 机巢」代价行上做匹配
    n_b = len(nests)
    task_nest = np.zeros((n_t, n_b))
    uav_nest = np.zeros((n_u, n_b))

    for jn, n in enumerate(nests):
        n_key = nest_offset + n.id
        for i, t in enumerate(tasks):
            c = pair_cost((t.id, n_key), t.position, n.position, 1.0)
            pairwise[(t.id, n_key)] = c
            task_nest[i, jn] = c
        for i, u in enumerate(avail):
            u_key = -(1 + u.id)
            c = pair_cost((u_key, n_key), u.position, n.position, 1.0)
            pairwise[(u_key, n_key)] = c
            uav_nest[i, jn] = c

    provider = build_cost_provider(
        tasks,
        nests,
        avail,
        pairwise=pairwise,
        nest_offset=nest_offset,
        distance_fn=distance_fn,
    )
    # 用估算 pairwise 覆盖（惰性源时即活视图本身）
    provider.pairwise = pairwise
    return CostMatrixBuildResult(
        provider=provider,
        uav_target=uav_target,
        task_task=task_task,
        task_nest=task_nest,
        uav_nest=uav_nest,
        n_nests=n_b,
        details=details,
        distance_fn=distance_fn,
    )
