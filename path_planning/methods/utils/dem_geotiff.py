# -*- coding: utf-8 -*-
"""dem_geotiff.py — 轻量 GeoTIFF DEM 读取与高程查询（无 GDAL 依赖）。

职责：解析 GeoTIFF 标签得到仿射变换；像素与经纬度互转；双线性高程采样；把经纬度 DEM
重采样为米制网格。
边界：只做栅格读取与采样，不计算航程；栅格解码用 PIL，坐标约定见 utils/coordinates.py。
相关：utils/coordinates.py、muas/cost/dem_terrain.py（消费米制地形网格）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import numpy as np

# 本模块唯一运行时跨文件依赖：fork 自 task_allocation 的米制地形网格结构。
# 复制而非 import 上层，原因是 path_planning 不在 task_allocation 包内，
# 反向依赖会让两个包无法独立运行（详见 docs/module_map.md）。
from .dem_terrain import DEMTerrain
from .geotiff_header import GeoTransform, parse_header

if TYPE_CHECKING:  # pragma: no cover
    from .coordinates import LocalFrame

__all__ = ["GeoTransform", "GeoTIFFDEM"]

_UNSET = object()  # “未显式指定”哨兵，用于区分 None（禁用空洞值）


# ---------------------------------------------------------------------------
# DEM 封装
# ---------------------------------------------------------------------------


class GeoTIFFDEM:
    """单波段 GeoTIFF DEM：地理参考 + 高程查询 + 米制重采样。

    栅格数据懒加载（首次访问 :attr:`elevation` 或采样时才解码），
    3601x3601 的 GDEM 约占 52 MB 内存。

    ``nodata``：空洞值。省略时依次取 GeoTIFF 的 GDAL_NODATA 标签、
    地理坐标系 DEM 的惯用值 -9999；显式传 ``None`` 则不做空洞值处理。
    """

    def __init__(
        self,
        path: str | Path,
        nodata: float | None | object = _UNSET,
    ) -> None:
        self._path = Path(path)
        self._transform, self._width, self._height, self._epsg, tag_nodata = parse_header(
            self._path
        )
        self._nodata = tag_nodata if nodata is _UNSET else nodata
        self._elevation: np.ndarray | None = None
        self._sampling_grid: tuple[np.ndarray, np.ndarray] | None = None

    # -- 基本信息 --------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    @property
    def transform(self) -> GeoTransform:
        return self._transform

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def epsg(self) -> int | None:
        """GeoKeyDirectory 中的坐标系代码（如 4326）；解析不到时为 None。"""
        return self._epsg

    @property
    def nodata(self) -> float | None:
        return self._nodata

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """像素外沿范围 ``(left, bottom, right, top)``，单位为度。"""
        t = self._transform
        left = t.c
        top = t.f
        right = t.c + self._width * t.xres
        bottom = t.f - self._height * t.yres
        return (left, bottom, right, top)

    @property
    def elevation(self) -> np.ndarray:
        """高程数组，shape = ``(height, width)``，按需解码。"""
        if self._elevation is None:
            self._elevation = self._load_elevation()
        return self._elevation

    def _sampling_layers(self) -> tuple[np.ndarray, np.ndarray]:
        """惰性构造「干净高程 + 空洞权重」两张与栅格同形的数组。

        只在首次采样时算一次并缓存：``to_metric_terrain`` 分块采样和撒点
        过滤都会反复调用 :meth:`sample_elevation`，若每次重建，大 DEM
        （如 3601x3601）会产生上百 MB 的重复分配。
        """
        cached = getattr(self, "_sampling_grid", None)
        if cached is None:
            raw = np.asarray(self.elevation, dtype=np.float64)
            if self._nodata is not None:
                invalid = ~np.isfinite(raw) | (raw == self._nodata)
            else:
                invalid = ~np.isfinite(raw)
            cached = (np.where(invalid, 0.0, raw), invalid.astype(np.float64))
            self._sampling_grid = cached
        return cached

    def _load_elevation(self) -> np.ndarray:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "读取 GeoTIFF 栅格需要 Pillow（pip install pillow）"
            ) from exc
        with Image.open(self._path) as img:
            arr = np.asarray(img)
        if arr.ndim != 2:
            raise ValueError(f"期望单波段栅格，得到 shape={arr.shape}")
        return arr

    # -- 像素 <-> 经纬度 -------------------------------------------------
    def pixel_to_latlon(self, row: float, col: float) -> tuple[float, float]:
        """像素**中心**索引 -> ``(纬度, 经度)``（支持小数索引）。"""
        t = self._transform
        lon = t.c + (col + 0.5) * t.xres
        lat = t.f - (row + 0.5) * t.yres
        return lat, lon

    def latlon_to_pixel(self, lat: float, lon: float) -> tuple[float, float]:
        """``(纬度, 经度)`` -> 像素**中心**索引 ``(row, col)``（支持小数）。"""
        t = self._transform
        col = (lon - t.c) / t.xres - 0.5
        row = (t.f - lat) / t.yres - 0.5
        return row, col

    # -- 高程采样 --------------------------------------------------------
    def sample_elevation(
        self,
        lat: float | np.ndarray,
        lon: float | np.ndarray,
        bilinear: bool = True,
    ) -> np.ndarray | float:
        """在 ``(纬度, 经度)`` 处采样高程；越界或加权邻域含空洞返回 ``np.nan``。

        ``bilinear=True`` 双线性插值，``False`` 取最近邻。
        标量输入返回 float，数组输入返回同形状 ndarray。

        空洞值（``nodata``）与非有限像素必须在插值**之前**掩掉：把
        -9999 / 255 当普通高程参与邻域加权，会产出有限却错误的高程
        （四邻含一个 -9999 时格心得到 -2424.75），而插值后再比较
        ``values == nodata`` 已判不中，错误值会直接流进垂直切面代价。
        判定按**权重**：只有实际参与加权的邻居是空洞才判无效，与
        ``DEMTerrain.query_elevations_batch`` 同口径，从而保证
        ``to_metric_terrain`` 的节点对齐契约。
        """
        lat_arr = np.asarray(lat, dtype=np.float64)
        lon_arr = np.asarray(lon, dtype=np.float64)
        scalar = lat_arr.ndim == 0 and lon_arr.ndim == 0
        la, lo = np.broadcast_arrays(lat_arr, lon_arr)

        row_f, col_f = self.latlon_to_pixel(la, lo)
        h, w = self.elevation.shape
        out_of_bounds = (row_f < 0) | (row_f > h - 1) | (col_f < 0) | (col_f > w - 1)

        clean, bad = self._sampling_layers()

        # row_f / col_f 已被上界约束在 [0, h-1] / [0, w-1]；clip 到
        # h-2 / w-2 后 dr、dc 自然落在 [0, 1]，不会产生负权重外插。
        row0 = np.clip(np.floor(row_f).astype(np.int64), 0, h - 2)
        col0 = np.clip(np.floor(col_f).astype(np.int64), 0, w - 2)
        dr = row_f - row0
        dc = col_f - col0

        if bilinear:
            w00 = (1 - dr) * (1 - dc)
            w01 = (1 - dr) * dc
            w10 = dr * (1 - dc)
            w11 = dr * dc
            values = (
                w00 * clean[row0, col0]
                + w01 * clean[row0, col0 + 1]
                + w10 * clean[row0 + 1, col0]
                + w11 * clean[row0 + 1, col0 + 1]
            )
            bad_weight = (
                w00 * bad[row0, col0]
                + w01 * bad[row0, col0 + 1]
                + w10 * bad[row0 + 1, col0]
                + w11 * bad[row0 + 1, col0 + 1]
            )
        else:
            row_i = np.clip(np.rint(row_f).astype(np.int64), 0, h - 1)
            col_i = np.clip(np.rint(col_f).astype(np.int64), 0, w - 1)
            values = clean[row_i, col_i]
            bad_weight = bad[row_i, col_i]

        values = np.asarray(values, dtype=np.float64)
        # 容差 1e-9：节点反算的浮点误差会给「0 权重」邻居约 1e-12 的权重，
        # 不应因此把一个有效节点判成空洞。
        values = np.where(bad_weight > 1e-9, np.nan, values)
        values = np.where(out_of_bounds, np.nan, values)
        return float(values) if scalar else values

    def sample_elevation_local(
        self,
        x: float | np.ndarray,
        y: float | np.ndarray,
        frame: "LocalFrame",
    ) -> np.ndarray | float:
        """在局部平面坐标 ``(x 东向, y 北向)`` 处采样高程（米）。"""
        lat, lon, _ = frame.to_geodetic(x, y, 0.0)
        return self.sample_elevation(lat, lon)

    # -- 米制重采样 ------------------------------------------------------
    def to_metric_terrain(
        self,
        frame: "LocalFrame",
        step_m: float = 50.0,
        extent: tuple[float, float, float, float] | None = None,
    ) -> "DEMTerrain":
        """重采样为局部平面的规则网格，返回 :class:`DEMTerrain`。

        参数：
            frame: 局部 ENU 平面（见 ``utils/coordinates.LocalFrame``）
            step_m: 网格间距（米）
            extent: 平面范围 ``(xmin, ymin, xmax, ymax)``；
                    省略时取 DEM 四角在平面中的包络

        返回的 ``DEMTerrain`` 与本方法的采样节点严格对齐：
        ``terrain.query_elevation(x_i, y_j) == grid[j, i]``，
        其中 ``x_i = xmin + i * step_m``、``y_j = ymax - j * step_m``。
        """
        if step_m <= 0:
            raise ValueError(f"step_m 必须为正：{step_m}")

        if extent is None:
            left, bottom, right, top = self.bounds
            # bounds 是像素**外沿**，而 latlon_to_pixel 以像素中心为整数索引；
            # 直接拿外沿当 extent 时，边界格点反算回经纬度会落在 DEM 之外
            # （叠加 ENU 反算的浮点误差更明显），造成网格外圈整圈 NaN
            # （guangzhou 实测 17.28%）。内缩一个像素：半像素把「外沿」
            # 映射到像素中心有效范围，另半像素吸收 ENU 正/反算在包络
            # 边缘的偏差（约数十米量级，不改变 DEM 内部采样值）。
            t = self._transform
            left += t.xres
            right -= t.xres
            top -= t.yres
            bottom += t.yres
            corner_lats = np.array([top, top, bottom, bottom])
            corner_lons = np.array([left, right, left, right])
            enu = frame.to_enu(corner_lats, corner_lons, 0.0)
            xmin = float(np.min(enu[..., 0]))
            xmax = float(np.max(enu[..., 0]))
            ymin = float(np.min(enu[..., 1]))
            ymax = float(np.max(enu[..., 1]))
        else:
            xmin, ymin, xmax, ymax = (float(v) for v in extent)
            if xmax <= xmin or ymax <= ymin:
                raise ValueError(f"extent 非法：{extent}")

        # 必须用 floor：round 在小数部分 >0.5 时会让最后一个节点
        # xmin+(nx-1)*step 超出 xmax 近一个 step（本场景 200m，远大于
        # DEM 像素尺寸），该列/行反算后整圈越界变成 NaN。
        nx = int(np.floor((xmax - xmin) / step_m)) + 1
        ny = int(np.floor((ymax - ymin) / step_m)) + 1
        if nx < 2 or ny < 2:
            raise ValueError("extent 相对 step_m 太小，无法构成网格")

        xs = xmin + np.arange(nx, dtype=np.float64) * step_m
        # 行序：北 -> 南，与 DEMTerrain 的 row_f = (top - y) / res_y 约定一致
        ys = ymax - np.arange(ny, dtype=np.float64) * step_m

        grid = np.empty((ny, nx), dtype=np.float64)
        chunk = max(1, int(2_000_000 // nx))
        for start in range(0, ny, chunk):
            stop = min(start + chunk, ny)
            gx, gy = np.meshgrid(xs, ys[start:stop])
            lat, lon, _ = frame.to_geodetic(gx, gy, 0.0)
            grid[start:stop, :] = self.sample_elevation(lat, lon, bilinear=True)

        # bounds 按 DEMTerrain 的节点索引约定给出，保证查询结果与 grid 一致
        bounds = (xmin, ymax - ny * step_m, xmin + nx * step_m, ymax)

        return DEMTerrain.from_array(grid, bounds=bounds)

