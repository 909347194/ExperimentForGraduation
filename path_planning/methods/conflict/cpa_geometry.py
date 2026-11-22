# -*- coding: utf-8 -*-
"""匀速直线对的闭式最近点（CPA）：纯函数、可对拍、无状态。

职责：给定两个匀速直线段（同起点时刻、同窗口）求相对距离最小点；给出两弦
      时间窗重叠范围；给出一对弦的重叠窗内最小距离。
边界：只做几何；不做证书判据、不做椭球变换、不读配置。
相关：``docs/conflict_model.md`` §5（闭式解推导与三支裁剪）、§7.2（阈值互补）、
      参考实现 ``utils/geometry.py:33 closest_approach_linear``。

**闭式解**：``r(u)=r0+vr·u``，``f(u)=‖r‖²=au²+bu+c``，``a=‖vr‖²``、``b=2r0·vr``。
``a>0`` 时极小点 ``u*=−b/(2a)``，再裁剪到 ``[0,Δt]`` 三支取值。``a≈0``（相对静止）
退化为 ``d=‖r0‖``——这不是异常，而是"同速同向时距离就是初始距离"的真实情况。

**夜里的坑**：``Δt`` 可能为 0（零长窗口），必须先把 ``dt=max(t1−t0, 0)``；
``min_sq`` 可能因浮点误差略负，必须 ``max(min_sq, 0)``。两者缺一个都会在
``sqrt`` 处返回 nan，而 nan 在比较里恒为假——会静默判"安全"。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from path_planning.common.plan_types import Vec3
from path_planning.methods.conflict.chord_sampling import MotionChord

__all__ = [
    "ClosestApproach",
    "closest_approach_linear",
    "overlap_window",
    "chord_pair_distance",
    "chord_pair_closest",
]

#: 相对速度平方低于此值即视为"相对静止"（m²/s²）
_STATIC_EPS = 1e-12


@dataclass(frozen=True)
class ClosestApproach:
    """单窗口 CPA 结果。

    Attributes:
        time_offset: 相对窗口起点的时刻（s），已裁剪到 ``[0,Δt]``
        distance:    该时刻两点的欧氏距离（m）
        at_start:    相对位置（窗口起点，即 ``r0``）
        relative_velocity: 相对速度（``vr``）
    """

    time_offset: float
    distance: float
    at_start: Vec3
    relative_velocity: Vec3

    @property
    def is_static_relative(self) -> bool:
        vx, vy, vz = self.relative_velocity
        return vx * vx + vy * vy + vz * vz <= _STATIC_EPS


def closest_approach_linear(
    p_a: Vec3,
    v_a: Vec3,
    p_b: Vec3,
    v_b: Vec3,
    dt: float,
) -> ClosestApproach:
    """两条匀速直线在 ``[0,dt]`` 内的最近点（闭式）。

    Args:
        p_a, v_a: A 的起点位置与速度
        p_b, v_b: B 的起点位置与速度
        dt:       窗口长度（s）；``<=0`` 时只判起点
    """
    pa = np.asarray(p_a, dtype=np.float64)
    pb = np.asarray(p_b, dtype=np.float64)
    va = np.asarray(v_a, dtype=np.float64)
    vb = np.asarray(v_b, dtype=np.float64)
    r0 = pa - pb
    vr = va - vb

    a = float(vr @ vr)
    b = float(2.0 * (r0 @ vr))
    c = float(r0 @ r0)
    hor = max(float(dt), 0.0)

    if a <= _STATIC_EPS:
        # 相对静止：距离恒为 ‖r0‖，与时刻无关
        u = 0.0
        min_sq = c
    else:
        u_star = -b / (2.0 * a)
        if u_star <= 0.0:
            u = 0.0
            min_sq = c
        elif u_star >= hor:
            u = hor
            min_sq = c + b * hor + a * hor * hor
        else:
            u = u_star
            min_sq = c - (b * b) / (4.0 * a)
    min_sq = max(min_sq, 0.0)
    return ClosestApproach(
        time_offset=float(u),
        distance=float(np.sqrt(min_sq)),
        at_start=(float(r0[0]), float(r0[1]), float(r0[2])),
        relative_velocity=(float(vr[0]), float(vr[1]), float(vr[2])),
    )


def overlap_window(
    a: MotionChord, b: MotionChord, *, eps_s: float = 0.0
) -> tuple[float, float] | None:
    """两弦的时间窗交集（含 ``eps_s`` 松弛），无交集返回 ``None``。

    ``eps_s`` 必须与证书判据同一口径（``max(time_eps, space_eps)``）：交集判据若
    比证书松，就会在临界带把"刚分开"的一对重新拉回来判，产生误报；若比证书严，
    就会漏掉真正重叠的一对。
    """
    lo = max(a.t0, b.t0)
    hi = min(a.t1, b.t1)
    if hi >= lo:
        return (lo, hi)
    # 间隙在 ε 以内：视为"刚好相接"，在间隙中点按零长窗口判一次
    if lo - hi <= eps_s:
        t = 0.5 * (lo + hi)
        return (t, t)
    return None


def chord_pair_closest(
    a: MotionChord, b: MotionChord, *, eps_s: float = 0.0
) -> ClosestApproach | None:
    """一对弦在重叠窗内的 CPA；无重叠返回 ``None``（**不是**判冲突）。"""
    win = overlap_window(a, b, eps_s=eps_s)
    if win is None:
        return None
    lo, hi = win
    ca = closest_approach_linear(
        a.position_at(lo), a.velocity, b.position_at(lo), b.velocity, hi - lo
    )
    return ca


def chord_pair_distance(a: MotionChord, b: MotionChord, *, eps_s: float = 0.0) -> float:
    """一对弦的最小距离（无重叠时返回 ``inf`` = 安全，统一 §7.4 口径）。"""
    ca = chord_pair_closest(a, b, eps_s=eps_s)
    return float("inf") if ca is None else ca.distance
