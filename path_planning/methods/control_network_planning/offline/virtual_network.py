# -*- coding: utf-8 -*-
"""扩展虚拟控制网络构建（Lin2021 §2.2.1 Fig.8-9）。

每个控制点派生 7 个运动状态虚拟节点（0=悬停；1..6=沿六方向以最高速通过）；
每条原始链路按方向归属连接 4 对虚拟链路：

    链路 A→B 方向属状态 s：  (0A→0B)、(0A→sB)、(sA→0B)、(sA→sB)
    反向 B→A 属状态 s'：     (0B→0A)、(0B→s'A)、(s'B→0A)、(s'B→s'A)

这样"转向必须减速"由网络结构自然表达（Fig.7/9：小角度可全速通过 s→s，
大角度只能 s→0→s' 即减速再加速）。

代价在构建时直接用 cost/link_cost 回填（Eq.1-4），省略独立 precompute 步骤。

不负责：链路剪枝（link_builder.py）、落盘（pipeline.py）。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from path_planning.common.config import UAVParams
from path_planning.common.types import (
    ControlPoint,
    ExpandedNetwork,
    Link,
    MotionState,
    VirtualLink,
    node_id,
)
from path_planning.methods.control_network_planning.cost.link_cost import link_time


def expand_network(
    cps: Sequence[ControlPoint],
    links: Sequence[Link],
    uav: UAVParams,
) -> ExpandedNetwork:
    """原始控制网络 → 扩展（7 状态）控制网络，含飞行时间代价。"""
    pts = np.array([cp.p for cp in cps], dtype=float)
    n_nodes = len(cps) * 7
    adj: list[list[tuple[int, float, float]]] = [[] for _ in range(n_nodes)]
    vlinks: list[VirtualLink] = []

    for l in links:
        for (i, j) in ((l.a, l.b), (l.b, l.a)):
            pi, pj = pts[i], pts[j]
            s = int(MotionState.from_dir(pj - pi))  # 该方向主导轴状态
            for sa in (0, s):
                for sb in (0, s):
                    t, d = link_time(pi, pj, sa, sb, uav)
                    u = node_id(i, sa)
                    v = node_id(j, sb)
                    adj[u].append((v, t, d))
                    vlinks.append(VirtualLink(u=u, v=v, time=t, dist=d))

    return ExpandedNetwork(
        points=pts,
        adj=adj,
        links=vlinks,
        meta={"n_virtual_links": len(vlinks), "n_links_raw": len(links)},
    )
