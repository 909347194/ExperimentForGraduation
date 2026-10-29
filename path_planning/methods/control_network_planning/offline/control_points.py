# -*- coding: utf-8 -*-
"""控制点布设（Lin2021 §2.2.1 Fig.5）。

规则：
    建筑外围     底面轮廓向外缓冲 d1（水平安全距离）后的环上布点
    垂直分层     从 d2（离地最小高度）起，按 d3 层距向上布设，直到屋顶之上
                 top_clearance（含一个顶部点），层数受 max_levels 限制
    静态禁飞     落在静态禁飞膨胀体内的点直接剔除（design §2.3）
    过近角点     同一层相邻点距离小于 corner_min_dist 时抽稀

输入→输出：CityEnvironment + OfflineParams → List[ControlPoint]。

不负责：连线（link_builder.py）、连通性保证（pipeline 侧统计孤立点）。
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np
from shapely.geometry import Point

from path_planning.common.types import CityEnvironment, ControlPoint
from path_planning.common.config import OfflineParams


def _ring_points(poly, min_dist: float) -> list[tuple[float, float]]:
    """取外环顶点（缓冲后多边形），按 min_dist 抽稀。"""
    g = poly.buffer(0)
    if g.geom_type != "Polygon":
        g = g.convex_hull
    if g.is_empty:
        return []
    coords = list(g.exterior.coords)[:-1]
    if min_dist <= 0 or len(coords) <= 4:
        return coords
    out: list[tuple[float, float]] = []
    for c in coords:
        if all(math.hypot(c[0] - o[0], c[1] - o[1]) >= min_dist for o in out):
            out.append(c)
    return out or coords


def _levels(h: float, ofp: OfflineParams) -> list[int]:
    """垂直层号序列（0 为最低层）。"""
    z_top = max(h + ofp.top_clearance, ofp.d2)
    n = int(math.floor((z_top - ofp.d2) / ofp.d3)) + 1
    n = max(1, min(n, int(ofp.max_levels)))
    return list(range(n))


def generate_control_points(
    env: CityEnvironment,
    ofp: OfflineParams,
    blocked_fn: Optional[callable] = None,
    static_blocked_fn: Optional[callable] = None,
) -> tuple[list[ControlPoint], dict]:
    """布设控制点。

    blocked_fn(p) -> bool：剔除不合法落点（**必须包含"落在相邻建筑内部"的检查**——
    密集城区按 d1 外扩后的点常常落进邻居建筑体内，不剔除会产生大量孤立点）。
    static_blocked_fn 为旧名，二者任一命中即剔除。
    """
    def _blocked(p) -> bool:
        return bool((blocked_fn is not None and blocked_fn(p))
                    or (static_blocked_fn is not None and static_blocked_fn(p)))

    pts: list[ControlPoint] = []
    n_dropped_nofly = 0

    for b in env.buildings:
        h = float(b.height if b.height > 0 else (b.z1 - b.z0))
        ring = _ring_points(b.poly.buffer(ofp.d1), ofp.corner_min_dist)
        if not ring:
            continue
        for lv in _levels(h, ofp):
            z = ofp.d2 + lv * ofp.d3
            z = min(z, max(h + ofp.top_clearance, ofp.d2))
            for (x, y) in ring:
                p = np.array([x, y, z], dtype=float)
                if _blocked(p):
                    n_dropped_nofly += 1
                    continue
                pts.append(ControlPoint(idx=len(pts), p=p, level=lv, src="building"))
        # 顶部点（屋顶之上）
        cx, cy = b.poly.centroid.x, b.poly.centroid.y
        z_top = max(h + ofp.top_clearance, ofp.d2)
        p = np.array([cx, cy, z_top], dtype=float)
        if not _blocked(p):
            pts.append(ControlPoint(idx=len(pts), p=p, level=-1, src="top"))
        else:
            n_dropped_nofly += 1

    # 自由空间补点层：网格 × 指定高度（使无人机可飞越建筑；
    # 禁飞区高度无限、不可飞越——其投影内的点会被 blocked_fn 中的静态禁飞检查剔除）
    n_free = 0
    if ofp.free_grid_step > 0 and env.buildings:
        xs = [b.poly.bounds[0] for b in env.buildings] + [b.poly.bounds[2] for b in env.buildings]
        ys = [b.poly.bounds[1] for b in env.buildings] + [b.poly.bounds[3] for b in env.buildings]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        step = float(ofp.free_grid_step)
        nx = int((x1 - x0) // step) + 1
        ny = int((y1 - y0) // step) + 1
        for ix in range(nx + 1):
            for iy in range(ny + 1):
                gx = x0 + ix * step
                gy = y0 + iy * step
                for z in ofp.free_levels:
                    p = np.array([gx, gy, float(z)], dtype=float)
                    if _blocked(p):
                        continue
                    pts.append(ControlPoint(idx=len(pts), p=p, level=-2, src="free"))
                    n_free += 1

    for i, cp in enumerate(pts):
        cp.idx = i

    stats = {
        "n_points": len(pts),
        "n_per_building": (len(pts) / len(env.buildings)) if env.buildings else 0.0,
        "n_dropped_blocked": n_dropped_nofly,
        "n_free_points": n_free,
    }
    return pts, stats


def points_array(cps: Sequence[ControlPoint]) -> np.ndarray:
    return np.array([cp.p for cp in cps], dtype=float)
