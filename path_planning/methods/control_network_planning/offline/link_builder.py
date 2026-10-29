# -*- coding: utf-8 -*-
"""链路连接与剪枝（Lin2021 §2.2.1 Fig.5(b)/Fig.6）。

流程：
    候选控制点对（网格哈希邻域，半径 d*）→ 三维长度截断 →
    CollisionQuery.link_feasible 剪掉穿越建筑 / 静态禁飞区的链路 →
    原始无向控制网络。

复杂度：网格哈希把 O(P²) 降到 O(P·K)，K 为 d* 邻域内平均点数。

不负责：虚拟节点扩展（virtual_network.py）。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from path_planning.common.config import OfflineParams
from path_planning.common.types import ControlPoint, Link
from path_planning.methods.control_network_planning.constraints.query import CollisionQuery


def _grid_neighbors(pts: np.ndarray, d_star: float):
    """网格哈希 → 每点的候选近邻索引列表（距离 ≤ d*）。"""
    n = len(pts)
    cell = max(d_star, 1e-6)
    keys = np.floor(pts[:, :2] / cell).astype(np.int64)
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, k in enumerate(keys):
        buckets.setdefault((int(k[0]), int(k[1])), []).append(i)

    nbrs: list[list[int]] = [[] for _ in range(n)]
    for i, k in enumerate(keys):
        kx, ky = int(k[0]), int(k[1])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((kx + dx, ky + dy), ()):
                    if j <= i:
                        continue
                    d = float(np.linalg.norm(pts[i] - pts[j]))
                    if d <= d_star:
                        nbrs[i].append(j)
    return nbrs


def build_links(
    cps: Sequence[ControlPoint],
    query: CollisionQuery,
    ofp: OfflineParams,
    verbose: bool = False,
) -> tuple[list[Link], dict]:
    """候选点对 → 剪枝后的无向链路集合。"""
    pts = np.array([cp.p for cp in cps], dtype=float)
    n = len(pts)
    nbrs = _grid_neighbors(pts, ofp.d_star)

    links: list[Link] = []
    n_cand = 0
    n_blocked_building = 0
    n_blocked_static = 0
    k = int(getattr(ofp, "max_degree", 0) or 0)
    for i in range(n):
        cand = nbrs[i]
        if k and len(cand) > k:  # 只保留最近的 K 个候选（密集场景防爆炸）
            cand = sorted(cand, key=lambda j: float(np.linalg.norm(pts[i] - pts[j])))[:k]
        for j in cand:
            n_cand += 1
            d = float(np.linalg.norm(pts[i] - pts[j]))
            if d <= 1e-6:
                continue
            if not query.link_feasible(pts[i], pts[j]):
                # 区分冲突来源（供统计）
                if query.buildings.link_feasible(pts[i], pts[j]):
                    n_blocked_static += 1
                else:
                    n_blocked_building += 1
                continue
            links.append(Link(a=i, b=j, dist=d))

    degree = np.zeros(n, dtype=int)
    for l in links:
        degree[l.a] += 1
        degree[l.b] += 1

    stats = {
        "n_points": n,
        "n_candidates": n_cand,
        "n_links": len(links),
        "n_blocked_by_building": n_blocked_building,
        "n_blocked_by_static_nofly": n_blocked_static,
        "n_isolated": int((degree == 0).sum()),
        "degree_mean": float(degree.mean()) if n else 0.0,
    }
    if verbose:
        print(
            f"[link_builder] 候选 {n_cand} → 保留 {len(links)} "
            f"(建筑剪 {n_blocked_building} / 静态禁飞剪 {n_blocked_static})，"
            f"孤立点 {stats['n_isolated']}"
        )
    return links, stats
