# -*- coding: utf-8 -*-
"""方法层工具集：坐标转换、DEM 地理参考查询与建筑图层。

相关模块：coordinates.py（坐标互转）、dem_geotiff.py（DEM 读取）、building_layer.py（建筑高度）。"""

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
from .building_layer import BuildingLayer
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
    "BuildingLayer",
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
