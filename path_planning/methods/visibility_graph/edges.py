# -*- coding: utf-8 -*-
"""边连接（可见性图 / 稀疏路图）。

流程：
    候选节点对（均匀网格哈希邻域，半径 max_edge_len）→ 三维长度截断 →
    ① LOS 可达（不穿越简化建筑障碍，已含 d_build 外扩 + edge_margin 余量）→
    ② 不穿越禁飞场（静态 + 动态-保守）→
    ③ 机动保护区：线段到最近障碍净空 < protection_clearance 则删边（删高风险近距边）→
    原始无向邻接表。
    可选：同一 (x,y) 不同高度层之间加竖向边（column 不在建筑内才加），允许变高。

复杂度：网格哈希把 O(N²) 降到 O(N·K)，K 为邻域内平均点数；LOS / 净空判定的
几何内核走 shapely STRtree 候选筛选 + segment_prism_hit / distance。

不负责：节点生成（nodes.py）、搜索（search.py）。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np

from .obstacles import SimplifiedObstacles
from .params import VisibilityGraphParams


def _grid_neighbors(pts: np.ndarray, r: float) -> list[list[int]]:
    """均匀网格哈希 → 每点的候选近邻索引（距离 ≤ r，j > i）。"""
    n = len(pts)
    cell = max(r, 1e-6)
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
                    if j > i:
                        nbrs[i].append(j)
    return nbrs


def build_edges(
    nodes: np.ndarray,
    obstacles: SimplifiedObstacles,
    params: VisibilityGraphParams,
    nofly_blocked: Optional[Callable[[np.ndarray, np.ndarray], bool]] = None,
    with_protection: bool = True,
    verbose: bool = True,
) -> tuple[list[list[tuple[int, float]]], dict]:
    """候选点对 → 剪枝后的无向邻接表 [(j, 3D距离)]。

    with_protection=False 用于对照实验（验证机动保护区对边数 / 路径长度的边际影响）。
    """
    t0 = time.perf_counter()
    n = len(nodes)
    pos = nodes
    nbrs = _grid_neighbors(pos, params.max_edge_len)
    k = int(params.max_degree) if params.max_degree and params.max_degree > 0 else 0

    # 每点最多保留 K 个最近候选（防密集场景候选爆炸、控边数）
    if k:
        capped: list[list[int]] = [[] for _ in range(n)]
        for i in range(n):
            js = nbrs[i]
            if len(js) > k:
                js_arr = np.asarray(js, dtype=np.int64)
                d = np.linalg.norm(pos[js_arr] - pos[i], axis=1)
                part = np.argpartition(d, k - 1)[:k]
                capped[i] = [int(js_arr[t]) for t in part]
            else:
                capped[i] = js
        nbrs = capped

    adj: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    n_cand = 0
    n_blocked_building = 0
    n_blocked_nofly = 0
    n_blocked_protection = 0
    prot_on = with_protection and params.protection_clearance > 0

    for i in range(n):
        pi = pos[i]
        for j in nbrs[i]:
            pj = pos[j]
            d = float(np.linalg.norm(pj - pi))
            if d <= 1e-6 or d > params.max_edge_len:
                continue
            n_cand += 1
            if not obstacles.segment_clear(pi, pj, margin=params.edge_margin):
                n_blocked_building += 1
                continue
            if nofly_blocked is not None and nofly_blocked(pi, pj):
                n_blocked_nofly += 1
                continue
            if prot_on:
                clr = obstacles.segment_clearance(pi, pj)
                if clr < params.protection_clearance:
                    n_blocked_protection += 1
                    continue
            adj[i].append((j, d))
            adj[j].append((i, d))

    n_edges = sum(len(a) for a in adj) // 2

    # ── 竖向边（同一 x,y 不同高度层；column 不在建筑内才加）──────────────
    n_vertical = 0
    if params.connect_layers:
        tol = 1e-3
        groups: dict[tuple[int, int], list[int]] = {}
        for k in range(n):
            key = (int(round(pos[k, 0] / tol)), int(round(pos[k, 1] / tol)))
            groups.setdefault(key, []).append(k)
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda m: pos[m, 2])
            for a, b in zip(members[:-1], members[1:]):
                if not obstacles.point_clear(pos[a]) or not obstacles.point_clear(pos[b]):
                    continue
                # 同 x,y，净空由点合法性保证（已外扩障碍），直接连
                d = float(pos[b, 2] - pos[a, 2])
                adj[a].append((b, d))
                adj[b].append((a, d))
                n_vertical += 1

    n_edges_total = sum(len(a) for a in adj) // 2
    stats = {
        "n_nodes": n,
        "n_candidates": n_cand,
        "n_edges": n_edges_total,
        "n_horizontal_edges": n_edges,
        "n_vertical_edges": n_vertical,
        "n_blocked_building": n_blocked_building,
        "n_blocked_nofly": n_blocked_nofly,
        "n_blocked_protection": n_blocked_protection,
        "with_protection": bool(prot_on),
        "build_time_s": round(time.perf_counter() - t0, 3),
    }
    if verbose:
        print(
            f"[edges] 候选 {n_cand} → 水平边 {n_edges}（建筑剪 {n_blocked_building} / "
            f"禁飞剪 {n_blocked_nofly} / 机动保护区剪 {n_blocked_protection}）+ 竖向边 {n_vertical}，"
            f"总边 {n_edges_total}"
        )
    return adj, stats
