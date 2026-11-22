# -*- coding: utf-8 -*-
"""多初值候选测试：评估指标、排序、去重、重采样。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.methods.spatial.corridor_window import CorridorRule, corridor_window
from path_planning.methods.spatial.initial_paths import (
    PathCandidate,
    dedup_candidates,
    evaluate_candidate,
    generate_initial_paths,
    _resample,
)
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    SdfField,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.spatial.rrt_path_search import RRT3D, RRTConfig, RRTResult

SPACING = (2.0, 2.0, 2.0)
ALT = (4.0, 24.0)
START, GOAL = (0.0, 0.0, 10.0), (40.0, 0.0, 10.0)

WALL = ObstaclePrism(
    np.array([[19.0, -14.0], [21.0, -14.0], [21.0, 14.0], [19.0, 14.0]]), 0.0, 14.0
)
FAR_PILLAR = ObstaclePrism(
    np.array([[0.0, -13.0], [2.0, -13.0], [2.0, -11.0], [0.0, -11.0]]), 0.0, 10.0
)


def _window_field(prisms):
    window = corridor_window(
        START, GOAL, CorridorRule(half_width_m=10.0, margin_m=2.0), SPACING, *ALT
    )
    occ = build_occupancy(prisms, window.grid)
    return SdfField(window.grid, build_sdf(occ, SPACING)), window


def _rrt(field, window, **over):
    kw = dict(
        step_m=6.0, min_step_m=2.0, sdf_step_scale=0.5, max_iters=4000,
        goal_sample_rate=0.15, clearance_m=2.0, seed=11, sample_step_m=1.0,
        shortcut_iters=100,
    )
    kw.update(over)
    return RRT3D(field, window, ALT, RRTConfig(**kw))


def _cand(path, *, length=0.0, min_sdf=0.0, lateral=0.0, inside=True) -> PathCandidate:
    return PathCandidate(
        result=RRTResult(np.asarray(path, dtype=np.float64), 1, 2, len(path)),
        length_m=length,
        min_sdf_m=min_sdf,
        max_lateral_m=lateral,
        inside_corridor=inside,
    )


class TestEvaluateCandidate(unittest.TestCase):
    def test_straight_path_metrics(self) -> None:
        field, window = _window_field([FAR_PILLAR])
        result = RRTResult(
            np.array([START, GOAL], dtype=np.float64), 0, 0, 2
        )
        cand = evaluate_candidate(result, field, window, sample_step_m=1.0)
        self.assertAlmostEqual(cand.length_m, 40.0, places=6)
        self.assertTrue(cand.inside_corridor)
        self.assertAlmostEqual(cand.max_lateral_m, 0.0, places=9)
        self.assertGreater(cand.min_sdf_m, 5.0, "远柱离直线的 SDF 应显著大于净空")

    def test_lateral_outside_corridor(self) -> None:
        field, window = _window_field([FAR_PILLAR])
        path = np.array(
            [START, (20.0, 9.0, 10.0), GOAL], dtype=np.float64
        )
        cand = evaluate_candidate(
            RRTResult(path, 5, 6, 3), field, window, sample_step_m=1.0
        )
        self.assertAlmostEqual(cand.max_lateral_m, 9.0, places=6)
        self.assertTrue(cand.inside_corridor, "9 m 仍在半宽 10 m 内")
        path_far = np.array(
            [START, (20.0, 11.0, 10.0), GOAL], dtype=np.float64
        )
        cand_far = evaluate_candidate(
            RRTResult(path_far, 5, 6, 3), field, window, sample_step_m=1.0
        )
        self.assertFalse(cand_far.inside_corridor, "11 m 超出半宽 10 m")


class TestGenerateInitialPaths(unittest.TestCase):
    def test_generate_sorted_and_feasible(self) -> None:
        field, window = _window_field([WALL])
        rrt = _rrt(field, window)
        cands = generate_initial_paths(
            rrt, START, GOAL, initializations=4, dedup_tol_m=1.0
        )
        self.assertGreaterEqual(len(cands), 1)
        for cand in cands:
            # 窗口是半宽 + margin，候选允许略超半宽（那是 NLP 的硬约束），
            # 但绝不允许超出窗口栅格
            self.assertLessEqual(cand.max_lateral_m, window.lateral_m + 1e-6)
            np.testing.assert_allclose(cand.path_enu[0], START, atol=1e-9)
            np.testing.assert_allclose(cand.path_enu[-1], GOAL, atol=1e-9)
        # 排序：走廊内优先，然后长度不增
        for c1, c2 in zip(cands[:-1], cands[1:]):
            if c1.inside_corridor != c2.inside_corridor:
                self.assertTrue(c1.inside_corridor)
            elif c1.inside_corridor == c2.inside_corridor:
                self.assertLessEqual(c1.length_m, c2.length_m + 1e-9)

    def test_generate_empty_when_blocked(self) -> None:
        field, window = _window_field(
            [ObstaclePrism(
                np.array([[19.0, -14.0], [21.0, -14.0], [21.0, 14.0], [19.0, 14.0]]),
                0.0, 26.0,
            )]
        )
        rrt = _rrt(field, window, max_iters=600)
        cands = generate_initial_paths(
            rrt, START, GOAL, initializations=2, dedup_tol_m=1.0
        )
        self.assertEqual(cands, [], "全堵走廊应返回空列表（上层报 RRT_NO_PATH）")


class TestDedupCandidates(unittest.TestCase):
    def test_near_duplicates_removed(self) -> None:
        base = np.array([[0, 0, 10], [20, 0, 10], [40, 0, 10]], dtype=np.float64)
        twin = base + np.array([[0.0, 0.3, 0.0], [0.0, -0.4, 0.0], [0.0, 0.2, 0.0]])
        far = base + np.array([[0.0, 15.0, 0.0]] * 3)
        cands = [
            _cand(base, length=41.0),
            _cand(twin, length=41.5),
            _cand(far, length=55.0),
        ]
        kept = dedup_candidates(cands, tol_m=1.0)
        self.assertEqual(len(kept), 2)
        # 保留的是排名靠前（已排序的前者），抖动副本被去掉
        np.testing.assert_allclose(kept[0].path_enu, base)
        np.testing.assert_allclose(kept[1].path_enu, far)

    def test_zero_tol_is_strict(self) -> None:
        base = np.array([[0, 0, 10], [40, 0, 10]], dtype=np.float64)
        cands = [_cand(base, length=40.0), _cand(base, length=40.0)]
        # 判据是严格小于：tol=0 时即使完全相同也保留（去重阈值由调用方负责）
        self.assertEqual(len(dedup_candidates(cands, tol_m=0.0)), 2)
        self.assertEqual(len(dedup_candidates(cands, tol_m=1e-9)), 1)


class TestResample(unittest.TestCase):
    def test_straight_line(self) -> None:
        path = np.array([[0, 0, 0], [10, 0, 0]], dtype=np.float64)
        out = _resample(path, 3)
        np.testing.assert_allclose(out, [[0, 0, 0], [5, 0, 0], [10, 0, 0]], atol=1e-9)

    def test_degenerate_path(self) -> None:
        path = np.array([[3, 4, 5]] * 3, dtype=np.float64)
        out = _resample(path, 4)
        self.assertEqual(out.shape, (4, 3))
        np.testing.assert_allclose(out, np.tile([3.0, 4.0, 5.0], (4, 1)))

    def test_bent_path_respects_arclength(self) -> None:
        path = np.array([[0, 0, 0], [3, 4, 0], [3, 4, 12]], dtype=np.float64)
        out = _resample(path, 3)
        # 总弧长 5 + 12 = 17，中点在竖直段上：z = 12 - (17/2 - 5) = 3.5
        np.testing.assert_allclose(out[1], [3.0, 4.0, 3.5], atol=1e-9)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
