# -*- coding: utf-8 -*-
"""可见性图 / 稀疏路图规划器（path_planning 内自洽实现）。

替代原 control_network_planning（Lin2021）的另一种离线建图 + 在线搜索思路：
    - 建筑外扩安全距离后简化（STRtree 加速 LOS 查询）
    - 节点在简化建筑外轮廓按大步长采样 + 离散高度层 + 关键点
    - 边只连视线可达且不太远的节点对，离线一次碰撞检测；再引入机动保护区删高风险近距边
    - 堆优化 Dijkstra/A*（自实现邻接表，不引入 TPR / 第三方地面风险模型）

用法：
    from path_planning.methods.visibility_graph import build_visibility_graph, VisibilityGraphParams
    graph, stats = build_visibility_graph(env, params, extra_points=ods)
    poly, reason, cost = graph.plan(origin, dest)
"""

from __future__ import annotations

from .params import VisibilityGraphParams
from .obstacles import NoFlyField, SimplifiedObstacles
from .nodes import generate_nodes
from .edges import build_edges
from .search import shortest_path
from .pipeline import VisibilityGraph, build_visibility_graph

__all__ = [
    "VisibilityGraphParams",
    "SimplifiedObstacles",
    "NoFlyField",
    "generate_nodes",
    "build_edges",
    "shortest_path",
    "VisibilityGraph",
    "build_visibility_graph",
]
