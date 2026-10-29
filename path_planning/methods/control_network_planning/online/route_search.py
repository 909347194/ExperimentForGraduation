# -*- coding: utf-8 -*-
"""扩展控制网络上的最短路搜索（Lin2021 §2.3；分层策略见 design_nofly_zones.md §5）。

**全程普通 Dijkstra，不引入时空搜索**（时空搜索列未来拓展，design §10）。

    L1    乐观网络 + 普通 Dijkstra → 用各边实际到达时刻做切段求交复核
    L2    复核失败 → 删冲突边重跑（≤ max_l2_iters 轮）
    L2.5  仍失败 → 保守全剪网络重跑（任务时段内任何时刻可能冲突的边全删）
    L4    无解 → 报不可行进闭环

起点/终点为任意点：先吸附到 d* 邻域内可达的控制点，接入/接出代价按 Eq.1-4 计算
（起点静止 → Eq.1/Eq.2；终点停靠 → Eq.1/Eq.3）。

不负责：代价定义（cost/）、复核判定（constraints/query.py）、平滑（dubins_smoother.py）。
"""

from __future__ import annotations

import heapq
import math
from typing import Optional, Sequence

import numpy as np

from path_planning.common.config import Config
from path_planning.common.types import (
    ExpandedNetwork,
    InfeasibilityReason,
    MotionState,
    ODRequest,
    Route,
    node_cp,
    node_id,
    node_state,
)
from path_planning.methods.control_network_planning.constraints.query import CollisionQuery
from path_planning.methods.control_network_planning.cost.link_cost import link_time


