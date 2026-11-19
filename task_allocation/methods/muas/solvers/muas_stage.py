# -*- coding: utf-8 -*-
"""muas_stage.py — 第二阶段选择性 MUAS 编排门面。

职责：串联选机、评价器与求解器，把搜索产生的 assignment 翻译为任务选择/分配/访问顺序/
终点机巢，完成选择性决策并返回阶段结果。
边界：只做编排与翻译；搜索由 dmde_solver 负责，问题定义与评价由 problem 负责。
相关：muas/solvers/stage_config.py、participating.py、tour_cost.py、decode_repair.py、
decode_constructive.py、route_polish.py、evaluator.py、stage_runner.py。"""

from task_allocation.methods.muas.solvers.evaluator import (
    EvalResult,
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.local_search import polish_assignment
from task_allocation.methods.muas.solvers.participating import select_participating_uavs
from task_allocation.methods.muas.solvers.stage_config import MUASStageConfig
from task_allocation.methods.muas.solvers.stage_runner import (
    MUASStageResult,
    enumerate_reference_participating,
    run_selective_muas,
)

__all__ = [
    "MUASStageConfig",
    "MUASStageResult",
    "EvalResult",
    "MUASAssignmentEvaluator",
    "select_participating_uavs",
    "run_selective_muas",
    "polish_assignment",
    "enumerate_reference_participating",
]
