# -*- coding: utf-8 -*-
"""WGS84 椭球换算：ECEF、大地线距离、UTM（Kruger 级数）。

职责：椭球常量的唯一定义处；经纬高 ↔ 地心地固直角坐标；Vincenty 大地线距离；
      UTM 正反算。
边界：不构造局部平面（``coordinates.LocalFrame`` 用本模块的 ECEF 互转），
      不解析文本坐标（``coordinate_text``）。
相关：``coordinates.py``（局部 ENU）、``dem_geotiff.py``（DEM 的地理参考）。
"""

from __future__ import annotations

import math

import numpy as np

__all__ = [
    "WGS84_A",
    "WGS84_B",
    "WGS84_E2",
    "WGS84_EP2",
    "WGS84_F",
    "WGS84_INV_F",
    "ecef_to_geodetic",
    "geodesic_distance",
    "geodetic_to_ecef",
    "latlon_to_utm",
    "utm_crs_id",
    "utm_to_latlon",
    "utm_zone",
]

# ---------------------------------------------------------------------------
# WGS84 椭球常量（取自 DEM 的空间参考定义）
# ---------------------------------------------------------------------------

WGS84_A: float = 6378137.0  # 长半轴
WGS84_INV_F: float = 298.257223563  # 反扁率
WGS84_F: float = 1.0 / WGS84_INV_F
WGS84_B: float = WGS84_A * (1.0 - WGS84_F)  # 短半轴 6356752.314245179
WGS84_E2: float = WGS84_F * (2.0 - WGS84_F)  # 第一偏心率平方
WGS84_EP2: float = WGS84_E2 / (1.0 - WGS84_E2)  # 第二偏心率平方


# ---------------------------------------------------------------------------
# 椭球 ECEF 互转
# ---------------------------------------------------------------------------


def geodetic_to_ecef(
    lat: float | np.ndarray,
    lon: float | np.ndarray,
    h: float | np.ndarray = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """大地坐标（度, 度, 米）-> 地心地固直角坐标（米）。"""
    phi = np.deg2rad(np.asarray(lat, dtype=np.float64))
    lam = np.deg2rad(np.asarray(lon, dtype=np.float64))
    hh = np.asarray(h, dtype=np.float64)
    sin_phi, cos_phi = np.sin(phi), np.cos(phi)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_phi**2)
    x = (n + hh) * cos_phi * np.cos(lam)
    y = (n + hh) * cos_phi * np.sin(lam)
    z = (n * (1.0 - WGS84_E2) + hh) * sin_phi
    return x, y, z


