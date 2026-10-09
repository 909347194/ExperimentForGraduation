# -*- coding: utf-8 -*-
"""第二阶段：选择性多机巢 MUAS（模型 + 求解）。

problem 回答：任务选择、分配、顺序、终点机巢的关系与目标是什么。
"""

from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
    build_cost_provider,
    make_fitness_evaluator,
)

__all__ = [
    "MUASProblemConfig",
    "SelectiveMUASProblem",
    "build_cost_provider",
    "make_fitness_evaluator",
]
