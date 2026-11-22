# -*- coding: utf-8 -*-
"""航段原语：退化航段（纯竖向）、停靠悬停段、沿弧长采样。

职责：提供与"单段规划"无关、但每段装配都要用的三个小构件。
边界：不做走廊 / RRT / NLP，只造 ``HermiteSegment`` 或采点。
相关：``planning/tour_planner.py``（唯一消费者）、``timeline/splines.py``（求值口径）。

**为什么单独成文件**：这三件事都有"顺手写进主流程就会写错"的口径——纯竖向段的
速度上限是 ``v_up``/``v_dn`` 而不是合速度、悬停段必须标 ``is_hover`` 才计入悬停、
真几何采样必须沿弧长而不是取节点。放在一处，改口径只改一次。
"""

from __future__ import annotations

import numpy as np

from path_planning.common.plan_types import HermiteSegment, Trajectory, TimeSource
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.timeline.splines import segment_position

__all__ = ["vertical_leg", "hover_segment", "sampled_positions"]

#: 竖向退化段的时长下限（秒），避免除零
_MIN_VERTICAL_S = 0.5


def vertical_leg(
    uav_id: int, vehicle: VehicleSpec, start: np.ndarray, goal: np.ndarray, t0: float
) -> Trajectory:
    """同一水平位置的上/下段：无走廊轴向，按纯竖向匀速段处理。

    为什么不做"先横移再爬"：起终点水平重合意味着这条航段本来没有水平自由度，
    给横向偏移会造出没有物理意义的绕行。竖向速度取 ``v_up`` / ``v_dn`` 各自的限制，
    **不能**用合速度上限——那会让下降段以 15 m/s 掉高。
    """
    dz = float(goal[2] - start[2])
    speed = vehicle.v_up_mps if dz > 0.0 else vehicle.v_dn_mps
    dt = max(abs(dz) / max(float(speed), 1e-6), _MIN_VERTICAL_S)
    v = (0.0, 0.0, dz / dt)
    seg = HermiteSegment(
        t0=float(t0), t1=float(t0) + dt,
        p0=tuple(float(x) for x in start), p1=tuple(float(x) for x in goal),
        v0=v, v1=v,
    )
    return Trajectory(
        uav_id=int(uav_id), segments=(seg,), path_len_m=abs(dz),
        time_source=TimeSource.TRAJECTORY,
    )


def hover_segment(
    position: np.ndarray, t0: float, service_time_s: float
) -> tuple[HermiteSegment, float]:
    """停靠点悬停段：位置不变、零速，时长 = 服务时长。

    ``is_hover=True`` 是硬要求：``Trajectory.hover_duration`` 只累加这一段标记，
    漏标的后果是"服务时长被算成飞行时长"，续航预算从此偏严而没人看得出原因。
    """
    p = tuple(float(x) for x in position)
    seg = HermiteSegment(
        t0=float(t0), t1=float(t0) + float(service_time_s),
        p0=p, p1=p, v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0), is_hover=True,
    )
    return seg, float(t0) + float(service_time_s)


def sampled_positions(traj: Trajectory, step_m: float) -> np.ndarray:
    """沿每段按弧长步长采点（真几何闸用）。

    不能只取节点：``n_nodes=60`` 时 5 km 航段的节点间距近 80 m，节点间直接连线会
    "穿过"曲线本该绕开的棱柱，真几何闸反而变成漏检。与 ``PrismClearance`` 内部的
    再插值同一口径。
    """
    step = max(float(step_m), 1e-6)
    chunks: list[np.ndarray] = []
    for seg in traj.segments:
        n = max(2, int(np.ceil(seg.chord_m / step)) + 1)
        chunks.append(segment_position(seg, np.linspace(0.0, 1.0, n)))
    return np.vstack(chunks) if chunks else np.zeros((0, 3))
