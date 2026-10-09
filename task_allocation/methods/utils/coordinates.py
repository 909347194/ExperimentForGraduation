# -*- coding: utf-8 -*-
"""coordinates.py — WGS84 经纬度与米制平面坐标互转工具。

背景：
    DEM（``task_allocation/data/ASTGTMV003_N29E091_dem.tif``）的空间参考为
    地理坐标系 WGS 1984（EPSG:4326，角度单位：度，长半轴 6378137.0，
    反扁率 298.257223563）；
    而 task_allocation 内部模型（``common/types.py`` 的 Task / UAV / Nest）与
    垂直切面代价估算（``methods/muas/cost``）统一使用米制平面坐标 (x, y, z)。
    本模块补齐两者之间的坐标转换。

内容：
    1. ``parse_coordinate`` / ``format_coordinate``
       解析 / 生成 "29.6472N 91.136532E" 这类文本坐标（兼容度分秒）
    2. ``LocalFrame``
       WGS84 经纬高 <-> 局部 ENU 米制平面；经椭球 ECEF 精确互转，非近似展开
    3. ``latlon_to_utm`` / ``utm_to_latlon``
       WGS84 <-> UTM（如 EPSG:32646），用于与 ArcGIS 等 GIS 软件交换数据
    4. ``geodesic_distance``
       WGS84 椭球大地线距离（Vincenty 反解），用于校核平面距离的投影变形

约定：
    x = 东向（east，米），y = 北向（north，米），z = 垂向（up，米），
    与 ``common/types.py`` 中 ``Task.x/y/z`` 的口径一致。
    输入角度单位为度，输出距离单位为米。

依赖：仅 numpy（无 GDAL / pyproj）。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------
# WGS84 椭球常量（取自 DEM 的空间参考定义）
# ---------------------------------------------------------------------------

WGS84_A: float = 6378137.0  # 长半轴
WGS84_INV_F: float = 298.257223563  # 反扁率
WGS84_F: float = 1.0 / WGS84_INV_F
WGS84_B: float = WGS84_A * (1.0 - WGS84_F)  # 短半轴 6356752.314245179
WGS84_E2: float = WGS84_F * (2.0 - WGS84_F)  # 第一偏心率平方
WGS84_EP2: float = WGS84_E2 / (1.0 - WGS84_E2)  # 第二偏心率平方

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


# ---------------------------------------------------------------------------
# 文本坐标解析
# ---------------------------------------------------------------------------

# 去掉度分秒符号两侧空白，便于按空白/逗号切分
_SPACE_RE = re.compile(r"\s*([°'′\"″])\s*")

_ANGLE_RE = re.compile(
    r"^(?:(?P<h_pre>[NSEW]))?\s*"
    r"(?P<sgn>[+-])?\s*"
    r"(?P<deg>\d{1,3}(?:\.\d+)?)\s*"
    r"(?:[°]\s*(?:(?P<min>\d{1,2}(?:\.\d+)?)\s*"
    r"(?:['′]\s*(?:(?P<sec>\d{1,2}(?:\.\d+)?)\s*[\"″]?)?)?)?)?\s*"
    r"(?:(?P<h_post>[NSEW]))?$",
    re.IGNORECASE,
)

_FIELD_SPLIT_RE = re.compile(r"[\s,;]+")


def _parse_angle(token: str) -> tuple[float, str]:
    """把单个角度片段解析为（绝对值, 半球字母）。

    支持：``29.6472N`` / ``N29.6472`` / ``29.6472`` /
    ``29°38'49.9"N`` / ``29°38'49.9N``。
    """
    m = _ANGLE_RE.match(token.strip())
    if not m:
        raise ValueError(f"无法解析角度片段：{token!r}")
    deg = float(m.group("deg"))
    minutes = float(m.group("min") or 0.0)
    seconds = float(m.group("sec") or 0.0)
    if minutes >= 60.0 or seconds >= 60.0:
        raise ValueError(f"分/秒必须小于 60：{token!r}")
    value = deg + minutes / 60.0 + seconds / 3600.0
    hemi = (m.group("h_pre") or m.group("h_post") or "").upper()
    if m.group("sgn") == "-":
        value = -value
    elif hemi in ("S", "W"):
        value = -abs(value)
    return value, hemi


def parse_coordinate(text: str) -> tuple[float, float]:
    """解析一条坐标文本，返回 ``(纬度, 经度)``（十进制度）。

    支持写法::

        "29.6472N 91.136532E"   # 机巢数据 CSV 的格式
        "29.6472, 91.136532"
        "N29.6472 E91.136532"
        "29.6472 91.136532"     # 默认前纬后经
        "29°38'49.9\"N 91°8'11.5\"E"

    负值或 ``S`` / ``W`` 表示南纬 / 西经。
    """
    if not isinstance(text, str):
        raise TypeError(f"期望字符串，得到 {type(text).__name__}")
    cleaned = _SPACE_RE.sub(r"\1", text.strip())
    tokens = [t for t in _FIELD_SPLIT_RE.split(cleaned) if t]
    if len(tokens) != 2:
        raise ValueError(f"期望恰好两个角度片段，得到 {len(tokens)} 个：{text!r}")

    parsed = [_parse_angle(t) for t in tokens]
    lat = lon = None
    for (value, hemi), token in zip(parsed, tokens):
        if hemi in ("N", "S"):
            if lat is not None:
                raise ValueError(f"纬度重复：{text!r}")
            lat = value
        elif hemi in ("E", "W"):
            if lon is not None:
                raise ValueError(f"经度重复：{text!r}")
            lon = value

    if lat is None and lon is None:  # 无半球：前纬后经
        lat, lon = parsed[0][0], parsed[1][0]
    elif lat is None:
        lon_val = lon
        lat = parsed[0][0] if parsed[1][1] in ("E", "W") else parsed[1][0]
        lon = lon_val
    elif lon is None:
        lon = parsed[0][0] if parsed[1][1] in ("N", "S") else parsed[1][0]

    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"纬度超出 [-90, 90]：{lat}（{text!r}）")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"经度超出 [-180, 180]：{lon}（{text!r}）")
    return lat, lon


def parse_coordinates(text: str) -> list[tuple[float, float]]:
    """按行解析多条坐标，跳过空行，返回 ``[(lat, lon), ...]``。"""
    result: list[tuple[float, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        result.append(parse_coordinate(line))
    return result


def format_coordinate(lat: float, lon: float, ndigits: int = 6) -> str:
    """``parse_coordinate`` 的逆操作，生成 "29.647200N 91.136532E"。"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.{ndigits}f}{ns} {abs(lon):.{ndigits}f}{ew}"


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
# 局部 ENU 平面
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalFrame:
    """以某个大地原点为基准的局部 ENU 米制平面。

    x = 东向（east）、y = 北向（north）、z = 垂向（up），单位米。
    变换经椭球 ECEF 精确互转：平面距离与大地线距离的差异只来自
    “平面化”本身（约 d²/2R²，100 km 约 0.12 m/km 量级），与投影近似无关。
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
