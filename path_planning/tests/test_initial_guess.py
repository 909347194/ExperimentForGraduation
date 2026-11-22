# -*- coding: utf-8 -*-
"""NLP 初值测试：弧长重采样、逐节点 dt/v/a、口径 B 可行性、放大循环。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_params import PlanParams
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.timeline.initial_guess import initial_guess, resample_path


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=50000.0, max_time_s=3000.0,
    )


class TestResamplePath(unittest.TestCase):
    def test_straight_equal_spacing(self) -> None:
        path = np.array([[0, 0, 0], [10, 0, 0]], dtype=float)
        out = resample_path(path, 5)
        np.testing.assert_allclose(out[:, 0], [0, 2.5, 5, 7.5, 10], atol=1e-9)

    def test_bent_path_arclength_uniform(self) -> None:
        path = np.array([[0, 0, 0], [3, 4, 0], [3, 4, 12]], dtype=float)
        out = resample_path(path, 3)
        # 总弧长 17，中点在竖直段 z=3.5
        np.testing.assert_allclose(out[1], [3, 4, 3.5], atol=1e-9)

    def test_degenerate_raises(self) -> None:
        with self.assertRaises(ValueError):
            resample_path(np.array([[1, 2, 3]]), 5)
        with self.assertRaises(ValueError):
            resample_path(np.array([[0, 0, 0], [0, 0, 0]]), 5)
        with self.assertRaises(ValueError):
            resample_path(np.array([[0, 0, 0], [1, 0, 0]]), 1)


class TestInitialGuess(unittest.TestCase):
    def test_shapes_and_bounds(self) -> None:
        path = resample_path(np.array([[0, 0, 10], [100, 0, 10]], dtype=float), 20)
        v, a, dt = initial_guess(path, _vehicle(), PlanParams())
        self.assertEqual(v.shape, (20, 3))
        self.assertEqual(a.shape, (20, 3))
        self.assertEqual(dt.shape, (20,))
        self.assertTrue(np.all(dt >= PlanParams().dt_min_s - 1e-9))
        self.assertTrue(np.all(dt <= PlanParams().dt_max_s + 1e-9))
        # 端点悬停
        np.testing.assert_allclose(v[0], 0, atol=1e-9)
        np.testing.assert_allclose(v[-1], 0, atol=1e-9)
        np.testing.assert_allclose(a[0], 0, atol=1e-9)
        np.testing.assert_allclose(a[-1], 0, atol=1e-9)
        # 末位 dt 镜像
        self.assertAlmostEqual(dt[-1], dt[-2], places=9)

    def test_gentle_path_feasible(self) -> None:
        path = resample_path(np.array([[0, 0, 10], [200, 0, 10]], dtype=float), 30)
        v, a, _ = initial_guess(path, _vehicle(), PlanParams())
        veh = _vehicle()
        self.assertTrue(np.all(np.linalg.norm(v[:, :2], axis=1) <= veh.v_max_plan_mps + 1e-6))
        self.assertTrue(np.all(np.linalg.norm(v, axis=1) <= veh.v_max_mps + 1e-6))
        self.assertTrue(np.all(np.linalg.norm(a, axis=1) <= veh.a_max_mps2 + 1e-6))

    def test_sharp_path_inflates_dt(self) -> None:
        # 短段长 + 高巡航 → 初始 dt 被 dt_min 顶住，中心差分速度超限 → 放大
        path = np.array([[0, 0, 10], [1, 0, 10], [2, 0, 10]], dtype=float)
        params = PlanParams(dt_min_s=0.3, dt_max_s=20.0)
        _, _, dt = initial_guess(path, _vehicle(), params)
        self.assertTrue(np.all(dt <= params.dt_max_s + 1e-9))
        self.assertGreaterEqual(float(dt[0]), params.dt_min_s)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
