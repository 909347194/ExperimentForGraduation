# -*- coding: utf-8 -*-
"""节点生成（可见性图 / 稀疏路图）。

策略（用户口径，可组合）：
    1) 简化建筑外轮廓按较大步长 node_step 采样（外扩 node_buffer 后环上布点），
       在几个离散飞行高度层 altitude_layers 重复；
    2) 一层「自由空间粗网格」骨干（grid_step × grid_layers）保证开路连通性；
    3) 额外关键点：机巢 / 任务可能出现的区域 / 路网中心线采样点（本实验无路网则略），
       以及 OD 端点（由 planner 在线接入，不在此预置）。

规模控制：节点总数硬上限 max_nodes（先瞄准 < 2000）。
    预算分配 = max_nodes − 粗网格节点 − 额外点；
    建筑轮廓节点按「面积降序」逐栋填充预算（大建筑优先覆盖），超预算即停。

落点合法性：所有候选点若落在（已外扩的）建筑障碍内或禁飞区内则剔除。

不负责：障碍简化（obstacles.py）、边连接（edges.py）。
"""

from __future__ import annotations

import time
from typing import Optional, Sequence

import numpy as np

from path_planning.common.types import CityEnvironment
from .obstacles import NoFlyField, SimplifiedObstacles
from .params import VisibilityGraphParams


def _resample_ring(coords, step: float, maxn: int):
    """把闭合环（coords 不含重复末点）按步长 step 等距重采样，最多 maxn 个点。"""
    if len(coords) <= 1:
        return [tuple(coords[0])] if coords else []
    pts = [np.array(c, dtype=float) for c in coords]
    loop = pts + [pts[0]]
    seglens = [float(np.linalg.norm(loop[i + 1] - loop[i])) for i in range(len(loop) - 1)]
    total = sum(seglens)
    if total < 1e-9:
        return [tuple(pts[0])]
    n = max(1, int(round(total / max(step, 1e-6))))
    n = min(n, max(1, int(maxn)))
    targets = [total * (k + 0.5) / n for k in range(n)]
    out = []
    acc = 0.0
    si = 0
    for t in targets:
        while si < len(seglens) - 1 and acc + seglens[si] < t:
            acc += seglens[si]
            si += 1
        sl = seglens[si] if seglens[si] > 1e-9 else 1.0
        f = (t - acc) / sl
        a, b = loop[si], loop[si + 1]
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def generate_nodes(
    env: CityEnvironment,
    params: VisibilityGraphParams,
    obstacles: SimplifiedObstacles,
    nofly: Optional[NoFlyField] = None,
    extra_points: Optional[Sequence[Sequence[float]]] = None,
) -> tuple[np.ndarray, dict, dict]:
    """生成节点数组 (N,3) + 元信息 + 统计。

    extra_points: OD 端点 / 机巢 / 任务点等，必加（规划查询依赖）。
    """
    t0 = time.perf_counter()
    scene = env.buildings
    if not scene:
        raise ValueError("env.buildings 为空，无法生成节点")

    pts: list[list[float]] = []
    kinds: list[str] = []
    src: list[int] = []

    xs = [b.poly.bounds[0] for b in scene] + [b.poly.bounds[2] for b in scene]
    ys = [b.poly.bounds[1] for b in scene] + [b.poly.bounds[3] for b in scene]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    # ── 阶段 A：自由空间粗网格骨干 ───────────────────────────────────────
    gstep = max(params.grid_step, 1.0)
    for z in params.grid_layers:
        nx = int((x1 - x0) // gstep)
        ny = int((y1 - y0) // gstep)
        for ix in range(nx + 1):
            for iy in range(ny + 1):
                pts.append([x0 + ix * gstep, y0 + iy * gstep, float(z)])
                kinds.append("grid")
                src.append(-1)

    # ── 预算（建筑轮廓节点）────────────────────────────────────────────
    n_extra = len(extra_points) if extra_points else 0
    budget = max(0, params.max_nodes - len(pts) - n_extra)

    # ── 阶段 B：建筑轮廓节点（面积降序，预算封顶）────────────────────────
    order = sorted(range(len(scene)), key=lambda i: -scene[i].poly.area)
    used = 0
    for i in order:
        if used >= budget:
            break
        b = scene[i]
        if b.poly.geom_type != "Polygon":
            ring_poly = b.poly.convex_hull
        else:
            ring_poly = b.poly
        try:
            # 节点应落在「已外扩障碍（轮廓 + d_build）」之外再退 node_buffer，
            # 否则节点贴着障碍边界 → 其出边净空≈0 → 机动保护区会把所有建筑边一刀切删掉。
            ring = ring_poly.buffer(params.d_build + params.node_buffer).exterior
        except Exception:
            continue
        coords = list(ring.coords)[:-1]
        sampled = _resample_ring(coords, params.node_step, params.max_nodes_per_building)
        for z in params.altitude_layers:
            for (x, y) in sampled:
                if used >= budget:
                    break
                pts.append([x, y, float(z)])
                kinds.append("building")
                src.append(b.bid)
                used += 1

    # ── 阶段 C：额外关键点（机巢 / 任务点 / OD 端点）────────────────────
    if extra_points:
        for p in extra_points:
            pts.append([float(p[0]), float(p[1]), float(p[2])])
            kinds.append("extra")
            src.append(-1)

    nodes = np.array(pts, dtype=float)

    # ── 落点合法性过滤：剔除落在建筑障碍 / 禁飞区内的点 ────────────────
    keep = np.ones(len(nodes), dtype=bool)
    n_drop_obstacle = 0
    n_drop_nofly = 0
    for k in range(len(nodes)):
        p = nodes[k]
        if not obstacles.point_clear(p):
            keep[k] = False
            n_drop_obstacle += 1
            continue
        if nofly is not None and nofly.point_blocked(p):
            keep[k] = False
            n_drop_nofly += 1
    nodes = nodes[keep]
    kinds = [kinds[k] for k in range(len(kinds)) if keep[k]]
    src = [src[k] for k in range(len(src)) if keep[k]]

    stats = {
        "n_nodes": int(len(nodes)),
        "n_grid": int(sum(1 for k in kinds if k == "grid")),
        "n_building": int(sum(1 for k in kinds if k == "building")),
        "n_extra": int(sum(1 for k in kinds if k == "extra")),
        "n_drop_obstacle": int(n_drop_obstacle),
        "n_drop_nofly": int(n_drop_nofly),
        "gen_time_s": round(time.perf_counter() - t0, 3),
    }
    meta = {"kinds": kinds, "src": src}
    return nodes, meta, stats
