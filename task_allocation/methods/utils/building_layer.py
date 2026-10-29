# -*- coding: utf-8 -*-
"""building_layer.py — 建筑轮廓/高度图层：城市垂直切面代价的障碍包络源。

城市环境中三维航程的主导竖向结构是**建筑**而非地形：垂直切面采样的障碍
包络 = max(地面高程, 建筑屋顶)。本模块把 ``data/<scenario>/building/`` 的
分类建筑 shapefile（WGS84 经纬度多边形 + ``Height`` 属性）载入局部 ENU
米制平面（与机巢/任务同一坐标框），提供按点查询的屋顶高度：

    heights_at(xs, ys) -> np.ndarray   不在任何建筑内 = 0，重叠取最高

高度语义（数据口径假设）：
    ``Height`` = 屋顶相对**当地地面**的高度（米，广州实测 1~225）。
    绝对屋顶高程 = 地面高程 + Height；无 DEM 时地面按 0 平面。

实现要点：
    * shapefile 读取用 pyshp（DBF 中文字段需 ``encoding='gbk'``）；
    * 顶点经 :meth:`LocalFrame.to_enu` **批量**转局部平面（向量化，
      2 万栋级一次性载入秒级）；
    * 查询内核 :class:`shapely.STRtree`（R-tree）：一次构建、批量点查询
      O(log n)，覆盖点取最高屋顶。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import shapefile as pyshp
from shapely import STRtree, points as shapely_points
from shapely.geometry import Polygon

#: 高度字段候选（按优先级取第一个存在的）
DEFAULT_HEIGHT_FIELDS: tuple[str, ...] = ("Height", "Height_1")


class BuildingLayer:
    """建筑图层（局部 ENU 米制平面；高度 = 屋顶相对当地地面）。"""

    def __init__(
        self,
        polygons: Sequence[Polygon],
        heights: Sequence[float],
    ) -> None:
        polys = list(polygons)
        hs = np.asarray(heights, dtype=np.float64)
        if len(polys) != hs.shape[0]:
            raise ValueError(
                f"polygons({len(polys)}) 与 heights({hs.shape[0]}) 数量不一致"
            )
        self._polys = polys
        self._heights = hs
        self._tree = STRtree(polys) if polys else None

    # -- 构建 --------------------------------------------------------

    @classmethod
    def from_polygons(
        cls,
        polygons: Sequence[Polygon],
        heights: Sequence[float],
    ) -> "BuildingLayer":
        """测试/合成入口：多边形已在局部米制平面。"""
        return cls(list(polygons), [float(h) for h in heights])

    @classmethod
    def from_shapefiles(
        cls,
        paths: Sequence[str | Path] | str | Path,
        frame,
        height_fields: Sequence[str] = DEFAULT_HEIGHT_FIELDS,
    ) -> "BuildingLayer":
        """载入建筑 shapefile（WGS84 经纬度多边形）→ 局部平面图层。

        Args:
            paths: ``.shp`` 路径序列，或**单个目录**（取目录下全部 ``*.shp``）。
            frame: 局部 ENU 平面（与机巢/任务同框）。
            height_fields: 高度字段候选，取第一个存在的。

        Raises:
            FileNotFoundError: 路径/目录不存在或目录内无 .shp。
            ValueError: 所有候选高度字段都缺失。
        """
        if isinstance(paths, (str, Path)):
            path_list = [Path(paths)]
        else:
            path_list = [Path(p) for p in paths]
        if len(path_list) == 1 and path_list[0].is_dir():
            path_list = sorted(path_list[0].glob("*.shp"))
        if not path_list:
            raise FileNotFoundError(f"没有建筑 shapefile：{paths}")

        polygons: list[Polygon] = []
        heights: list[float] = []
        for shp_path in path_list:
            reader = pyshp.Reader(str(shp_path), encoding="gbk")
            names = [f[0] for f in reader.fields[1:]]
            h_idx = next(
                (names.index(n) for n in height_fields if n in names), None
            )
            if h_idx is None:
                raise ValueError(
                    f"{shp_path} 缺少高度字段 {tuple(height_fields)}（实际：{names}）"
                )
            for rec, shp in zip(reader.iterRecords(), reader.iterShapes()):
                try:
                    h = float(rec[h_idx])
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(h) or h <= 0:
                    continue
                pts = np.asarray(shp.points, dtype=np.float64)  # (n, 2)：lon, lat
                if pts.shape[0] < 3:
                    continue
                starts = list(shp.parts) + [pts.shape[0]]
                for a, b in zip(starts[:-1], starts[1:]):
                    ring = pts[a:b]
                    if ring.shape[0] < 3:
                        continue
                    # shapefile 存 (lon, lat)；to_enu 吃 (lat, lon) → [x, y, z]
                    enu = frame.to_enu(ring[:, 1], ring[:, 0])
                    poly = cls._safe_polygon(enu[:, :2])
                    if poly is None:
                        continue
                    polygons.append(poly)
                    heights.append(h)
        return cls(polygons, heights)

    @staticmethod
    def _safe_polygon(xy: np.ndarray) -> Polygon | None:
        """构造有效多边形；自相交等脏环经 buffer(0) 修复，修不好则丢弃。"""
        try:
            poly = Polygon(xy)
        except Exception:
            return None
        if not poly.is_valid:
            fixed = poly.buffer(0)
            if fixed.geom_type == "MultiPolygon" and fixed.geoms:
                fixed = max(fixed.geoms, key=lambda g: g.area)
            if fixed.geom_type != "Polygon" or fixed.is_empty:
                return None
            poly = fixed
        return poly

    # -- 查询 --------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self._polys)

    def heights_at(self, xs, ys) -> np.ndarray:
        """逐点建筑屋顶高度（相对当地地面）。

        不在任何建筑内 = 0；被多栋覆盖取最高。支持任意 shape 的批量输入。
        """
        xa = np.asarray(xs, dtype=np.float64)
        ya = np.asarray(ys, dtype=np.float64)
        if xa.shape != ya.shape:
            raise ValueError(f"xs{xa.shape} 与 ys{ya.shape} 形状不一致")
        out = np.zeros(xa.shape, dtype=np.float64)
        if self._tree is None or xa.size == 0:
            return out
        flat = out.ravel()
        pts = shapely_points(xa.ravel(), ya.ravel())
        src, dst = self._tree.query(pts, predicate="intersects")
        if src.size:
            np.maximum.at(flat, src, self._heights[dst])
        return out
