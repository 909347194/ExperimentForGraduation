# -*- coding: utf-8 -*-
"""纯几何工具（L1，无领域语义）。

职责：
    LocalCRS            WGS84 经纬度 ↔ 局部米制（局部等距圆柱，5km 域误差 <0.1%）
    segment_prism_hit   三维线段 × 竖直棱柱的精确相交判定    —— Lin2021 §2.2.1 Fig.6
    sample_segment      按弧长采样线段（供复核与平滑使用）

不负责：建筑/禁飞区对象语义（constraints/）；布点（offline/control_points.py）。
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

import numpy as np
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as _shp_transform


class LocalCRS:
    """WGS84 经纬度 → 局部米制（局部等距圆柱）。

    x = (lon - lon0) * 111320 * cos(lat0)；y = (lat - lat0) * 110574。
    域 >25 km 时应改用 UTM 49N（EPSG:32649）。
    """

    M_PER_DEG_LAT = 110574.0

    def __init__(self, lon0: float, lat0: float, lat_ref: Optional[float] = None):
        self.lon0 = float(lon0)
        self.lat0 = float(lat0)
        lat_ref = float(lat_ref) if lat_ref is not None else self.lat0
        self.m_per_deg_lon = 111320.0 * math.cos(math.radians(lat_ref))

    @classmethod
    def from_bbox(cls, bbox: Sequence[float]) -> "LocalCRS":
        """bbox = (minx, miny, maxx, maxy)，单位：经纬度。"""
        minx, miny, maxx, maxy = bbox
        return cls(minx, miny, lat_ref=(miny + maxy) / 2.0)

    def to_local(self, lon: float, lat: float) -> tuple[float, float]:
        return ((float(lon) - self.lon0) * self.m_per_deg_lon,
                (float(lat) - self.lat0) * self.M_PER_DEG_LAT)

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return (self.lon0 + float(x) / self.m_per_deg_lon,
                self.lat0 + float(y) / self.M_PER_DEG_LAT)

    def transform_geom(self, g: BaseGeometry) -> BaseGeometry:
        """经纬度几何 → 局部米制几何（逐坐标变换）。"""
        if g is None or g.is_empty:
            return g
        return _shp_transform(lambda x, y: self.to_local(x, y), g)


def segment_prism_hit(
    p: Sequence[float],
    q: Sequence[float],
    poly: BaseGeometry,
    z0: float,
    z1: float,
    margin: float = 0.0,
) -> bool:
    """三维线段 p→q 是否穿过竖直棱柱（底面 poly，竖向区间 [z0, z1]）。

    做法（精确、非采样）：把线段投影到 XY 平面求其在底面内的参数区间 [t0,t1]，
    沿该区间取 z 的范围，与 [z0,z1] 有交即判定穿越；竖直线段退化为点包含 + z 重叠。

    margin: 安全余量（米）——等价于把棱柱向外膨胀 margin 后判定（保守剪枝用）。
    """
    px, py, pz = float(p[0]), float(p[1]), float(p[2])
    qx, qy, qz = float(q[0]), float(q[1]), float(q[2])

    geom = poly.buffer(margin) if margin > 0 else poly
    if geom is None or geom.is_empty:
        return False

    len2d = math.hypot(qx - px, qy - py)
    if len2d < 1e-9:
        pt = Point(px, py)
        if not (geom.contains(pt) or geom.touches(pt)):
            return False
        zmin, zmax = (pz, qz) if pz <= qz else (qz, pz)
        return zmax >= z0 and zmin <= z1

    line = LineString([(px, py), (qx, qy)])
    if not line.intersects(geom):
        return False
    inter = line.intersection(geom)
    if inter.is_empty:
        return False

    gt = inter.geom_type
    if gt == "Point":
        ds = [line.project(inter)]
    elif gt in ("LineString", "MultiLineString"):
        parts = list(inter.geoms) if gt == "MultiLineString" else [inter]
        ds = []
        for part in parts:
            cs = list(part.coords)
            ds.append(line.project(Point(cs[0])))
            ds.append(line.project(Point(cs[-1])))
    else:
        ds = [line.project(Point(g)) for g in getattr(inter, "geoms", [inter])]
    if not ds:
        return False

    t0, t1 = max(0.0, min(ds) / len2d), min(1.0, max(ds) / len2d)
    if t1 < t0:
        return False
    zt0 = pz + t0 * (qz - pz)
    zt1 = pz + t1 * (qz - pz)
    zmin, zmax = (zt0, zt1) if zt0 <= zt1 else (zt1, zt0)
    return (zmax >= z0) and (zmin <= z1)


def sample_segment(p: Sequence[float], q: Sequence[float], step: float) -> np.ndarray:
    """线段按弧长采样（含两端点），step 为最大步长。"""
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    d = float(np.linalg.norm(q - p))
    n = max(2, int(math.ceil(d / max(step, 1e-6))) + 1)
    ts = np.linspace(0.0, 1.0, n)
    return p + ts[:, None] * (q - p)


def polyline_length(pts: np.ndarray) -> float:
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
