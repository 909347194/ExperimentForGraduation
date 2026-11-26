# -*- coding: utf-8 -*-
r"""Hermite → 匀速弦的弦化：给出**实测**最大偏差，供膨胀与判定使用。

职责：把 ``Trajectory`` 的 Hermite 段递归二等分成匀速直线弦（``MotionChord``），
      逐弦记录"弦与真曲线"的实测最大偏差 ``error_m``。
边界：不改轨迹、不做判定、不做椭球变换（变换在 ``ellipsoid_metric.py``）。
相关：``docs/conflict_model.md`` §2/§3/§9.3（弦近似误差界、自适应细分、硬保护）、
      ``timeline/splines.py``（真曲线求值）、``cpa_geometry.py``（弦上的闭式 CPA）。

**为什么要弦化而不是直接对 Hermite 求 CPA**：两段 Hermite 之间的最近距离没有
闭式解（相对运动是三次多项式，‖r‖² 是六次，极小点要解五次方程）。弦化把问题
降到"匀速直线对匀速直线"，闭式解精确，代价是引入偏差——所以必须**实测**这个
偏差而不是用上界公式估：实测值直接进判定半径的膨胀量（``r_eff = r + 2e + e_geo``），
估大了会过度保守、估小了会漏判，只有实测最诚实。

细分依据（§3.1 定理）：Hermite 偏离弦的量是 \(O(\Delta t^4)\)，粗弦往往一次就过；
但中点采样会错过峰值，故**先按时间上限硬切**（初始弦长不超过
\(2\sqrt{8\epsilon_a/a_{max}}\)），保证"再过也不会太粗"，再按实测偏差细切。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from path_planning.common.plan_types import Trajectory, Vec3
from path_planning.methods.timeline.splines import segment_position

__all__ = [
    "MotionChord",
    "max_initial_chord_s",
    "chordify",
    "chordify_many",
]


@dataclass(frozen=True)
class MotionChord:
    """一段匀速弦：时间窗 ``[t0,t1]``、端点位置、实测弦化偏差。

    ``v`` 由端点与时长导出（匀速），故不单独存字段，避免与 ``p0/p1/t0/t1`` 冲突。
    """

    t0: float
    t1: float
    p0: Vec3
    p1: Vec3
    error_m: float
    segment: int

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def velocity(self) -> Vec3:
        d = self.duration
        if d <= 0.0:
            return (0.0, 0.0, 0.0)
        return (
            (self.p1[0] - self.p0[0]) / d,
            (self.p1[1] - self.p0[1]) / d,
            (self.p1[2] - self.p0[2]) / d,
        )

    @property
    def is_static(self) -> bool:
        return (
            abs(self.p1[0] - self.p0[0]) < 1e-12
            and abs(self.p1[1] - self.p0[1]) < 1e-12
            and abs(self.p1[2] - self.p0[2]) < 1e-12
        )

    def position_at(self, t: float) -> Vec3:
        """弦上绝对时刻 ``t`` 的位置（按时间线性内插，越界夹持）。"""
        d = self.duration
        if d <= 0.0:
            return self.p0
        s = min(1.0, max(0.0, (t - self.t0) / d))
        return (
            self.p0[0] + (self.p1[0] - self.p0[0]) * s,
            self.p0[1] + (self.p1[1] - self.p0[1]) * s,
            self.p0[2] + (self.p1[2] - self.p0[2]) * s,
        )


def max_initial_chord_s(eps_a_m: float, a_max_mps2: float) -> float:
    r"""初始弦长硬上限 \(2\sqrt{8\epsilon_a/a_{max}}\)（§9.3）。

    取固定步长的 2 倍：既享受粗弦的低成本，又保证"每根弦都不会长得离谱"。
    """
    if eps_a_m <= 0.0:
        raise ValueError(f"eps_a_m 必须为正：{eps_a_m}")
    if a_max_mps2 <= 0.0:
        raise ValueError(f"a_max_mps2 必须为正：{a_max_mps2}")
    return 2.0 * float(np.sqrt(8.0 * eps_a_m / a_max_mps2))


def chordify(
    traj: Trajectory,
    *,
    eps_a_m: float,
    max_chord_s: float,
    max_depth: int = 8,
) -> list[MotionChord]:
    """整条轨迹弦化（逐段递归二分），返回按时间递增、首尾相接的弦列表。

    Args:
        traj:       被弦化的轨迹
        eps_a_m:    允许的最大弦化偏差（米）
        max_chord_s: 单根弦的时长硬上限（见 ``max_initial_chord_s``）
        max_depth:  递归深度上限（安全阀；触顶时保留超差弦并把实测偏差记在弦上）

    Raises:
        ValueError: 参数非正
    """
    if eps_a_m <= 0.0:
        raise ValueError(f"eps_a_m 必须为正：{eps_a_m}")
    if max_chord_s <= 0.0:
        raise ValueError(f"max_chord_s 必须为正：{max_chord_s}")
    out: list[MotionChord] = []
    for i, seg in enumerate(traj.segments):
        if seg.duration <= 0.0:
            continue
        out.extend(
            _refine(seg, seg.t0, seg.t1, i, eps_a_m=eps_a_m, max_chord_s=max_chord_s,
                    max_depth=max_depth, depth=0)
        )
    return out


def chordify_many(
    trajectories: Sequence[Trajectory],
    *,
    eps_a_m: float,
    max_chord_s: float,
    max_depth: int = 8,
) -> list[list[MotionChord]]:
    """多条轨迹各自弦化；返回与输入同序的列表。"""
    return [
        chordify(t, eps_a_m=eps_a_m, max_chord_s=max_chord_s, max_depth=max_depth)
        for t in trajectories
    ]


# ---------------------------------------------------------------------------
# 内部：递归细分
# ---------------------------------------------------------------------------


def _refine(
    seg,
    ta: float,
    tb: float,
    index: int,
    *,
    eps_a_m: float,
    max_chord_s: float,
    max_depth: int,
    depth: int,
) -> list[MotionChord]:
    pa = _point(seg, ta)
    pb = _point(seg, tb)
    dev = _chord_deviation(seg, ta, tb, pa, pb)
    if dev <= eps_a_m and (tb - ta) <= max_chord_s:
        return [MotionChord(ta, tb, pa, pb, dev, index)]
    if depth >= max_depth:
        # 安全阀：宁可带着超差弦返回（上游据此把膨胀量取大），也不无限递归
        return [MotionChord(ta, tb, pa, pb, dev, index)]
    tm = 0.5 * (ta + tb)
    return _refine(seg, ta, tm, index, eps_a_m=eps_a_m, max_chord_s=max_chord_s,
                   max_depth=max_depth, depth=depth + 1) + _refine(
        seg, tm, tb, index, eps_a_m=eps_a_m, max_chord_s=max_chord_s,
        max_depth=max_depth, depth=depth + 1)


def _chord_deviation(seg, ta: float, tb: float, pa: Vec3, pb: Vec3) -> float:
    """弦 ``pa→pb`` 与真曲线的最大偏差（在 1/2、1/4、3/4 三点实测取最大）。

    三个内点而不是只测中点：Hermite 的偏差在子区间内可能有两个峰，只测中点会
    低估（§9.3 点名的风险）。三点仍只比真值略小，但配合硬切上限足够；若将来
    要求严格界，可改为在这一步做一维极值搜索。
    """
    if tb - ta <= 0.0:
        return 0.0
    worst = 0.0
    for frac in (0.25, 0.5, 0.75):
        t = ta + (tb - ta) * frac
        true_p = np.asarray(_point(seg, t), dtype=np.float64)
        chord_p = np.asarray(pa, dtype=np.float64) + (
            np.asarray(pb, dtype=np.float64) - np.asarray(pa, dtype=np.float64)
        ) * frac
        worst = max(worst, float(np.linalg.norm(true_p - chord_p)))
    return worst


def _point(seg, t: float) -> Vec3:
    """段内绝对时刻的位置（Hermite 的时间参数是线性的，故 ``s`` 精确可解）。"""
    s = 0.0 if seg.duration <= 0.0 else (t - seg.t0) / seg.duration
    s = min(1.0, max(0.0, s))
    p = segment_position(seg, s)
    return (float(p[0]), float(p[1]), float(p[2]))
