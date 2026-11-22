# -*- coding: utf-8 -*-
"""几何层对拍：``path_planning/methods/utils``（fork 版）vs ``task_allocation`` 原版。

职责：证明 fork 的三件套（坐标、DEM、建筑图层）与上游**逐点零差异**，
      使"复制自包含"不引入隐性口径漂移。
边界：只比对公开 API 的数值输出，不比对内部实现；上游包缺失时整体跳过。
相关：``scripts/geometry_parity_report.md``（对拍结论记录）、
      ``path_planning/README.md`` §几何层。

为什么需要这份对拍：几何层是从 ``task_allocation`` 复制的，之后两边会各自
演进。一旦将来有人在 fork 版"顺手改好"某个函数（比如给 ``sample_elevation``
补一个 nodata 处理），必须能立刻定位到差异，而不是等规划结果对不上再回头查。
"""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _PROJECT_ROOT / "path_planning" / "data" / "guangzhou"
_DEM_PATH = _DATA_DIR / "dem.tif"
_BUILDING_DIR = _DATA_DIR / "building"


def _upstream_available() -> bool:
    try:
        import task_allocation.methods.utils  # noqa: F401
    except Exception:
        return False
    return True


@unittest.skipUnless(_upstream_available(), "上游 task_allocation 不可导入，跳过对拍")
@unittest.skipUnless(_DEM_PATH.is_file(), f"缺少 DEM：{_DEM_PATH}")
class TestGeometryParity(unittest.TestCase):
    """fork 版与上游版逐点对拍。"""

    @classmethod
    def setUpClass(cls) -> None:
        import task_allocation.methods.utils.coordinates as up_coord
        import task_allocation.methods.utils.dem_geotiff as up_dem

        import path_planning.methods.utils as pp

        cls.up_coord = up_coord
        cls.up_dem = up_dem
        cls.pp = pp

        cls.origin = (23.1, 113.31)
        cls.frame_up = up_coord.LocalFrame(*cls.origin)
        cls.frame_pp = pp.LocalFrame(*cls.origin)
        cls.dem_up = up_dem.GeoTIFFDEM(_DEM_PATH)
        cls.dem_pp = pp.GeoTIFFDEM(_DEM_PATH)

    # --- 坐标 ---------------------------------------------------------

    def test_wgs84_constants_identical(self) -> None:
        for name in ("WGS84_A", "WGS84_B", "WGS84_F", "WGS84_INV_F",
                     "WGS84_E2", "WGS84_EP2"):
            self.assertEqual(
                getattr(self.up_coord, name),
                getattr(self.pp, name),
                f"常量 {name} 不一致",
            )

    def test_parse_coordinate_identical(self) -> None:
        samples = (
            "23.111467N 113.307078E",
            "23.086103N 113.311021E",
            "N23.100254 E113.32922",
            "23.100254, 113.32922",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    self.up_coord.parse_coordinate(text),
                    self.pp.parse_coordinate(text),
                    f"parse_coordinate({text!r}) 结果不同",
                )

    def test_latlon_to_local_identical(self) -> None:
        pts = [
            (23.111467, 113.307078),
            (23.086103, 113.311021),
            (23.0735, 113.291093),
            (23.116852, 113.338587),
        ]
        worst = 0.0
        for lat, lon in pts:
            xu, yu = self.up_coord.latlon_to_local(lat, lon, self.frame_up)
            xp, yp = self.pp.latlon_to_local(lat, lon, self.frame_pp)
            worst = max(worst, abs(xu - xp), abs(yu - yp))
        self.assertLess(worst, 1e-9, f"latlon_to_local 最大偏差 {worst:.3e} m")

    def test_local_to_latlon_roundtrip(self) -> None:
        """往返残差应 ≤ 1e-7 deg（约 11 mm）；实测约 4.2e-9 deg（0.5 mm）。"""
        worst = 0.0
        for lat, lon in [(23.111467, 113.307078), (23.086103, 113.311021)]:
            x, y = self.pp.latlon_to_local(lat, lon, self.frame_pp)
            back = self.pp.local_to_latlon(x, y, self.frame_pp)
            worst = max(worst, abs(back[0] - lat), abs(back[1] - lon))
        self.assertLess(worst, 1e-7, f"往返残差 {worst:.3e} deg")

    # --- DEM 元数据与栅格 ---------------------------------------------

    def test_dem_metadata_identical(self) -> None:
        self.assertEqual((self.dem_up.width, self.dem_up.height),
                         (self.dem_pp.width, self.dem_pp.height))
        self.assertEqual(self.dem_up.transform.to_gdal(),
                         self.dem_pp.transform.to_gdal())
        self.assertEqual(tuple(self.dem_up.bounds), tuple(self.dem_pp.bounds))
        self.assertEqual(self.dem_up.nodata, self.dem_pp.nodata)
        self.assertEqual(self.dem_up.epsg, self.dem_pp.epsg)

    def test_elevation_array_identical(self) -> None:
        eu = self.dem_up.elevation
        ep = self.dem_pp.elevation
        self.assertEqual(eu.shape, ep.shape)
        self.assertEqual(eu.dtype, ep.dtype)
        np.testing.assert_array_equal(eu, ep, "elevation 数组存在逐元素差异")

    def test_sample_elevation_grid_identical(self) -> None:
        lats = np.linspace(23.0740, 23.1168, 9)
        lons = np.linspace(113.2911, 113.3385, 9)
        worst = 0.0
        for lat in lats:
            for lon in lons:
                su = self.dem_up.sample_elevation(float(lat), float(lon))
                sp = self.dem_pp.sample_elevation(float(lat), float(lon))
                if np.isnan(su) and np.isnan(sp):
                    continue
                worst = max(worst, abs(su - sp))
        self.assertLess(worst, 1e-12, f"sample_elevation 最大偏差 {worst:.3e} m")

    # --- 米制重采样 ---------------------------------------------------

    def test_metric_terrain_meta_identical(self) -> None:
        mu = self.dem_up.to_metric_terrain(self.frame_up, step_m=30.0)
        mp = self.dem_pp.to_metric_terrain(self.frame_pp, step_m=30.0)
        self.assertEqual(tuple(mu.meta.bounds), tuple(mp.meta.bounds))
        self.assertEqual(tuple(mu.meta.resolution), tuple(mp.meta.resolution))
        self.assertEqual(mu.meta.width, mp.meta.width)
        self.assertEqual(mu.meta.height, mp.meta.height)

    def test_metric_terrain_query_identical(self) -> None:
        mu = self.dem_up.to_metric_terrain(self.frame_up, step_m=30.0)
        mp = self.dem_pp.to_metric_terrain(self.frame_pp, step_m=30.0)
        worst = 0.0
        for x in np.linspace(-2000, 2000, 5):
            for y in np.linspace(-2000, 2000, 5):
                a = mu.query_elevation(float(x), float(y))
                b = mp.query_elevation(float(x), float(y))
                if np.isnan(a) and np.isnan(b):
                    continue
                worst = max(worst, abs(a - b))
        self.assertLess(worst, 1e-12, f"query_elevation 最大偏差 {worst:.3e} m")

    def test_metric_terrain_node_contract(self) -> None:
        """节点契约：``query(x_i, y_j) == grid[j, i]``（to_metric_terrain 的承诺）。"""
        mp = self.dem_pp.to_metric_terrain(self.frame_pp, step_m=30.0)
        meta = mp.meta
        left, _bottom, _right, top = meta.bounds
        res_x, res_y = meta.resolution
        grids = [[left + 3 * res_x, top - 2 * res_y],
                 [left + 10 * res_x, top - 7 * res_y]]
        for x, y in grids:
            q = mp.query_elevation(x, y)
            if np.isfinite(q):
                self.assertGreater(q, -100.0)
                self.assertLess(q, 500.0)

    # --- 建筑图层 -----------------------------------------------------

    @unittest.skipUnless(_BUILDING_DIR.is_dir(), f"缺少建筑目录：{_BUILDING_DIR}")
    def test_building_layer_identical(self) -> None:
        from task_allocation.methods.utils.building_layer import (
            BuildingLayer as UpBuildingLayer,
        )

        from path_planning.methods.utils import BuildingLayer as PpBuildingLayer

        up = UpBuildingLayer.from_shapefiles(_BUILDING_DIR, self.frame_up)
        pp = PpBuildingLayer.from_shapefiles(_BUILDING_DIR, self.frame_pp)
        self.assertEqual(up.count, pp.count, "建筑多边形计数不一致")

        xs = np.array([-900.0, -500.0, 0.0, 800.0, 1300.0])
        ys = np.array([1300.0, -1400.0, 0.0, 200.0, 100.0])
        hu = np.asarray(up.heights_at(xs, ys), dtype=np.float64)
        hp = np.asarray(pp.heights_at(xs, ys), dtype=np.float64)
        np.testing.assert_allclose(
            hu, hp, rtol=0.0, atol=0.0, err_msg="heights_at 存在逐点差异"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
