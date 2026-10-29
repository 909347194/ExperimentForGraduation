# -*- coding: utf-8 -*-
"""堆优化最短路搜索（Dijkstra / A*）。

邻接表为 list[list[(邻居, 代价)]]；代价默认取边权（三维欧氏距离）。
A* 用「到目标的直线距离」作可采纳启发式，不影响最优性。
"""

from __future__ import annotations

import heapq
import math
from typing import Optional, Sequence

import numpy as np


def shortest_path(
    adj: Sequence[Sequence[tuple[int, float]]],
    nodes: np.ndarray,
    start: int,
    goal: int,
    astar: bool = True,
    dynamic_blocked=None,
    depart_time: float = 0.0,
    speed: float = 15.0,
) -> tuple[Optional[list[int]], float]:
    """返回 (节点索引序列, 总代价)；不可达返回 (None, inf)。

    dynamic_blocked: 可选回调 dynamic_blocked(u_xyz, v_xyz, t0, t1) -> bool，
        用于动态禁飞的时间窗可行性门（Option A）。若边在 [t0,t1] 内被动态禁飞阻挡则跳过该边。
        t 由「出发时刻 depart_time + 累积路径长度 / speed」换算（匀速假设）。
    """
    n = len(nodes)
    if start < 0 or goal < 0 or start >= n or goal >= n:
        return None, float("inf")
    dist = [float("inf")] * n
    prev = [-1] * n
    dist[start] = 0.0
    gx, gy, gz = float(nodes[goal, 0]), float(nodes[goal, 1]), float(nodes[goal, 2])
    if not astar:
        h = lambda _u: 0.0
    else:
        def h(u: int) -> float:
            dx = nodes[u, 0] - gx
            dy = nodes[u, 1] - gy
            dz = nodes[u, 2] - gz
            return math.sqrt(dx * dx + dy * dy + dz * dz)
    pq: list[tuple[float, float, int]] = [(h(start), 0.0, start)]
    while pq:
        f, du, u = heapq.heappop(pq)
        if du > dist[u] + 1e-12:
            continue
        if u == goal:
            break
        for (v, w) in adj[u]:
            if dynamic_blocked is not None and speed > 0:
                tu = depart_time + du / speed
                tv = depart_time + (du + w) / speed
                if dynamic_blocked(nodes[u], nodes[v], tu, tv):
                    continue
            nd = du + w
            if nd < dist[v] - 1e-12:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd + h(v), nd, v))
    if dist[goal] == float("inf"):
        return None, float("inf")
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path, dist[goal]