class RouteSearcher:
    """扩展网络上的路由搜索 + 分层迭代修正。"""

    def __init__(self, net: ExpandedNetwork, cfg: Config, query: CollisionQuery):
        self.net = net
        self.cfg = cfg
        self.query = query
        self.uav = cfg.uav

    # ── OD 吸附 ───────────────────────────────────────────────────────────

    def _attach(self, p: np.ndarray, outgoing: bool) -> list[tuple[int, float]]:
        """把任意点吸附到邻域控制点：`outgoing=True` 返回 [(进入虚拟节点, 代价)]。"""
        pts = self.net.points
        d = np.linalg.norm(pts - p, axis=1)
        idx = np.where(d <= self.cfg.offline.d_star)[0]
        out = []
        for i in idx:
            pi = pts[i]
            if not self.query.link_feasible(p, pi):
                continue
            if outgoing:  # 起点：静止出发
                s = int(MotionState.from_dir(pi - p))
                t0, _ = link_time(p, pi, 0, 0, self.uav)
                ts, _ = link_time(p, pi, 0, s, self.uav)
                out.append((node_id(int(i), 0), t0))
                out.append((node_id(int(i), s), ts))
            else:  # 终点：停靠
                s = int(MotionState.from_dir(p - pi))
                t0, _ = link_time(pi, p, 0, 0, self.uav)
                ts, _ = link_time(pi, p, s, 0, self.uav)
                out.append((node_id(int(i), 0), t0))
                out.append((node_id(int(i), s), ts))
        return out

    # ── 普通 Dijkstra ────────────────────────────────────────────────────

    def dijkstra(
        self,
        starts: Sequence[tuple[int, float]],
        goals: Sequence[tuple[int, float]],
        blocked_pairs: Optional[set] = None,
    ) -> Optional[tuple[list[int], float, float]]:
        """返回 (虚拟节点序列, 总时间, 总距离)。goals 为 [(节点, 接出代价)]。"""
        blocked = blocked_pairs or set()
        goal_cost = {g: c for g, c in goals}
        dist: dict[int, float] = {}
        dist_d: dict[int, float] = {}
        prev: dict[int, int] = {}
        pq: list[tuple[float, int]] = []
        for s, c in starts:
            if s not in dist or c < dist[s]:
                dist[s] = c
                dist_d[s] = 0.0
                heapq.heappush(pq, (c, s))

        best_node, best_total = None, math.inf
        while pq:
            du, u = heapq.heappop(pq)
            if du > dist.get(u, math.inf) + 1e-12:
                continue
            if u in goal_cost:
                tot = du + goal_cost[u]
                if tot < best_total:
                    best_total, best_node = tot, u
                # 不提前退出：其他目标可能更优（目标集含多种状态）
            for (v, t, d) in self.net.adj[u]:
                if blocked and (node_cp(u), node_cp(v)) in blocked:
                    continue
                nd = du + t
                if nd < dist.get(v, math.inf) - 1e-12:
                    dist[v] = nd
                    dist_d[v] = dist_d.get(u, 0.0) + d
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        if best_node is None:
            return None
        nodes = [best_node]
        while nodes[-1] in prev:
            nodes.append(prev[nodes[-1]])
        nodes.reverse()
        total_d = sum(
            self._edge_dist(a, b) for a, b in zip(nodes[:-1], nodes[1:])
        )
        return nodes, best_total, float(total_d)

    def _edge_dist(self, u: int, v: int) -> float:
        for (vv, t, d) in self.net.adj[u]:
            if vv == v:
                return d
        return float(np.linalg.norm(self.net.point_of(v) - self.net.point_of(u)))

    # ── 分层求解 ──────────────────────────────────────────────────────────

    def plan(self, od: ODRequest) -> tuple[Optional[Route], str, dict]:
        """L1 → L2 → L2.5 → L4。返回 (route, level, stats)。

        复核覆盖**完整飞行段**（含起点接入段与控制点到终点的接出段），
        否则单控制点的 route 会因为没有"边"而跳过检查。
        """
        stats = {"n_dijkstra": 0, "n_l2_iters": 0, "blocked_pairs": 0}
        origin = np.asarray(od.origin, dtype=float)
        dest = np.asarray(od.dest, dtype=float)

        starts_all = self._attach(origin, outgoing=True)
        goals_all = self._attach(dest, outgoing=False)
        # 预先剔除"接入段本身撞禁飞"的起点候选（该段时间确定，可一次性判定），
        # 否则 L2 轮次会被接入段冲突白白消耗
        starts_all = [
            (n, c)
            for n, c in starts_all
            if self.query.edge_occupancy_free(origin, self.net.point_of(n), od.t_dep, od.t_dep + c)
        ]
        if not starts_all:
            return None, "L4", {**stats, "reason": InfeasibilityReason.ORIGIN_UNREACHABLE}
        if not goals_all:
            return None, "L4", {**stats, "reason": InfeasibilityReason.DEST_UNREACHABLE}
        s_cost = {n: c for n, c in starts_all}
        g_cost = {n: c for n, c in goals_all}

        blocked: set[tuple[int, int]] = set()
        blocked_starts: set[int] = set()
        blocked_goals: set[int] = set()

        for attempt in range(self.cfg.online.max_l2_iters + 1):
            starts = [(n, c) for n, c in starts_all if n not in blocked_starts]
            goals = [(n, c) for n, c in goals_all if n not in blocked_goals]
            if not starts or not goals:
                break
            res = self.dijkstra(starts, goals, blocked)
            stats["n_dijkstra"] += 1
            if res is None:
                break
            nodes, total_t, _ = res
            legs = self._legs(nodes, origin, dest, od.t_dep, s_cost, g_cost)
            bad = [i for i, (pa, pb, t0, t1) in enumerate(legs)
                   if not self.query.edge_occupancy_free(pa, pb, t0, t1)]
            stats["n_l2_iters"] = attempt
            if not bad:
                dist = float(sum(np.linalg.norm(pb - pa) for pa, pb, _, _ in legs))
                route = Route(
                    nodes=nodes,
                    points=[node_cp(n) for n in nodes],
                    time=total_t,
                    dist=dist,
                )
                stats["blocked_pairs"] = len(blocked)
                return route, ("L1" if attempt == 0 else "L2"), stats

            # L2：冲突段分类处理（接入/接出段禁用该接入点，中间段删边）
            for i in bad:
                if i == 0:
                    blocked_starts.add(nodes[0])
                elif i == len(legs) - 1:
                    blocked_goals.add(nodes[-1])
                else:
                    a, b = node_cp(nodes[i - 1]), node_cp(nodes[i])
                    blocked.add((a, b))
                    blocked.add((b, a))
            if not self.cfg.online.enable_l2:
                break

        # L2.5：保守全剪（任务时段内任何时刻可能冲突的链路全删）
        if self.cfg.online.enable_l25:
            starts = [(n, c) for n, c in starts_all if n not in blocked_starts]
            goals = [(n, c) for n, c in goals_all if n not in blocked_goals]
            if starts and goals:
                # 只用保守集、不叠加 L2 累积的删边：保守集本就是"任务时段内任何时刻
                # 可能冲突"的超集，再叠加会把网络过删到不连通
                res = self.dijkstra(starts, goals, self._conservative_pairs(od))
                stats["n_dijkstra"] += 1
                if res is not None:
                    nodes, total_t, _ = res
                    legs = self._legs(nodes, origin, dest, od.t_dep, s_cost, g_cost)
                    ok = all(
                        self.query.edge_occupancy_free(pa, pb, t0, t1)
                        for pa, pb, t0, t1 in legs
                    )
                    if ok:
                        dist = float(sum(np.linalg.norm(pb - pa) for pa, pb, _, _ in legs))
                        return (
                            Route(nodes=nodes, points=[node_cp(n) for n in nodes],
                                  time=total_t, dist=dist),
                            "L2.5",
                            stats,
                        )
                    stats["l25_still_conflict"] = True

        return None, "L4", {**stats, "reason": InfeasibilityReason.DYNAMIC_BLOCKED}

    def _legs(self, nodes, origin, dest, t_dep, s_cost, g_cost):
        """完整飞行段 [(pa, pb, t0, t1)]：起点接入段 + 网络内边 + 终点接出段。"""
        legs = []
        t = float(t_dep)
        p0 = self.net.point_of(nodes[0])
        t1 = t + s_cost[nodes[0]]
        legs.append((origin, p0, t, t1))
        t = t1
        for a, b in zip(nodes[:-1], nodes[1:]):
            tt = self._edge_time(a, b)
            legs.append((self.net.point_of(a), self.net.point_of(b), t, t + tt))
            t += tt
        legs.append((self.net.point_of(nodes[-1]), dest, t, t + g_cost[nodes[-1]]))
        return legs

    def _edge_time(self, u: int, v: int) -> float:
        for (vv, t, d) in self.net.adj[u]:
            if vv == v:
                return t
        # 兜底：用 Eq.(4) 估算
        pa, pb = self.net.point_of(u), self.net.point_of(v)
        tt, _ = link_time(pa, pb, node_state(u), node_state(v), self.uav)
        return tt

    def _conservative_pairs(self, od: ODRequest) -> set[tuple[int, int]]:
        """L2.5：任务时段内任何时刻可能冲突的链路（控制点对）。"""
        t0 = od.t_dep
        t1 = t0 + self.cfg.online.t_horizon
        out: set[tuple[int, int]] = set()
        seen = set()
        for u, edges in enumerate(self.net.adj):
            a = node_cp(u)
            for (v, _, _) in edges:
                b = node_cp(v)
                if (a, b) in seen or a == b:
                    continue
                seen.add((a, b))
                pa, pb = self.net.points[a], self.net.points[b]
                if self.query.conservative_blocked(pa, pb, t0, t1):
                    out.add((a, b))
                    out.add((b, a))
        return out
