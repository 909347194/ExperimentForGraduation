# -*- coding: utf-8 -*-
"""greedy.py — 贪心分配（占位）。

职责（实现后）：
    按代价或综合优先级贪心匹配 UAV 与任务，作为简单上界/对照。

约定：
    - 类名 GreedySolver，name == "greedy"
    - 继承 BaseOptimizer，返回 SolverResult
    - 无迭代：cost_history=[]，total_generations=0

状态：planned（仅规范占位，不含算法逻辑）
"""

from __future__ import annotations

# from task_allocation.methods.muas.base import BaseOptimizer, SolverResult
#
# class GreedySolver(BaseOptimizer):
#     @property
#     def name(self) -> str:
#         return "greedy"
#
#     def solve(self, cost_matrix, n_uavs, n_targets, **kwargs) -> SolverResult:
#         ...
