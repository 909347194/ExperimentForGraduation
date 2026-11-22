# -*- coding: utf-8 -*-
"""编排层：单航次全流程（走廊 → RRT → NLP → 复核 → 真几何 → 预算）。

职责：把各层的"原子操作"按固定次序串成一条 tour，并装配退化段与悬停段。
边界：不承担对外契约（``api.py``）、不做多机协调（``coordination``）、不写 Registry。
相关：``docs/architecture.md`` §1、改造方案 §3.2。

本包文件分工：

* ``tour_planner.py``  单航次编排（逐段走廊/RRT/NLP/复核/净空/预算）
* ``leg_segments.py``  航段原语（纯竖向段、悬停段、沿弧长采点）
"""

from path_planning.methods.planning.leg_segments import (
    hover_segment,
    sampled_positions,
    vertical_leg,
)
from path_planning.methods.planning.tour_planner import TourPlanningContext, plan_tour

__all__ = [
    "TourPlanningContext",
    "plan_tour",
    "hover_segment",
    "sampled_positions",
    "vertical_leg",
]
