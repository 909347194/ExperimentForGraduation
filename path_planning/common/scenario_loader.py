# -*- coding: utf-8 -*-
"""场景装配：``data/guangzhou`` → ``Scenario``。

职责：读机巢 CSV、DEM、建筑 shapefile、禁飞区 geojson，转到以机巢群中心为原点的
      局部 ENU 米制平面，装配成不可变 ``Scenario``。
边界：不做查询（``scenario_types.py``）、不做地形填充（``terrain_hole_filling.py``）。
相关：``scenario_types.py``、``terrain_hole_filling.py``、``methods/utils``。

坐标链路::

    CSV "23.111467N 113.307078E" --parse_coordinate--> (lat, lon)
        --LocalFrame.to_enu--> 米制平面 (x, y)
        --terrain.query_elevation--> 地面高程 z

数据与配置的边界：机巢位置、建筑、DEM 是**实测事实**（放 ``data/``）；用几个机巢、
容量多少是**实验设定**（放 ``experiments/<exp>/uav_config.json``），只经本模块参数注入。
"""

from __future__ import annotations

import csv as _csv
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from path_planning.common.scenario_types import (
    DEFAULT_CAPACITY,
    NestSite,
    NoFlyZone,
    Scenario,
)
from path_planning.common.terrain_hole_filling import fill_terrain_holes
from path_planning.methods.utils import (
    BuildingLayer,
    GeoTIFFDEM,
    LocalFrame,
    parse_coordinate,
)

__all__ = ["load_nest_latlon", "load_scenario"]

#: 机巢 CSV 的坐标列名
_COORD_COLUMN = "Coordinate"


