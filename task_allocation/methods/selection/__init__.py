# -*- coding: utf-8 -*-
"""第一阶段：动态任务选择（论文 §3，3.1–3.5）。

流水线：基础优先级 → 能力上界 cap(t) → 候选预筛选 n_cand（与 cap 解耦，
含事件驱动 / 防饥饿强制纳入）→ 初步可行性过滤 → 边际收益增量选择
（n_sel 卡 cap）→ 候选集检查 → 淘汰原因诊断
"""

from task_allocation.methods.selection.pipeline import SelectionConfig, run
from task_allocation.methods.selection.priority import PriorityConfig, PriorityWeights
from task_allocation.methods.selection.capability import CapabilityScaleConfig
from task_allocation.methods.selection.feasibility import FeasibilityConfig
from task_allocation.methods.selection.marginal_gain import MarginalConfig

__all__ = [
    "run",
    "SelectionConfig",
    "PriorityConfig",
    "PriorityWeights",
    "CapabilityScaleConfig",
    "FeasibilityConfig",
    "MarginalConfig",
]