def ecef_to_geodetic(
    x: float | np.ndarray,
    y: float | np.ndarray,
    z: float | np.ndarray,
    tol: float = 1e-12,
    max_iter: int = 15,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """地心地固直角坐标（米）-> 大地坐标（纬度, 经度, 高程）。"""
    xx = np.asarray(x, dtype=np.float64)
    yy = np.asarray(y, dtype=np.float64)
    zz = np.asarray(z, dtype=np.float64)

    lam = np.arctan2(yy, xx)
    p = np.hypot(xx, yy)
    lat = np.arctan2(zz, p * (1.0 - WGS84_E2))

    p_safe = np.where(p > 1e-9, p, 1.0)  # 极点附近避免除零
    for _ in range(max_iter):
        sin_lat = np.sin(lat)
        n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
        h = p_safe / np.cos(lat) - n
        denom = p * (1.0 - WGS84_E2 * n / (n + h))
        lat_new = np.arctan2(zz, np.where(p > 1e-9, denom, 1.0))
        if np.all(np.abs(lat_new - lat) < tol):
            lat = lat_new
            break
        lat = lat_new

    sin_lat = np.sin(lat)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    with np.errstate(divide="ignore", invalid="ignore"):
        h = np.where(p > 1e-9, p / np.cos(lat) - n, np.abs(zz) - WGS84_B)
    return np.rad2deg(lat), np.rad2deg(lam), h


# ---------------------------------------------------------------------------
# 大地线距离（Vincenty 反解）
# ---------------------------------------------------------------------------


def geodesic_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """WGS84 椭球上两点的大地线距离（米，Vincenty 反解）。"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    lam = math.radians(lon2 - lon1)
    lon_diff = lam  # 迭代式固定加上原始经差 L，不是当前 λ

    u1 = math.atan((1.0 - WGS84_F) * math.tan(phi1))
    u2 = math.atan((1.0 - WGS84_F) * math.tan(phi2))
    sin_u1, cos_u1 = math.sin(u1), math.cos(u1)
    sin_u2, cos_u2 = math.sin(u2), math.cos(u2)

    lam_prev = lam
    sin_sigma = cos_sigma = sigma = sin_alpha = cos2_alpha = cos2_sigma_m = 0.0
    for _ in range(200):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(
            cos_u2 * sin_lam,
            cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lam,
        )
        if sin_sigma == 0.0:
            return 0.0  # 两点重合
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lam / sin_sigma
        cos2_alpha = 1.0 - sin_alpha**2
        if cos2_alpha == 0.0:  # 赤道线
            cos2_sigma_m = 0.0
        else:
            cos2_sigma_m = cos_sigma - 2.0 * sin_u1 * sin_u2 / cos2_alpha
        c = WGS84_F / 16.0 * cos2_alpha * (4.0 + WGS84_F * (4.0 - 3.0 * cos2_alpha))
        lam_prev, lam = lam, lon_diff + (1.0 - c) * WGS84_F * sin_alpha * (
            sigma
            + c
            * sin_sigma
            * (cos2_sigma_m + c * cos_sigma * (-1.0 + 2.0 * cos2_sigma_m**2))
        )
        if abs(lam - lam_prev) < 1e-13:
            break

    u_sq = cos2_alpha * (WGS84_A**2 - WGS84_B**2) / WGS84_B**2
    a_coef = 1.0 + u_sq / 16384.0 * (
        4096.0 + u_sq * (-768.0 + u_sq * (320.0 - 175.0 * u_sq))
    )
    b_coef = u_sq / 1024.0 * (256.0 + u_sq * (-128.0 + u_sq * (74.0 - 47.0 * u_sq)))
    delta_sigma = b_coef * sin_sigma * (
        cos2_sigma_m
        + b_coef
        / 4.0
        * (
            cos_sigma * (-1.0 + 2.0 * cos2_sigma_m**2)
            - b_coef
            / 6.0
            * cos2_sigma_m
            * (-3.0 + 4.0 * sin_sigma**2)
            * (-3.0 + 4.0 * cos2_sigma_m**2)
        )
    )
    return WGS84_B * a_coef * (sigma - delta_sigma)


# ---------------------------------------------------------------------------
# UTM（横轴墨卡托，Kruger 级数）
# ---------------------------------------------------------------------------

UTM_K0 = 0.9996  # 中央经线比例因子
UTM_FALSE_EASTING = 500_000.0


def utm_zone(lon: float) -> int:
    """由经度（度）取 UTM 带号（1..60）。"""
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"经度超出 [-180, 180]：{lon}")
    if lon == 180.0:
        return 60
    return int((lon + 180.0) // 6.0) + 1


def utm_crs_id(zone: int, northern: bool = True) -> str:
    """UTM 带号 -> EPSG 代码字符串，如 46 北半球 -> ``"EPSG:32646"``。"""
    if not 1 <= zone <= 60:
        raise ValueError(f"UTM 带号超出 [1, 60]：{zone}")
    return f"EPSG:{(32600 if northern else 32700) + zone}"


def _utm_forward_constants() -> tuple[float, float, float, float]:
    n = WGS84_F / (2.0 - WGS84_F)
    a1 = n / 2.0 - 2.0 * n**2 / 3.0 + 5.0 * n**3 / 16.0
    a2 = 13.0 * n**2 / 48.0 - 3.0 * n**3 / 5.0
    a3 = 61.0 * n**3 / 248.0
    a_series = WGS84_A / (1.0 + n) * (1.0 + n**2 / 4.0 + n**4 / 64.0)
    return a_series, a1, a2, a3


def _utm_inverse_constants() -> tuple[float, float, float, float]:
    n = WGS84_F / (2.0 - WGS84_F)
    b1 = n / 2.0 - 2.0 * n**2 / 3.0 + 37.0 * n**3 / 96.0
    b2 = n**2 / 48.0 + n**3 / 15.0
    b3 = 17.0 * n**3 / 480.0
    a_series = WGS84_A / (1.0 + n) * (1.0 + n**2 / 4.0 + n**4 / 64.0)
    return a_series, b1, b2, b3


def latlon_to_utm(
    lat: float,
    lon: float,
    zone: int | None = None,
    northern: bool | None = None,
) -> tuple[float, float, int]:
    """WGS84 经纬度 -> UTM，返回 ``(东坐标, 北坐标, 带号)``（米）。

    ``northern`` 省略时按纬度符号判断；北半球北坐标不含 10000000 米假北。
    """
    if zone is None:
        zone = utm_zone(lon)
    if not 1 <= zone <= 60:
        raise ValueError(f"UTM 带号超出 [1, 60]：{zone}")
    if northern is None:
        northern = lat >= 0.0

    lam0 = math.radians(-183.0 + 6.0 * zone)
    phi = math.radians(lat)
    dlam = math.radians(lon) - lam0

    e = math.sqrt(WGS84_E2)
    q = math.asinh(math.tan(phi)) - e * math.atanh(e * math.sin(phi))
    beta = math.atan(math.sinh(q))
    eta0 = math.atanh(max(-1.0, min(1.0, math.cos(beta) * math.sin(dlam))))
    xi0 = math.asin(max(-1.0, min(1.0, math.sin(beta) * math.cosh(eta0))))

    a_series, a1, a2, a3 = _utm_forward_constants()
    xi = xi0 + (
        a1 * math.sin(2.0 * xi0) * math.cosh(2.0 * eta0)
        + a2 * math.sin(4.0 * xi0) * math.cosh(4.0 * eta0)
        + a3 * math.sin(6.0 * xi0) * math.cosh(6.0 * eta0)
    )
    eta = eta0 + (
        a1 * math.cos(2.0 * xi0) * math.sinh(2.0 * eta0)
        + a2 * math.cos(4.0 * xi0) * math.sinh(4.0 * eta0)
        + a3 * math.cos(6.0 * xi0) * math.sinh(6.0 * eta0)
    )

    easting = UTM_FALSE_EASTING + UTM_K0 * a_series * eta
    northing = UTM_K0 * a_series * xi
    if not northern:
        northing += 10_000_000.0
    return easting, northing, zone


def utm_to_latlon(
    easting: float,
    northing: float,
    zone: int,
    northern: bool = True,
) -> tuple[float, float]:
    """UTM -> WGS84 经纬度，返回 ``(纬度, 经度)``（度）。"""
    if not 1 <= zone <= 60:
        raise ValueError(f"UTM 带号超出 [1, 60]：{zone}")
    n_val = northing if northern else northing - 10_000_000.0
    lam0 = math.radians(-183.0 + 6.0 * zone)

    a_series, b1, b2, b3 = _utm_inverse_constants()
    eta_p = (easting - UTM_FALSE_EASTING) / (UTM_K0 * a_series)
    xi_p = n_val / (UTM_K0 * a_series)

    xi = xi_p - (
        b1 * math.sin(2.0 * xi_p) * math.cosh(2.0 * eta_p)
        + b2 * math.sin(4.0 * xi_p) * math.cosh(4.0 * eta_p)
        + b3 * math.sin(6.0 * xi_p) * math.cosh(6.0 * eta_p)
    )
    eta = eta_p - (
        b1 * math.cos(2.0 * xi_p) * math.sinh(2.0 * eta_p)
        + b2 * math.cos(4.0 * xi_p) * math.sinh(4.0 * eta_p)
        + b3 * math.cos(6.0 * xi_p) * math.sinh(6.0 * eta_p)
    )

    beta = math.asin(max(-1.0, min(1.0, math.sin(xi) / math.cosh(eta))))
    q = math.asinh(math.tan(beta))

    e = math.sqrt(WGS84_E2)
    phi = beta
    for _ in range(15):
        phi_new = math.atan(math.sinh(q + e * math.atanh(e * math.sin(phi))))
        if abs(phi_new - phi) < 1e-14:
            phi = phi_new
            break
        phi = phi_new

    lam = lam0 + math.atan2(math.sinh(eta), math.cos(xi))
    lat = math.degrees(phi)
    lon = math.degrees(lam)
    if lon > 180.0:
        lon -= 360.0
    elif lon < -180.0:
        lon += 360.0
    return lat, lon
