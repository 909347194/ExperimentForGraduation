# -*- coding: utf-8 -*-
"""跨层编排层：任务分配 × 轨迹规划，按周期逐段推进，带事实回灌与硬不可行剔除。

不含算法——只做两侧编排与消息契约。依赖方向单向：

    orchestration ──► path_planning.api（下层唯一门面）
                └─► task_allocation（上层求解入口）

    task_allocation ✗ path_planning，path_planning ✗ task_allocation，
    两侧均不得 import 本包

**与旧设计的不同**：不再有 ``path_planning/interface/`` —— 下层门面就是顶层
``api.py::PathPlanner``；环只认它的 ``plan_fleet / busy_until / prune``。

**闭环口径（v3，事实回灌）**：下层逐段报事实（``LegCostRecord``），环只在最终交付
解里出现**几何不可行**的边时做**一次**剔除式重解（``max_in_cycle=1``），最后只提交
最终版。不做在线估计校准、不做边覆盖、不做跨周期代价缓存——上层那把尺子只负责排序，
不负责精确；"旧 OD 的 实测/估计 比"在任务点逐周期变化时没有可迁移对象。

协议、端口、适配器见 README.md 与 ``docs/in_cycle_resolve.md``。
"""

from .feedback import (
    INFEASIBLE_CODES,
    build_cost_update,
    infeasible_edges_of,
    nest_ended_edges_of,
)
from .orchestrator import LoopDriver
from .protocol import (
    AllocatorPort,
    CostUpdate,
    CyclePlan,
    CycleSnapshot,
    LoopTrace,
    PlannerPort,
    PlannerReport,
    ResolveAttempt,
    TourOrder,
    TourStop,
    TourTicket,
)

__all__ = [
    "AllocatorPort",
    "CostUpdate",
    "CyclePlan",
    "CycleSnapshot",
    "INFEASIBLE_CODES",
    "LoopDriver",
    "LoopTrace",
    "PlannerPort",
    "PlannerReport",
    "ResolveAttempt",
    "TourOrder",
    "TourStop",
    "TourTicket",
    "build_cost_update",
    "infeasible_edges_of",
    "nest_ended_edges_of",
]
