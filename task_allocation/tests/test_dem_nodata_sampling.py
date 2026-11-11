# -*- coding: utf-8 -*-
"""GeoTIFF DEM 空洞值处理与米制网格对齐契约（2026-11-10 修复）。

背景（只读审查实测结论）：
    1. ``sample_elevation`` 原先在双线性插值**之后**才比较 nodata，
       空洞像素（本场景 255，通用 -9999）会作为有限高程混进邻域加权和，
       产出「看似有效」的错误高程（合成复现 -2424.75）；
    2. ``to_metric_terrain`` 用 DEM 像素**外沿**当 extent，边界格点反算后
       落在 DEM 之外，形成整圈 NaN（guangzhou 实测 17.28%）；
    3. ``DEMTerrain.query_elevations_batch`` 的 mask 用 ``>= w-1`` / ``>= h-1``
       误杀最后一行/列，破坏 ``to_metric_terrain`` docstring 承诺的
       ``query_elevation(x_i, y_j) == grid[j, i]`` 节点对齐契约。

本文件锁定三件事：空洞邻域不得产出错误有限值、越界仍返回 NaN（撒点
过滤依赖此契约）、网格节点与查询严格对齐。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain  # noqa: E402
from task_allocation.methods.utils.coordinates import LocalFrame  # noqa: E402
from task_allocation.methods.utils.dem_geotiff import GeoTIFFDEM  # noqa: E402


def _synthetic_dem(
    arr: np.ndarray,
    nodata: float | None,
    *,
    c: float = 113.0,
    f: float = 23.2,
    res: float = 0.001,
) -> GeoTIFFDEM:
    """绕过文件 IO 造一个内存 GeoTIFFDEM（只填采样所需状态）。"""
    g = object.__new__(GeoTIFFDEM)
    g._elevation = np.asarray(arr, dtype=np.float64)
    g._height, g._width = g._elevation.shape
    g._nodata = nodata
    g._sampling_grid = None
    g._transform = types.SimpleNamespace(c=c, f=f, xres=res, yres=res)
    g._path = Path("synthetic.tif")
    return g


def _grid_dem(nodata: float = 255.0, *, with_hole: bool = True) -> GeoTIFFDEM:
    """3x3 栅格，中心像素为 nodata；transform 使格心 (1.5, 1.5) 可采样。"""
    arr = np.array([[10.0, 20.0, 30.0],
                    [40.0, -9999.0 if nodata == -9999.0 else 255.0, 60.0],
                    [70.0, 80.0, 90.0]])
    if not with_hole:
        arr[1, 1] = 50.0
    # c=0, f=3, res=1（度）：格心采样 lat=2.0, lon=1.0 -> row=0.5, col=0.5
    return _synthetic_dem(arr, nodata, c=0.0, f=3.0, res=1.0)


# ---------------------------------------------------------------------------
# 1) nodata 不得混进插值 / 最近邻
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nodata", [-9999.0, 255.0])
def test_bilinear_with_nodata_neighbor_returns_nan(nodata: float):
    """格心四邻各占 0.25 权重，中心是 nodata -> 必须判无效，不得给出错误有限值。"""
    g = _grid_dem(nodata)
    z = g.sample_elevation(2.0, 1.0, bilinear=True)
    assert np.isnan(z), f"nodata 混进加权和，得到「看似有效」的错误高程 {z}"


def test_bilinear_without_nodata_is_unchanged():
    """无空洞时双线性结果与修复前逐位一致（10/20/40/50 四格平均 = 30）。"""
    g = _grid_dem(with_hole=False)
    z = g.sample_elevation(2.0, 1.0, bilinear=True)
    assert z == pytest.approx(30.0)


@pytest.mark.parametrize("nodata", [-9999.0, 255.0])
def test_nearest_with_nodata_returns_nan(nodata: float):
    """最近邻命中空洞像素同样必须判无效。"""
    g = _grid_dem(nodata)
    # lat=1.5, lon=1.5 -> row=1.0, col=1.0 -> 最近邻 (1, 1)
    z = g.sample_elevation(1.5, 1.5, bilinear=False)
    assert np.isnan(z)


def test_zero_weight_nodata_neighbor_does_not_poison():
    """权重为 0 的邻居是空洞时不应污染结果（与 DEMTerrain 权重法对齐）。

    lat=2.5, lon=0.6 -> row=0.0, col=0.1；四邻中 (1, 1) 权重恒为 0，
    只有 (0, 0) 与 (0, 1) 参与加权。
    """
    g = _grid_dem()  # 中心 (1, 1) 是 nodata，但此处它的权重恒为 0
    z = g.sample_elevation(2.5, 0.6, bilinear=True)
    assert np.isfinite(z)
    assert z == pytest.approx(10.0 * 0.9 + 20.0 * 0.1)


def test_out_of_bounds_still_returns_nan():
    """撒点过滤（task_scenario._reject_outside_dem）依赖「越界返回 NaN」，不可破坏。"""
    g = _grid_dem()
    assert np.isnan(g.sample_elevation(3.5, 1.0))
    assert np.isnan(g.sample_elevation(2.0, -0.5))
    assert np.isnan(g.sample_elevation(-0.5, 1.0))


# ---------------------------------------------------------------------------
# 2) DEMTerrain 节点对齐（含最后一行 / 最后一列）
# ---------------------------------------------------------------------------


def test_demterrain_node_alignment_includes_last_row_and_column():
    """bounds 为节点外沿时，col_f=i / row_f=j 必须返回 grid[j, i]。"""
    grid = np.arange(25, dtype=np.float64).reshape(5, 5)
    t = DEMTerrain.from_array(grid, bounds=(0.0, 0.0, 250.0, 250.0))
    for j in range(5):
        for i in range(5):
            q = t.query_elevation(50.0 * i, 250.0 - 50.0 * j)
            assert q == pytest.approx(grid[j, i]), f"节点 ({j}, {i}) 未对齐：{q} != {grid[j, i]}"


def test_demterrain_beyond_bounds_is_nan():
    """允许最后一个节点，但真正越界仍须判 NaN。"""
    t = DEMTerrain.from_array(np.ones((5, 5)), bounds=(0.0, 0.0, 250.0, 250.0))
    assert np.isnan(t.query_elevation(250.0, 250.0))
    assert np.isnan(t.query_elevation(-1.0, 250.0))


def test_demterrain_nan_neighbor_with_zero_weight_is_finite():
    """权重为 0 的 NaN 邻居不应把有效节点判成 NaN（节点契约的前提）。"""
    grid = np.ones((5, 5))
    grid[1, 1] = np.nan
    t = DEMTerrain.from_array(grid, bounds=(0.0, 0.0, 250.0, 250.0))
    # (x=0, y=250) -> col_f=0, row_f=0，权重全给 (0,0)
    assert np.isfinite(t.query_elevation(0.0, 250.0))


# ---------------------------------------------------------------------------
# 3) to_metric_terrain 端到端（合成 + 真实 guangzhou 数据）
# ---------------------------------------------------------------------------


def test_metric_terrain_node_alignment_synthetic():
    """合成 DEM：to_metric_terrain 的节点与 DEMTerrain 查询逐位对齐。"""
    arr = np.arange(16, dtype=np.float64).reshape(4, 4) + 10.0
    g = _synthetic_dem(arr, nodata=None, c=113.0, f=23.2, res=0.001)
    frame = LocalFrame(lat0=23.198, lon0=113.002)
    t = g.to_metric_terrain(frame, step_m=50.0)
    grid = np.asarray(t._elevation)
    ny, nx = grid.shape
    left, bottom, right, top = t.meta.bounds
    res_x, res_y = t.meta.resolution
    assert res_x == pytest.approx(50.0)
    assert res_y == pytest.approx(50.0)
    for j in range(ny):
        for i in range(nx):
            q = t.query_elevation(left + i * res_x, top - j * res_y)
            np.testing.assert_allclose(q, grid[j, i], atol=1e-6, equal_nan=True)


def test_metric_terrain_no_fake_finite_from_nodata():
    """空洞邻域在米制网格里只能表现为 NaN，不能是掺入空洞值的有限高程。"""
    arr = np.full((6, 6), 20.0)
    arr[2:4, 2:4] = 255.0
    g = _synthetic_dem(arr, nodata=255.0, c=113.0, f=23.2, res=0.001)
    frame = LocalFrame(lat0=23.197, lon0=113.003)
    grid = np.asarray(g.to_metric_terrain(frame, step_m=50.0)._elevation)
    finite = grid[np.isfinite(grid)]
    assert finite.size > 0
    assert finite.max() <= 20.0 + 1e-9, f"出现 nodata 派生的错误高程，max={finite.max()}"


# 真实数据：数据环境齐备时才跑，避免无数据环境误报
_REAL_DATA = REPO / "task_allocation" / "data" / "guangzhou"


@pytest.fixture(scope="module")
def real_terrain():
    pytest.importorskip("PIL")
    if not (_REAL_DATA / "dem.tif").exists() or not (_REAL_DATA / "nest_location_data.csv").exists():
        pytest.skip("guangzhou 数据未就绪")
    from task_allocation.common import scenario_data
    from task_allocation.common.scenario import build_nest_scenario

    sc = build_nest_scenario(
        scenario_data.resolve_file("nest_location_data.csv", "guangzhou"),
        count=3, capacity=2, column="Coordinate",
        dem_path=scenario_data.resolve_file("dem.tif", "guangzhou"),
    )
    t = sc.dem.to_metric_terrain(sc.frame, step_m=200.0)
    return sc.dem, t


def test_real_guangzhou_no_nodata_derived_elevation(real_terrain):
    """真实 DEM：网格有限高程不得超过原始 DEM 的有效上界。"""
    dem, terrain = real_terrain
    raw = np.asarray(dem.elevation, dtype=np.float64)
    valid = raw[raw != dem.nodata] if dem.nodata is not None else raw
    valid = valid[np.isfinite(valid)]
    upper = float(valid.max())
    grid = np.asarray(terrain._elevation)
    finite = grid[np.isfinite(grid)]
    assert finite.size > 0
    assert finite.max() <= upper + 1e-6, (
        f"网格出现高于 DEM 有效上界 {upper} 的高程 {finite.max()}，"
        "说明 nodata 仍混进了插值"
    )


def test_real_guangzhou_outer_ring_nan_ratio(real_terrain):
    """外圈整圈 NaN 应被消除；剩余 NaN 只应来自 DEM 空洞邻域（正确行为）。"""
    _, terrain = real_terrain
    grid = np.asarray(terrain._elevation)
    for name, edge in (
        ("上边", grid[0, :]), ("下边", grid[-1, :]),
        ("左边", grid[:, 0]), ("右边", grid[:, -1]),
    ):
        assert not np.isnan(edge).all(), f"{name} 仍整圈 NaN，说明越界未修复"


def test_real_guangzhou_nest_elevations_finite(real_terrain):
    """修复不得把机巢所在位置的地形采样变成 NaN。"""
    dem, _ = real_terrain
    from task_allocation.common import scenario_data
    from task_allocation.common.scenario import build_nest_scenario

    sc = build_nest_scenario(
        scenario_data.resolve_file("nest_location_data.csv", "guangzhou"),
        count=3, capacity=2, column="Coordinate",
        dem_path=scenario_data.resolve_file("dem.tif", "guangzhou"),
    )
    assert len(sc.nests) == 3
    for nest in sc.nests:
        assert np.isfinite(nest.z), f"机巢 {nest.id} 处地形采样变成 NaN：z={nest.z}"
        if dem.nodata is not None:
            assert nest.z != dem.nodata, f"机巢 {nest.id} 采到了 nodata 值"
