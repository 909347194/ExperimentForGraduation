# -*- coding: utf-8 -*-
"""几何与环境基础工具层（自包含复制，不跨包 import 上层同模块）。

职责：提供坐标换算、GeoTIFF DEM 读取、米制地形网格、建筑包络查询四组基础能力，
      并把常用 API 在本包命名空间集中导出，调用方无需记深路径。
边界：纯几何与栅格读取，不做路径搜索、不做冲突判定、不做代价计算。
      本层代码 **fork 自** ``task_allocation/methods/utils``，只允许改动
      依赖指向与注释，**算法与数值口径保持不变**——否则两边对拍会失去意义。

为什么复制而不是 import：

``task_allocation.common.scenario`` 调 ``methods.utils``，而 path_planning 的
``api`` 又要被上层 horizon 调用；若 path_planning 反向 import task_allocation，
两个包就无法独立运行（先 import 谁都能触发对方初始化）。复制的代价是 4 个
文件重复，风险是口径漂移——因此 **必须配逐点对拍测试**
（仓库根 ``tests/test_geometry_parity.py``，按"包内不 import 对方"的规则，它放在
两包之外），复制后任何数值差异都要在对拍里报出来。

已知两处与上游一致的口径，复制时不得"顺手修好"：

- 建筑 ``.dbf`` 是 **GBK** 编码，``pyshp`` 默认 utf-8 会抛
  ``dbfFileException``；``BuildingLayer`` 内部已按 GBK 读取。
- ``GeoTIFFDEM.elevation`` 是 **property 不是方法**，写成 ``d.elevation()``
  会抛 ``'numpy.ndarray' object is not callable``。

相关：``common/scenario.py``（场景装配）、``methods/spatial/field.py``（SDF 场）。
"""

from __future__ import annotations

from .building_layer import BuildingLayer
from .coordinates import (
    LocalFrame,
    WGS84_A,
    WGS84_B,
    WGS84_E2,
    WGS84_EP2,
    WGS84_F,
    WGS84_INV_F,
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
from .dem_terrain import DEMMeta, DEMProfile, DEMTerrain

__all__ = [
    # 坐标
    "WGS84_A",
    "WGS84_B",
    "WGS84_E2",
    "WGS84_EP2",
    "WGS84_F",
    "WGS84_INV_F",
    "LocalFrame",
    "parse_coordinate",
    "parse_coordinates",
    "format_coordinate",
    "geodetic_to_ecef",
    "ecef_to_geodetic",
    "latlon_to_local",
    "local_to_latlon",
    "geodesic_distance",
    "latlon_to_utm",
    "utm_to_latlon",
    "utm_zone",
    "utm_crs_id",
    # DEM 与地形
    "GeoTIFFDEM",
    "GeoTransform",
    "DEMTerrain",
    "DEMMeta",
    "DEMProfile",
    # 建筑
    "BuildingLayer",
]
