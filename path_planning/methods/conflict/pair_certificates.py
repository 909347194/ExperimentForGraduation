# -*- coding: utf-8 -*-
"""三级证书判定：时间分离 → 包围盒分离 → 相对运动下界（与 CPA 合并）。

职责：对一对**已弦化并在 T 空间**的航迹做快速豁免判定，给出"由哪一级豁免"或
      "冲突（含 CPA 时刻与最小距离）"。
边界：不做配对索引、不做聚类、不做重规划、不读全局配置。
相关：``docs/conflict_model.md`` §7（判定树、阈值互补、C3/CPA 合并）、
      ``cpa_geometry.py``（弦对闭式 CPA）、``ellipsoid_metric.py``（T 空间与半径）。

**为什么 C3 的结果直接等价于冲突**（§7.3）：C3 的豁免条件是
``min_dist > r+ε``，而 CPA 的冲突条件是 ``min_dist ≤ r+ε``，两者是同一不等式的
取反，严格互补、无缝隙无重叠。于是 C3 算完即可定论，不必再算一次 CPA——省掉
一半工作量，且**不会**改变结论。只有关掉 C3 的消融模式才需要单独走 CPA 分支。

**阈值口径**（§7.2）：统一 ``ε = max(time_eps, space_eps)``。若证书侧用更松的 ε，
就会在临界带打开"误豁免窗口"——把真冲突判成安全。取较大值保证证书阈值不低于
CPA 阈值。代价是有一小段可判安全的对会被判冲突（误报），但误报是保守的。

**边界统一**（§7.4）：无任何重叠弦对时 ``min_dist = inf`` 判**安全**，与
参考实现 ``motion_lower_bound`` 的相反结论不一致——那条是错的，无重叠即不可能
处于冲突。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from path_planning.methods.conflict.chord_sampling import MotionChord
from path_planning.methods.conflict.cpa_geometry import chord_pair_closest
from path_planning.methods.conflict.ellipsoid_metric import (
    Aabb,
    EllipsoidMetric,
    aabb_distance,
    aabb_of_chord,
)

__all__ = [
    "CertificateKind",
    "PairVerdict",
    "certify_pair",
    "certify_pair_enu",
    "unified_eps",
]


class CertificateKind(str, Enum):
    """豁免来源；``CONFLICT`` 表示未获任何证书。"""

    TIME_SEPARATION = "time_separation"
    BBOX_SEPARATION = "bbox_separation"
    MOTION_LOWER_BOUND = "motion_lower_bound"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class PairVerdict:
    """一对航迹（或一对段窗）的判定结果。

    Attributes:
        safe:          是否安全
        kind:          豁免来源或 ``CONFLICT``
        min_distance:  T 空间最小距离；无重叠弦对时为 ``inf``
        cpa_time:      最小距离出现的绝对时刻（无重叠为 ``None``）
        chords_compared: 真正做了 CPA 的弦对数（性能账本）
        radius:        本次使用的判定半径（含膨胀）
    """

    safe: bool
    kind: CertificateKind
    min_distance: float
    cpa_time: float | None = None
    chords_compared: int = 0
    radius: float = 0.0
    detail: dict[str, float] = field(default_factory=dict)


def unified_eps(time_eps_s: float, space_eps_m: float) -> float:
    """统一 ε = max(时间容差, 空间容差)（§7.2）。"""
    if time_eps_s < 0.0 or space_eps_m < 0.0:
        raise ValueError(f"容差不能为负：{time_eps_s}, {space_eps_m}")
    return float(max(time_eps_s, space_eps_m))


def certify_pair(
    chords_a: Sequence[MotionChord],
    chords_b: Sequence[MotionChord],
    *,
    metric: EllipsoidMetric,
    eps: float,
    e_geo_m: float = 0.0,
    measured_error_m: float | None = None,
    use_bbox: bool = True,
) -> PairVerdict:
    """三级证书 + 合并 CPA。

    Args:
        chords_a, chords_b: **已在 T 空间**的弦序列
        metric:             椭球度量（提供半径与包围盒余量）
        eps:                统一 ε（用 ``unified_eps`` 求）
        e_geo_m:            几何离散误差（建场/棱柱化/AGL 舍入合成）
        measured_error_m:   实测弦化偏差（T 空间）；None = 取两序列的最大值
        use_bbox:           消融开关：关掉 C2 后强制走逐弦 CPA（结论不变，只变开销）

    没有 ``use_motion`` 开关：C3 与 CPA 严格互补（§7.3），关掉 C3 等于什么都没关，
    留着这个参数只会让人以为存在第二条独立的判定路径。消融要测的只有 C1/C2。

    弦必须已在 T 空间：这里不再做变换——若在这里再变一次，调用方无法察觉，
    半径与距离会同时翻倍，判据静默变形。
    """
    if eps < 0.0:
        raise ValueError(f"eps 不能为负：{eps}")
    err = (
        max((c.error_m for c in chords_a), default=0.0) if measured_error_m is None
        else float(measured_error_m)
    )
    err = max(err, max((c.error_m for c in chords_b), default=0.0))
    radius = metric.effective_radius(err, e_geo_m)
    base = dict(radius=radius, eps=float(eps))

    if not chords_a or not chords_b:
        # 任一方无可比运动（零长轨迹）：安全，且不需要任何证书
        return PairVerdict(True, CertificateKind.MOTION_LOWER_BOUND, float("inf"),
                           radius=radius, detail=dict(base, ladder="empty"))

    # C1 时间分离
    a_lo = min(c.t0 for c in chords_a)
    a_hi = max(c.t1 for c in chords_a)
    b_lo = min(c.t0 for c in chords_b)
    b_hi = max(c.t1 for c in chords_b)
    if a_lo > b_hi + eps or b_lo > a_hi + eps:
        return PairVerdict(True, CertificateKind.TIME_SEPARATION, float("inf"),
                           radius=radius, detail=dict(base, ladder="C1"))

    # C2 包围盒分离
    if use_bbox:
        box_a = _union_box(chords_a)
        box_b = _union_box(chords_b)
        box_gap = aabb_distance(box_a, box_b)
        if box_gap - metric.margin_t > radius + eps:
            return PairVerdict(
                True, CertificateKind.BBOX_SEPARATION, float("inf"), radius=radius,
                detail=dict(base, ladder="C2", aabb_distance=box_gap,
                            margin_t=metric.margin_t),
            )

    # C3 相对运动下界（时间重叠窗内逐弦对 CPA），结果即定论
    min_dist = float("inf")
    cpa_time: float | None = None
    compared = 0
    for chord_a in chords_a:
        for chord_b in chords_b:
            hit = chord_pair_closest(chord_a, chord_b, eps_s=eps)
            if hit is None:
                continue
            compared += 1
            if hit.distance < min_dist:
                min_dist = hit.distance
                cpa_time = max(chord_a.t0, chord_b.t0) + hit.time_offset

    if min_dist == float("inf"):
        # 无重叠弦对：安全（§7.4 统一口径，与参考实现的相反结论区分）
        return PairVerdict(True, CertificateKind.MOTION_LOWER_BOUND, min_dist,
                           radius=radius, detail=dict(base, ladder="C3-empty"))

    if min_dist > radius + eps:
        return PairVerdict(
            True, CertificateKind.MOTION_LOWER_BOUND, min_dist, cpa_time=cpa_time,
            chords_compared=compared, radius=radius,
            detail=dict(base, ladder="C3", min_distance=min_dist),
        )

    return PairVerdict(
        False, CertificateKind.CONFLICT, min_dist, cpa_time=cpa_time,
        chords_compared=compared, radius=radius,
        detail=dict(base, ladder="CPA", min_distance=min_dist,
                    threshold=radius + eps),
    )


def certify_pair_enu(
    chords_a: Sequence[MotionChord],
    chords_b: Sequence[MotionChord],
    *,
    metric: EllipsoidMetric,
    eps: float,
    e_geo_m: float = 0.0,
    measured_error_m: float | None = None,
    use_bbox: bool = True,
) -> PairVerdict:
    """ENU 弦的安全入口：先做椭球变换，再交给 ``certify_pair``。

    存在这个函数是因为"忘了变换"是**静默错误**：判据依然能跑、依然给结论，只是
    半径与距离同时偏心，冲突被系统性低估（垂直方向最严重，误差恰为 2 倍）。
    凡从 ``chordify`` 拿到的弦都应走这里。
    """
    ta = [metric.chord(c) for c in chords_a]
    tb = [metric.chord(c) for c in chords_b]
    me = (
        None if measured_error_m is None
        else float(measured_error_m) * max(1.0, metric.z_amplification)
    )
    return certify_pair(
        ta, tb, metric=metric, eps=eps, e_geo_m=e_geo_m,
        measured_error_m=me, use_bbox=use_bbox,
    )


def _union_box(chords: Sequence[MotionChord]) -> Aabb:
    boxes = [aabb_of_chord(c) for c in chords]
    lo = (min(b.lo[0] for b in boxes), min(b.lo[1] for b in boxes),
          min(b.lo[2] for b in boxes))
    hi = (max(b.hi[0] for b in boxes), max(b.hi[1] for b in boxes),
          max(b.hi[2] for b in boxes))
    return Aabb(lo=lo, hi=hi)
