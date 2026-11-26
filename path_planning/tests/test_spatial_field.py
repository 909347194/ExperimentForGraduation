# -*- coding: utf-8 -*-
"""空间层测试：栅格契约、障碍栅格化、SDD 符号、全域场取样与覆盖契约。

职责：用合成小场景验证 ``grid_spec`` / ``occupancy_sdf`` / ``world_field`` 的
      几何与符号口径（全部可在毫秒级跑完，不依赖真实城市数据）。
边界：真实全域建场（约 1 分钟 / 数百 MB）由一次性脚本验证，不进单元测试。
相关：``methods/spatial/world_field.py``、``methods/spatial/occupancy_sdf.py``、
      ``common/grid_spec.py``。

重点覆盖三处"错了也不会抛异常"的陷阱：

1. 轴序：第 2 轴必须是高度，否则 (4,4,2) 会被当成水平 2 m / 竖直 4 m。
2. 符号：缓冲 EDT 接反会让整张 SDF 变 0，路径笔直穿楼。
3. 覆盖：窗口出界时 ``map_coordinates(mode="nearest")`` 会静默取边缘值，
   必须由 ``check_coverage`` 主动拦住。
"""

from __future__ import annotations

import math
import unittest

import numpy as np
from shapely.geometry import Polygon

from path_planning.common.grid_spec import GridSpec
from path_planning.common.scenario_types import Scenario
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.spatial.field_contract import WindowOutsideField
from path_planning.methods.spatial.field_artifact import field_signature
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.spatial.world_field_builder import build_world_field
from path_planning.methods.utils import BuildingLayer


def _square(cx: float, cy: float, side: float) -> np.ndarray:
    h = 0.5 * side
    return np.array(
        [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]],
        dtype=np.float64,
    )


def _scenario(polys=(), heights=(), *, nofly=()) -> Scenario:
    """无地形（地面恒 0）、给定建筑的合成场景。"""
    buildings = (
        BuildingLayer.from_polygons([Polygon(p) for p in polys], heights)
        if polys
        else None
    )
    return Scenario(
        name="synthetic",
        frame=None,  # type: ignore[arg-type]  # 合成场景不做经纬换算
        dem=None,
        terrain=None,
        buildings=buildings,
        nests=(),
        nofly=tuple(nofly),
        bounds_xy=(0.0, 0.0, 40.0, 40.0),
        data_bounds_xy=(0.0, 0.0, 40.0, 40.0),
        meta={"synthetic": True},
    )


class TestGridSpec(unittest.TestCase):
    def test_axis_aligned_roundtrip(self) -> None:
        g = GridSpec(origin=(100.0, -50.0, 10.0), spacing=(4.0, 4.0, 2.0), shape=(5, 4, 3))
        idx = np.array([[0.0, 0.0, 0.0], [4.0, 3.0, 2.0], [2.0, 1.0, 1.0]])
        enu = g.index_to_enu(idx)
        np.testing.assert_allclose(g.enu_to_index(enu), idx, atol=1e-12)

    def test_yaw_roundtrip(self) -> None:
        g = GridSpec(
            origin=(0.0, 0.0, 0.0),
            spacing=(4.0, 4.0, 2.0),
            shape=(3, 3, 2),
            yaw_rad=math.radians(37.0),
        )
        idx = np.array([[0.5, 0.5, 0.5], [2.0, 1.0, 1.0]])
        np.testing.assert_allclose(g.enu_to_index(g.index_to_enu(idx)), idx, atol=1e-12)

    def test_axes_are_orthonormal(self) -> None:
        g = GridSpec(origin=(0, 0, 0), spacing=(1, 1, 1), shape=(2, 2, 2), yaw_rad=0.3)
        a0, a1 = g.axes_xy
        self.assertAlmostEqual(float(a0 @ a1), 0.0, places=12)
        self.assertAlmostEqual(float(a0 @ a0), 1.0, places=12)

    def test_bounds_and_covers(self) -> None:
        g = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(1.0, 1.0, 1.0), shape=(3, 4, 2))
        self.assertEqual(g.bounds, (0.0, 0.0, 0.0, 2.0, 3.0, 1.0))
        pts = np.array([[0.0, 0.0, 0.0], [2.0, 3.0, 1.0], [3.0, 3.0, 1.0]])
        self.assertEqual(g.covers(pts).tolist(), [True, True, False])

    def test_invalid_shape_raises(self) -> None:
        with self.assertRaises(ValueError):
            GridSpec(origin=(0, 0, 0), spacing=(1, 1, 1), shape=(0, 2, 2))
        with self.assertRaises(ValueError):
            GridSpec(origin=(0, 0, 0), spacing=(1, -1, 1), shape=(2, 2, 2))


