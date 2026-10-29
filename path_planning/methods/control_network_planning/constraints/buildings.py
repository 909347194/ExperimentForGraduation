# -*- coding: utf-8 -*-
"""建筑障碍模型。

职责：长方体化建筑集合的存储与碰撞查询（Lin2021 §2.2.1：每建筑视为 cuboid）。

    link_feasible    三维线段是否穿越任一建筑（离线剪枝 + 二次复核共用）
    point_feasible   点是否在建筑内部（控制点合法性校验）
    polyline_feasible 折线（平滑后路径）是否侵入建筑

实现：2D 用 STRtree 索引底面轮廓做候选筛选，再用 utils.geometry.segment_prism_hit
做精确的三维穿越判定（非采样）。

不负责：禁飞区（nofly_zones.py）；统一对外入口在 query.py。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from shapely.geometry import Point, box
from shapely.strtree import STRtree

from path_planning.common.types import Building
from path_planning.utils.geometry import segment_prism_hit


class BuildingQuery:
    """建筑碰撞查询（无时间：建筑是静态障碍）。"""

    def __init__(self, buildings: Sequence[Building]):
        self.buildings = list(buildings)
        self.polys = [b.poly for b in self.buildings]
        self.z0 = np.array([b.z0 for b in self.buildings], dtype=float)
        self.z1 = np.array([b.z1 for b in self.buildings], dtype=float)
        # shapely ≥2.0：STRtree.query 返回输入几何的**索引**
        self.tree = STRtree(self.polys) if self.polys else None

    def __len__(self) -> int:
        return len(self.buildings)

    def _candidates(self, p, q, margin: float) -> list[int]:
        if self.tree is None:
            return []
        x0, x1 = min(p[0], q[0]) - margin, max(p[0], q[0]) + margin
        y0, y1 = min(p[1], q[1]) - margin, max(p[1], q[1]) + margin
        return [int(i) for i in self.tree.query(box(x0, y0, x1, y1))]

    def link_feasible(self, p: Sequence[float], q: Sequence[float], margin: float = 0.0) -> bool:
        """线段 p→q 不穿越任何建筑（含 margin 安全膨胀）则为 True。"""
        if not self.buildings:
            return True
        for i in self._candidates(p, q, margin):
            if segment_prism_hit(p, q, self.polys[i], self.z0[i], self.z1[i], margin=margin):
                return False
        return True

    def blocking(self, p, q, margin: float = 0.0) -> list[int]:
        """返回与线段冲突的建筑 bid 列表（供 L2 删边诊断）。"""
        bad = []
        for i in self._candidates(p, q, margin):
            if segment_prism_hit(p, q, self.polys[i], self.z0[i], self.z1[i], margin=margin):
                bad.append(self.buildings[i].bid)
        return bad

    def point_feasible(self, p: Sequence[float], margin: float = 0.0) -> bool:
        pt = Point(float(p[0]), float(p[1]))
        for i, poly in enumerate(self.polys):
            g = poly.buffer(margin) if margin > 0 else poly
            if g.contains(pt) and self.z0[i] <= p[2] <= self.z1[i]:
                return False
        return True

    def polyline_feasible(self, pts: np.ndarray, margin: float = 0.0) -> bool:
        pts = np.asarray(pts, dtype=float)
        for a, b in zip(pts[:-1], pts[1:]):
            if not self.link_feasible(a, b, margin):
                return False
        return True
