# -*- coding: utf-8 -*-
"""exp01_smoke 图组：**process 层——运行过程的观测与演化**。

放进本层的判断标准：**随滚动闭环推进而变化**，用来回答「这一轮是怎么回事」。
离开了具体的这一次运行，这些图就失去意义（这正是与 ``scene`` 层的分界）。

层内按 topic 可再细分为四小类（见 ``plot/registry.py`` 的 ``TOPICS``）：

    mechanism   机制与水位诊断 —— cycle_metrics / task_flow / selection_funnel
    allocation  单周期分配结果 —— allocation_gantt（结构）/ assignment_map（空间）
    spatial     逐周期空间演化 —— geo_evolution / geo_task_lifecycle /
                                  geo_fleet_transition
    solver      求解器内部行为 —— convergence

这四类是**语义标签而非目录**：它们共用同一套地理/数值底座，再拆一层目录只会
让 import 路径变长而没有收益。
"""

from .allocation_gantt import plot_allocation_gantt
from .assignment_map import plot_assignment_map
from .convergence import plot_convergence
from .cycle_metrics import plot_cycle_metrics
from .geo_evolution import plot_geo_evolution
from .geo_fleet_transition import plot_geo_fleet_transition
from .geo_task_lifecycle import plot_geo_task_lifecycle
from .selection_funnel import plot_selection_funnel
from .task_flow import plot_task_flow

__all__ = [
    "plot_allocation_gantt",
    "plot_assignment_map",
    "plot_convergence",
    "plot_cycle_metrics",
    "plot_geo_evolution",
    "plot_geo_fleet_transition",
    "plot_geo_task_lifecycle",
    "plot_selection_funnel",
    "plot_task_flow",
]
