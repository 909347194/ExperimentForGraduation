# -*- coding: utf-8 -*-
"""城市环境/场景数据解析（L0 中唯一允许做文件读取的模块）。

职责：把 data/raw/<scenario_id>/ 下的原始数据解析为 CityEnvironment：

    building/*.shp       建筑轮廓 → 长方体（局部米制；DBF 为 **GBK 编码**）
    region.geojson       研究区外包（提供坐标换算原点）
    nofly_static.geojson 静态永久禁飞区（缺文件时为空集，不报错）
    nofly_dynamic.geojson 动态临时禁飞区（缺文件时为空集）
    dem.tif              地形（暂缺；预留入口，缺时按平地）

输入→输出：data/raw/<scenario_id>/* → CityEnvironment。

不负责：几何相交判定（utils/geometry.py）、控制点布设（offline/control_points.py）。
"""

from __future__ import annotations

import glob
import json
import math
import os
from datetime import datetime
from typing import Optional, Sequence

import numpy as np
import shapefile
from shapely.geometry import Polygon, box, shape as shp_shape
from shapely.geometry.base import BaseGeometry

from .types import Building, CityEnvironment, DynamicNoFly, Period, StaticNoFly
from ..utils.geometry import LocalCRS

# 建筑类别文件名 → 类别标签
DEFAULT_CLASSES = (
    "Residential",
    "Commercial",
    "TechnologyIndustry",
    "PublicServices",
    "EducationalCultural",
)


def _read_building_shp(path: str, bclass: str, crs: LocalCRS, bid0: int) -> list[Building]:
    """读一类建筑 shp → 局部米制长方体列表（DBF 必须 GBK 解码）。"""
    out: list[Building] = []
    r = shapefile.Reader(path, encoding="gbk", encodingErrors="replace")
    bid = bid0
    for sr in r.shapeRecords():
        try:
            geom = shp_shape(sr.shape.__geo_interface__)
        except Exception:
            continue
        if geom.is_empty:
            continue
        try:
            h = float(sr.record["Height"])
        except (KeyError, IndexError, TypeError, ValueError):
            h = 0.0
        if not np.isfinite(h) or h <= 0:
            h = 0.0
        poly = crs.transform_geom(geom)
        if poly.geom_type == "MultiPolygon":  # 少数要素是多部件，取最大部件
            poly = max(poly.geoms, key=lambda g: g.area)
        if poly.is_empty or poly.area < 1.0:  # 丢弃碎屑
            continue
        out.append(
            Building(
                bid=bid,
                poly=poly if poly.geom_type == "Polygon" else poly.buffer(0),
                z0=0.0,  # 平地假设（广州 DEM 待补）
                z1=h,
                bclass=bclass,
                height=h,
            )
        )
        bid += 1
    return out


def _load_region(scenario_dir: str) -> tuple[Optional[BaseGeometry], Optional[Sequence[float]]]:
    path = os.path.join(scenario_dir, "region.geojson")
    if not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        gj = json.load(f)
    feats = gj.get("features", [])
    if not feats:
        return None, None
    geom = shp_shape(feats[0]["geometry"])
    return geom, geom.bounds  # (minx, miny, maxx, maxy) 经纬度


def _geojson_polygons(path: str):
    with open(path, "r", encoding="utf-8") as f:
        gj = json.load(f)
    for feat in gj.get("features", []):
        yield feat.get("properties", {}) or {}, shp_shape(feat["geometry"])


def _parse_time(s, base: Optional[datetime]) -> float:
    """ISO 8601 → 相对秒（以 base 为原点；base 缺省则首见时刻为 0）。"""
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))
    if t.tzinfo is not None:
        t = t.replace(tzinfo=None)
    if base is None:
        return 0.0
    return (t - base).total_seconds()


def load_static_nofly(path: str, crs: LocalCRS, default_buffer: float) -> list[StaticNoFly]:
    if not os.path.exists(path):
        return []
    out = []
    for props, geom in _geojson_polygons(path):
        poly = crs.transform_geom(geom)
        if poly.is_empty:
            continue
        z1_raw = props.get("height_max")
        z1 = math.inf if (props.get("infinite") or z1_raw is None) else float(z1_raw)
        out.append(
            StaticNoFly(
                zid=str(props.get("id", f"NFZ-S-{len(out):03d}")),
                poly=poly.buffer(float(props["buffer_m"])) if props.get("buffer_m") else poly,
                z0=float(props.get("height_min", 0.0)),
                z1=z1,
                name=str(props.get("name", "")),
                source=str(props.get("source", "unknown")),
                buffer_m=float(props["buffer_m"]) if props.get("buffer_m") else None,
            )
        )
    return out


