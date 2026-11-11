# -*- coding: utf-8 -*-
"""dem_terrain.py — DEM 高程与垂直切面地形剖面。

从参考实现 environment_dmde/dem_terrain 精简而来：
    - from_array 无需 rasterio，便于仿真
    - extract_profile：沿 S-T 水平连线采样高程，映射到切面二维坐标
坐标默认按米制平面（输电巡检场景）；不依赖经纬度换算。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DEMMeta:
    width: int
    height: int
    resolution: tuple[float, float]  # (res_x, res_y)
    bounds: tuple[float, float, float, float]  # left, bottom, right, top


@dataclass(frozen=True)
class DEMProfile:
    """垂直切面上的地形剖面。"""

    distances: np.ndarray  # 沿水平投影的累计距离
    elevations: np.ndarray  # 对应高程
    coords_xy: np.ndarray  # shape (n, 2)


class DEMTerrain:
    """规则网格 DEM。"""

    def __init__(self, elevation: np.ndarray, meta: DEMMeta) -> None:
        if elevation.ndim != 2:
            raise ValueError(f"elevation must be 2-D, got {elevation.shape}")
        self._elevation = np.asarray(elevation, dtype=np.float64)
        self._meta = meta

    @property
    def meta(self) -> DEMMeta:
        return self._meta

    @classmethod
    def from_array(
        cls,
        elevation: np.ndarray,
        bounds: tuple[float, float, float, float],
    ) -> DEMTerrain:
        """bounds = (left, bottom, right, top)。"""
        elev = np.asarray(elevation, dtype=np.float64)
        if elev.ndim != 2:
            raise ValueError("elevation must be 2-D")
        height, width = elev.shape
        left, bottom, right, top = bounds
        res_x = (right - left) / max(width, 1)
        res_y = (top - bottom) / max(height, 1)
        meta = DEMMeta(
            width=width,
            height=height,
            resolution=(res_x, res_y),
            bounds=bounds,
        )
        return cls(elevation=elev, meta=meta)

    @classmethod
    def flat(
        cls,
        z0: float = 0.0,
        bounds: tuple[float, float, float, float] = (0.0, 0.0, 1000.0, 1000.0),
        shape: tuple[int, int] = (32, 32),
    ) -> DEMTerrain:
        """平坦地形（退化情况，航迹接近直线）。"""
        elev = np.full(shape, z0, dtype=np.float64)
        return cls.from_array(elev, bounds)

    def query_elevation(self, x: float, y: float) -> float:
        vals = self.query_elevations_batch(np.array([x]), np.array([y]))
        return float(vals[0])

    def query_elevations_batch(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        left, bottom, right, top = self._meta.bounds
        res_x, res_y = self._meta.resolution
        col_f = (xs - left) / max(res_x, 1e-12)
        row_f = (top - ys) / max(res_y, 1e-12)
        h, w = self._elevation.shape
        # 上界必须是严格大于：col_f = w-1 / row_f = h-1 是最后一个**有效
        # 节点**（bounds 的 right/top 是节点外沿，对应 col_f = w / row_f = h）。
        # 旧实现用 >= w-1 把最后一行/列整圈误判为越界，破坏
        # to_metric_terrain 承诺的 query(x_i, y_j) == grid[j, i]。
        mask = (col_f < 0) | (col_f > w - 1) | (row_f < 0) | (row_f > h - 1)
        # clip 上界保留 w-1 / h-1：最后一个节点要靠 dx=1 / dy=1 取到
        # 最后一列/行；若把 col_f 直接压到 w-2，dx 会变成 0 而错取倒数第二列。
        col_f = np.clip(col_f, 0, w - 1)
        row_f = np.clip(row_f, 0, h - 1)
        row0 = np.clip(np.floor(row_f).astype(int), 0, h - 2)
        col0 = np.clip(np.floor(col_f).astype(int), 0, w - 2)
        dy = row_f - row0
        dx = col_f - col0

        elev = np.asarray(self._elevation, dtype=np.float64)
        invalid = ~np.isfinite(elev)
        clean = np.where(invalid, 0.0, elev)
        bad = invalid.astype(np.float64)
        w00 = (1 - dx) * (1 - dy)
        w01 = dx * (1 - dy)
        w10 = (1 - dx) * dy
        w11 = dx * dy
        result = (
            w00 * clean[row0, col0]
            + w01 * clean[row0, col0 + 1]
            + w10 * clean[row0 + 1, col0]
            + w11 * clean[row0 + 1, col0 + 1]
        )
        # 只有实际参与加权的邻居无效才判 NaN（与 GeoTIFFDEM.sample_elevation
        # 同口径）。旧实现 any_nan 四邻全算，会让「邻居是空洞但权重为 0」
        # 的有效节点也变成 NaN，节点契约因此不成立。
        bad_weight = (
            w00 * bad[row0, col0]
            + w01 * bad[row0, col0 + 1]
            + w10 * bad[row0 + 1, col0]
            + w11 * bad[row0 + 1, col0 + 1]
        )
        result = np.asarray(result, dtype=np.float64)
        result[bad_weight > 1e-9] = np.nan
        result[mask] = np.nan
        return result

    def extract_profile(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        num_samples: int = 200,
    ) -> DEMProfile:
        if num_samples < 2:
            raise ValueError("num_samples must be >= 2")
        sx, sy = start
        ex, ey = end
        ts = np.linspace(0.0, 1.0, num_samples)
        xs = sx + (ex - sx) * ts
        ys = sy + (ey - sy) * ts
        elevations = self.query_elevations_batch(xs, ys)
        # 米制平面累计水平距离
        dxy = np.sqrt(np.diff(xs) ** 2 + np.diff(ys) ** 2)
        distances = np.concatenate([[0.0], np.cumsum(dxy)])
        coords = np.column_stack([xs, ys])
        return DEMProfile(distances=distances, elevations=elevations, coords_xy=coords)
