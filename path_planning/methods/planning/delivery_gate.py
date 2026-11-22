# -*- coding: utf-8 -*-
"""交付闸：整条航迹在全域场上的稠密净空 + 全域真几何。

职责：回答"这条航迹能不能交付"——在**全域场**上按 0.2 m 稠密采样量净空（剔除端点豁免
      球内的点），再过一遍全域真几何（棱柱）。
边界：不排航迹、不改航迹、不做参数校验。
相关：``tour_planner.py``（组装完整条 trajectory 后立即调用）、
      ``experiments/<exp>/support/quality_report.py``（各实验私有，审计侧同一口径的"稠密净空"判据）。

**为什么需要它**：段内复核跑在各段自己的走廊窗口上（旋转栅格 + 窗口外观测值再插值），
窗口里的值比全域场平滑约 0.1 m，段内 2 m 采样又比 0.2 m 稠密采样系统性偏高。叠加后实测
"段内判 6.00 通过、全域 0.2 m 只有 5.88"（差 0.12 m）；段间接缝与任务点折返处的低点更是
直接落在窗口盲区里。实测 exp06 有一条航段在折返点上空 5.6 m，段内复核却判通过——段内可行
不等于整条可交付，故交付前必须按交付口径重验一次。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from path_planning.methods.planning.leg_segments import sampled_positions

if TYPE_CHECKING:  # 只为类型注解，避免与 tour_planner 形成循环导入
    from path_planning.common.plan_types import Trajectory
    from path_planning.methods.planning.tour_planner import TourPlanningContext

__all__ = ["DENSE_STEP_M", "dense_clearance", "global_gate"]

#: 交付净空口径的采样步长（与实验审计的"稠密净空"同源）
DENSE_STEP_M = 0.2


def dense_clearance(ctx: TourPlanningContext, traj: Trajectory) -> float:
    """整条航迹在全域场上的 0.2 m 稠密净空（剔除端点豁免球内的点）。"""
    pts = sampled_positions(traj, DENSE_STEP_M)
    sdf = np.asarray(ctx.field.at(pts), dtype=float)
    radius = float(ctx.endpoint_exempt_radius_m)
    if radius > 0.0 and sdf.size:
        for end in (traj.start_position, traj.end_position):
            near = np.linalg.norm(pts - np.asarray(end, dtype=float), axis=1) <= radius
            sdf = np.where(near, np.inf, sdf)
    return float(sdf.min()) if sdf.size else float("inf")


def global_gate(
    ctx: TourPlanningContext, traj: Trajectory, prisms: Any = None
) -> tuple[float, str | None]:
    """交付闸，返回 ``(最差净空, 失败说明)``；说明为 None 即通过。"""
    limit = float(ctx.params.clearance_m)
    worst = dense_clearance(ctx, traj)
    if worst < limit - 1e-6:
        return worst, f"稠密净空 {worst:.2f} m < {limit} m"
    if prisms is not None:
        step = float(ctx.params.clearance_sample_step_m)
        clear = float(prisms.min_clearance(sampled_positions(traj, step), step))
        if clear < limit - 1e-6:
            return min(worst, clear), f"真几何净空 {clear:.2f} m < {limit} m"
    return worst, None
