# -*- coding: utf-8 -*-
"""椭球度量与三级证书测试：变换保距、z 放大、余量、阈值互补、三级豁免各自可达。"""

from __future__ import annotations

import unittest

from path_planning.methods.conflict.chord_sampling import MotionChord
from path_planning.methods.conflict.ellipsoid_metric import (
    EllipsoidMetric,
    aabb_distance,
    aabb_of_chord,
)
from path_planning.methods.conflict.pair_certificates import (
    CertificateKind,
    certify_pair_enu,
    unified_eps,
)

METRIC = EllipsoidMetric(semiaxes_m=(30.0, 30.0, 15.0), bbox_margin_m=5.0)


def _chord(t0, t1, p0, p1, err=0.0, seg=0) -> MotionChord:
    return MotionChord(t0, t1, p0, p1, err, seg)


def _hover(t0, t1, p) -> MotionChord:
    return MotionChord(t0, t1, p, p, 0.0, 0)


class TestEllipsoidMetric(unittest.TestCase):
    def test_scale_margin_and_radius(self) -> None:
        self.assertAlmostEqual(METRIC.vertical_scale, 0.5)
        self.assertAlmostEqual(METRIC.z_amplification, 2.0)
        self.assertAlmostEqual(METRIC.radius, 30.0)
        self.assertAlmostEqual(METRIC.margin_t, 10.0)  # 5/0.5，对 z 取严

    def test_vertical_distance_is_amplified(self) -> None:
        """z 差 10 m 在 T 空间是 20 m：这就是"椭球"的全部含义。"""
        self.assertAlmostEqual(METRIC.distance((0, 0, 0), (0, 0, 10)), 20.0)
        self.assertAlmostEqual(METRIC.distance((0, 0, 0), (10, 0, 0)), 10.0)

    def test_chord_transform_keeps_speed_ratio(self) -> None:
        c = _chord(0.0, 2.0, (0.0, 0.0, 0.0), (0.0, 0.0, 10.0))
        t = METRIC.chord(c)
        # 匀速性：T 空间速度 = 原速度的 z 分量 ×2
        self.assertAlmostEqual(t.velocity[2], 10.0)
        self.assertAlmostEqual(t.error_m, c.error_m * 2.0)
        self.assertEqual(t.segment, c.segment)

    def test_effective_radius_inflates_both_sides(self) -> None:
        self.assertAlmostEqual(METRIC.effective_radius(0.05, 0.5), 30.0 + 0.1 + 0.5)
        with self.assertRaises(ValueError):
            METRIC.effective_radius(-1.0, 0.0)

    def test_requires_equal_horizontal_semiaxes(self) -> None:
        with self.assertRaises(ValueError):
            EllipsoidMetric(semiaxes_m=(30.0, 20.0, 15.0), bbox_margin_m=5.0)

    def test_aabb_distance_zero_when_overlapping(self) -> None:
        a = aabb_of_chord(_chord(0, 1, (0, 0, 0), (10, 0, 0)))
        b = aabb_of_chord(_chord(0, 1, (5, 0, 0), (15, 0, 0)))
        self.assertEqual(aabb_distance(a, b), 0.0)
        c = aabb_of_chord(_chord(0, 1, (0, 0, 0), (10, 0, 0)))
        d = aabb_of_chord(_chord(0, 1, (0, 0, 20), (10, 0, 20)))
        self.assertAlmostEqual(aabb_distance(c, d), 20.0)


