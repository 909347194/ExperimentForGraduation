# -*- coding: utf-8 -*-
"""约束集合（论文 §5(4) 约束处理）。

把约束判定与惩罚从 ``problem.check_feasible`` 拆出，形成可单测的独立模块：
规划期（软约束，进 penalty）与落地期（硬约束，拒绝并告警）共用同一份判定，
避免两道校验的口径漂移（见 ``docs/design_nest_state.md`` §6）。
"""

from .nest_capacity import (
    CapacityReport,
    check_nest_capacity,
    choose_end_nests,
    fleet_occupancy,
    free_slots,
    nest_occupancy,
)

__all__ = [
    "CapacityReport",
    "check_nest_capacity",
    "choose_end_nests",
    "fleet_occupancy",
    "free_slots",
    "nest_occupancy",
]
