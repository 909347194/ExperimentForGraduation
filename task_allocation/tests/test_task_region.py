# -*- coding: utf-8 -*-
"""城关区区域门禁回归测试（2026-10-26）。

契约：配置 ``region_geojson``（城关区多边形）后，无论撒点域是
``dem_extent`` 还是 ``nest_anchored``，**生成的任务必须全部落在多边形内**——
任务点不允许逸出城关区。无区域配置时行为与旧口径一致（不受限）。

用真实城关区数据（6 机巢 + Chengguan_DEM.tif + Chengguan_district.geojson）
做端到端验证，避免只看单元逻辑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.scenario import build_nest_scenario  # noqa: E402
from task_allocation.common.task_scenario import (  # noqa: E402
    DOMAIN_DEM_EXTENT,
    DOMAIN_NEST_ANCHORED,
    DOMAIN_REGION,
    RegionPolygon,
    SyntheticTaskConfig,
    _load_region,
    _pip,
    build_synthetic_tasks,
)

DATA = ROOT / "data" / "lasa_chengguan"  # 场景数据目录（data/<scenario>/，2026-10-27 起按场景分目录）
DEM = DATA / "Chengguan_DEM.tif"
NEST_CSV = DATA / "nest_location_data.csv"
REGION = DATA / "Chengguan_district.geojson"


def _scenario():
    return build_nest_scenario(
        NEST_CSV,
        count=6,
        capacity=4,
        column="Coordinate",
        dem_path=DEM,
    )


def _all_inside(tasks, region: RegionPolygon) -> bool:
    if not tasks:
        return True
    xy = np.array([[t.x, t.y] for t in tasks], dtype=float)
    return bool(region.contains(xy).all())


# ---------------------------------------------------------------------------
# 1. 射线法逐点判定正确性（单位正方形，含边界/外部/内部）
# ---------------------------------------------------------------------------


def test_pip_unit_square():
    ring = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    pts = np.array([
        [0.5, 0.5],  # 内部
        [0.0, 0.5],  # 左边界（算内）
        [1.0, 1.0],  # 顶点（算内）
        [-0.1, 0.5],  # 外部
        [2.0, 2.0],  # 外部
    ])
    inside = _pip(ring, pts)
    assert list(inside) == [True, True, True, False, False]


def test_pip_horizontal_edge_excluded():
    # 水平边不应被误判为穿越：点在水平边延长线上方/下方
    ring = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]])
    pts = np.array([[1.0, 0.0], [1.0, -0.5], [1.0, 1.0]])
    inside = _pip(ring, pts)
    assert list(inside) == [True, False, True]


# ---------------------------------------------------------------------------
# 2. 区域门禁：任务全部落在城关区多边形内（两个撒点域）
# ---------------------------------------------------------------------------


def test_dem_extent_tasks_inside_region():
    sc = _scenario()
    region = _load_region(REGION, sc.frame)
    assert region is not None, "城关区多边形应成功加载"
    cfg = SyntheticTaskConfig(
        n_tasks=80,
        seed=42,
        domain=DOMAIN_DEM_EXTENT,
        dem_inset_m=0.0,
        max_reject_trials=8,
        region_geojson=str(REGION),
    )
    tasks = build_synthetic_tasks(sc.nests, sc.frame, sc.dem, cfg)
    assert len(tasks) == 80
    outside = [t for t in tasks if not bool(region.contains(np.array([[t.x, t.y]]))[0])]
    assert _all_inside(tasks, region), (
        "dem_extent 下存在逸出城关区的任务点："
        f"{[(t.x, t.y) for t in outside[:5]]}"
    )


def test_nest_anchored_tasks_inside_region():
    sc = _scenario()
    region = _load_region(REGION, sc.frame)
    cfg = SyntheticTaskConfig(
        n_tasks=80,
        seed=7,
        domain=DOMAIN_NEST_ANCHORED,
        spread_m=15000.0,
        max_reject_trials=8,
        anchor_to_nests=True,
        region_geojson=str(REGION),
    )
    tasks = build_synthetic_tasks(sc.nests, sc.frame, sc.dem, cfg)
    assert len(tasks) == 80
    assert _all_inside(tasks, region), "nest_anchored 下存在逸出城关区的任务点"


def test_region_domain_tasks_inside_region():
    """domain=region：任务直接在多边形内均匀撒点，全部落在多边形内。"""
    sc = _scenario()
    region = _load_region(REGION, sc.frame)
    cfg = SyntheticTaskConfig(
        n_tasks=200,
        seed=123,
        domain=DOMAIN_REGION,
        max_reject_trials=8,
        region_geojson=str(REGION),
    )
    tasks = build_synthetic_tasks(sc.nests, sc.frame, sc.dem, cfg)
    assert len(tasks) == 200
    assert _all_inside(tasks, region), "domain=region 下存在逸出多边形的任务点"


def test_region_domain_requires_geojson():
    """domain=region 但未给 region_geojson 时应抛错（不静默退化）。"""
    sc = _scenario()
    cfg = SyntheticTaskConfig(
        n_tasks=5, seed=1, domain=DOMAIN_REGION, region_geojson=None,
    )
    try:
        build_synthetic_tasks(sc.nests, sc.frame, sc.dem, cfg)
        raised = False
    except ValueError:
        raised = True
    assert raised, "domain=region 缺少 region_geojson 应抛 ValueError"


def test_no_region_unchanged_behavior():
    """不设区域时：dem_extent 仍全域撒点、不报错（向后兼容）。"""
    sc = _scenario()
    cfg = SyntheticTaskConfig(
        n_tasks=30,
        seed=1,
        domain=DOMAIN_DEM_EXTENT,
        region_geojson=None,
    )
    tasks = build_synthetic_tasks(sc.nests, sc.frame, sc.dem, cfg)
    assert len(tasks) == 30  # 不受限仍生成满额


def test_region_geojson_missing_raises():
    sc = _scenario()
    cfg = SyntheticTaskConfig(
        n_tasks=5, seed=1, domain=DOMAIN_DEM_EXTENT,
        region_geojson=str(DATA / "no_such_district.geojson"),
    )
    try:
        build_synthetic_tasks(sc.nests, sc.frame, sc.dem, cfg)
        raised = False
    except FileNotFoundError:
        raised = True
    assert raised, "缺失的区域多边形文件应抛 FileNotFoundError"
