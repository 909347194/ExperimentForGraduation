# -*- coding: utf-8 -*-
"""muas_stage.py — 第二阶段编排门面（兼容层，2026-10-27 拆分）。

职责边界：

    DMDESolver            只负责**搜索**：在连续空间做差分进化，产出
                          ``best_assignment``（局部下标的 (uav_idx, task_idx) 序列）
    SelectiveMUASProblem  只负责**问题定义与评价**：y / x / π / z、F1 / F2、约束惩罚
    第二阶段编排           负责**两者之间的翻译**与选择性决策

翻译内容（对应论文 §4.2 决策变量）：

    assignment ──► π_u  任务访问顺序（保留 DMDE 给出的基因顺序）
                ──► y_j  任务是否执行（默认执行，可因代价过高置 0）
                ──► z_ub 终点机巢（内层精确分配，见 constraints.solve_end_nest_assignment）
                ──► x_uj 由 π_u 与 y_j 导出（一任务一机，自动去重）

关于模型类型（N/M 关系）：

    DMDE 按 ``n_uavs`` 与 ``n_targets`` 的大小自动选模型：
      N == M → balanced（一一对应，无巡游）
      N  > M → overloaded（UAV 不重复、**任务可重复**）
      N  < M → srp（UAV 巡游，任务不重复）

    本问题要求「一任务一机」，因此 **overloaded 不可用**（它会把同一任务
    分给多架 UAV）。故参与规划的 UAV 数 K 恒小于任务数 N（即 srp 巡游模型），
    其余可用 UAV 本周期保持空闲 —— 这正是论文允许的 ``U_t^exec ⊆ U_t^avail``。

模块地图（2026-10-27 拆分；本模块保留全部既有导入路径，逐字节兼容）：

    stage_config.py        MUASStageConfig / POLICY_*（编排参数）
    participating.py       select_participating_uavs 及选机策略
    tour_cost.py           TourCostMixin（航次代价 / ETA / 时间窗原语）
    decode_repair.py       DecodeRepairMixin（repair_chain 解码与修复算子组）
    decode_constructive.py ConstructiveDecodeMixin（构造式解码，exp04 B1）
    route_polish.py        RoutePolishMixin（交付解成本 polish 算子组）
    evaluator.py           MUASAssignmentEvaluator / EvalResult（评价核心）
    stage_runner.py        MUASStageResult / run_selective_muas / 枚举参照

    结构语义：``build_solution`` 的修复链不是补丁堆叠，而是**按固定管线
    调用的修复算子组**（各算子定义与接受纪律见 decode_repair.py 与
    route_polish.py 的 docstring）。
"""

from task_allocation.methods.muas.solvers.evaluator import (
    EvalResult,
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.local_search import (
    DEFAULT_MOVES,
    MOVE_POSITION,
    MOVE_RELOCATE,
    MOVE_SWAP,
    PolishResult,
    polish_assignment,
)
from task_allocation.methods.muas.solvers.participating import (
    _argmin_share,
    _capacity_served_count,
    _covering_cost,
    _greedy_demand_coverage,
    _resolve_tour_cap,
    _select_by_lexicographic_marginal,
    _select_by_marginal_takeoff,
    select_participating_uavs,
)
from task_allocation.methods.muas.solvers.stage_config import (
    POLICY_CHEAPEST,
    POLICY_DEMAND,
    POLICY_DEMAND_MARGINAL,
    POLICY_FIRST,
    POLICY_MARGINAL,
    MUASStageConfig,
)
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
