# -*- coding: utf-8 -*-
"""muas.base — 求解器统一接口。

主算法与 baselines 共用 BaseOptimizer / SolverResult，
保证对比实验输出口径一致。
"""

from .base_optimizer import BaseOptimizer, SolverResult

__all__ = ["BaseOptimizer", "SolverResult"]