def load_dynamic_nofly(path: str, crs: LocalCRS) -> list[DynamicNoFly]:
    """动态禁飞区：periods 列表，每项可自带 geometry（几何随时段变化）。"""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        gj = json.load(f)
    feats = gj.get("features", [])
    if not feats:
        return []

    tb = gj.get("time_base") or (feats[0].get("properties", {}) or {}).get("time_base")
    base = datetime.fromisoformat(str(tb).replace(" ", "T")) if tb else None
    if base is None:
        starts = []
        for feat in feats:
            for p in (feat.get("properties", {}) or {}).get("periods", []):
                starts.append(str(p["start"]))
        if starts:
            base = datetime.fromisoformat(min(starts).replace(" ", "T"))

    out: list[DynamicNoFly] = []
    for feat in feats:
        props = feat.get("properties", {}) or {}
        top_geom = shp_shape(feat["geometry"]) if feat.get("geometry") else None
        feat_inf = bool(props.get("infinite"))
        feat_z1_raw = props.get("height_max")
        periods: list[Period] = []
        for p in props.get("periods", []):
            g = shp_shape(p["geometry"]) if p.get("geometry") else top_geom
            if g is None or g.is_empty:
                continue
            p_inf = bool(p.get("infinite", feat_inf))
            p_z1_raw = p.get("height_max", feat_z1_raw)
            z1 = math.inf if (p_inf or p_z1_raw is None) else float(p_z1_raw)
            periods.append(
                Period(
                    start=_parse_time(p["start"], base),
                    end=_parse_time(p["end"], base),
                    poly=crs.transform_geom(g),
                    z0=float(p.get("height_min", props.get("height_min", 0.0))),
                    z1=z1,
                )
            )
        if not periods:
            continue
        out.append(
            DynamicNoFly(
                zid=str(props.get("id", f"NFZ-D-{len(out):03d}")),
                periods=periods,
                name=str(props.get("name", "")),
                version=int(props.get("version", 1)),
                buffer_m=float(props["buffer_m"]) if props.get("buffer_m") else None,
                revoked=bool(props.get("revoked", False)),
            )
        )
    return out


def synthesize_dynamic_nofly(
    env: CityEnvironment,
    n: int = 2,
    radius: float = 60.0,
    t_start: float = 60.0,
    duration: float = 300.0,
    gap: float = 120.0,
    seed: int = 42,
    z_top: float = 80.0,
    source_classes: Sequence[str] = ("PublicServices", "EducationalCultural"),
) -> list[DynamicNoFly]:
    """合成动态禁飞区（**仅供无真实数据时的正确性调试**）。

    优先落在敏感设施（公共服务/教科文）建筑上方，否则落在建筑密集处；
    两个时段各用不同几何（覆盖 design §15 的完整形态）。
    """
    rng = np.random.default_rng(seed)
    cands = [b for b in env.buildings if b.bclass in source_classes] or env.buildings
    if not cands:
        return []
    picks = rng.choice(len(cands), size=min(n, len(cands)), replace=False)
    out = []
    for i, idx in enumerate(picks):
        b = cands[int(idx)]
        cx, cy = b.poly.centroid.x, b.poly.centroid.y
        out.append(
            DynamicNoFly(
                zid=f"NFZ-SYN-{i:03d}",
                name=f"synthetic-{i}",
                periods=[
                    Period(start=t_start, end=t_start + duration,
                           poly=box(cx - radius, cy - radius, cx + radius, cy + radius),
                           z0=0.0, z1=math.inf),
                    Period(start=t_start + duration + gap, end=t_start + 2 * duration + gap,
                           poly=box(cx - radius * 0.6, cy - radius * 0.6,
                                    cx + radius * 1.4, cy + radius * 1.4),
                           z0=0.0, z1=math.inf),
                ],
            )
        )
    return out


def load_scenario(
    scenario_dir: str,
    classes: Sequence[str] = DEFAULT_CLASSES,
    crop: Optional[Sequence[float]] = None,
    min_area: float = 1.0,
    with_nofly: bool = True,
) -> CityEnvironment:
    """加载场景 → CityEnvironment。

    crop: 局部米制裁剪窗口 (x0, y0, x1, y1)；None 表示全量（20,670 栋，慎用）。
    """
    region, bbox_ll = _load_region(scenario_dir)
    if bbox_ll is None:
        # 无 region.geojson 时以建筑数据自身 bbox 为原点
        bbox_ll = (113.2910928837698, 23.073499711374893,
                   113.33841698453455, 23.116852006517036)
    crs = LocalCRS.from_bbox(bbox_ll)

    buildings: list[Building] = []
    for cls in classes:
        for path in sorted(glob.glob(os.path.join(scenario_dir, "building", f"{cls}.shp"))):
            buildings.extend(_read_building_shp(path, cls, crs, bid0=len(buildings)))
    if not buildings:
        raise FileNotFoundError(f"未读到建筑数据：{scenario_dir}/building/*.shp")

    if crop is not None:
        win = box(*crop)
        buildings = [b for b in buildings if b.poly.intersects(win) and b.poly.area >= min_area]
        # 重新编号，保证 bid 连续
        for i, b in enumerate(buildings):
            b.bid = i

    region_local = crs.transform_geom(region) if region is not None else None
    env = CityEnvironment(buildings=buildings, region=region_local, dem=None)
    env.meta.update(
        {
            "scenario_dir": scenario_dir,
            "crs": {"lon0": crs.lon0, "lat0": crs.lat0, "m_per_deg_lon": crs.m_per_deg_lon},
            "classes": list(classes),
            "crop": list(crop) if crop else None,
        }
    )

    if with_nofly:
        env.static_nofly = load_static_nofly(
            os.path.join(scenario_dir, "nofly_static.geojson"), crs, default_buffer=0.0
        )
        env.dynamic_nofly = load_dynamic_nofly(
            os.path.join(scenario_dir, "nofly_dynamic.geojson"), crs
        )
        env.meta["nofly_files"] = {
            "static": os.path.exists(os.path.join(scenario_dir, "nofly_static.geojson")),
            "dynamic": os.path.exists(os.path.join(scenario_dir, "nofly_dynamic.geojson")),
        }
    return env
