# -*- coding: utf-8 -*-
"""可见性图 / 稀疏路图规划器门面（path_planning 内自洽实现，不依赖 NetworkX）。

流程（离线建图）：
    CityEnvironment + 禁飞区 → SimplifiedObstacles → generate_nodes → build_edges
        → VisibilityGraph（邻接表 + 节点坐标 + 障碍/禁飞查询）

在线规划：
    plan(origin, dest) → 把端点吸附到 attach_radius 内「视线可达」的节点（临时加边），
        跑堆优化 Dijkstra/A*，返回折线（world 坐标）+ 总三维代价 + 可行性原因。

只用几何约束（建筑 + 禁飞区），不引入 TPR（第三方地面风险模型）。

不负责：场景解析（common/scenario）、代价语义（methods/control_network_planning）。
"""

from __future__ import annotations

import time
from typing import Optional, Sequence

import numpy as np

from path_planning.common.types import CityEnvironment, DynamicNoFly, StaticNoFly
from .edges import build_edges
from .nodes import generate_nodes
from .obstacles import NoFlyField, SimplifiedObstacles
from .params import VisibilityGraphParams
from .search import shortest_path


class VisibilityGraph:
    """建好的可见性图 + 在线规划接口。"""

    def __init__(
        self,
        nodes: np.ndarray,
        adj: list,
        obstacles: SimplifiedObstacles,
        nofly: NoFlyField,
        params: VisibilityGraphParams,
        meta: Optional[dict] = None,
    ):
        self.nodes = nodes
        self.adj = adj
        self.obstacles = obstacles
        self.nofly = nofly
        self.params = params
        self.meta = meta or {}

    # ── 端点接入 ───────────────────────────────────────────────────────────

    def _attach(self, p: np.ndarray):
        p = np.asarray(p, dtype=float)
        d = np.linalg.norm(self.nodes - p, axis=1)
        cand = np.where(d <= self.params.attach_radius)[0]
        out = []
        for j in cand:
            pj = self.nodes[j]
            if not self.obstacles.segment_clear(p, pj, margin=self.params.edge_margin):
                continue
            if self.nofly.blocked(p, pj):
                continue
            out.append((int(j), float(np.linalg.norm(pj - p))))
        return out

    # ── 在线规划 ───────────────────────────────────────────────────────────

    def plan(self, origin, dest, depart_time: float = 0.0):
        """返回 (polyline: list[np.ndarray] | None, reason: str, cost: float)。

        depart_time: 出发时刻（秒）。用于动态禁飞的时间窗检查（Option A）：
            图的几何边（建筑 + 静态禁飞）在离线建图时固定，动态禁飞只在 A* 松弛每条边时，
            按该边被穿越的到达时刻窗 [t0,t1] 做可行性判定 —— 图本身只建一次、长期复用。
        """
        origin = np.asarray(origin, dtype=float)
        dest = np.asarray(dest, dtype=float)

        # 动态禁飞在「查询时」按时间窗检查（Option A）。
        # 若 include_dynamic_as_static=True（Option B），动态已烤进建图，查询期不再重复检查。
        dyn_cb = (self.nofly.dynamic_blocked
                  if (not self.params.include_dynamic_as_static and self.nofly.has_dynamic)
                  else None)
        speed = self.params.cruise_speed_mps

        so = self._attach(origin)
        sd = self._attach(dest)
        if not so:
            return None, "origin_unreachable", float("inf")
        if not sd:
            return None, "dest_unreachable", float("inf")

        base_n = len(self.nodes)
        # 注意：必须复制内层边表，否则 aug[j].append 会污染 self.adj，
        # 使后续 plan() 调用继承前次 OD 的临时边 → 产生顺序相关的错误最短路径。
        aug = [list(a) for a in self.adj] + [[] for _ in range(2)]
        s_idx, d_idx = base_n, base_n + 1
        for (j, w) in so:
            aug[s_idx].append((j, w))
            aug[j].append((s_idx, w))
        for (j, w) in sd:
            aug[d_idx].append((j, w))
            aug[j].append((d_idx, w))

        # 直达边（若视线可达且不在动态禁飞时间窗内）
        od = float(np.linalg.norm(dest - origin))
        if od > 1e-6 and od <= self.params.max_edge_len:
            if self.obstacles.segment_clear(origin, dest, margin=self.params.edge_margin) \
                    and not self.nofly.blocked(origin, dest):
                t1 = depart_time + (od / speed if speed > 0 else 0.0)
                blocked_dyn = (dyn_cb is not None and dyn_cb(origin, dest, depart_time, t1))
                if not blocked_dyn:
                    aug[s_idx].append((d_idx, od))
                    aug[d_idx].append((s_idx, od))

        aug_nodes = np.vstack([self.nodes, origin[None, :], dest[None, :]])
        path, cost = shortest_path(
            aug, aug_nodes, s_idx, d_idx, astar=True,
            dynamic_blocked=dyn_cb, depart_time=depart_time, speed=speed,
        )
        if path is None:
            return None, "no_route", float("inf")

        poly: list[np.ndarray] = []
        for u in path:
            if u == s_idx:
                poly.append(origin)
            elif u == d_idx:
                poly.append(dest)
            else:
                poly.append(self.nodes[u])
        return poly, "ok", cost


def build_visibility_graph(
    env: CityEnvironment,
    params: VisibilityGraphParams,
    extra_points: Optional[Sequence[Sequence[float]]] = None,
    verbose: bool = True,
) -> tuple[VisibilityGraph, dict]:
    """离线建图全流程。返回 (图, 各阶段统计)。"""
    t0 = time.perf_counter()

    obstacles = SimplifiedObstacles(
        env.buildings,
        d_build=params.d_build,
        mode=params.simplify_mode,
        tol=params.simplify_tol,
    )
    nofly = NoFlyField(
        static=env.static_nofly,
        dynamic=env.dynamic_nofly,
        as_static=params.include_dynamic_as_static,
    )
    nodes, node_meta, node_stats = generate_nodes(
        env, params, obstacles, nofly, extra_points=extra_points
    )
    adj, edge_stats = build_edges(
        nodes,
        obstacles,
        params,
        nofly_blocked=nofly.blocked,
        with_protection=True,
        verbose=verbose,
    )

    stats = {
        "obstacles": obstacles.summary(),
        "nofly": nofly.summary(),
        "nodes": node_stats,
        "edges": edge_stats,
        "build_time_s": round(time.perf_counter() - t0, 3),
    }
    graph = VisibilityGraph(
        nodes=nodes, adj=adj, obstacles=obstacles, nofly=nofly,
        params=params, meta=node_meta,
    )
    return graph, stats
