# -*- coding: utf-8 -*-
"""约束集合（论文 §3.3 / §4.3 / §5(4) 约束处理）。

把约束判定与惩罚从 ``problem.check_feasible`` 拆出，形成可单测的独立模块；
规划期（软约束，进 penalty）与落地期（硬约束，拒绝并告警）共用同一份判定，
避免两道校验的口径漂移（见 ``docs/design_nest_state.md`` §6）。

三类约束（参考借鉴 Experiments-For-Dissertation 的 single / coop / evaluator 结构，
并按本 MUAS 模型裁剪）：

    single_constraints  单机飞行性能约束：航程、飞行时间（可选）
    coop_constraints    任务侧协同约束：时间窗、时序（可选）、同时到达（本模型 N/A）
    nest_capacity      机巢容量 / 可用性约束（终点机巢决策 z_ub 的硬约束）
    evaluator          以上三类违背量的统一聚合（ConstraintReport）

``problem.check_feasible`` 调用这些模块并把结果汇入 ``ConstraintReport``，
其 ``as_details()`` 透出 ``{violations, penalty, nest_capacity}`` 供滚动层与指标消费。
"""

from .nest_capacity import (
    CapacityReport,
    check_nest_capacity,
    choose_end_nests,
    fleet_occupancy,
    free_slots,
    nest_occupancy,
)
from .single_constraints import (
    SingleConstraintViolation,
    check_range_constraint,
    check_time_constraint,
)
from .coop_constraints import (
    CoopConstraintViolation,
    check_sequence_constraint,
    check_sync_constraint,
    check_time_window_constraint,
)
from .evaluator import ConstraintReport

__all__ = [
    # nest_capacity
    "CapacityReport",
    "check_nest_capacity",
    "choose_end_nests",
    "fleet_occupancy",
    "free_slots",
    "nest_occupancy",
    # single_constraints
    "SingleConstraintViolation",
    "check_range_constraint",
    "check_time_constraint",
    # coop_constraints
    "CoopConstraintViolation",
    "check_sequence_constraint",
    "check_sync_constraint",
    "check_time_window_constraint",
    # evaluator
    "ConstraintReport",
]
