# -*- coding: utf-8 -*-
"""dem_geotiff.py — 轻量 GeoTIFF DEM 读取、地理参考与高程查询。

针对 ``task_allocation/data/ASTGTMV003_N29E091_dem.tif``（ASTER GDEM v3：
1 角秒、3601x3601、int16、LZW、EPSG:4326）设计，对一般的单波段
GeoTIFF 同样适用。**不依赖 rasterio / GDAL / pyproj**：

    - 栅格解码：PIL（支持 LZW / Deflate / PackBits / 未压缩）
    - 地理参考：直接读 GeoTIFF 标签
      （ModelPixelScale + ModelTiepoint + GeoKeyDirectory），
      得到与 GDAL ``GetGeoTransform`` 一致的仿射参数（PixelIsArea 约定）

能力：
    1. ``pixel_to_latlon`` / ``latlon_to_pixel``：像素 <-> 经纬度互转
    2. ``sample_elevation``：经纬度 / 局部平面坐标处双线性高程采样（矢量化）
    3. ``to_metric_terrain``：把经纬度 DEM 重采样成米制规则网格，
       直接交给 ``muas.cost.dem_terrain.DEMTerrain`` 用于垂直切面代价估算

坐标约定见 ``utils/coordinates.py``：x 东向、y 北向、z 垂向，单位米。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
    from task_allocation.methods.utils.coordinates import LocalFrame

__all__ = ["GeoTransform", "GeoTIFFDEM"]

# --- GeoTIFF 标签号 -------------------------------------------------------
_TAG_IMAGEWIDTH = 256
_TAG_IMAGELENGTH = 257
_TAG_BITSPERSAMPLE = 258
_TAG_SAMPLESPERPIXEL = 277
_TAG_SAMPLEFORMAT = 339
_TAG_MODELPIXELSCALE = 33550
_TAG_MODELTRANSFORMATION = 33551
_TAG_MODELTIEPOINT = 33922
_TAG_GEOKEYDIRECTORY = 34735
_TAG_GEODOUBLEPARAMS = 34736
_TAG_GEOASCIIPARAMS = 34737
_TAG_GDAL_NODATA = 42113

_GEOKEY_PROJECTED_CS = 3072
_GEOKEY_GEOGRAPHIC_CS = 2048

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_TYPE_FMT = {3: "H", 4: "I", 5: "Q", 11: "f", 12: "d"}

_UNSET = object()  # “未显式指定”哨兵，用于区分 None（禁用空洞值）


@dataclass(frozen=True)
class GeoTransform:
    """与 GDAL ``GetGeoTransform`` 同口径的仿射参数。

    ``lon = c + col * xres``，``lat = f + row * yres``（col / row 为像素角点索引）。
    """

    c: float  # 左上角经度
    xres: float  # 每像素经度跨度（度）
    f: float  # 左上角纬度
    yres: float  # 每像素纬度跨度（度，通常为负值的相反数）

    def to_gdal(self) -> tuple[float, float, float, float, float, float]:
        """转成 GDAL 六元组 ``(c, xres, 0, f, 0, -yres)``。"""
        return (self.c, self.xres, 0.0, self.f, 0.0, -self.yres)

    @classmethod
    def from_gdal(cls, gt: Sequence[float]) -> "GeoTransform":
        return cls(c=float(gt[0]), xres=float(gt[1]), f=float(gt[3]), yres=abs(float(gt[5])))


# ---------------------------------------------------------------------------
# GeoTIFF 头解析（纯 struct，不读栅格数据）
# ---------------------------------------------------------------------------


def _read_tiff_tags(path: str | Path) -> dict[int, tuple[int, int, bytes, object]]:
    """读取第一个 IFD 的全部标签，返回 {tag: (type, count, value_bytes, file_obj)}。"""
    with open(path, "rb") as fh:
        header = fh.read(8)
        if len(header) < 8:
            raise ValueError(f"文件过短，不是 TIFF：{path}")
        if header[:2] == b"II":
            endian = "<"
        elif header[:2] == b"MM":
            endian = ">"
        else:
            raise ValueError(f"非法 TIFF 字节序标记：{header[:2]!r}")
        magic = struct.unpack(endian + "H", header[2:4])[0]
        if magic == 43:
            raise NotImplementedError("暂不支持 BigTIFF")
        if magic != 42:
            raise ValueError(f"非法 TIFF magic：{magic}")

        ifd_offset = struct.unpack(endian + "I", header[4:8])[0]
        fh.seek(ifd_offset)
        (count,) = struct.unpack(endian + "H", fh.read(2))
        tags: dict[int, tuple[int, int, bytes, object]] = {}
        for _ in range(count):
            entry = fh.read(12)
            if len(entry) < 12:
                raise ValueError("IFD 条目被截断")
            tag, typ, num = struct.unpack(endian + "HHI", entry[:8])
            tags[tag] = (typ, num, entry[8:12], endian)
        return tags


def _tag_values(tags: dict, tag: int, path: str | Path) -> tuple:
    """把某个标签的值读成 python 元组（自动处理内联/偏移）。"""
    if tag not in tags:
        raise KeyError(f"缺少 GeoTIFF 标签 {tag}")
    typ, num, value_bytes, endian = tags[tag]
    size = _TYPE_SIZE.get(typ)
    fmt = _TYPE_FMT.get(typ)
    if size is None or fmt is None:
        raise NotImplementedError(f"标签 {tag} 的类型 {typ} 暂不支持")
    nbytes = size * num
    if nbytes <= 4:
        raw = value_bytes[:nbytes]
    else:
        (offset,) = struct.unpack(endian + "I", value_bytes)
        with open(path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read(nbytes)
    return struct.unpack(endian + f"{num}{fmt}", raw)


def _tag_ascii(tags: dict, tag: int, path: str | Path) -> str:
    if tag not in tags:
        raise KeyError(tag)
    typ, num, value_bytes, endian = tags[tag]
    nbytes = num  # ASCII 每字符 1 字节
    if nbytes <= 4:
        raw = value_bytes[:nbytes]
    else:
        (offset,) = struct.unpack(endian + "I", value_bytes)
        with open(path, "rb") as fh:
            fh.seek(offset)
            raw = fh.read(nbytes)
    return raw.split(b"\x00")[0].decode("ascii", errors="replace").strip()


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
        self._transform, self._width, self._height, self._epsg, tag_nodata = _parse_header(
            self._path
        )
        self._nodata = tag_nodata if nodata is _UNSET else nodata
        self._elevation: np.ndarray | None = None

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
        """在 ``(纬度, 经度)`` 处采样高程；越界返回 ``np.nan``。

        ``bilinear=True`` 双线性插值，``False`` 取最近邻。
        标量输入返回 float，数组输入返回同形状 ndarray。
        """
        lat_arr = np.asarray(lat, dtype=np.float64)
        lon_arr = np.asarray(lon, dtype=np.float64)
        scalar = lat_arr.ndim == 0 and lon_arr.ndim == 0
        la, lo = np.broadcast_arrays(lat_arr, lon_arr)

        row_f, col_f = self.latlon_to_pixel(la, lo)
        h, w = self.elevation.shape
        out_of_bounds = (row_f < 0) | (row_f > h - 1) | (col_f < 0) | (col_f > w - 1)

        if bilinear:
            row0 = np.floor(row_f).astype(np.int64)
            col0 = np.floor(col_f).astype(np.int64)
            row0 = np.clip(row0, 0, h - 2)
            col0 = np.clip(col0, 0, w - 2)
            dr = row_f - row0
            dc = col_f - col0
            z00 = self.elevation[row0, col0].astype(np.float64)
            z01 = self.elevation[row0, col0 + 1].astype(np.float64)
            z10 = self.elevation[row0 + 1, col0].astype(np.float64)
            z11 = self.elevation[row0 + 1, col0 + 1].astype(np.float64)
            values = (
                z00 * (1 - dr) * (1 - dc)
                + z01 * (1 - dr) * dc
                + z10 * dr * (1 - dc)
                + z11 * dr * dc
            )
        else:
            row_i = np.clip(np.rint(row_f).astype(np.int64), 0, h - 1)
            col_i = np.clip(np.rint(col_f).astype(np.int64), 0, w - 1)
            values = self.elevation[row_i, col_i].astype(np.float64)

        values = np.asarray(values, dtype=np.float64)
        values = np.where(out_of_bounds, np.nan, values)
        if self._nodata is not None:
            values = np.where(values == self._nodata, np.nan, values)
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

        nx = int(round((xmax - xmin) / step_m)) + 1
        ny = int(round((ymax - ymin) / step_m)) + 1
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
        from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain

        return DEMTerrain.from_array(grid, bounds=bounds)


# ---------------------------------------------------------------------------
# 头解析
# ---------------------------------------------------------------------------


def _parse_header(
    path: str | Path,
) -> tuple[GeoTransform, int, int, int | None, float | None]:
    tags = _read_tiff_tags(path)

    (width,) = _tag_values(tags, _TAG_IMAGEWIDTH, path)
    (height,) = _tag_values(tags, _TAG_IMAGELENGTH, path)
    width, height = int(width), int(height)

    if _TAG_MODELTRANSFORMATION in tags:
        matrix = _tag_values(tags, _TAG_MODELTRANSFORMATION, path)
        transform = GeoTransform(
            c=matrix[3], xres=matrix[0], f=matrix[7], yres=abs(matrix[5])
        )
    elif _TAG_MODELPIXELSCALE in tags and _TAG_MODELTIEPOINT in tags:
        scale = _tag_values(tags, _TAG_MODELPIXELSCALE, path)
        tie = _tag_values(tags, _TAG_MODELTIEPOINT, path)
        if len(tie) < 6:
            raise ValueError(f"ModelTiepoint 长度异常：{len(tie)}")
        # tie = (i, j, k, X, Y, Z)：像素 (i, j) 对应地图坐标 (X, Y)
        sx, sy = float(scale[0]), float(scale[1])
        px, py, _, map_x, map_y, _ = (float(v) for v in tie[:6])
        transform = GeoTransform(
            c=map_x - px * sx,
            xres=sx,
            f=map_y + py * sy,
            yres=sy,
        )
    else:
        raise ValueError("缺少地理参考标签（ModelPixelScale / ModelTiepoint）")

    epsg: int | None = None
    if _TAG_GEOKEYDIRECTORY in tags:
        keys = _tag_values(tags, _TAG_GEOKEYDIRECTORY, path)
        num_keys = int(keys[3])
        for idx in range(num_keys):
            base = 4 + 4 * idx
            key_id, location, _count, value = (
                int(keys[base]),
                int(keys[base + 1]),
                int(keys[base + 2]),
                int(keys[base + 3]),
            )
            if location == 0 and key_id in (_GEOKEY_PROJECTED_CS, _GEOKEY_GEOGRAPHIC_CS):
                if value not in (32767, 0):  # 32767 = user-defined
                    epsg = value
                    break

    nodata: float | None = None
    if _TAG_GDAL_NODATA in tags:
        try:
            text = _tag_ascii(tags, _TAG_GDAL_NODATA, path)
            nodata = float(text) if text not in ("", "nan") else float("nan")
        except ValueError:
            nodata = None
    elif epsg in (4326, 4269, 4258):  # 地理坐标系 DEM 惯用 -9999 作空洞值
        nodata = -9999.0

    return transform, width, height, epsg, nodata
