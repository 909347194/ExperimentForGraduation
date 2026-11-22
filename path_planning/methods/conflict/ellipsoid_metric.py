# -*- coding: utf-8 -*-
"""椭球度量：把安全的椭球邻域拉成球，判据与阈值都在同一空间内比较。

职责：提供 ``(x,y,z) → (x,y,z/s)`` 的线性变换、弦/点的变换、T 空间包围盒余量、
      与实测膨胀量合成的判定半径。
边界：不做配对、不发证书、不做聚类。
相关：``docs/conflict_model.md`` §4（保守膨胀）、§6（椭球化与保序性）、
      ``chord_sampling.py``（被变换的弦）、``pair_certificates.py``（消费者）。

**为什么可以这样变**：线性变换保持直线与匀速（与时间无关），时间轴不动，于是
CPA 闭式解、时间窗、包围盒判据全部原样成立，只是"半径"从各向同性 30 m 变成
椭球——在 T 空间里就是球。垂直半轴取 15 m 的依据是相对竖直速度上限 8 m/s
远小于水平 30 m/s，且 15/30 比 8/30 更保守（§6.5）。

**两个必须写明的偏差**：
1. T 空间里 5 m 的包围盒余量被放大成 10 m（z 被放大 2×）。这里统一取
   ``margin/s``（即 10 m），对水平方向也按 z 向处理——偏严但只有一种逻辑。
2. T 空间欧氏距离**不是**原空间欧氏距离，对外报告"最近多少米"时必须注明是
   ``d_T``，否则与目视的米数不符（§6.4）。
"""

from __future__ import annotations

from dataclasses import dataclass

from path_planning.common.plan_types import Vec3
from path_planning.methods.conflict.chord_sampling import MotionChord

__all__ = ["EllipsoidMetric", "Aabb", "aabb_of_chord", "aabb_distance"]


@dataclass(frozen=True)
class Aabb:
    """轴对齐包围盒（T 空间）。"""

    lo: Vec3
    hi: Vec3


@dataclass(frozen=True)
class EllipsoidMetric:
    """椭球安全域 → T 空间球的度量。

    Attributes:
        semiaxes_m: ``(a, b, c)`` 水平 x/y 与垂直半轴（米）
        bbox_margin_m: ENU 空间的包围盒余量（米），变换后统一按 z 向放大
    """

    semiaxes_m: tuple[float, float, float]
    bbox_margin_m: float

    def __post_init__(self) -> None:
        a, b, c = (float(x) for x in self.semiaxes_m)
        if min(a, b, c) <= 0.0:
            raise ValueError(f"椭球半轴必须为正：{self.semiaxes_m}")
        if abs(a - b) > 1e-9:
            # 只用 z 缩放把椭球变成球，要求 a==b；否则得再补一个平面缩放
            raise ValueError(f"当前实现要求水平半轴相等 a==b：{(a, b)}")
        if self.bbox_margin_m < 0.0:
            raise ValueError(f"包围盒余量不能为负：{self.bbox_margin_m}")

    # -- 基本量 --------------------------------------------------------

    @property
    def vertical_scale(self) -> float:
        """``s = c/a``；T 把 z 除以 s。"""
        return self.semiaxes_m[2] / self.semiaxes_m[0]

    @property
    def radius(self) -> float:
        """T 空间球半径 = 水平半轴 ``a``。"""
        return float(self.semiaxes_m[0])

    @property
    def margin_t(self) -> float:
        """T 空间的包围盒余量 ``margin × max(1, 1/s)``（对全部方向取 z 向的放大）。

        变换是 ``z→z/s``，故 z 向距离被放大 ``1/s`` 倍；5 m 余量在 T 空间表现为
        10 m（``s=0.5``）。水平方向本来不放大，但为省掉第二套逻辑，统一按最大
        倍数处理——偏严，且严的方向恰好是"垂直余量更足"这一安全侧。
        """
        return self.bbox_margin_m * max(1.0, self.z_amplification)

    @property
    def z_amplification(self) -> float:
        """z 向放大倍数 ``1/s``（弦化偏差进 T 空间时按此放大）。"""
        return 1.0 / self.vertical_scale

    # -- 变换 ----------------------------------------------------------

    def point(self, p: Vec3) -> Vec3:
        s = self.vertical_scale
        return (float(p[0]), float(p[1]), float(p[2]) / s)

    def chord(self, c: MotionChord) -> MotionChord:
        """弦进 T 空间：端点缩放即可（匀速性自动保持，速度由端点导出）。"""
        return MotionChord(
            t0=c.t0, t1=c.t1,
            p0=self.point(c.p0), p1=self.point(c.p1),
            # 偏差是各向同性界，按最大放大倍数换算，保证 T 空间内不低估
            error_m=c.error_m * max(1.0, self.z_amplification),
            segment=c.segment,
        )

    def distance(self, pa: Vec3, pb: Vec3) -> float:
        """T 空间欧氏距离（对外须注明是 ``d_T``）。"""
        ta = self.point(pa)
        tb = self.point(pb)
        return float(((ta[0] - tb[0]) ** 2 + (ta[1] - tb[1]) ** 2
                      + (ta[2] - tb[2]) ** 2) ** 0.5)

    # -- 判定半径 ------------------------------------------------------

    def effective_radius(self, measured_error_m: float, e_geo_m: float) -> float:
        """判定半径 ``r_T + 2e + e_geo``（§4.3 的膨胀推论）。

        ``measured_error_m`` 必须是**已变换到 T 空间**的实测弦化偏差；取两条
        轨迹的较大者（两条都可能偏离自己的弦）。
        """
        if measured_error_m < 0.0 or e_geo_m < 0.0:
            raise ValueError(
                f"膨胀量不能为负：error={measured_error_m}, e_geo={e_geo_m}"
            )
        return self.radius + 2.0 * measured_error_m + e_geo_m


def aabb_of_chord(c: MotionChord) -> Aabb:
    """单弦的 T 空间包围盒（弦已在 T 空间时直接调用）。"""
    lo = (min(c.p0[0], c.p1[0]), min(c.p0[1], c.p1[1]), min(c.p0[2], c.p1[2]))
    hi = (max(c.p0[0], c.p1[0]), max(c.p0[1], c.p1[1]), max(c.p0[2], c.p1[2]))
    return Aabb(lo=lo, hi=hi)


def aabb_distance(a: Aabb, b: Aabb) -> float:
    """两包围盒的最小欧氏距离（相交时为 0）。"""
    dx = max(0.0, max(a.lo[0], b.lo[0]) - min(a.hi[0], b.hi[0]))
    dy = max(0.0, max(a.lo[1], b.lo[1]) - min(a.hi[1], b.hi[1]))
    dz = max(0.0, max(a.lo[2], b.lo[2]) - min(a.hi[2], b.hi[2]))
    return float((dx * dx + dy * dy + dz * dz) ** 0.5)
