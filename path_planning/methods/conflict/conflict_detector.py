# -*- coding: utf-8 -*-
"""冲突检测器：把"轨迹集合"变成"冲突对集合 + 证书统计"。

职责：对同一轮全部航迹两两配对、各自弦化一次（缓存）、走三级证书、汇总冲突，
      并记录每一级证书的命中计数（性能与密度的唯一诚实账本）。
边界：不重规划、不聚类（聚类在 ``conflict_clusters.py``）、不写 Registry。
相关：``docs/conflict_model.md`` §7（判定树）、§8（配对与索引）、§9（复杂度）、
      ``chord_sampling.py``、``pair_certificates.py``。

**配对口径**（§8.1）：本函数只按"航迹 × 航迹"两两判定，不区分新/旧。谁可动是
**协调层**的信息，检测层不该知道——把"可动侧"塞进检测器会让检测结果依赖调用顺序。

**为什么先做时间窗与包围盒预筛**：``certify_pair`` 内部已含 C1/C2，但每对都要
先算一次弦化偏差与包围盒。这里在配对前用"整条轨迹时间窗是否重叠"再挡一道，
它是纯标量比较，比弦化便宜得多；命中率高的场景下这一道能把工作量从 O(n²·弦²)
降到 O(n²) 次标量比较 + 少量真判定。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.conflict.chord_sampling import (
    MotionChord,
    chordify,
    max_initial_chord_s,
)
from path_planning.methods.conflict.ellipsoid_metric import EllipsoidMetric
from path_planning.methods.conflict.pair_certificates import (
    CertificateKind,
    PairVerdict,
    certify_pair,
    unified_eps,
)

__all__ = [
    "DetectionConfig",
    "ConflictPair",
    "DetectionReport",
    "detect_conflicts",
    "detect_pair",
]


@dataclass(frozen=True)
class DetectionConfig:
    """检测配置（由 ``PlanParams`` + 机体 + 几何误差派生）。"""

    metric: EllipsoidMetric
    eps: float
    eps_a_m: float
    max_chord_s: float
    e_geo_m: float = 0.0
    use_bbox: bool = True
    max_depth: int = 8

    @classmethod
    def from_params(
        cls, params: PlanParams, vehicle: VehicleSpec, *, e_geo_m: float = 0.0
    ) -> DetectionConfig:
        return cls(
            metric=EllipsoidMetric(
                semiaxes_m=tuple(float(x) for x in params.ellipsoid_semiaxes_m),
                bbox_margin_m=float(params.bbox_margin_m),
            ),
            eps=unified_eps(params.time_eps_s, params.space_eps_m),
            eps_a_m=float(params.eps_a_m),
            max_chord_s=max_initial_chord_s(float(params.eps_a_m), vehicle.a_max_mps2),
            e_geo_m=float(e_geo_m),
        )


@dataclass(frozen=True)
class ConflictPair:
    """一对航迹的判定结果（对外可读形态）。"""

    uav_a: int
    uav_b: int
    safe: bool
    kind: CertificateKind
    min_distance: float
    cpa_time: float | None
    chords_compared: int
    radius: float

    @property
    def ids(self) -> tuple[int, int]:
        return (self.uav_a, self.uav_b)


@dataclass(frozen=True)
class DetectionReport:
    """一轮检测的汇总。

    Attributes:
        pairs:          全部判定过的航迹对（含安全对，便于统计）
        conflicts:      其中判为冲突的对
        certificate_counts: 各级证书命中次数（含 ``conflict``）
        chord_pairs:    实际做 CPA 的弦对数总和（性能账本，§9.2 要求实测）
        elapsed_s:      墙钟耗时
    """

    pairs: tuple[ConflictPair, ...]
    certificate_counts: dict[str, int] = field(default_factory=dict)
    chord_pairs: int = 0
    elapsed_s: float = 0.0

    @property
    def conflicts(self) -> tuple[ConflictPair, ...]:
        return tuple(p for p in self.pairs if not p.safe)

    @property
    def conflict_ids(self) -> set[int]:
        out: set[int] = set()
        for p in self.conflicts:
            out.update(p.ids)
        return out


def detect_pair(
    traj_a: Trajectory,
    traj_b: Trajectory,
    cfg: DetectionConfig,
) -> ConflictPair:
    """两条轨迹的判定（各自弦化后走证书；不缓存，供局部重规划复用）。"""
    a = chordify(traj_a, eps_a_m=cfg.eps_a_m, max_chord_s=cfg.max_chord_s,
                 max_depth=cfg.max_depth)
    b = chordify(traj_b, eps_a_m=cfg.eps_a_m, max_chord_s=cfg.max_chord_s,
                 max_depth=cfg.max_depth)
    verdict = _verdict(a, b, cfg)
    return _to_pair(traj_a.uav_id, traj_b.uav_id, verdict)


def detect_conflicts(
    trajectories: Sequence[Trajectory], cfg: DetectionConfig
) -> DetectionReport:
    """全对检测。弦化每条轨迹一次（缓存），配对时只做标量预筛与证书。

    Raises:
        ValueError: 机号重复（冲突归属会无法区分，必须由调用方修数据）
    """
    ids = [t.uav_id for t in trajectories]
    if len(set(ids)) != len(ids):
        raise ValueError(f"机号重复，无法唯一归属冲突：{ids}")

    t_start = time.perf_counter()
    chords = {
        t.uav_id: chordify(t, eps_a_m=cfg.eps_a_m, max_chord_s=cfg.max_chord_s,
                           max_depth=cfg.max_depth)
        for t in trajectories
    }
    counts: dict[str, int] = {}
    total_pairs = 0
    total_chord_pairs = 0
    pairs: list[ConflictPair] = []

    for i, ta in enumerate(trajectories):
        for tb in trajectories[i + 1:]:
            if not _windows_overlap(ta, tb, cfg.eps):
                # 时间窗彻底错开：连弦化偏差都不必比，直接记 C1（仍留一条安全对记录）
                counts[CertificateKind.TIME_SEPARATION.value] = (
                    counts.get(CertificateKind.TIME_SEPARATION.value, 0) + 1
                )
                total_pairs += 1
                pairs.append(ConflictPair(
                    uav_a=ta.uav_id, uav_b=tb.uav_id, safe=True,
                    kind=CertificateKind.TIME_SEPARATION, min_distance=float("inf"),
                    cpa_time=None, chords_compared=0, radius=0.0,
                ))
                continue
            verdict = _verdict(chords[ta.uav_id], chords[tb.uav_id], cfg)
            total_pairs += 1
            total_chord_pairs += verdict.chords_compared
            counts[verdict.kind.value] = counts.get(verdict.kind.value, 0) + 1
            pairs.append(_to_pair(ta.uav_id, tb.uav_id, verdict))

    return DetectionReport(
        pairs=tuple(pairs),
        certificate_counts=counts,
        chord_pairs=total_chord_pairs,
        elapsed_s=time.perf_counter() - t_start,
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _verdict(
    a: Sequence[MotionChord], b: Sequence[MotionChord], cfg: DetectionConfig
) -> PairVerdict:
    ta = [cfg.metric.chord(c) for c in a]
    tb = [cfg.metric.chord(c) for c in b]
    return certify_pair(
        ta, tb, metric=cfg.metric, eps=cfg.eps, e_geo_m=cfg.e_geo_m,
        use_bbox=cfg.use_bbox,
    )


def _to_pair(id_a: int, id_b: int, v: PairVerdict) -> ConflictPair:
    return ConflictPair(
        uav_a=id_a, uav_b=id_b, safe=v.safe, kind=v.kind,
        min_distance=v.min_distance, cpa_time=v.cpa_time,
        chords_compared=v.chords_compared, radius=v.radius,
    )


def _windows_overlap(a: Trajectory, b: Trajectory, eps: float) -> bool:
    """两条轨迹的时间窗是否重叠（含 ε）。

    不需要空轨迹分支：``Trajectory`` 构造即禁止零段（``plan_types`` 的不变量），
    在这里再判一次是永远走不到的代码。
    """
    return not (a.t0 > b.t_end + eps or b.t0 > a.t_end + eps)
