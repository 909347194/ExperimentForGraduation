# -*- coding: utf-8 -*-
"""建筑障碍简化 + R-tree 加速的视线（LOS）查询。

职责：
    SimplifiedObstacles
        把每栋建筑外扩 d_build 安全距离后简化成一个轻量多边形（AABB / 定向包围盒 /
        Douglas-Peucker 简化 / 凸包），保留各自楼高；用 shapely STRtree 做二维候选
        筛选，再用 utils.geometry.segment_prism_hit 做精确三维穿越判定，以及线段到
        最近障碍的多边形距离（净空）查询。

    NoFlyField
        静态 + （可选）动态禁飞区「按任意时段可能冲突」保守并入的静态禁飞场，
        同样用 STRtree 加速，供边/节点合法性判定一次性调用。

不负责：节点/边生成（nodes.py / edges.py）、搜索（search.py）。
"""

from __future__ import annotations

import time
from typing import Optional, Sequence

import numpy as np
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from path_planning.common.types import Building, DynamicNoFly, StaticNoFly
from path_planning.utils.geometry import segment_prism_hit


class SimplifiedObstacles:
    """外扩 + 简化建筑集合的碰撞查询（无时间：建筑是静态障碍）。"""

    def __init__(
        self,
        buildings: Sequence[Building],
        d_build: float = 5.0,
        mode: str = "simplify",
        tol: float = 3.0,
    ):
        self.d_build = float(d_build)
        self.mode = mode
        self.polys: list[Polygon] = []
        self.z1: list[float] = []
        t0 = time.perf_counter()
        for b in buildings:
            g = self._simplify(b.poly, mode, tol)
            if g is None or g.is_empty:
                continue
            poly = g.buffer(self.d_build) if self.d_build > 0 else g
            if poly.geom_type != "Polygon":
                poly = poly.convex_hull
                if poly.geom_type != "Polygon":
                    continue
            self.polys.append(poly)
            self.z1.append(float(b.height if b.height > 0 else (b.z1 - b.z0)))
        self.z1 = np.array(self.z1, dtype=float)
        self.tree = STRtree(self.polys) if self.polys else None
        self.build_time_s = time.perf_counter() - t0

    def filter_by_min_height(self, min_z: float) -> None:
        """丢弃楼高低于 min_z 的障碍（其顶面低于所有飞行节点，不会阻挡节点间连线）。

        直接就地缩减障碍数，重建 STRtree，显著加速边的 LOS 判定。
        """
        if not self.polys:
            return
        keep = [i for i in range(len(self.polys)) if self.z1[i] >= min_z]
        if len(keep) == len(self.polys):
            return
        self.polys = [self.polys[i] for i in keep]
        self.z1 = self.z1[keep]
        self.tree = STRtree(self.polys) if self.polys else None

    @staticmethod
    def _simplify(poly, mode: str, tol: float):
        if poly is None or poly.is_empty:
            return None
        if poly.geom_type != "Polygon":
            poly = poly.convex_hull
            if poly.geom_type != "Polygon":
                return None
        if mode == "convex_hull":
            return poly.convex_hull
        if mode == "axis_align":
            return box(*poly.bounds)
        if mode == "oriented_bbox":
            return poly.minimum_rotated_rectangle
        g = poly.simplify(tol, preserve_topology=True)
        return g if (not g.is_empty) else poly

    def _candidates(self, p, q, margin: float) -> list[int]:
        if self.tree is None:
            return []
        x0, x1 = min(p[0], q[0]) - margin, max(p[0], q[0]) + margin
        y0, y1 = min(p[1], q[1]) - margin, max(p[1], q[1]) + margin
        return [int(i) for i in self.tree.query(box(x0, y0, x1, y1))]

    def segment_clear(self, p: Sequence[float], q: Sequence[float], margin: float = 0.0) -> bool:
        """线段 p→q 不穿越任何建筑棱柱（z∈[0,height]）则为 True。"""
        if not self.polys:
            return True
        for i in self._candidates(p, q, margin):
            if segment_prism_hit(p, q, self.polys[i], 0.0, self.z1[i], margin=margin):
                return False
        return True

    def segment_clearance(self, p: Sequence[float], q: Sequence[float]) -> float:
        """线段到最近（已外扩）障碍多边形的 2D 净空（米）；无障碍返回 inf。"""
        if not self.polys:
            return float("inf")
        seg = LineString([(float(p[0]), float(p[1])), (float(q[0]), float(q[1]))])
        best = float("inf")
        for i in self._candidates(p, q, 0.0):
            d = seg.distance(self.polys[i])
            if d < best:
                best = d
        return best

    def point_clear(self, p: Sequence[float]) -> bool:
        """点 p 是否安全（不在任何建筑棱柱内）。"""
        return self.segment_clear(p, p, margin=0.0)

    def summary(self) -> dict:
        hs = [h for h in self.z1 if h > 0]
        return {
            "n_obstacles": len(self.polys),
            "simplify_mode": self.mode,
            "d_build": self.d_build,
            "height_max": float(max(hs)) if hs else 0.0,
            "build_time_s": round(self.build_time_s, 4),
        }


