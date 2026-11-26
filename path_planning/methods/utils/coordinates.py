# -*- coding: utf-8 -*-
"""局部 ENU 米制平面（x 东、y 北、z 上），以及经纬高与本平面的便捷换算。

职责：定义 ``LocalFrame``（椭球原点 + ENU 旋转）与标量便捷包装；并把椭球/文本
      坐标两层的公开名称在本模块**再导出**，调用方仍可 ``from ...utils.coordinates import``。
边界：纯几何换算，不读取 DEM，不涉及代价计算。
相关：``geodesy.py``（ECEF/大地线/UTM）、``coordinate_text.py``（文本解析）、
      ``utils/dem_geotiff.py`` 与 ``utils/building_layer.py`` 使用本模块的坐标约定。

``LocalFrame`` 的变换经椭球 ECEF 精确互转：平面距离与大地线距离的差异只来自
"平面化"本身（约 d²/2R²，100 km 约 0.12 m/km 量级），与投影近似无关。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .coordinate_text import format_coordinate, parse_coordinate, parse_coordinates
from .geodesy import (
    WGS84_A,
    WGS84_B,
    WGS84_E2,
    WGS84_EP2,
    WGS84_F,
    WGS84_INV_F,
    ecef_to_geodetic,
    geodesic_distance,
    geodetic_to_ecef,
    latlon_to_utm,
    utm_crs_id,
    utm_to_latlon,
    utm_zone,
)

__all__ = [
    "WGS84_A",
    "WGS84_B",
    "WGS84_F",
    "WGS84_INV_F",
    "WGS84_E2",
    "WGS84_EP2",
    "parse_coordinate",
    "parse_coordinates",
    "format_coordinate",
    "geodetic_to_ecef",
    "ecef_to_geodetic",
    "LocalFrame",
    "latlon_to_local",
    "local_to_latlon",
    "geodesic_distance",
    "utm_zone",
    "utm_crs_id",
    "latlon_to_utm",
    "utm_to_latlon",
]


@dataclass(frozen=True)
class LocalFrame:
    """以某个大地原点为基准的局部 ENU 米制平面。

    x = 东向（east）、y = 北向（north）、z = 垂向（up），单位米。
    """

    lat0: float
    lon0: float
    alt0: float = 0.0

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat0 <= 90.0:
            raise ValueError(f"lat0 超出 [-90, 90]：{self.lat0}")
        if not -180.0 <= self.lon0 <= 180.0:
            raise ValueError(f"lon0 超出 [-180, 180]：{self.lon0}")

    # -- 原点 ------------------------------------------------------------
    @classmethod
    def from_geodetic_points(
        cls,
        lats: Sequence[float] | np.ndarray,
        lons: Sequence[float] | np.ndarray,
        alts: Sequence[float] | np.ndarray | None = None,
    ) -> "LocalFrame":
        """取一组经纬度的算术中心作为平面原点。"""
        la = np.asarray(lats, dtype=np.float64)
        lo = np.asarray(lons, dtype=np.float64)
        if la.size == 0:
            raise ValueError("至少需要一个点")
        if la.shape != lo.shape:
            raise ValueError("lats 与 lons 形状不一致")
        alt0 = 0.0 if alts is None else float(np.mean(np.asarray(alts, dtype=np.float64)))
        return cls(lat0=float(np.mean(la)), lon0=float(np.mean(lo)), alt0=alt0)

    # -- 正算 ------------------------------------------------------------
    def to_enu(
        self,
        lat: float | np.ndarray,
        lon: float | np.ndarray,
        h: float | np.ndarray = 0.0,
    ) -> np.ndarray:
        """大地坐标 -> 局部 ENU，返回 ``[..., 3]`` 的 ``[x, y, z]``（米）。"""
        x, y, z = geodetic_to_ecef(lat, lon, h)
        x0, y0, z0 = geodetic_to_ecef(self.lat0, self.lon0, self.alt0)
        dx, dy, dz = x - x0, y - y0, z - z0

        lam = math.radians(self.lon0)
        phi = math.radians(self.lat0)
        sl, cl = math.sin(lam), math.cos(lam)
        sp, cp = math.sin(phi), math.cos(phi)

        e = -sl * dx + cl * dy
        n = -sp * cl * dx - sp * sl * dy + cp * dz
        u = cp * cl * dx + cp * sl * dy + sp * dz
        return np.stack(np.broadcast_arrays(e, n, u), axis=-1)

    # -- 反算 ------------------------------------------------------------
    def to_geodetic(
        self,
        x: float | np.ndarray,
        y: float | np.ndarray,
        z: float | np.ndarray = 0.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """局部 ENU（米）-> 大地坐标，返回 ``(纬度, 经度, 高程)``。"""
        ee = np.asarray(x, dtype=np.float64)
        nn = np.asarray(y, dtype=np.float64)
        uu = np.asarray(z, dtype=np.float64)

        lam = math.radians(self.lon0)
        phi = math.radians(self.lat0)
        sl, cl = math.sin(lam), math.cos(lam)
        sp, cp = math.sin(phi), math.cos(phi)

        dx = -sl * ee - sp * cl * nn + cp * cl * uu
        dy = cl * ee - sp * sl * nn + cp * sl * uu
        dz = cp * nn + sp * uu

        x0, y0, z0 = geodetic_to_ecef(self.lat0, self.lon0, self.alt0)
        return ecef_to_geodetic(x0 + dx, y0 + dy, z0 + dz)

    # -- 平面两点距离 ----------------------------------------------------
    def planar_distance(
        self,
        p: tuple[float, float] | Sequence[float],
        q: tuple[float, float] | Sequence[float],
    ) -> float:
        """平面内两点的水平距离（米，只取 x / y 分量）。

        ``p`` / ``q`` 为 ``(x, y)`` 或 ``(x, y, z)``，z 分量忽略。
        """
        p_arr = np.asarray(p, dtype=np.float64)
        q_arr = np.asarray(q, dtype=np.float64)
        if p_arr.shape != q_arr.shape or p_arr.shape[0] not in (2, 3):
            raise ValueError("p / q 应为等长的 2 维或 3 维坐标")
        return float(np.linalg.norm(p_arr[:2] - q_arr[:2]))


# 标量便捷包装（面向日常调用，数组批量用 LocalFrame.to_enu / to_geodetic）


def latlon_to_local(
    lat: float,
    lon: float,
    frame: LocalFrame,
    h: float = 0.0,
) -> tuple[float, float]:
    """``(纬度, 经度)`` -> ``(x 东向, y 北向)`` 米。"""
    enu = frame.to_enu(lat, lon, h)
    return float(enu[0]), float(enu[1])


def local_to_latlon(
    x: float,
    y: float,
    frame: LocalFrame,
    z: float = 0.0,
) -> tuple[float, float]:
    """``(x 东向, y 北向)`` 米 -> ``(纬度, 经度)``。"""
    lat, lon, _ = frame.to_geodetic(x, y, z)
    return float(lat), float(lon)
