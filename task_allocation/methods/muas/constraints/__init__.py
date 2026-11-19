# -*- coding: utf-8 -*-
"""约束包：单机性能、任务协同、机巢容量三类约束判定与违背聚合。

职责：把约束判定从 problem.check_feasible 拆出为可单测模块；规划期软约束与落地期硬约束
共用同一份判定。
相关：single_constraints.py（单机）、coop_constraints.py（协同）、nest_capacity.py（机巢）、
evaluator.py（聚合为 ConstraintReport）；调用方为 muas/problem.py。"""

from .nest_capacity import (
    CapacityReport,
    check_nest_capacity,
    choose_end_nests,
    fleet_occupancy,
    free_slots,
    nest_occupancy,
    solve_end_nest_assignment,
)
from .single_constraints import (
    SingleConstraintViolation,
    check_range_constraint,
    check_time_constraint,
)
from .coop_constraints import (
    CoopConstraintViolation,
    arrival_times,
    check_sequence_constraint,
    check_sync_constraint,
    check_time_window_constraint,
    sequence_violation_pairs,
    task_window,
)
from .evaluator import ConstraintReport

__all__ = [
    # nest_capacity
    "CapacityReport",
    "check_nest_capacity",
    "choose_end_nests",
    "solve_end_nest_assignment",
    "fleet_occupancy",
    "free_slots",
    "nest_occupancy",
    # single_constraints
    "SingleConstraintViolation",
    "check_range_constraint",
    "check_time_constraint",
    # coop_constraints
    "CoopConstraintViolation",
    "arrival_times",
    "check_sequence_constraint",
    "check_sync_constraint",
    "check_time_window_constraint",
    "sequence_violation_pairs",
    "task_window",
    # evaluator
    "ConstraintReport",
]