class TestOccupancyAndSdf(unittest.TestCase):
    def test_building_prism_occupies_voxels(self) -> None:
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(1.0, 1.0, 1.0), shape=(10, 10, 12))
        prism = ObstaclePrism(_square(5.0, 5.0, 6.0), 0.0, 10.0)
        occ = build_occupancy([prism], grid, conservative_buffer=False)
        # 建在 x/y ∈ [2,8]、z ∈ [0,10] 内
        self.assertTrue(occ[5, 5, 5], "建筑中心应为占据")
        self.assertFalse(occ[0, 0, 5], "远端应为自由")
        self.assertFalse(occ[5, 5, 11], "屋顶之上应为自由")

    def test_conservative_buffer_catches_narrow_building(self) -> None:
        """比格距还窄的建筑也必须至少占一格（否则整栋漏检）。"""
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(4.0, 4.0, 2.0), shape=(6, 6, 4))
        prism = ObstaclePrism(_square(10.0, 10.0, 1.0), 0.0, 6.0)
        occ = build_occupancy([prism], grid)
        self.assertGreater(int(occ.sum()), 0, "窄建筑在保守膨胀后仍漏检")

    def test_ground_is_solid(self) -> None:
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(1.0, 1.0, 1.0), shape=(4, 4, 6))
        ground = np.full((4, 4), 2.0)
        occ = build_occupancy([], grid, ground_m=ground)
        self.assertTrue(np.all(occ[:, :, 0:3]), "地面及以下应为占据")
        self.assertFalse(np.any(occ[:, :, 3:]), "地面以上应为自由")

    def test_sdf_sign_convention(self) -> None:
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(1.0, 1.0, 1.0), shape=(10, 10, 12))
        occ = build_occupancy([ObstaclePrism(_square(5.0, 5.0, 6.0), 1.0, 5.0)], grid)
        sdf = build_sdf(occ, grid.spacing)
        self.assertLess(float(sdf[occ].max()), 0.0, "占据体素应为负")
        self.assertGreater(float(sdf[~occ].min()), 0.0, "自由体素应为正")
        # 屋顶（z=5）之上 2 m ≈ +2（体素间距 1 m）
        self.assertAlmostEqual(float(sdf[5, 5, 7]), 2.0, delta=1.0)

    def test_sdf_not_inverted(self) -> None:
        """接反 EDT 会让整张场退化为 0；这里用非零比例做探针。"""
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(2.0, 2.0, 2.0), shape=(8, 8, 8))
        occ = build_occupancy([ObstaclePrism(_square(8.0, 8.0, 4.0), 0.0, 6.0)], grid)
        sdf = build_sdf(occ, grid.spacing)
        self.assertGreater(float(np.count_nonzero(sdf)), 0.5 * sdf.size)

    def test_sdf_field_query_and_segment(self) -> None:
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(1.0, 1.0, 1.0), shape=(10, 10, 12))
        occ = build_occupancy([ObstaclePrism(_square(5.0, 5.0, 4.0), 1.0, 5.0)], grid)
        field = SdfField(grid, build_sdf(occ, grid.spacing))
        # 正上方：水平距离 0，只剩竖直距离。建筑 z∈[1,5]，保守膨胀各外扩半格后
        # 顶面在 5.5，故 (5,5,8) 约为 2.5（不是 3）
        v = field.at_one(5.0, 5.0, 8.0)
        self.assertGreater(v, 2.0)
        self.assertLess(v, 3.2)
        # 斜上方（0.5,0.5,8）的 3D 距离 = sqrt(2.5²+2.5²+3²) ≈ 4.7
        self.assertAlmostEqual(field.at_one(0.5, 0.5, 8.0), 4.675, delta=0.2)
        # 穿过建筑的线段最小值应为负（复核必须能发现穿越）
        self.assertLess(field.segment_min((2.5, 5.0, 3.0), (7.5, 5.0, 3.0), 0.5), 0.0)


