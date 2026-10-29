# -*- coding: utf-8 -*-
"""链路飞行时间代价（动力学预计算，Lin2021 §2.2.2）。

4 种虚拟链路状态组合的最小飞行时间（Lin2021 Eq.(1)-(4)）：

    sA=0, sB=0    停→停：加-匀-减 或 纯加减三角形（Eq.1）
    sA=0, sB≠0    停→通：Eq.(2)
    sA≠0, sB=0    通→停：Eq.(3)
    sA≠0, sB≠0    通→通：D / v_u（Eq.4）

沿路径最大加速度由升力限幅 [F_min,F_max] 与重力合成（Fig.10）。令净力沿飞行方向：

    a = −g·u_z + sqrt( (F/m)² − g²·L²/D² )，u_z = (z_B−z_A)/D

再叠加"升力竖直分量非负" F_z = m·a·u_z + mg ≥ 0，分出四种情形：

    上升 u_z ≥ 0            F = F_max，a = sqrt(...) − g·u_z
    缓降 r ≤ thr1           F = F_max，a = sqrt(...) + g·r
    中降 thr1 < r ≤ thr2    F = |G|·L/H，a = sqrt((F/m)²+g²) = g·D/H
    陡降 r > thr2           F = F_min，a = g·r − sqrt((F_min/m)² − g²L²/D²)

其中 r = |u_z| = H/D，thr = |G|/sqrt(|G|²+F²)。限速 v_u 由 v_max/v_up/v_down
与航段俯仰决定（§2.2.2）。

不负责：TPR 项（methods/tpr/）、加权合成（composite.py）。
"""

from __future__ import annotations

import math
from typing import Sequence

from path_planning.common.config import UAVParams


def max_accel(D: float, L: float, u_z: float, uav: UAVParams) -> float:
    """沿飞行方向的最大加速度 a（u_z = (z_B−z_A)/D，A→B 方向）。"""
    g, m, fmax, fmin = uav.g, uav.mass, uav.f_max, uav.f_min
    G = m * g
    if D <= 1e-9:
        return 1e-3
    base = math.sqrt(max((fmax / m) ** 2 - (g * L / D) ** 2, 0.0))
    if u_z >= 0.0:
        a = base - g * u_z
    else:
        r = -u_z
        thr1 = G / math.sqrt(G * G + fmax * fmax)
        thr2 = G / math.sqrt(G * G + fmin * fmin)
        if r <= thr1:
            a = base + g * r
        elif r <= thr2:
            a = g / r  # 与原文 sqrt((|G|L/H/m)² + g²) 等价（= g·D/H）
        else:
            a = g * r - math.sqrt(max((fmin / m) ** 2 - (g * L / D) ** 2, 0.0))
    return max(a, 1e-3)


def max_speed(D: float, dz: float, uav: UAVParams) -> float:
    """方向限速 v_u（§2.2.2）。"""
    if abs(dz) < 1e-9:
        return uav.v_max
    H = abs(dz)
    if dz > 0:  # 爬升
        return min(uav.v_max, uav.v_up * D / H)
    return min(uav.v_max, uav.v_down * D / H)  # 下降


def link_time(
    p_a: Sequence[float],
    p_b: Sequence[float],
    s_a: int,
    s_b: int,
    uav: UAVParams,
) -> tuple[float, float]:
    """虚拟链路飞行时间（Eq.1-4）与三维长度 D。返回 (time, D)。"""
    ax, ay, az = float(p_a[0]), float(p_a[1]), float(p_a[2])
    bx, by, bz = float(p_b[0]), float(p_b[1]), float(p_b[2])
    D = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2 + (bz - az) ** 2)
    if D < 1e-9:
        return 0.0, 0.0
    L = math.hypot(bx - ax, by - ay)
    dz = bz - az
    u_z = dz / D

    v_u = max_speed(D, dz, uav)
    a_ab = max_accel(D, L, u_z, uav)   # 加速（A→B）
    a_ba = max_accel(D, L, -u_z, uav)  # 减速（等价 B→A 方向加速）

    if s_a == 0 and s_b == 0:  # Eq.(1)
        d_need = (1.0 / a_ab + 1.0 / a_ba) * v_u * v_u / 2.0
        t = (
            v_u / a_ab + v_u / a_ba + (D - d_need) / v_u
            if D >= d_need
            else math.sqrt(2.0 * D * (1.0 / a_ab + 1.0 / a_ba))
        )
    elif s_a == 0 and s_b != 0:  # Eq.(2)
        d_need = v_u * v_u / (2.0 * a_ab)
        t = v_u / a_ab + (D - d_need) / v_u if D >= d_need else math.sqrt(2.0 * D / a_ab)
    elif s_a != 0 and s_b == 0:  # Eq.(3)
        d_need = v_u * v_u / (2.0 * a_ba)
        t = v_u / a_ba + (D - d_need) / v_u if D >= d_need else math.sqrt(2.0 * D / a_ba)
    else:  # Eq.(4)
        t = D / v_u
    return float(t), float(D)
