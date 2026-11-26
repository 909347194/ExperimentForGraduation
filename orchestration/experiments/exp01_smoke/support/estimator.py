# -*- coding: utf-8 -*-
"""上层真代价估计器的可缩放包装 + DEM/建筑装配。

职责：把真正的 ``VerticalSectionCostEstimator``（DEM + 建筑垂直切面）包一层
      ``scale``，作为"上层这把尺子整体偏保守"的**一次性初值**；原估计器代码不改。
边界：只缩放输出距离/代价，不改剖面采样与障碍包络。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
from task_allocation.methods.muas.cost.vertical_section import (
    CostEstimationResult,
    VerticalSectionCostEstimator,
)
from task_allocation.methods.utils.building_layer import BuildingLayer
from task_allocation.methods.utils.coordinates import LocalFrame
from task_allocation.methods.utils.dem_geotiff import GeoTIFFDEM

from .config import resolve


class ScaledVerticalSectionEstimator:
    """真估计器 + 固定初值倍率（不随周期更新）；``estimate`` 签名与返回值形状保持一致。"""

    def __init__(self, inner: VerticalSectionCostEstimator, scale: float = 1.0) -> None:
        if scale <= 0.0:
            raise ValueError(f"scale 必须为正：{scale}")
        self.inner = inner
        self.scale = float(scale)

    def estimate(self, start, end, weight: float = 1.0) -> CostEstimationResult:
        result = self.inner.estimate(start, end, weight=weight)
        return replace(
            result,
            distance=float(result.distance) * self.scale,
            cost=float(result.cost) * self.scale,
        )


def build_estimator(cfg: dict[str, Any], scenario) -> tuple[ScaledVerticalSectionEstimator, dict[str, Any]]:
    """用与下层同一份数据建上层地形/建筑层，确认真场确实挂上。"""
    data_dir = resolve(cfg["data"]["scenario_dir"])
    frame = LocalFrame.from_geodetic_points(
        [float(n.lat) for n in scenario.nests],
        [float(n.lon) for n in scenario.nests],
    )
    dem = GeoTIFFDEM(data_dir / "dem.tif")
    u = cfg["upper"]
    xs = [float(n.x) for n in scenario.nests]
    ys = [float(n.y) for n in scenario.nests]
    margin = float(u["dem_margin_m"])
    extent = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
    terrain: DEMTerrain = dem.to_metric_terrain(frame, step_m=float(u["dem_step_m"]), extent=extent)

    bdir = data_dir / str(u["buildings_dir"])
    paths = sorted(bdir.glob("*.shp")) if bdir.is_dir() else [bdir]
    if not paths or not all(Path(p).is_file() for p in paths):
        raise FileNotFoundError(f"建筑 shapefile 不存在：{bdir}")
    buildings = BuildingLayer.from_shapefiles(paths, frame)

    cost = u["cost"]
    inner = VerticalSectionCostEstimator(
        dem_terrain=terrain,
        min_clearance=float(cost["min_clearance"]),
        max_clearance=float(cost["max_clearance"]),
        climb_rate=float(cost["climb_rate"]),
        num_samples=int(cost["num_samples"]),
        buildings=buildings,
    )
    fb = cfg["feedback"]
    scale = float(fb["initial_estimate_scale"]) if fb.get("enabled") else 1.0
    estimator = ScaledVerticalSectionEstimator(inner, scale=scale)
    info = {
        "dem_shape": [int(terrain.meta.height), int(terrain.meta.width)],
        "building_count": int(buildings.count),
        "frame": {"lat0": float(frame.lat0), "lon0": float(frame.lon0)},
        "scale": scale,
    }
    return estimator, info