class TestWorldField(unittest.TestCase):
    def _build(self, **kwargs) -> WorldField:
        sc = _scenario(
            polys=[_square(6.0, 6.0, 4.0), _square(20.0, 20.0, 8.0)],
            heights=[8.0, 20.0],
        )
        return build_world_field(sc, **kwargs)

    def test_build_and_shape(self) -> None:
        f = self._build(spacing=(2.0, 2.0, 2.0))
        self.assertEqual(f.grid.yaw_rad, 0.0)
        nz, nx, ny = f.sdf.shape
        self.assertEqual((nx, ny, nz), f.grid.shape, "sdf 必须是 z-major")
        self.assertEqual(f.sdf.dtype, np.float32)

    def test_at_matches_window_nodes(self) -> None:
        """窗口节点上的 window_sdf 与 at 应一致（同一点两种取样路径）。"""
        f = self._build(spacing=(2.0, 2.0, 2.0))
        win = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(2.0, 2.0, 2.0), shape=(9, 9, 8))
        sdf_w = f.window_sdf(win)
        idx = np.array(
            [[i, j, k] for i in (0, 4, 8) for j in (0, 4, 8) for k in (0, 3, 7)],
            dtype=np.float64,
        )
        pts = win.index_to_enu(idx)
        vals_at = f.at(pts)
        vals_w = np.array([sdf_w[int(i), int(j), int(k)] for i, j, k in idx])
        np.testing.assert_allclose(vals_at, vals_w, atol=1e-4)

    def test_yawed_window_also_samples(self) -> None:
        f = self._build(spacing=(2.0, 2.0, 2.0))
        win = GridSpec(
            origin=(10.0, 2.0, 2.0),
            spacing=(2.0, 2.0, 2.0),
            shape=(8, 4, 6),
            yaw_rad=math.radians(25.0),
        )
        sdf_w = f.window_sdf(win)
        self.assertEqual(sdf_w.shape, win.shape)
        self.assertTrue(np.all(np.isfinite(sdf_w)))

    def test_window_outside_raises(self) -> None:
        f = self._build(spacing=(2.0, 2.0, 2.0))
        far = GridSpec(
            origin=(f.grid.bounds[3] + 50.0, 0.0, 0.0),
            spacing=(2.0, 2.0, 2.0),
            shape=(4, 4, 4),
        )
        with self.assertRaises(WindowOutsideField):
            f.window_sdf(far)

    def test_tallest_building_covered(self) -> None:
        """z 上界必须兜住最高楼顶，否则高楼被裁掉一半、SDF 乐观。"""
        f = self._build(spacing=(2.0, 2.0, 2.0))
        self.assertGreater(float(f.grid.bounds[5]), 20.0, "竖直上界未包住 20 m 高楼")

    def test_field_signature(self) -> None:
        f = self._build(spacing=(2.0, 2.0, 2.0))
        sig = field_signature(f, sample_bias_m=0.1)
        self.assertIn("world_field", sig)
        self.assertEqual(sig["world_field"]["shape"], list(f.grid.shape))
        self.assertEqual(sig["world_field"]["sample_bias_m"], 0.1)
        self.assertEqual(field_signature(None), {})

    def test_sample_bias_makes_conservative(self) -> None:
        base = self._build(spacing=(2.0, 2.0, 2.0))
        biased = self._build(spacing=(2.0, 2.0, 2.0), sample_bias_m=0.5)
        p = np.array([[1.0, 1.0, 5.0]])
        self.assertAlmostEqual(
            float(base.at(p)[0] - biased.at(p)[0]), 0.5, places=6
        )

    def test_max_nodes_guard(self) -> None:
        sc = _scenario(polys=[_square(10.0, 10.0, 4.0)], heights=[5.0])
        with self.assertRaises(ValueError):
            build_world_field(sc, spacing=(0.5, 0.5, 0.5), max_nodes=1000)

    def test_pad_must_be_multiple_of_spacing(self) -> None:
        """pad 非间距整数倍会让整张网格相对机巢坐标平移——必须当场报错。"""
        sc = _scenario(polys=[_square(10.0, 10.0, 4.0)], heights=[5.0])
        with self.assertRaises(ValueError):
            build_world_field(sc, spacing=(4.0, 4.0, 2.0), pad_m=5.0)
        with self.assertRaises(ValueError):
            build_world_field(sc, spacing=(4.0, 4.0, 2.0), pad_m=-4.0)
        f = build_world_field(sc, spacing=(4.0, 4.0, 2.0), pad_m=12.0)
        # data_bounds x0=0 ⇒ 原点外移 pad，且节点仍落在 4 m 整数格上
        self.assertAlmostEqual(float(f.grid.origin[0]), -12.0, places=6)
        self.assertAlmostEqual(float(f.grid.origin[1]), -12.0, places=6)

    def test_nofly_zone_is_obstacle(self) -> None:
        from path_planning.common.scenario_types import NoFlyZone

        zone = NoFlyZone(
            id="z", vertices=((25.0, 5.0), (35.0, 5.0), (35.0, 15.0), (25.0, 15.0))
        )
        sc = _scenario(nofly=(zone,))
        f = build_world_field(sc, spacing=(2.0, 2.0, 2.0))
        # 禁飞区中心（30, 10）在飞行带内应被占据
        self.assertLess(f.at_one(30.0, 10.0, 30.0), 0.0)

    def test_sdf_on_grid_matches_world_field(self) -> None:
        """``SdfOnGrid``（NLP 内层）与 ``WorldField.at``（复核）必须逐点一致。

        回归要点：``WorldField.sdf`` 是 **z-major** ``(nz,nx,ny)``，而 ``SdfOnGrid``
        要按 ``(nx,ny,nz)`` 展平成"i 变化最快"喂 ``interpolant``。漏掉转置时整场被
        换成另一个坐标的场：局部重规划会把节点推到 155 m、弦上读到 −60 m，IPOPT 只报
        ``Infeasible``，而复核依旧按真场判死——症状像"求解器不行"，其实是读错了场。
        故这里用**不对称的 8×6×5 小场**逐点对拍，任何轴序错误都会立刻暴露。
        """
        from path_planning.methods.spatial.sdf_casadi import SdfOnGrid

        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(3.0, 2.0, 1.5), shape=(8, 6, 5))
        # 三个轴取值不同 ⇒ 转置后不会"碰巧相等"
        nx, ny, nz = grid.shape
        z_major = np.empty((nz, nx, ny), dtype=np.float32)
        for k in range(nz):
            for i in range(nx):
                z_major[k, i, :] = 1.0 + 2.0 * i + 0.5 * i * i + 3.0 * k
        field = WorldField(grid, z_major)
        reader = SdfOnGrid(field)

        pts = np.array(
            [[0.0, 0.0, 0.0], [7.5, 4.0, 3.0], [21.0, 10.0, 6.0], [9.0, 2.0, 1.5]],
            dtype=np.float64,
        )
        got = np.array(
            [float(reader.at(p.reshape(3, 1))) for p in pts], dtype=np.float64
        )
        want = field.at(pts)
        np.testing.assert_allclose(got, want, atol=1e-6)


