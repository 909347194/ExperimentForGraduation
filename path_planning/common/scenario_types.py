# -*- coding: utf-8 -*-
"""场景数据类型：机巢、禁飞区、场景对象及其查询方法。

职责：定义 ``NestSite`` / ``NoFlyZone`` / ``Scenario``，并提供地面高程、屋顶高程、
      禁飞区点判、范围判等**轻量查询**。
边界：不做文件读取与装配（``scenario_loader.py``）、不做地形空洞填充
      （``terrain_hole_filling.py``）、不建 SDF。
相关：``scenario_loader.py``、``terrain_hole_filling.py``、``methods/utils``。

口径（与上游一致，不得"顺手改好"）：

- 机巢 ``z`` 取自**重采样后的米制 terrain**，与全模块的高程查询同源；``z_dem``
  是原始 DEM 直采样的诊断值，两者可差 1 m 级（实测 −0.34 ~ −1.3 m）。混用两个源
  等于给净空埋一个恒定偏移，且不会报错。
- 建筑 ``Height`` 是**屋顶相对当地地面**的 AGL，绝对 z = 地面高程 + Height。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from path_planning.methods.utils import BuildingLayer, DEMTerrain, GeoTIFFDEM, LocalFrame

__all__ = ["DEFAULT_CAPACITY", "NestSite", "NoFlyZone", "Scenario"]

#: 单机巢默认容量（架）。实验可覆盖。
DEFAULT_CAPACITY = 10


@dataclass(frozen=True)
class NestSite:
    """一个机巢。

    Attributes:
        id:       机巢 id（CSV 行序，0 起）
        x/y/z:    局部 ENU 米制坐标（z = 地面高程）
        lat/lon:  原始经纬度（溯源与跨模块对齐用）
        capacity: 可停架数
        z_dem:    原始 DEM 直采样高程（诊断用，见模块说明）
    """

    id: int
    x: float
    y: float
    z: float
    lat: float
    lon: float
    capacity: int = DEFAULT_CAPACITY
    z_dem: float | None = None

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True)
class NoFlyZone:
    """一块禁飞区（局部平面多边形）。

    Attributes:
        id:          来源标识（``<geojson 文件名>:<feature id>``，保证跨文件唯一）
        vertices:    平面顶点 ``((x, y), ...)``，首尾不必闭合
        z_min/z_max: 竖直范围（m，绝对高程）；``None`` = 不限
        active:      生效时间窗 ``(t_start, t_end)``；``None`` = 全时段
        meta:        原始 properties 透传
    """

    id: str
    vertices: tuple[tuple[float, float], ...]
    z_min: float | None = None
    z_max: float | None = None
    active: tuple[float, float] | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.vertices) < 3:
            raise ValueError(f"禁飞区 {self.id} 顶点少于 3 个")


@dataclass(frozen=True)
class Scenario:
    """一次算例的完整场景（不可变）。

    Attributes:
        name:           场景名（如 ``guangzhou``）
        frame:          局部 ENU 平面（原点默认取机巢群中心）
        dem:            原始 GeoTIFF DEM（可为 None）
        terrain:        米制地形网格（可为 None）
        buildings:      建筑图层（可为 None）
        nests:          机巢（按 CSV 行序）
        nofly:          禁飞区
        bounds_xy:      **可规划范围**（= 有数据区外扩 margin）
        data_bounds_xy: **有数据区**（地形节点包络）。高程查询按此夹持。
    """

    name: str
    frame: LocalFrame
    dem: GeoTIFFDEM | None
    terrain: DEMTerrain | None
    buildings: BuildingLayer | None
    nests: tuple[NestSite, ...]
    nofly: tuple[NoFlyZone, ...]
    bounds_xy: tuple[float, float, float, float]
    data_bounds_xy: tuple[float, float, float, float]
    meta: Mapping[str, Any] = field(default_factory=dict)

    # --- 派生 ---------------------------------------------------------

    @property
    def nest_count(self) -> int:
        return len(self.nests)

    @property
    def nest_ids(self) -> tuple[int, ...]:
        return tuple(n.id for n in self.nests)

    def nest(self, nest_id: int) -> NestSite:
        for n in self.nests:
            if n.id == nest_id:
                return n
        raise KeyError(f"场景 {self.name!r} 无 id={nest_id} 的机巢")

    # --- 高程查询 -----------------------------------------------------

    def ground_height(self, xs, ys) -> np.ndarray:
        """地面高程（m，绝对）。无地形数据时恒 0。

        查询点先夹到 ``data_bounds_xy``：夹持只吃掉浮点抖动与 margin 带，不会真的
        挪动 30 m。越界点是否参与规划由 ``is_inside_bounds`` 决定，不由这里静默否决。
        """
        xa = np.asarray(xs, dtype=np.float64)
        ya = np.asarray(ys, dtype=np.float64)
        if self.terrain is None:
            return np.zeros(xa.shape, dtype=np.float64)
        xmin, ymin, xmax, ymax = self.data_bounds_xy
        return np.asarray(
            self.terrain.query_elevations_batch(
                np.clip(xa.ravel(), xmin, xmax), np.clip(ya.ravel(), ymin, ymax)
            )
        ).reshape(xa.shape)

    def ground_height_at(self, x: float, y: float) -> float:
        return float(self.ground_height(np.array([x]), np.array([y]))[0])

    def roof_height_at(self, x: float, y: float) -> float:
        """单点屋顶高程；建筑外退化为地面高程。"""
        return float(self.roof_height(np.array([x]), np.array([y]))[0])

    def roof_height(self, xs, ys) -> np.ndarray:
        """屋顶高程 = 地面 + 建筑 AGL。建筑外即地面。"""
        base = self.ground_height(xs, ys)
        if self.buildings is None:
            return base
        return base + np.asarray(self.buildings.heights_at(xs, ys), dtype=np.float64)

    def is_inside_bounds(self, x: float, y: float) -> bool:
        xmin, ymin, xmax, ymax = self.bounds_xy
        return xmin <= x <= xmax and ymin <= y <= ymax

    def nofly_at(
        self, x: float, y: float, t: float | None = None, z: float | None = None
    ) -> str | None:
        """点是否落在某个禁飞区内；返回 id（落在多个取第一个），否则 None。

        只做平面点判 + 竖直/时间窗过滤，供快速剔除；正式判定在 SDF 场。
        """
        for zone in self.nofly:
            if not _point_in_polygon(x, y, zone.vertices):
                continue
            if z is not None:
                if zone.z_min is not None and z < zone.z_min:
                    continue
                if zone.z_max is not None and z > zone.z_max:
                    continue
            if t is not None and zone.active is not None:
                if not (zone.active[0] <= t <= zone.active[1]):
                    continue
            return zone.id
        return None

    def summary(self) -> dict[str, Any]:
        """一行式摘要，供日志与实验记录。"""
        return {
            "name": self.name,
            "nests": self.nest_count,
            "nofly": len(self.nofly),
            "buildings": 0 if self.buildings is None else self.buildings.count,
            "dem": self.dem is not None,
            "terrain": self.terrain is not None,
            "bounds_xy": tuple(round(v, 1) for v in self.bounds_xy),
            "frame_origin": (round(self.frame.lat0, 6), round(self.frame.lon0, 6)),
        }


def _point_in_polygon(x: float, y: float, verts) -> bool:
    """射线法点在多边形内判定（单一外环，不含洞）。"""
    inside = False
    n = len(verts)
    for i in range(n):
        x1, y1 = verts[i]
        x2, y2 = verts[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside
