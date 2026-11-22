# -*- coding: utf-8 -*-
"""场景装配层测试。

职责：验证 ``common/scenario.py`` 的数据装配、高程同源、地形空洞填充与边界语义。
边界：不测 SDF 构建与路径搜索；真实数据缺失时相关用例跳过。
相关：``common/scenario.py``、``methods/utils/dem_terrain.py``、``docs/architecture.md``。

覆盖的关键口径（都是会静默出错、不会抛异常的那类）：

1. 机巢 CSV 是 ``"纬度N 经度E"``，纬度在前——顺序搞反不报错，只得到无意义高程。
2. 机巢 z、屋顶 AGL、净空全部走同一张米制 terrain；用原始 DEM 直采样会差 1 m 级。
3. terrain 的 NaN 空洞必须补齐，否则净空判据 ``z < nan + clearance`` 恒 False，
   等于静默放行。
4. ``bounds_xy`` 是"可规划区"（有数据区 + margin），``data_bounds_xy`` 是"有数据区"；
   高程查询按后者夹持，否则 margin 带整圈是 NaN。
"""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from path_planning.common.scenario_loader import load_nest_latlon, load_scenario
from path_planning.common.scenario_types import NoFlyZone, Scenario
from path_planning.common.terrain_hole_filling import fill_terrain_holes
from path_planning.methods.utils import DEMTerrain

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _PROJECT_ROOT / "path_planning" / "data" / "guangzhou"
_CSV = _DATA_DIR / "nest_location_data.csv"


def _grid(values: list[list[float]], res: float = 10.0) -> DEMTerrain:
    elev = np.array(values, dtype=np.float64)
    h, w = elev.shape
    bounds = (0.0, 0.0, w * res, h * res)
    return DEMTerrain.from_array(elev, bounds)


def _node(i: int, j: int, h: int, res: float = 10.0) -> tuple[float, float]:
    """网格节点 (i=列, j=行) 的平面坐标。

    ``DEMTerrain`` 的节点定义是 ``x = left + i*res_x``、``y = top - j*res_y``，
    其中 ``res_y = (top-bottom)/height``——**不是**像素中心，所以最后一行节点在
    ``y = top - (h-1)*res_y``，而不是 ``bottom``。取 ``y=bottom`` 会越界得到 NaN。
    """
    return (i * res, (h - j) * res)


class TestFillTerrainHoles(unittest.TestCase):
    """地形空洞填充。"""

    def test_no_holes_returns_same_object(self) -> None:
        t = _grid([[1.0, 2.0], [3.0, 4.0]])
        out, stats = fill_terrain_holes(t)
        self.assertIs(out, t)
        self.assertEqual(stats["holes"], 0)
        self.assertEqual(stats["filled"], 0)

    def test_center_hole_filled_by_neighbours(self) -> None:
        # 把 3.0 放在中心格的**左邻**（1,0），四邻即 1/3/1/1
        t = _grid([[1.0, 1.0, 1.0],
                   [3.0, np.nan, 1.0],
                   [1.0, 1.0, 1.0]])
        out, stats = fill_terrain_holes(t)
        self.assertEqual(stats["holes"], 1)
        self.assertEqual(stats["filled"], 1)
        self.assertEqual(stats["residual"], 0)
        self.assertEqual(stats["iterations"], 1)
        # 四邻是 1,3,1,1 → 均值 1.5（扩散法不是"取最近"，是"取已确定邻居均值"）
        v = out.query_elevation(*_node(1, 1, 3))
        self.assertAlmostEqual(v, 1.5, places=12)

    def test_edge_holes_filled(self) -> None:
        t = _grid([[np.nan, np.nan, np.nan],
                   [np.nan, 7.0, np.nan],
                   [np.nan, np.nan, np.nan]])
        out, stats = fill_terrain_holes(t)
        self.assertEqual(stats["filled"], 8)
        self.assertEqual(stats["residual"], 0)
        grid = np.array(
            [[out.query_elevation(*_node(i, j, 3)) for i in range(3)]
             for j in range(3)]
        )
        np.testing.assert_allclose(grid, np.full((3, 3), 7.0), atol=0.0)

    def test_all_holes_raises(self) -> None:
        t = _grid([[np.nan, np.nan], [np.nan, np.nan]])
        with self.assertRaises(ValueError):
            fill_terrain_holes(t)

    def test_original_terrain_not_mutated(self) -> None:
        t = _grid([[1.0, np.nan], [3.0, 4.0]])
        out, _ = fill_terrain_holes(t)
        self.assertIsNot(out, t)
        self.assertTrue(np.isnan(t.query_elevation(*_node(1, 0, 2))))

    def test_deep_hole_iterations_bounded(self) -> None:
        """5 列全 nan 只有一端有种子 → 迭代次数 = 可达深度。"""
        t = _grid([[1.0, np.nan, np.nan, np.nan, np.nan]])
        out, stats = fill_terrain_holes(t)
        self.assertEqual(stats["filled"], 4)
        self.assertLessEqual(stats["iterations"], 5)
        self.assertEqual(stats["residual"], 0)


