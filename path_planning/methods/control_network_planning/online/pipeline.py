# -*- coding: utf-8 -*-
"""在线阶段编排门面（README §4.3.5 在线规划阶段）。

只做编排：

    (net, OD, 动态禁飞) → route_search（L1/L2/L2.5 分层）
                        → dubins_smoother + post_check
                        → PlanResult

PlanResult 聚合：route / path / trajectory、总飞行时间、可行性标记与原因、
分层触发统计（用于 design §2.4 的"分层触发率"指标）。
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from path_planning.common.config import Config
from path_planning.common.types import (
    ExpandedNetwork,
    InfeasibilityReason,
    ODRequest,
    Path,
    PlanResult,
    Trajectory,
    node_cp,
)
from path_planning.methods.control_network_planning.constraints.query import CollisionQuery
from path_planning.methods.control_network_planning.online.post_check import smooth_and_check
from path_planning.methods.control_network_planning.online.route_search import RouteSearcher
from path_planning.methods.tpr import tpr_enabled  # TPR 槽位（本阶段恒 False）


class OnlinePlanner:
    def __init__(self, net: ExpandedNetwork, cfg: Config, query: CollisionQuery):
        self.net = net
        self.cfg = cfg
        self.query = query
        self.searcher = RouteSearcher(net, cfg, query)

    def plan(self, od: ODRequest) -> PlanResult:
        t0 = time.perf_counter()
        route, level, stats = self.searcher.plan(od)
        res = PlanResult(level=level, stats=stats)
        if route is None:
            res.feasible = False
            res.infeasible_reason = stats.get("reason", InfeasibilityReason.DYNAMIC_BLOCKED)
            res.stats["plan_time_s"] = round(time.perf_counter() - t0, 4)
            return res

        # 平滑对象 = 起点 + route 控制点 + 终点（保证路径真正连接 OD）
        route_pts = [np.asarray(od.origin, dtype=float)] + [
            self.net.points[i] for i in route.points
        ] + [np.asarray(od.dest, dtype=float)]
        times_box: list = []
        pts, d_used, n_check = smooth_and_check(
            route_pts,
            self.query,
            self.cfg.online,
            total_time=route.time,
            t_dep=od.t_dep,
            times_out=times_box,
        )
        res.route = route
        if pts is None:
            # 平滑后始终冲突：走廊(route)仍可用，但 path/trajectory 不存在——
            # 不可把 route 伪装成 path/trajectory（route / path / trajectory 三者必须严格区分）。
            res.feasible = True
            res.path = None
            res.trajectory = None
            res.total_time = route.time
            res.stats.update({"smooth_failed": True, "d_used": d_used, "n_check_iters": n_check})
        else:
            times = times_box[0] if times_box else np.linspace(
                od.t_dep, od.t_dep + route.time, len(pts)
            )
            # 速度剖面：匀速占位（非 Lin2021 §2.4 时间最优轨迹；time_optimal.py 待实现）。
            # trajectory 与 path 的区别仅在于"多了一维时间"，须严格分开存储、不可合并。
            speeds = np.full(len(pts), route.dist / max(route.time, 1e-6))
            res.path = Path(pts=pts, arc_length=float(
                np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()), deviations=[])
            res.trajectory = Trajectory(pts=pts, times=times, speeds=speeds)
            res.total_time = route.time
            res.feasible = True
            res.stats.update({"d_used": d_used, "n_check_iters": n_check,
                              "traj_timing": "uniform_placeholder"})

        res.total_tpr = 0.0 if not tpr_enabled() else 0.0  # λ_r = 0（槽位保留）
        res.stats["plan_time_s"] = round(time.perf_counter() - t0, 4)
        return res
