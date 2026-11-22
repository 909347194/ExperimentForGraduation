# -*- coding: utf-8 -*-
"""building_layer.py — 建筑轮廓/高度图层，为垂直切面代价提供障碍包络。

职责：载入 data/<scenario>/building/ 的建筑 shapefile，转到局部 ENU 平面；按点查询屋顶
高度（不在建筑内为 0，重叠取最高）。
边界：只提供高度查询，不做地形采样或路径规划；Height 为屋顶相对当地地面的高度。
相关：utils/coordinates.py（顶点转 ENU）、muas/cost/vertical_section.py（消费障碍包络）。"""

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

    # --- 以下三个只读访问器是 path_planning fork **相对上游新增**的 ---
    # SDF 构建需要逐个取多边形几何与高度，逐点 heights_at 拿不到"哪栋楼在哪"。
    # 只暴露只读视图，不参与任何计算逻辑，因此不影响与上游的逐点对拍。

    @property
    def polygons(self) -> tuple:
        """建筑多边形元组（局部米制平面，只读）。"""
        return tuple(self._polys)

    @property
    def heights(self) -> np.ndarray:
        """建筑屋顶高度 ``Height``（AGL，米）的只读副本。"""
        return self._heights.copy()

    @property
    def tree(self):
        """``STRtree`` 索引（只读）。用 ``tree.query(box(...))`` 取窗口内建筑下标。"""
        return self._tree

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