class TestNoFlyZone(unittest.TestCase):
    def test_too_few_vertices_raises(self) -> None:
        with self.assertRaises(ValueError):
            NoFlyZone(id="z", vertices=((0.0, 0.0), (1.0, 0.0)))

    def test_polygon_point_inclusion(self) -> None:
        zone = NoFlyZone(id="z", vertices=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)))
        sc = Scenario(
            name="t",
            frame=None,  # type: ignore[arg-type]  # 本用例不触 frame
            dem=None,
            terrain=None,
            buildings=None,
            nests=(),
            nofly=(zone,),
            bounds_xy=(0.0, 0.0, 10.0, 10.0),
            data_bounds_xy=(0.0, 0.0, 10.0, 10.0),
        )
        self.assertEqual(sc.nofly_at(5.0, 5.0), "z")
        self.assertIsNone(sc.nofly_at(15.0, 5.0))

    def test_vertical_and_time_windows(self) -> None:
        zone = NoFlyZone(
            id="z",
            vertices=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
            z_min=20.0,
            z_max=50.0,
            active=(100.0, 200.0),
        )
        sc = Scenario(
            name="t",
            frame=None,  # type: ignore[arg-type]
            dem=None,
            terrain=None,
            buildings=None,
            nests=(),
            nofly=(zone,),
            bounds_xy=(0.0, 0.0, 10.0, 10.0),
            data_bounds_xy=(0.0, 0.0, 10.0, 10.0),
        )
        self.assertEqual(sc.nofly_at(5.0, 5.0, t=150.0, z=30.0), "z")
        self.assertIsNone(sc.nofly_at(5.0, 5.0, t=50.0, z=30.0), "时间窗外应放行")
        self.assertIsNone(sc.nofly_at(5.0, 5.0, t=150.0, z=10.0), "高度窗外应放行")


@unittest.skipUnless(_CSV.is_file(), f"缺少机巢 CSV：{_CSV}")
class TestNestCsv(unittest.TestCase):
    def test_reads_three_nests_lat_first(self) -> None:
        pts = load_nest_latlon(_CSV)
        self.assertEqual(len(pts), 3)
        # 纬度在前：广州纬度 23.x，经度 113.x。若顺序读反会得到 113.x 的"纬度"，
        # 数值本身就露馅。
        for lat, lon in pts:
            self.assertTrue(20.0 < lat < 30.0, f"纬度异常：{lat}")
            self.assertTrue(110.0 < lon < 120.0, f"经度异常：{lon}")
        self.assertAlmostEqual(pts[0][0], 23.111467, places=6)
        self.assertAlmostEqual(pts[0][1], 113.307078, places=6)

    def test_missing_column_raises(self) -> None:
        with self.assertRaises(ValueError):
            load_nest_latlon(_CSV, column="NotExist")


