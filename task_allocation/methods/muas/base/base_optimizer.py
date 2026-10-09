# -*- coding: utf-8 -*-
"""base_optimizer.py — 求解器统一接口约定。

职责：
    定义主算法（DMDE）与所有对比算法必须遵守的统一接口，
    保证输出口径一致，便于 experiments 横向对比与复现。

约定：
    1. 凡进入对比表的求解器，均须继承 BaseOptimizer 并实现 name / solve。
    2. solve 的返回值必须是 SolverResult，字段含义固定，禁止私自增删必填字段。
    3. 扩展信息只放入 SolverResult.extra，不污染必填字段。
    4. 本模块只放接口与结果结构，不放任何具体算法逻辑。

对应：
    主算法：methods/muas/solvers/dmde_solver.py
    对比算法：task_allocation/baselines/
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class SolverResult:
    """求解器统一输出结构。

    Attributes:
        best_assignment:   最优分配方案，元素为 (uav_id, task_id) 或项目约定的三元组。
                           具体元素结构由 problem 层约定，对比时保持一致即可。
        best_fitness:      最优适应度（标量；多目标时取实验约定的标量化结果或主指标）。
        cost_history:      每代最优适应度历史（无迭代的方法可为空列表）。
        total_generations: 实际迭代代数（无迭代方法填 0）。
        elapsed_seconds:   求解耗时（秒）。
        solver_name:       求解器名称，须与 BaseOptimizer.name 一致。
        extra:             附加信息。建议 extra["solution"] = AllocationSolution（见 common.types）；
                           其它键（约束违反量等）自行约定并文档化。
    """

    best_assignment: list[tuple[int, int]]
    best_fitness: float
    cost_history: list[float]
    total_generations: int
    elapsed_seconds: float
    solver_name: str = "unknown"
    extra: dict[str, Any] = field(default_factory=dict)


class BaseOptimizer(ABC):
    """优化器抽象基类。

    主算法与 baselines 下所有对比求解器必须继承此类。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """求解器短名，用于配置、日志与结果文件命名（小写+下划线优先）。"""
        ...

    @abstractmethod
    def solve(
        self,
        cost_matrix: np.ndarray,
        n_uavs: int,
        n_targets: int,
        **kwargs: Any,
    ) -> SolverResult:
        """求解分配问题。

        Args:
            cost_matrix: 代价矩阵（由 muas/cost 或实验准备阶段提供）。
            n_uavs:      UAV 数量。
            n_targets:   任务/目标数量。
            **kwargs:    求解器特定参数；常见键：
                         - fitness_evaluator: 适应度评估回调
                         - problem: SelectiveMUASProblem 实例（可选）
                         - seed: 随机种子

        Returns:
            SolverResult，字段齐全、口径与主算法一致。
        """
        ...
