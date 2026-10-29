# -*- coding: utf-8 -*-
"""离线阶段编排门面（README §4.3.5 离线预处理）。

只做编排调度，不堆细节：

    CityEnvironment → control_points → link_builder → virtual_network → ExpandedNetwork

输出：扩展控制网络 + 各阶段统计（规模、剪枝数、耗时）。
"""

from __future__ import annotations

import time

from path_planning.common.config import Config
from path_planning.common.types import CityEnvironment, ControlPoint, ExpandedNetwork
from path_planning.methods.control_network_planning.constraints.query import CollisionQuery
from path_planning.methods.control_network_planning.offline.control_points import generate_control_points
from path_planning.methods.control_network_planning.offline.link_builder import build_links
from path_planning.methods.control_network_planning.offline.virtual_network import expand_network


def build_network(env: CityEnvironment, cfg: Config, verbose: bool = True) -> tuple[ExpandedNetwork, dict]:
    """离线建网全流程。"""
    t0 = time.perf_counter()
    ofp = cfg.offline
    query = CollisionQuery(env, d_s_stat=ofp.d_s_stat, d_s_dyn=ofp.d_s_dyn)

    def blocked(p) -> bool:
        # 落在建筑内的点（密集区外扩点常落进邻居）+ 静态禁飞体内的点，一并剔除
        return (not query.buildings.point_feasible(p)) or query.nofly.static_blocked(p, p)

    cps, cp_stats = generate_control_points(env, ofp, blocked_fn=blocked)
    t1 = time.perf_counter()

    links, link_stats = build_links(cps, query, ofp, verbose=verbose)
    t2 = time.perf_counter()

    net = expand_network(cps, links, cfg.uav)
    t3 = time.perf_counter()

    stats = {
        "scenario": env.summary(),
        "control_points": cp_stats,
        "links": link_stats,
        "network": {
            "n_control_points": net.n_points,
            "n_virtual_nodes": net.n_nodes,
            "n_virtual_links": net.meta["n_virtual_links"],
        },
        "timing_s": {
            "control_points": round(t1 - t0, 3),
            "link_builder": round(t2 - t1, 3),
            "expand": round(t3 - t2, 3),
            "total": round(t3 - t0, 3),
        },
    }
    if verbose:
        print(
            f"[offline] 控制点 {net.n_points} → 虚拟节点 {net.n_nodes} → "
            f"虚拟链路 {net.meta['n_virtual_links']}，"
            f"建网耗时 {stats['timing_s']['total']}s"
        )
    return net, stats


def build_control_points_only(env: CityEnvironment, cfg: Config):
    """仅布点（供可视化/调试）。"""
    query = CollisionQuery(env, d_s_stat=cfg.offline.d_s_stat, d_s_dyn=cfg.offline.d_s_dyn)
    return generate_control_points(
        env,
        cfg.offline,
        blocked_fn=lambda p: (not query.buildings.point_feasible(p))
        or query.nofly.static_blocked(p, p),
    )
