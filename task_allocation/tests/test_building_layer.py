# -*- coding: utf-8 -*-
"""建筑图层与建筑感知垂直切面代价的契约测试（2026-10-29 城市场景）。

契约：
    1. ``heights_at``：不在建筑内 = 0，覆盖点取最高屋顶；
    2. 垂直切面代价把建筑并入**障碍包络**（= 地面 + 建筑屋顶高）：
       航路必须爬升过建筑（≥ 屋顶 + 最小离地余量），航程不低于无建筑口径；
    3. 无 DEM 但有建筑：地面按 0 平面，建筑仍生效（航程 > 空间直线）；
    4. 无建筑时行为与改造前**逐位一致**（有 DEM 走地形跟随，无 DEM 走空间直线）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from shapely.geometry import box  # noqa: E402

from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain  # noqa: E402
from task_allocation.methods.muas.cost.vertical_section import (  # noqa: E402
    VerticalSectionCostEstimator,
)
from task_allocation.methods.utils.building_layer import BuildingLayer  # noqa: E402


def test_heights_at_inside_outside():
    layer = BuildingLayer.from_polygons([box(0, 0, 10, 10)], [30.0])
    zs = layer.heights_at(np.array([5.0, 15.0]), np.array([5.0, 5.0]))
    assert zs[0] == 30.0
    assert zs[1] == 0.0


def test_heights_at_overlap_takes_max():
    layer = BuildingLayer.from_polygons(
        [box(0, 0, 10, 10), box(5, 5, 15, 15)], [10.0, 20.0]
    )
    assert layer.heights_at(np.array([7.0]), np.array([7.0]))[0] == 20.0


def test_vertical_section_climbs_over_building():
    """有 DEM：建筑进障碍包络，航路爬过屋顶 + 最小离地余量。"""
    dem = DEMTerrain.flat(z0=0.0, bounds=(0, 0, 1000, 1000))
    est_plain = VerticalSectionCostEstimator(dem_terrain=dem)
    layer = BuildingLayer.from_polygons([box(400, 450, 600, 550)], [80.0])
    est_bld = VerticalSectionCostEstimator(dem_terrain=dem, buildings=layer)

    a, b = (100.0, 500.0, 100.0), (900.0, 500.0, 100.0)
    r0 = est_plain.estimate(a, b)
    r1 = est_bld.estimate(a, b)

    assert r1.distance >= r0.distance
    assert r1.profile_zs.max() >= 80.0  # 包络含屋顶
    # 航路在建筑上方必须 ≥ 屋顶 + min_clearance（=50）
    assert r1.path_zs.max() >= 80.0 + 50.0 - 1e-6


def test_vertical_section_buildings_without_dem():
    """无 DEM 但有建筑：地面按 0 平面，建筑仍构成障碍包络。"""
    layer = BuildingLayer.from_polygons([box(400, 450, 600, 550)], [80.0])
    est = VerticalSectionCostEstimator(
        dem_terrain=None, buildings=layer, min_clearance=50.0
    )
    a, b = (100.0, 500.0, 100.0), (900.0, 500.0, 100.0)
    r = est.estimate(a, b)

    assert r.distance > 800.0  # 爬升/下降使航程长于水平直线
    assert r.path_zs.max() >= 130.0 - 1e-6  # ≥ 屋顶 + min_clearance


def test_no_buildings_unchanged():
    """无建筑：历史口径逐位不变（无 DEM = 空间直线）。"""
    est = VerticalSectionCostEstimator(dem_terrain=None)
    a, b = (0.0, 0.0, 10.0), (300.0, 400.0, 50.0)
    r = est.estimate(a, b)
    expected = float(np.sqrt(300**2 + 400**2 + 40**2))
    assert r.distance == pytest.approx(expected)


def test_no_dem_no_building_profile_fields():
    """无 DEM 无建筑：历史退化分支的字段形状不变。"""
    est = VerticalSectionCostEstimator(dem_terrain=None)
    r = est.estimate((0.0, 0.0, 0.0), (100.0, 0.0, 0.0))
    assert r.profile_xs.shape == (2,)
    assert r.profile_zs.shape == (2,)