class TestWindowForPoints(unittest.TestCase):
    """局部让路的窗口场：格点对齐 + 与全域数值一致（不是另一套 SDF）。"""

    def test_aligned_window_matches_global(self) -> None:
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(4.0, 4.0, 2.0), shape=(20, 15, 8))
        nx, ny, nz = grid.shape
        raw = np.empty((nz, nx, ny), dtype=np.float32)
        for k in range(nz):
            for i in range(nx):
                raw[k, i, :] = 3.0 + 2.0 * i + 0.5 * k + 0.01 * np.arange(ny)
        bias = 1.25
        field = WorldField(grid, raw, sample_bias_m=bias)

        pts = np.array([[20.0, 20.0, 6.0], [28.0, 24.0, 8.0], [36.0, 28.0, 10.0]])
        window = field.window_for_points(pts, margin_m=8.0)

        # 原点必须落在全域格点上（浮点数比较用间距整除容差）；窗口必须真变小。
        gx0, gy0, gz0 = grid.origin
        sx, sy, sz = grid.spacing
        for value, origin, spacing in zip(
            window.grid.origin[:2], (gx0, gy0), (sx, sy)
        ):
            self.assertAlmostEqual((value - origin) / spacing,
                                   round((value - origin) / spacing), places=9)
        self.assertAlmostEqual(window.grid.origin[2], gz0, places=9)
        self.assertLess(window.grid.node_count, grid.node_count)

        # 窗口节点值 = 全域对应格点值（已扣同一偏置）；点插值也必须逐点相等。
        i0 = round((window.grid.origin[0] - gx0) / sx)
        j0 = round((window.grid.origin[1] - gy0) / sy)
        wi, wj, wk = window.grid.shape
        expected = raw[:wk, i0:i0 + wi, j0:j0 + wj] - np.float32(bias)
        np.testing.assert_array_equal(window.sdf, expected.transpose(1, 2, 0))
        np.testing.assert_allclose(window.at(pts), field.at(pts), atol=1e-5)

    def test_outside_points_raise(self) -> None:
        grid = GridSpec(origin=(0.0, 0.0, 0.0), spacing=(4.0, 4.0, 2.0), shape=(10, 10, 5))
        raw = np.ones((5, 10, 10), dtype=np.float32)
        field = WorldField(grid, raw)
        with self.assertRaises(WindowOutsideField):
            field.window_for_points(np.array([[1000.0, 0.0, 2.0]]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
