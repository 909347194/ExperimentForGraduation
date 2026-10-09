# -*- coding: utf-8 -*-
"""第一阶段：动态任务选择（论文 3.2）。

流水线：基础优先级 → 初步可行性过滤 → 边际收益增量选择 → 候选集检查
"""

from task_allocation.methods.selection.pipeline import SelectionConfig, run
from task_allocation.methods.selection.priority import PriorityConfig, PriorityWeights
from task_allocation.methods.selection.feasibility import FeasibilityConfig
from task_allocation.methods.selection.marginal_gain import MarginalConfig

__all__ = [
    "run",
    "SelectionConfig",
    "PriorityConfig",
    "PriorityWeights",
    "FeasibilityConfig",
    "MarginalConfig",
]