class TestCertificates(unittest.TestCase):
    def test_unified_eps_takes_max(self) -> None:
        self.assertEqual(unified_eps(0.001, 0.001), 0.001)
        self.assertEqual(unified_eps(0.01, 0.001), 0.01)

    def test_C1_time_separation(self) -> None:
        a = [_chord(0.0, 5.0, (0, 0, 0), (50, 0, 0))]
        b = [_chord(100.0, 110.0, (0, 0, 0), (50, 0, 0))]
        v = certify_pair_enu(a, b, metric=METRIC, eps=0.001)
        self.assertTrue(v.safe)
        self.assertEqual(v.kind, CertificateKind.TIME_SEPARATION)

    def test_C2_bbox_separation(self) -> None:
        a = [_chord(0.0, 5.0, (0, 0, 0), (10, 0, 0))]
        b = [_chord(0.0, 5.0, (0, 200, 0), (10, 200, 0))]
        v = certify_pair_enu(a, b, metric=METRIC, eps=0.001)
        self.assertTrue(v.safe)
        self.assertEqual(v.kind, CertificateKind.BBOX_SEPARATION)
        # 关掉 C2 后必须落到 C3 且结论一致（证明 C2 只是加速）
        v2 = certify_pair_enu(a, b, metric=METRIC, eps=0.001, use_bbox=False)
        self.assertTrue(v2.safe)
        self.assertEqual(v2.kind, CertificateKind.MOTION_LOWER_BOUND)

    def test_head_on_conflict(self) -> None:
        a = [_chord(0.0, 5.0, (-50, 0, 0), (50, 0, 0))]
        b = [_chord(0.0, 5.0, (50, 0, 0), (-50, 0, 0))]
        v = certify_pair_enu(a, b, metric=METRIC, eps=0.001)
        self.assertFalse(v.safe)
        self.assertEqual(v.kind, CertificateKind.CONFLICT)
        self.assertLess(v.min_distance, 1e-9)
        self.assertAlmostEqual(v.cpa_time, 2.5, places=9)

    def test_vertical_pass_is_safe_after_ellipsoid(self) -> None:
        """同样 20 m 间隔：垂直安全（T 空间 40 m）、水平冲突（T 空间 20 m）。

        这是椭球与"半径 30 的球"唯一的实质差别：各向同性球会把 20 m 垂直间隔判成
        冲突，从而逼无人机做无必要的水平绕行。
        """
        a = [_hover(0.0, 10.0, (0, 0, 10.0))]
        above = [_hover(0.0, 10.0, (0, 0, 30.0))]
        self.assertTrue(certify_pair_enu(a, above, metric=METRIC, eps=0.0).safe)
        beside = [_hover(0.0, 10.0, (20.0, 0, 10.0))]
        self.assertFalse(certify_pair_enu(a, beside, metric=METRIC, eps=0.0).safe)

    def test_threshold_strict_complementarity(self) -> None:
        """min_dist 恰等于 threshold 必须判**冲突**（与 C3 的 > 严格互补）。"""
        radius = METRIC.radius
        a = [_hover(0.0, 10.0, (0, 0, 0))]
        # 水平间隔 = radius + eps，T 空间距离恰为阈值
        b = [_hover(0.0, 10.0, (radius, 0, 0))]
        v = certify_pair_enu(a, b, metric=METRIC, eps=0.0)
        self.assertFalse(v.safe)
        self.assertAlmostEqual(v.min_distance, radius)
        self.assertAlmostEqual(v.detail["threshold"], radius)

    def test_no_overlap_is_safe_infinity(self) -> None:
        a = [_chord(0.0, 1.0, (0, 0, 0), (10, 0, 0))]
        b = [_chord(5.0, 6.0, (0, 0, 0), (10, 0, 0))]
        v = certify_pair_enu(a, b, metric=METRIC, eps=0.0)
        self.assertTrue(v.safe)  # C1 先拦下
        # 用极小 eps 且时间窗"相接"时，C1 不拦，C3 无重叠 → 安全
        v2 = certify_pair_enu(a, b, metric=METRIC, eps=0.0, use_bbox=False)
        self.assertTrue(v2.safe)

    def test_empty_side_is_safe(self) -> None:
        v = certify_pair_enu([], [_chord(0, 1, (0, 0, 0), (1, 0, 0))], metric=METRIC, eps=0.0)
        self.assertTrue(v.safe)
        self.assertEqual(v.detail["ladder"], "empty")

    def test_measured_error_enters_radius(self) -> None:
        a = [_chord(0.0, 10.0, (0, 0, 0), (0, 0, 0), err=0.5)]
        b = [_hover(0.0, 10.0, (32.0, 0, 0))]
        # 弦化偏差 0.5 m 在 T 空间放大为 1.0 → 半径 30 + 2×1.0 = 32；
        # 水平间距恰为 32 → 落在阈值上，必须判冲突（严格互补）
        v = certify_pair_enu(a, b, metric=METRIC, eps=0.0)
        self.assertFalse(v.safe)
        self.assertAlmostEqual(v.radius, 32.0)
        # 拉开 0.2 m 即安全
        b2 = [_hover(0.0, 10.0, (32.2, 0, 0))]
        self.assertTrue(certify_pair_enu(a, b2, metric=METRIC, eps=0.0).safe)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
