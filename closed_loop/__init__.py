# -*- coding: utf-8 -*-
"""跨层集成层：任务分配 × 轨迹规划（update–reassign–replan 的环）。

不含算法——只做两侧编排与消息契约。依赖方向单向：

    closed_loop ──► path_planning.api（下层唯一门面）
                └─► task_allocation（上层求解入口）

    task_allocation ✗ path_planning，path_planning ✗ task_allocation，
    两侧均不得 import 本包

**与旧设计的不同**：不再有 ``path_planning/interface/`` —— 下层门面就是顶层
``api.py::PathPlanner``；环只认它的 ``plan_fleet / busy_until / prune``。
协议、端口、适配器与现状的差异见 README.md。
"""

from .orchestrator import LoopDriver
from .protocol import (
    AllocatorPort,
    CyclePlan,
    CycleSnapshot,
    LoopTrace,
    PlannerPort,
    PlannerReport,
    TourOrder,
    TourStop,
    TourTicket,
)

__all__ = [
    "AllocatorPort",
    "CyclePlan",
    "CycleSnapshot",
    "LoopDriver",
    "LoopTrace",
    "PlannerPort",
    "PlannerReport",
    "TourOrder",
    "TourStop",
    "TourTicket",
]
