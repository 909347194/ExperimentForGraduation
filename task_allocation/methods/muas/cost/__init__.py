# -*- coding: utf-8 -*-
"""muas.cost — 三维地理近似航程代价（垂直切面法）。

忽略复杂飞行动力学限制，在 S–T 垂直切面上做地形跟随，
得到供任务选择与 MUAS 使用的近似三维航程代价。
"""

from task_allocation.methods.muas.cost.cost_matrix import (
    CostMatrixBuildResult,
    build_pairwise_costs,
)
from task_allocation.methods.muas.cost.dem_terrain import DEMMeta, DEMProfile, DEMTerrain
from task_allocation.methods.muas.cost.vertical_section import (
    CostEstimationResult,
    VerticalSectionCostEstimator,
)

__all__ = [
    "DEMTerrain",
    "DEMMeta",
    "DEMProfile",
    "VerticalSectionCostEstimator",
    "CostEstimationResult",
    "build_pairwise_costs",
    "CostMatrixBuildResult",
]