class NoFlyField:
    """静态 / 动态禁飞区合并成的静态不可飞场（z 区间 [z0,z1]，z1=inf 即不可飞越）。

    动态禁飞区若 `as_static=True`，则把每个时段的几何都按「任意时段可能冲突」并入
    （等价于保守离线障碍），不区分具体到达时刻。

    关键加速：把相同的 (z0,z1) 区间的禁飞多边形做 unary_union 合并，使每对线段只做
    极少次 segment_prism_hit（而非逐个时段），避免 O(候选边 × 时段数) 的爆量判定。
    """

    def __init__(
        self,
        static: Sequence[StaticNoFly] = (),
        dynamic: Sequence[DynamicNoFly] = (),
        as_static: bool = True,
    ):
        # 收集 (poly, z0, z1)
        raw: list[tuple] = []
        for z in static:
            raw.append((z.poly, float(z.z0), float(z.z1 if z.z1 is not None else 1e9)))
        if as_static:
            for z in dynamic:
                if z.revoked or not z.periods:
                    continue
                for p in z.periods:
                    raw.append((p.poly, float(p.z0), float(p.z1 if p.z1 is not None else 1e9)))
        # 按 (z0,z1) 分桶并合并；每个多边形（含合并后展开的多部件）独立登记其 z 区间
        buckets: dict[tuple[float, float], list] = {}
        for poly, z0, z1 in raw:
            buckets.setdefault((z0, z1), []).append(poly)
        self.polys: list = []
        self.z0: list[float] = []
        self.z1: list[float] = []
        for (z0, z1), polys in buckets.items():
            if len(polys) == 1:
                self.polys.append(polys[0])
                self.z0.append(z0)
                self.z1.append(z1)
            else:
                merged = unary_union(polys)
                geoms = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]
                for g in geoms:
                    if g.geom_type == "Polygon" and not g.is_empty:
                        self.polys.append(g)
                        self.z0.append(z0)
                        self.z1.append(z1)
        self.tree = STRtree(self.polys) if self.polys else None
        # 动态禁飞区始终单独留存，供 A* 查询时按时间窗检查（无论 as_static 如何）
        self.dynamic = [z for z in dynamic if not getattr(z, "revoked", False)]
        self.has_dynamic = len(self.dynamic) > 0

    def blocked(self, p: Sequence[float], q: Sequence[float], margin: float = 0.0) -> bool:
        """线段 p→q 是否穿越任一禁飞体（无时间入口）。"""
        if self.tree is None:
            return False
        x0, x1 = min(p[0], q[0]) - margin, max(p[0], q[0]) + margin
        y0, y1 = min(p[1], q[1]) - margin, max(p[1], q[1]) + margin
        for i in self.tree.query(box(x0, y0, x1, y1)):
            idx = int(i)
            if segment_prism_hit(p, q, self.polys[idx], self.z0[idx], self.z1[idx], margin=margin):
                return True
        return False

    def point_blocked(self, p: Sequence[float], margin: float = 0.0) -> bool:
        return self.blocked(p, p, margin=margin)

    def dynamic_blocked(self, p: Sequence[float], q: Sequence[float], t0: float, t1: float,
                        margin: float = 0.0) -> bool:
        """时间窗 [t0,t1] 内，线段 p→q 是否穿越任一*生效*的动态禁飞体（z 区间 [z0,z1]）。

        复用 DynamicNoFly.live_periods 取与 [t0,t1] 相交的时段，再用 segment_prism_hit 做
        精确三维穿越判定（z 方向同样校验）。用于 A* 查询期的「时间窗可行性门」，不污染建图。
        """
        if not self.has_dynamic:
            return False
        for zone in self.dynamic:
            for (ts, te, per) in zone.live_periods(t0, t1):
                if segment_prism_hit(
                    p, q, per.poly,
                    float(getattr(per, "z0", 0.0)),
                    float(getattr(per, "z1", 1e9)),
                    margin=margin,
                ):
                    return True
        return False

    def summary(self) -> dict:
        return {"n_nofly_prisms": len(self.polys)}
