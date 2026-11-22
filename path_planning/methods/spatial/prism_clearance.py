# -*- coding: utf-8 -*-
"""真几何净空：折线/轨迹到障碍棱柱的最小 3D 距离（**不经栅格**）。

职责：用 shapely 对"点到棱柱"直接量距离，给出可复核的净空值、最差点与命中数。
边界：不建场、不查 SDF、不做判定（判据在 ``timeline/feasibility.py`` 与协调层）。
相关：``occupancy_sdf.py``（棱柱类型与保守膨胀）、``world_field_builder.py``
      （``obstacle_prisms`` 是棱柱的唯一来源）、参考实现 ``core/clearance_check.py``。

**为什么 SDF 不够**：栅格有三个先天误差——格心判据把障碍最多缩掉半格对角线、
细楼可能整栋不进栅格、三线性插值再加半格。即使按半格保守膨胀，竖直量化仍可达
2 m 量级（与竖直格距同量级）。所以 SDF 是"规划期近似"，**不能当交付净空口径**；
这里量的是拿尺子能复核的数（参考实现实测 SDF 比真几何乐观最大 26 m、p99 4.6 m）。

三条口径（照参考实现，不得引申）：

1. 建筑与禁飞区都视为**竖直棱柱** ``footprint × [z_low, z_high]``；点在棱柱正上/
   正下方时取竖直间距，其余取 ``sqrt(水平² + 竖直²)``。
2. 地面不参与：飞行真高带已把贴地飞行排除，地面不构成额外净空约束。
3. 邻域用**方框粗筛**而非 ``dwithin``：后者为每个查询点构造缓冲多边形，5 km 航段
   要十几秒；方框求交是纯索引比较。命中但真实距离超量程的值**一律截到量程上限**，
   与"附近什么都没有"同口径——同一字段不能有两种语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from shapely import STRtree, distance, points
from shapely import box as shapely_box
from shapely.geometry import Point, Polygon

from path_planning.common.plan_types import Vec3
from path_planning.methods.spatial.occupancy_sdf import ObstaclePrism

__all__ = [
    "DEFAULT_SEARCH_RADIUS_M",
    "PrismClearance",
    "densify_polyline",
]

#: 邻域搜索半径（米）。参考实现同名常量默认 30 m：与判定半径同量级即可，
#: 因为它只是"量程上限"，不是判据。
DEFAULT_SEARCH_RADIUS_M = 30.0


def densify_polyline(path, step_m: float) -> np.ndarray:
    """折线按 ``step_m`` 补点（含两端）；零长段退化为单点重复。

    必须**含两端**：端点贴楼是常见失败，漏掉端点会让净空虚高。这也是参考实现
    ``densify`` 的口径，别改成只采内部点。
    """
    pts = np.asarray(path, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError(f"path 应为 (n, 3)：{pts.shape}")
    if pts.shape[0] < 2:
        return pts.copy()
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    n = np.maximum(2, np.ceil(seg / max(float(step_m), 1e-6)).astype(int) + 1)
    chunks = [np.linspace(a, b, k) for a, b, k in zip(pts[:-1], pts[1:], n)]
    return np.vstack(chunks) if chunks else pts


@dataclass(frozen=True)
class PrismClearance:
    """一批静态障碍棱柱的真几何查询（STRtree 一次构建、多次量）。

    Attributes:
        search_radius_m: 量程上限（米）；超过它的距离一律报这个值
    """

    prisms: tuple[ObstaclePrism, ...]
    search_radius_m: float = DEFAULT_SEARCH_RADIUS_M

    def __post_init__(self) -> None:
        if self.search_radius_m <= 0.0:
            raise ValueError(f"search_radius_m 必须为正：{self.search_radius_m}")
        if not isinstance(self.prisms, tuple):
            object.__setattr__(self, "prisms", tuple(self.prisms))

    # -- 惰性几何（frozen dataclass 不能存可变索引，故用缓存字典）----------

    @property
    def _index(self):
        cache = getattr(self, "_index_cache", None)
        if cache is not None:
            return cache
        polys = [Polygon(p.footprint) for p in self.prisms]
        tree = STRtree(polys) if polys else None
        geoms = np.array(polys, dtype=object)
        z = (
            np.array([[p.z_low, p.z_high] for p in self.prisms], dtype=np.float64)
            if self.prisms else np.zeros((0, 2))
        )
        cache = (tree, geoms, z)
        object.__setattr__(self, "_index_cache", cache)
        return cache

    def __len__(self) -> int:
        return len(self.prisms)

    def contains(self, point) -> bool:
        """点是否落在任一楼柱内部/边界（端点落在禁飞区要单独失败）。"""
        tree, geoms, z = self._index
        if tree is None:
            return False
        x, y, zz = (float(v) for v in point)
        probe = Point(x, y)
        for idx in np.atleast_1d(tree.query(probe)):
            i = int(idx)
            if z[i, 0] <= zz <= z[i, 1] and (
                geoms[i].contains(probe) or geoms[i].touches(probe)
            ):
                return True
        return False

    def worst(self, path, sample_step_m: float = 1.0) -> tuple[float, Vec3]:
        """沿折线的最小净空与取到它的点；棱柱内部为 0，量程外截到量程上限。"""
        pts = densify_polyline(path, sample_step_m)
        tree, geoms, z = self._index
        if tree is None:
            return float(self.search_radius_m), _vec(pts[0])
        r = float(self.search_radius_m)
        probe = points(pts[:, 0], pts[:, 1])
        boxes = shapely_box(pts[:, 0] - r, pts[:, 1] - r, pts[:, 0] + r, pts[:, 1] + r)
        hit_pt, hit_poly = tree.query(boxes)
        if len(hit_pt) == 0:
            # 整条航段量程内没有障碍：报"至少这么远"，没有最差点，用起点充数
            return r, _vec(pts[0])
        d2 = np.asarray(distance(probe[hit_pt], geoms[hit_poly]), dtype=np.float64)
        ph = pts[hit_pt, 2]
        lo, hi = z[hit_poly, 0], z[hit_poly, 1]
        dz = np.where((ph >= lo) & (ph <= hi), 0.0,
                      np.minimum(np.abs(ph - lo), np.abs(ph - hi)))
        dist = np.sqrt(d2 * d2 + dz * dz)
        k = int(np.argmin(dist))
        return min(float(dist[k]), r), _vec(pts[hit_pt[k]])

    def min_clearance(self, path, sample_step_m: float = 1.0) -> float:
        """只取数值的最小净空。"""
        return float(self.worst(path, sample_step_m)[0])


def _vec(p) -> Vec3:
    return (float(p[0]), float(p[1]), float(p[2]))