def load_nest_latlon(
    csv_path: str | Path, column: str = _COORD_COLUMN
) -> list[tuple[float, float]]:
    """读机巢位置 CSV，返回 ``[(纬度, 经度), ...]``。

    首行表头，坐标列默认 ``Coordinate``，值形如 ``"23.111467N 113.307078E"``
    （**纬度在前**）。按 ``utf-8-sig`` 读以吃掉 Excel 导出的 BOM。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 无表头、缺列、或没有记录。
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"机巢位置文件不存在：{path}")

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV 没有表头：{path}")
        field_map = {n.strip().lstrip("\ufeff"): n for n in reader.fieldnames}
        if column not in field_map:
            raise ValueError(
                f"CSV 缺少列 {column!r}，实际列：{list(field_map)}（{path}）"
            )
        key = field_map[column]
        texts = [(row.get(key) or "").strip() for row in reader]
    texts = [t for t in texts if t]
    if not texts:
        raise ValueError(f"CSV 中没有机巢记录：{path}")
    return [parse_coordinate(t) for t in texts]


def load_scenario(
    data_dir: str | Path,
    *,
    name: str | None = None,
    nest_count: int | None = None,
    capacity: int = DEFAULT_CAPACITY,
    frame: LocalFrame | None = None,
    terrain_step_m: float = 30.0,
    fill_holes: bool = True,
    load_buildings: bool = True,
    load_nofly: bool = True,
    bbox_margin_m: float = 5.0,
) -> Scenario:
    """装配场景。

    Args:
        data_dir: 场景数据目录（``nest_location_data.csv``、``dem.tif``、``building/``、
                  ``nofly_*.geojson``）
        name:     场景名；缺省取目录名
        nest_count: 取前几个机巢；``None`` = 全部
        capacity: 单机巢容量
        frame:    指定平面原点；缺省取选中机巢的经纬度中心
        terrain_step_m: 米制地形网格间距
        fill_holes: 是否对地形 NaN 空洞做最近邻填充（**不要关**，见
                    ``terrain_hole_filling`` 的说明）
        load_buildings / load_nofly: 是否加载建筑 / 禁飞区
        bbox_margin_m: 可规划范围相对有数据区的外扩量

    Raises:
        FileNotFoundError: 数据目录不存在。
        ValueError: 机巢数/容量非法，或机巢超出 DEM 范围。
    """
    root = Path(data_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"场景数据目录不存在：{root}")
    scen_name = name or root.name

    csv_path = root / "nest_location_data.csv"
    coords = load_nest_latlon(csv_path)
    if nest_count is not None:
        if nest_count < 1:
            raise ValueError(f"nest_count 必须 >= 1：{nest_count}")
        if nest_count > len(coords):
            raise ValueError(
                f"请求 {nest_count} 个机巢，但 {csv_path} 只有 {len(coords)} 个"
            )
        coords = coords[:nest_count]
    if capacity < 1:
        raise ValueError(f"capacity 必须 >= 1：{capacity}")

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    if frame is None:
        frame = LocalFrame.from_geodetic_points(lats, lons)

    dem_path = root / "dem.tif"
    dem = GeoTIFFDEM(dem_path) if dem_path.is_file() else None
    terrain = (
        dem.to_metric_terrain(frame, step_m=terrain_step_m) if dem is not None else None
    )
    fill_stats: dict[str, Any] | None = None
    if terrain is not None and fill_holes:
        terrain, fill_stats = fill_terrain_holes(terrain)

    nests: list[NestSite] = []
    for k, (lat, lon) in enumerate(coords):
        enu = frame.to_enu(lat, lon, 0.0)
        x, y = float(enu[0]), float(enu[1])
        z_dem: float | None = None
        if dem is not None:
            z_raw = float(dem.sample_elevation(lat, lon))
            if math.isnan(z_raw):
                raise ValueError(f"机巢 {k} ({lat}, {lon}) 超出 DEM 范围，采样为 nan")
            z_dem = z_raw
        z = float(terrain.query_elevation(x, y)) if terrain is not None else (z_dem or 0.0)
        nests.append(
            NestSite(
                id=k, x=x, y=y, z=z, lat=lat, lon=lon, capacity=capacity, z_dem=z_dem
            )
        )

    buildings: BuildingLayer | None = None
    bdir = root / "building"
    if load_buildings and bdir.is_dir() and any(bdir.glob("*.shp")):
        buildings = BuildingLayer.from_shapefiles(bdir, frame)

    nofly: list[NoFlyZone] = []
    if load_nofly:
        for gpath in sorted(root.glob("nofly_*.geojson")):
            nofly.extend(_load_nofly_geojson(gpath, frame))

    data_bounds = _compute_bounds(terrain, nests)
    m = float(bbox_margin_m)
    bounds = (
        data_bounds[0] - m,
        data_bounds[1] - m,
        data_bounds[2] + m,
        data_bounds[3] + m,
    )

    return Scenario(
        name=scen_name,
        frame=frame,
        dem=dem,
        terrain=terrain,
        buildings=buildings,
        nests=tuple(nests),
        nofly=tuple(nofly),
        bounds_xy=bounds,
        data_bounds_xy=data_bounds,
        meta={
            "data_dir": str(root),
            "terrain_step_m": terrain_step_m,
            "capacity": capacity,
            "terrain_fill": fill_stats,
        },
    )


def _load_nofly_geojson(path: Path, frame: LocalFrame) -> list[NoFlyZone]:
    """读禁飞区 geojson（Polygon / MultiPolygon），转局部平面。"""
    with path.open(encoding="utf-8") as fh:
        obj = json.load(fh)
    feats = obj.get("features") if isinstance(obj, dict) else None
    if not feats:
        return []

    out: list[NoFlyZone] = []
    for i, feat in enumerate(feats):
        geom = (feat or {}).get("geometry") or {}
        props = dict((feat or {}).get("properties") or {})
        gtype = geom.get("type")
        raw = geom.get("coordinates") or []
        if gtype == "Polygon":
            rings = [raw[0]] if raw else []
        elif gtype == "MultiPolygon":
            rings = [poly[0] for poly in raw if poly]
        else:
            continue
        for j, ring in enumerate(rings):
            if len(ring) < 3:
                continue
            pts = np.asarray(ring, dtype=np.float64)  # (n, 2)：lon, lat
            enu = frame.to_enu(pts[:, 1], pts[:, 0])
            verts = tuple((float(a), float(b)) for a, b in enu[:, :2])
            # id 必须带文件名前缀：static 与 dynamic 各自用 1/2/3 编号，
            # 不加前缀会重号，下游按 id 索引时静默串台
            raw_id = props.get("id") or props.get("name") or f"{i}_{j}"
            out.append(
                NoFlyZone(
                    id=f"{path.stem}:{raw_id}",
                    vertices=verts,
                    z_min=_first_float(props, ("z_min", "min_alt", "floor")),
                    z_max=_first_float(props, ("z_max", "max_alt", "ceiling")),
                    meta=props,
                )
            )
    return out


def _first_float(props: dict, keys: Sequence[str]) -> float | None:
    for k in keys:
        if k in props and props[k] is not None:
            try:
                v = float(props[k])
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                return v
    return None


def _compute_bounds(
    terrain, nests: Sequence[NestSite]
) -> tuple[float, float, float, float]:
    """有数据区 ``(xmin, ymin, xmax, ymax)``。

    有地形时取**节点包络**而非 ``meta.bounds``：后者的 right/top 是"节点外沿"，
    ``query`` 在 ``x == right`` 处越界返回 NaN。照它划定有数据区，最外 30 m 一圈
    整圈查不到高程，而这一圈最容易被 SDF 采样到。
    """
    if terrain is not None:
        meta = terrain.meta
        left, _bottom, right, top = meta.bounds
        res_x, res_y = meta.resolution
        return (
            left,
            top - (meta.height - 1) * res_y,
            left + (meta.width - 1) * res_x,
            top,
        )
    if nests:
        return (
            min(n.x for n in nests),
            min(n.y for n in nests),
            max(n.x for n in nests),
            max(n.y for n in nests),
        )
    raise ValueError("既无地形也无机巢，无法确定平面范围")
