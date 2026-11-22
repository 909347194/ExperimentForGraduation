# -*- coding: utf-8 -*-
"""检测器与聚类测试：正碰、时间分离、空间分离、机号校验、并查集分组。"""

from __future__ import annotations

import unittest

import numpy as np

from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import HermiteSegment, Trajectory, TimeSource
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.conflict.conflict_clusters import cluster_conflicts
from path_planning.methods.conflict.conflict_detector import (
    CertificateKind,
    DetectionConfig,
    detect_conflicts,
    detect_pair,
)


def _vehicle() -> VehicleSpec:
    return VehicleSpec(
        model="test", mass_kg=1.0, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=2.0,
        a_max_mps2=3.0, f_max_n=16.0, f_min_n=9.0,
        max_range_m=50000.0, max_time_s=3000.0,
    )


def _cfg() -> DetectionConfig:
    return DetectionConfig.from_params(PlanParams(), _vehicle())


def _line(uav_id, p0, p1, dur, t0=0.0) -> Trajectory:
    """匀速直线轨迹（v 与位移匹配，弦化偏差为 0）。"""
    u = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
    v = u / dur
    seg = HermiteSegment(t0=t0, t1=t0 + dur, p0=p0, p1=p1,
                         v0=tuple(v), v1=tuple(v))
    return Trajectory(uav_id=uav_id, segments=(seg,),
                      path_len_m=float(np.linalg.norm(u)),
                      time_source=TimeSource.TRAJECTORY)


class TestDetector(unittest.TestCase):
    def test_head_on_is_conflict(self) -> None:
        cfg = _cfg()
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(2, (40.0, 0.0, 10.0), (0.0, 0.0, 10.0), 4.0)
        rep = detect_conflicts([a, b], cfg)
        self.assertEqual(len(rep.pairs), 1)
        self.assertEqual(rep.pairs[0].kind, CertificateKind.CONFLICT)
        self.assertEqual(rep.conflict_ids, {1, 2})
        self.assertGreater(rep.chord_pairs, 0)

    def test_time_separated_is_safe_c1(self) -> None:
        cfg = _cfg()
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0, t0=0.0)
        b = _line(2, (40.0, 0.0, 10.0), (0.0, 0.0, 10.0), 4.0, t0=100.0)
        rep = detect_conflicts([a, b], cfg)
        self.assertEqual(rep.pairs[0].kind, CertificateKind.TIME_SEPARATION)
        self.assertEqual(rep.conflicts, ())
        self.assertEqual(rep.certificate_counts["time_separation"], 1)
        self.assertEqual(rep.pairs[0].chords_compared, 0)

    def test_space_separated_is_safe_c2(self) -> None:
        cfg = _cfg()
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(2, (40.0, 200.0, 10.0), (0.0, 200.0, 10.0), 4.0)
        rep = detect_conflicts([a, b], cfg)
        self.assertEqual(rep.pairs[0].kind, CertificateKind.BBOX_SEPARATION)
        self.assertTrue(rep.pairs[0].safe)

    def test_duplicate_ids_raise(self) -> None:
        cfg = _cfg()
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(1, (40.0, 0.0, 10.0), (0.0, 0.0, 10.0), 4.0)
        with self.assertRaises(ValueError):
            detect_conflicts([a, b], cfg)

    def test_detect_pair_single(self) -> None:
        cfg = _cfg()
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(2, (40.0, 0.0, 10.0), (0.0, 0.0, 10.0), 4.0)
        pair = detect_pair(a, b, cfg)
        self.assertFalse(pair.safe)
        self.assertEqual(pair.ids, (1, 2))
        self.assertAlmostEqual(pair.cpa_time, 2.0, delta=0.6)

    def test_empty_trajectory_is_rejected_by_invariant(self) -> None:
        """零段轨迹在构造处就被拒：检测器不需要、也不该有空轨迹分支。"""
        with self.assertRaises(ValueError):
            Trajectory(uav_id=2, segments=(), path_len_m=0.0,
                       time_source=TimeSource.TRAJECTORY)


class TestClusters(unittest.TestCase):
    def test_chain_forms_one_cluster(self) -> None:
        cl = cluster_conflicts([(1, 2), (2, 3)])
        self.assertEqual(len(cl), 1)
        self.assertEqual(cl[0].uav_ids, (1, 2, 3))
        self.assertEqual(cl[0].pairs, ((1, 2), (2, 3)))

    def test_disjoint_forms_two_clusters_largest_first(self) -> None:
        cl = cluster_conflicts([(4, 5), (1, 2), (2, 3)])
        self.assertEqual([c.uav_ids for c in cl], [(1, 2, 3), (4, 5)])

    def test_self_loop_dropped(self) -> None:
        cl = cluster_conflicts([(7, 7), (1, 2)])
        self.assertEqual(len(cl), 1)
        self.assertEqual(cl[0].uav_ids, (1, 2))

    def test_unknown_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            cluster_conflicts([(1, 9)], ids=[1, 2, 3])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
