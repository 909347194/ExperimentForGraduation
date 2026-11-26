# -*- coding: utf-8 -*-
"""局部重规划：候选几何 → 冻结 t0 重解时间 → 独立复核 + 真几何净空 → 选最优。

职责：对每个让路候选跑内层 NLP（``t0`` 冻结、速度口径不变、只改几何），交独立
      验证器复核，再用真几何净空做最后一道闸；净空不达标按参考实现的做法抬阈值
      重解一次；全部候选都不行则报 ``NO_LOCAL_SOLUTION``。
边界：不决定"改哪台机"（那是协调编排的事）、不做全局复检（``global_recheck``）、
      不写 Registry。
相关：``timeline/trajectory_optimizer.py``（内层求解）、
      ``timeline/feasibility.py``（复核）、``spatial/prism_clearance.py``（真几何）、
      ``docs/conflict_model.md`` §10.4/§10.6（内层必须重解时间、无解上报）。

**为什么必须重解时间而不是"保持原时间剖面"**（§10.4）：几何一改，弧长就变；
把原来的 ``(v, a, Δt)`` 硬套到新几何上，速度会沿着新弧长被"拉长"，端点处出现
速度跳变或超限。冻结的只有 ``t0`` 与服务时长，飞行时长必须由内层重解——这正是
"只改空间"的精确定义。

**净空重解阶梯**（照 ``route_segment_trajectory.py``）：SDF 有三项先天乐观（格心
判据、细楼漏检、三线性），实测屋顶附近误差可到 2 m 量级。所以真几何不达标时把
阈值抬 ``clearance_fix_m`` 再解一次；两轮都不过才是真交付不了——这条不能省，否则
会静默交付一条贴着楼顶飞的轨迹。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.conflict.conflict_detector import (
    DetectionConfig,
    detect_conflicts,
)
from path_planning.methods.coordination.detour_candidates import (
    DetourCandidate,
    generate_detours,
)
from path_planning.methods.coordination.leg_stitching import (
    LegGeometry,
    leg_blocks,
    resolve_legs,
    stitch_leg,
    target_index,
)
from path_planning.methods.coordination.local_region import LocalRegion
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.feasibility import (
    VerificationLimits,
    verify_trajectory,
)
from path_planning.methods.timeline.splines import segment_position
from path_planning.methods.timeline.trajectory_optimizer import NlpConfig, optimize_trajectory

__all__ = ["LocalReplanRequest", "LocalReplanResult", "TrialRecord", "replan_locally"]

#: 净空比较的数值余量（米）
_EPS_CLEARANCE = 1e-6


@dataclass(frozen=True)
class LocalReplanRequest:
    """一次局部重规划的输入（全部显式传入，便于复现）。

    Attributes:
        uav_id:          机号（写进轨迹）
        t0:              冻结的出发时刻
        nlp:             内层 NLP 配置（含 ``clearance_m`` 与走廊/时间上限）
        limits:          独立复核限值
        region:          局部域
        params:          参数（取采样步长与让路档）
        vehicle:         机体（复核用）
        clearance_fix_m: 真几何不达标时的抬阈值量
        path:            原几何折线（``(n,3)``）。**仅单航段口径用**；给 ``legs`` 时忽略。
        prisms:          真几何障碍（None = 跳过真几何闸，只在合成测试里这么用）
        others:          同批次其他航迹（给了 ``detection`` 时，候选必须先证"对它们无冲突"）
        detection:       冲突检测配置；``None`` = 不做对手侧验算（只做自身复核）
        base:            原整条轨迹。给出时按航段重解并拼回（多航段航次的正路）
        legs:            航段几何（``legs_of`` 产出）；None = 由 ``path`` 合成单段
        target_time:     冲突时刻（CPA）。用来挑"该重解哪一段"；None = 取第一段
    """

    uav_id: int
    t0: float
    nlp: NlpConfig
    limits: VerificationLimits
    region: LocalRegion
    params: PlanParams
    vehicle: VehicleSpec
    clearance_fix_m: float
    path: np.ndarray | None = None
    prisms: PrismClearance | None = None
    others: tuple[Trajectory, ...] = ()
    detection: DetectionConfig | None = None
    base: Trajectory | None = None
    legs: tuple[LegGeometry, ...] | None = None
    target_time: float | None = None


@dataclass(frozen=True)
class TrialRecord:
    """单次尝试的留痕（候选标签 + 抬阈值量 + 结果）。"""

    label: str
    clearance_fix_m: float
    accepted: bool
    duration_s: float | None
    reason: str


@dataclass(frozen=True)
class LocalReplanResult:
    """局部重规划结果。``ok=False`` 时 ``reason`` 为 ``NO_LOCAL_SOLUTION``。"""

    ok: bool
    trajectory: Trajectory | None
    label: str
    reason: FailureCode | None
    trials: tuple[TrialRecord, ...] = ()
    details: dict[str, object] = field(default_factory=dict)


def replan_locally(
    request: LocalReplanRequest,
    field: SdfField | WorldField,
    *,
    warm: Trajectory | None = None,
    others: tuple[Trajectory, ...] | None = None,
    detection: DetectionConfig | None = None,
    sdf_cache: dict | None = None,
) -> LocalReplanResult:
    """按候选次序尝试局部重规划，返回最短可行轨迹。

    Args:
        request: 输入（含原几何、冻结 t0、NLP 配置、复核限值、局部域）
        field:   SDF（内层净空约束用；与复核同一张场）。窗口 ``SdfField`` 与全域
                 ``WorldField`` 都收：传全域场时本函数先按折线外接盒取对齐窗口，
                 再供候选与复核共用，避免把整张全域场送进 CasADi。
        warm:    热启动轨迹（通常是原轨迹；结构须与新几何节点数一致才有效，
                 不一致时求解器会退化为冷启动而非报错——这是 ``warm`` 的已知语义）
        others:  同批次其他航迹（覆盖 ``request.others``，编排层把"当前全集"传进来）
        detection: 冲突检测配置（覆盖 ``request.detection``）
        sdf_cache: 插值器缓存；同一窗口场在候选间复用
    """
    others_use = tuple(request.others if others is None else others)
    detection_use = request.detection if detection is None else detection
    legs = resolve_legs(request.legs, request.path, request.t0)
    index = target_index(legs, request.target_time)
    leg = legs[index]
    blocks = leg_blocks(request.base) if request.base is not None else ()
    field_use = _local_field(request, field, leg.path)
    candidates = generate_detours(
        leg.path,
        request.region,
        vertical_steps_m=tuple(float(v) for v in request.params.detour_vertical_steps_m),
        lateral_steps_m=tuple(float(v) for v in request.params.detour_lateral_steps_m),
    )
    if not candidates:
        return LocalReplanResult(
            ok=False, trajectory=None, label="none", reason=FailureCode.NO_LOCAL_SOLUTION,
            details={"cause": "局部域内没有可移动的节点（折线离域中心太远）"},
        )

    trials: list[TrialRecord] = []
    best: tuple[float, DetourCandidate, Trajectory, Trajectory] | None = None
    guide = float(request.params.local_replan_guide_radius_m)
    limits = _leg_limits(request.limits, leg)
    tried = 0
    for cand in candidates:
        fixes = (0.0,) if request.clearance_fix_m <= 0.0 else (
            0.0, float(request.clearance_fix_m)
        )
        for fix in fixes:
            base = replace(request.nlp, guide_radius_m=guide)
            cfg = base if fix == 0.0 else replace(
                base, clearance_m=base.clearance_m + fix
            )
            tried += 1
            res = optimize_trajectory(
                np.asarray(cand.path, dtype=np.float64), field_use, cfg,
                uav_id=request.uav_id, t0=leg.t0, warm=warm,
                sdf_cache=sdf_cache,
            )
            traj = res.trajectory
            if traj is None:
                trials.append(TrialRecord(cand.label, fix, False, None,
                                          f"NLP 无解：{res.solver_status}"))
                continue
            verdict = verify_trajectory(
                traj, request.vehicle, field_use, limits, params=request.params
            )
            if not verdict.ok:
                trials.append(TrialRecord(
                    cand.label, fix, False, traj.flight_duration,
                    f"复核不通过：{verdict.codes()}",
                ))
                continue
            if request.prisms is not None:
                clear, _worst = request.prisms.worst(
                    _sample_positions(traj, request.params.clearance_sample_step_m),
                    request.params.clearance_sample_step_m,
                )
                # 比对基准必须是**本次规划的净空口径**（limits），不是 PlanParams 的默认值：
                # 若用后者，调用方把净空调松（例如机巢附近 2 m）时这道闸会用 6 m 判死，
                # 表现为"局部重规划永远无解"，而真正的原因只是两个字段不同步。
                need = float(request.limits.clearance_m)
                if clear < need - _EPS_CLEARANCE:
                    trials.append(TrialRecord(
                        cand.label, fix, False, traj.flight_duration,
                        f"真几何净空 {clear:.2f} < {need:.2f}",
                    ))
                    continue
            whole = (
                stitch_leg(request.base, blocks, index, traj)
                if request.base is not None else traj
            )
            if detection_use is not None and others_use:
                clash = _conflict_with_others(whole, others_use, detection_use)
                if clash is not None:
                    trials.append(TrialRecord(
                        cand.label, fix, False, traj.flight_duration,
                        f"对手侧验算仍冲突（机 {clash[0]}，T 距离 {clash[1]:.2f}）",
                    ))
                    continue
            trials.append(TrialRecord(cand.label, fix, True, traj.flight_duration, "accepted"))
            best = (traj.flight_duration, cand, traj, whole)
            break
        if best is not None:
            # 首个通过即停（first-fit）：候选次序已按"先便宜后贵"排好，每档都要跑
            # 一次完整 NLP + 复核 + 真几何 + 对手证书，全枚举 24 档在真实场景是分钟级
            break

    if best is None:
        return LocalReplanResult(
            ok=False, trajectory=None, label="none", reason=FailureCode.NO_LOCAL_SOLUTION,
            trials=tuple(trials),
            details={"candidates": len(candidates), "tried": tried,
                     "note": "全部候选复核或净空不达标"},
        )
    duration, cand, traj, whole = best
    return LocalReplanResult(
        ok=True, trajectory=whole, label=cand.label, reason=None,
        trials=tuple(trials),
        details={
            "candidates": len(candidates),
            "tried": tried,
            "accepted": sum(1 for t in trials if t.accepted),
            "duration_s": duration,
            "offset_m": cand.offset_m,
            "legs": len(legs),
            "leg_index": index,
            "target_time": request.target_time,
        },
    )


def _leg_limits(limits: VerificationLimits, leg: LegGeometry) -> VerificationLimits:
    """复核限值换成本段的端点（起终点必须与候选几何两端一致）。"""
    return replace(
        limits,
        expected_start=tuple(float(v) for v in leg.path[0]),
        expected_end=tuple(float(v) for v in leg.path[-1]),
    )


def _local_field(
    request: LocalReplanRequest, field: SdfField | WorldField, path: np.ndarray
) -> SdfField | WorldField:
    """给全域场包一张覆盖整条待重解折线的局部窗口场（与全域格点对齐）。

    不是"只改冲突附近"：当前 NLP 仍在整条折线的 120 个节点上求解，端点与域外
    几何仍是同一条约束链。窗口只是**取样范围**——按折线外接盒外扩到足以容纳所有
    让路档与引导管，因此对优化问题本身没有口径变化；但插值器从 1.7 亿体素降到
    几十万体素。窗口节点与全域格点重合（见 ``WorldField.window_for_points``），
    SDF 数值与原来的全域场一致。
    """
    if not isinstance(field, WorldField):
        return field
    reach = (
        max(
            max(float(v) for v in request.params.detour_vertical_steps_m),
            max(float(v) for v in request.params.detour_lateral_steps_m),
        )
        + float(request.params.local_replan_guide_radius_m)
        + 2.0 * max(float(v) for v in field.grid.spacing)
    )
    margin = max(float(request.params.local_region_margin_m), reach)
    return field.window_for_points(path, margin)


def _conflict_with_others(
    traj: Trajectory, others: tuple[Trajectory, ...], detection: DetectionConfig
) -> tuple[int, float] | None:
    """候选对"同批次其他航迹"是否仍有冲突；有则返回 ``(对方机号, T 距离)``。

    为什么候选验收要带对手：只看自身可行性的话，最短的小档让路（8 m）会全部
    "通过"，但竖直间隔 8 m 远不够椭球要求的 15 m——于是每轮都挑最便宜的那个，
    冲突永远消不掉（表现为"轮次耗尽、冲突就是不动"）。把对手侧证书放进验收，
    才让"先试便宜的"与"必须真避开"同时成立。
    """
    report = detect_conflicts((traj, *others), detection)
    for pair in report.conflicts:
        if traj.uav_id in (pair.uav_a, pair.uav_b):
            other = pair.uav_b if pair.uav_a == traj.uav_id else pair.uav_a
            return (other, float(pair.min_distance))
    return None


def _sample_positions(traj: Trajectory, step_m: float) -> np.ndarray:
    """沿轨迹按 ``step_m`` 采点（真几何净空用；与 ``PrismClearance`` 再插值一次不冲突）。"""
    step = max(float(step_m), 1e-6)
    out: list[np.ndarray] = []
    for seg in traj.segments:
        n = max(2, int(np.ceil(seg.chord_m / step)) + 1)
        out.append(segment_position(seg, np.linspace(0.0, 1.0, n)))
    return np.vstack(out) if out else np.zeros((0, 3))