@unittest.skipUnless(_DATA_DIR.is_dir() and _CSV.is_file(), f"缺少场景数据：{_DATA_DIR}")
class TestLoadScenario(unittest.TestCase):
    """真实广州场景装配。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sc = load_scenario(_DATA_DIR)

    def test_nests(self) -> None:
        self.assertEqual(self.sc.nest_count, 3)
        self.assertEqual(self.sc.nest_ids, (0, 1, 2))
        for n in self.sc.nests:
            self.assertTrue(np.isfinite(n.z), f"机巢 {n.id} 高程不是有限值")
            self.assertGreater(n.z, 0.0)
            self.assertLess(n.z, 100.0)
            # z 与原始 DEM 直采样的偏差应在 2 m 内（实测 0.3~1.3 m）
            self.assertIsNotNone(n.z_dem)
            self.assertLess(abs(n.z - n.z_dem), 2.0)

    def test_nest_lookup(self) -> None:
        self.assertEqual(self.sc.nest(1).id, 1)
        with self.assertRaises(KeyError):
            self.sc.nest(99)

    def test_nest_count_clamped(self) -> None:
        with self.assertRaises(ValueError):
            load_scenario(_DATA_DIR, nest_count=99)
        with self.assertRaises(ValueError):
            load_scenario(_DATA_DIR, nest_count=0)

    def test_terrain_holes_filled(self) -> None:
        stats = self.sc.meta["terrain_fill"]
        self.assertIsNotNone(stats)
        self.assertEqual(stats["residual"], 0, "填充后仍有残量空洞")
        self.assertGreater(stats["holes"], 0, "该场景本应有空洞，若为 0 说明 DEM 变了")

    def test_ground_height_finite_over_bounds(self) -> None:
        """整个可规划范围（含 margin 带）都应能取到有限高程。"""
        xmin, ymin, xmax, ymax = self.sc.bounds_xy
        xs = np.linspace(xmin, xmax, 60)
        ys = np.linspace(ymin, ymax, 60)
        gx, gy = np.meshgrid(xs, ys)
        g = self.sc.ground_height(gx.ravel(), gy.ravel())
        self.assertEqual(int((~np.isfinite(g)).sum()), 0, "可规划范围内存在 NaN 高程")
        self.assertGreater(float(g.min()), 0.0)

    def test_roof_above_ground_somewhere(self) -> None:
        xmin, ymin, xmax, ymax = self.sc.data_bounds_xy
        rng = np.random.default_rng(7)
        xs = rng.uniform(xmin, xmax, 2000)
        ys = rng.uniform(ymin, ymax, 2000)
        g = self.sc.ground_height(xs, ys)
        r = self.sc.roof_height(xs, ys)
        self.assertGreater(float((r > g).mean()), 0.02, "建筑覆盖比例异常偏低")
        self.assertGreater(float((r - g).max()), 20.0, "屋顶 AGL 最大值为异常")

    def test_buildings_loaded(self) -> None:
        self.assertIsNotNone(self.sc.buildings)
        self.assertGreater(self.sc.buildings.count, 20000)

    def test_nofly_ids_unique(self) -> None:
        ids = [z.id for z in self.sc.nofly]
        self.assertEqual(len(ids), len(set(ids)), f"禁飞区 id 重号：{ids}")
        self.assertGreater(len(ids), 0)
        for key in ids:
            self.assertIn(":", key, "id 应带 geojson 文件前缀")

    def test_bounds_ordering(self) -> None:
        xmin, ymin, xmax, ymax = self.sc.bounds_xy
        self.assertLess(xmin, xmax)
        self.assertLess(ymin, ymax)
        dxmin, dymin, dxmax, dymax = self.sc.data_bounds_xy
        # bounds_xy 是 data_bounds_xy 向外扩 margin：所以它的四个边界都更"外"
        self.assertLess(xmin, dxmin, "可规划区应比有数据区更靠西")
        self.assertLess(ymin, dymin, "可规划区应比有数据区更靠南")
        self.assertGreater(xmax, dxmax, "可规划区应比有数据区更靠东")
        self.assertGreater(ymax, dymax, "可规划区应比有数据区更靠北")

    def test_inside_bounds(self) -> None:
        xmin, ymin, xmax, ymax = self.sc.bounds_xy
        self.assertTrue(self.sc.is_inside_bounds(0.0, 0.0))
        self.assertFalse(self.sc.is_inside_bounds(xmin - 1.0, 0.0))

    def test_frame_origin_near_nest_centroid(self) -> None:
        lat0, lon0 = self.sc.frame.lat0, self.sc.frame.lon0
        mean_lat = float(np.mean([n.lat for n in self.sc.nests]))
        mean_lon = float(np.mean([n.lon for n in self.sc.nests]))
        self.assertAlmostEqual(lat0, mean_lat, places=6)
        self.assertAlmostEqual(lon0, mean_lon, places=6)

    def test_summary(self) -> None:
        s = self.sc.summary()
        self.assertEqual(s["nests"], 3)
        self.assertEqual(s["name"], "guangzhou")
        self.assertTrue(s["dem"] and s["terrain"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
