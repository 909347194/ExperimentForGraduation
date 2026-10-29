# -*- coding: utf-8 -*-
"""三维 Dubins 曲线平滑（Lin2021 §2.3 Eq.(5)-(10)）。

route（控制点序列）→ path（空间曲线）：逐三点组在由其确定的平面上生成转向圆弧。

    θ        控制点处两条链路的夹角（Eq.6 的 θ_{i+1}）
    r = d·sin(θ/2) / (1 − sin(θ/2))        Eq.(6)
    T = r·tan(θ/2)                          Eq.(7)  转向起止点距控制点的距离
    圆心  C = p_{i+1} + normalize(u+w)·(r+d)（u,w 为指向前后点的单位向量）

相邻控制点过近（T 超过相邻链路长的一半）时按 shrink_factor 逐次收缩 d（Fig.14）。

不负责：碰撞正确性——平滑可能侵入走廊，由 post_check 把关并回调偏移量。
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from path_planning.common.config import OnlineParams


def _arc_points(c: np.ndarray, a: np.ndarray, b: np.ndarray, r: float, n: int = 12) -> np.ndarray:
    """以 c 为圆心、半径 r，从 a 转到 b 的圆弧采样（Rodrigues 旋转）。"""
    va = a - c
    vb = b - c
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na < 1e-9 or nb < 1e-9:
        return np.vstack([a, b])
    va, vb = va / na, vb / nb
    axis = np.cross(va, vb)
    na_axis = np.linalg.norm(axis)
    if na_axis < 1e-9:
        return np.vstack([a, b])
    axis = axis / na_axis
    ang = math.atan2(np.linalg.norm(np.cross(va, vb)), float(np.dot(va, vb)))
    ang = min(ang, math.pi - 1e-6)
    ts = np.linspace(0.0, 1.0, max(2, n))
    out = []
    for t in ts:
        th = ang * t
        v = va * math.cos(th) + np.cross(axis, va) * math.sin(th) + axis * np.dot(axis, va) * (1 - math.cos(th))
        out.append(c + r * v)
    return np.array(out)


def smooth_route(
    pts: Sequence[np.ndarray],
    d: float,
    onp: OnlineParams,
) -> tuple[np.ndarray, list[float]]:
    """平滑控制点折线 → 采样路径。返回 (pts(M,3), 各控制点实际偏差 d)。"""
    pts = [np.asarray(p, dtype=float) for p in pts]
    if len(pts) < 3:
        return np.array(pts), []

    deviations: list[float] = []
    out: list[np.ndarray] = [pts[0]]

    for k in range(1, len(pts) - 1):
        p0, p1, p2 = pts[k - 1], pts[k], pts[k + 1]
        u = p0 - p1
        w = p2 - p1
        lu, lw = np.linalg.norm(u), np.linalg.norm(w)
        if lu < 1e-6 or lw < 1e-6:
            deviations.append(0.0)
            continue
        u, w = u / lu, w / lw
        cos_t = float(np.clip(np.dot(u, w), -1.0, 1.0))
        theta = math.acos(cos_t)

        dd = float(d)
        r = T = 0.0
        for _ in range(onp.max_shrink_iters):
            s = math.sin(theta / 2.0)
            if s > 0.99:  # 近 U 形急转（θ→180°）：圆弧半径公式 r=d·sin/(1−sin) 在此发散，
                          # 不做圆弧、保留控制点顶点（route 的两条直线段几何本就可行）
                r = T = 0.0
                break
            r = dd * s / (1.0 - s)
            T = r * math.tan(theta / 2.0)
            # Fig.14：切点距离不得超过相邻链路的一半，否则收缩偏差
            if T <= 0.45 * min(lu, lw):
                break
            dd *= onp.shrink_factor
        deviations.append(dd)

        if r <= 1e-6 or T <= 1e-6:
            # 急转/近直线：不做圆弧，但仍须经过该控制点（保留走廊顶点），
            # 否则多控制点会被整段跳过、路径塌缩成 origin→dest 直线而离开走廊。
            out.append(p1)
            continue

        t_minus = p1 + T * u
        t_plus = p1 + T * w
        bis = u + w
        nb = np.linalg.norm(bis)
        if nb < 1e-6:
            continue
        c = p1 + (bis / nb) * (r + dd)

        # 直线段：从当前末端到 t_minus，再接圆弧到 t_plus
        out.append(t_minus)
        out.extend(list(_arc_points(c, t_minus, t_plus, r)))
        out.append(t_plus)

    out.append(pts[-1])
    arr = np.array([np.asarray(p, dtype=float) for p in out])

    # 去重相邻重复点
    keep = [0]
    for i in range(1, len(arr)):
        if np.linalg.norm(arr[i] - arr[keep[-1]]) > 1e-6:
            keep.append(i)
    return arr[keep], deviations
