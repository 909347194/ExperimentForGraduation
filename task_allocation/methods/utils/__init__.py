# -*- coding: utf-8 -*-
"""方法层工具集：WGS84 经纬度与米制平面坐标互转、GeoTIFF DEM 地理参考查询。

两个模块：

- :mod:`.coordinates`：文本坐标解析、WGS84 <-> 局部 ENU 米制平面 / UTM、大地线距离
- :mod:`.dem_geotiff`：无 GDAL 依赖的 GeoTIFF DEM 读取、像素 <-> 经纬度、高程采样

典型用法（把机巢经纬度转成 ``common.types.Nest`` 需要的米制 x / y）::

    from task_allocation.methods.utils import GeoTIFFDEM, LocalFrame, parse_coordinate

    dem = GeoTIFFDEM("task_allocation/data/ASTGTMV003_N29E091_dem.tif")
    frame = LocalFrame.from_geodetic_points(lats, lons)   # 取机巢群中心为原点
    lat, lon = parse_coordinate("29.6472N 91.136532E")
    x, y = frame.to_enu(lat, lon)[:2]
    z = dem.sample_elevation(lat, lon)                    # 机巢处地形高程
"""

from .coordinates import (
    WGS84_A,
    WGS84_B,
    WGS84_E2,
    WGS84_EP2,
    WGS84_F,
    WGS84_INV_F,
    LocalFrame,
    ecef_to_geodetic,
    format_coordinate,
    geodesic_distance,
    geodetic_to_ecef,
    latlon_to_local,
    latlon_to_utm,
    local_to_latlon,
    parse_coordinate,
    parse_coordinates,
    utm_crs_id,
    utm_to_latlon,
    utm_zone,
)
from .dem_geotiff import GeoTIFFDEM, GeoTransform

__all__ = [
    "WGS84_A",
    "WGS84_B",
    "WGS84_E2",
    "WGS84_EP2",
    "WGS84_F",
    "WGS84_INV_F",
    "LocalFrame",
    "GeoTIFFDEM",
    "GeoTransform",
    "ecef_to_geodetic",
    "format_coordinate",
    "geodesic_distance",
    "geodetic_to_ecef",
    "latlon_to_local",
    "latlon_to_utm",
    "local_to_latlon",
    "parse_coordinate",
    "parse_coordinates",
    "utm_crs_id",
    "utm_to_latlon",
    "utm_zone",
]
