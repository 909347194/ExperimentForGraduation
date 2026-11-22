# -*- coding: utf-8 -*-
"""协调编排：检测 → 聚类 → 逐域局部重规划 → 全局复检 → 轮次终止。

职责：在一轮规划内驱动"冲突消解"的完整循环，维护"当前航迹集合"，把每轮结果
      （冲突数前后、改了哪些机）留痕，并在轮次耗尽后按失败码上报。
边界：不做单机初规划（``planning``）、不写 Registry（``trajectory_registry``）、
      不做轨迹内的运动学复核（``timeline/feasibility.py``，局部重规划已内置）。
相关：``conflict/conflict_detector.py``、``conflict/conflict_clusters.py``、
      ``local_region.py``、``local_replan.py``、``docs/conflict_model.md`` §11/§12。

**为什么必须"改完再全对复检"**（§11.2）：局部让路只保证"被改的那条航迹避开了
原来那台"，它可能正好撞上第三台；被改航迹的时间也变了（t0 冻结但飞行时长重解），
时间重叠关系随之改变。因此不能只复检被改动的对，必须全对重跑。

**可动侧规则**：同一对冲突里只改**机号较大**的那台（等价于"后来的让先到的"）。
这条规则写死在编排层而不是检测层：检测层不该知道"谁可动"（那会让检测结果依赖
调用顺序）。跨周期遗留的在飞航迹（旧）由上层以"只允许新侧可动"的机号安排自然满足。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.conflict.conflict_clusters import cluster_conflicts
from path_planning.methods.conflict.conflict_detector import (
    DetectionConfig,
    DetectionReport,
    detect_conflicts,
)
from path_planning.methods.coordination.local_region import LocalRegion, region_from_conflict
from path_planning.methods.coordination.local_replan import (
    LocalReplanRequest,
    replan_locally,
)
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.feasibility import VerificationLimits
from path_planning.methods.timeline.splines import segment_position
from path_planning.methods.timeline.trajectory_optimizer import NlpConfig

__all__ = ["RoundRecord", "CoordinationOutcome", "coordinate_conflicts"]


@dataclass(frozen=True)
class RoundRecord:
    """一轮冲突消解的结果（写进上层日志与论文表格）。"""

    round_index: int
    conflicts_before: int
    conflicts_after: int
    replanned: tuple[int, ...]
    failed: tuple[int, ...] = ()


@dataclass(frozen=True)
class CoordinationOutcome:
    """协调结果。

    Attributes:
        ok:           是否已无冲突
        trajectories: 最终航迹（机号 → 航迹）
        rounds:       每轮留痕
        reason:       ``ok=False`` 时的失败码（``CONFLICT_UNRESOLVED`` 或
                      ``NO_LOCAL_SOLUTION`` / ``OPT_FAILED`` 之类透传）
        report:       最后一轮的检测报告（用于论文里的证书分布统计）
    """

    ok: bool
    trajectories: dict[int, Trajectory]
    rounds: tuple[RoundRecord, ...] = ()
    reason: FailureCode | None = None
    report: DetectionReport | None = None
    detail: dict[str, object] = field(default_factory=dict)


def coordinate_conflicts(
    trajectories: Sequence[Trajectory],
    *,
    field: SdfField | WorldField,
    params: PlanParams,
    vehicle: VehicleSpec,
    detection: DetectionConfig,
    nlp_for: Callable[[int], NlpConfig],
    prisms: PrismClearance | None = None,
    max_rounds: int | None = None,
    time_pad_s: float = 10.0,
) -> CoordinationOutcome:
    """驱动冲突消解循环。

    Args:
        trajectories: 当前航迹集合（每机一条）
        field:        SDF（局部重规划内层约束用；窗口场与全域场都收）
        params:       参数（让路档、局部域半径、采样步长）
        vehicle:      机体（复核与真几何闸用；异构机队请先按同机型分组调用）
        detection:    检测配置（椭球、ε、弦化）
        nlp_for:      按机号取内层 NLP 配置（异构机队各机速度/推力不同）
        prisms:       真几何障碍（None = 跳过真几何闸，仅合成测试用）
        max_rounds:   轮次上限；None = ``params.max_replan_rounds``
        time_pad_s:   局部域的时间松弛（只用于报告与域构造）

    Raises:
        ValueError: 机号重复（无法唯一归属）
    """
    rounds_limit = int(params.max_replan_rounds if max_rounds is None else max_rounds)
    if rounds_limit < 1:
        raise ValueError(f"max_rounds 至少 1：{rounds_limit}")

    current: dict[int, Trajectory] = {t.uav_id: t for t in trajectories}
    if len(current) != len(trajectories):
        raise ValueError("机号重复，无法唯一归属冲突")
    records: list[RoundRecord] = []
    moves: dict[str, str] = {}  # 机号 → 被接受的让路候选标签（留痕，供实验层记录）
    last_report: DetectionReport | None = None
    last_reason: FailureCode | None = None

    for round_index in range(1, rounds_limit + 1):
        trajs = tuple(current[k] for k in sorted(current))
        report = detect_conflicts(trajs, detection)
        last_report = report
        before = len(report.conflicts)
        if before == 0:
            records.append(RoundRecord(round_index, 0, 0, ()))
            return CoordinationOutcome(
                ok=True, trajectories=dict(current), rounds=tuple(records),
                report=report,
                detail={"rounds_used": round_index - 1, "moves": {}},
            )

        # 同域一起改：域内冲突必须协同处理，逐个独立让路会来回振荡（§8.3）
        clusters = cluster_conflicts(p.ids for p in report.conflicts)
        replanned: list[int] = []
        failed: list[int] = []
        for cluster in clusters:
            # 每个冲突对只改机号较大的一台；同域内去重后逐台重规划
            # 注意 ``cluster.pairs`` 是 ``(id_a, id_b)`` 元组，不是 ``ConflictPair``
            movable = sorted({max(pair) for pair in cluster.pairs})
            for uav_id in movable:
                traj = current[uav_id]
                partner = next(
                    (p for p in report.conflicts
                     if uav_id in p.ids and p.min_distance < float("inf")),
                    None,
                )
                other = (
                    current[_other_id(partner, uav_id)] if partner is not None else None
                )
                region = (
                    region_from_conflict(traj, other, partner.cpa_time,
                                         margin_m=params.local_region_margin_m,
                                         time_pad_s=time_pad_s)
                    if other is not None
                    else _fallback_region(traj, params)
                )
                nlp = nlp_for(uav_id)
                limits = _limits_from(nlp, traj)
                req = LocalReplanRequest(
                    path=_polyline_of(traj), uav_id=uav_id,
                    t0=traj.t0, nlp=nlp, limits=limits, region=region,
                    params=params, vehicle=vehicle,
                    clearance_fix_m=params.local_replan_clearance_fix_m,
                    prisms=prisms,
                )
                out = replan_locally(
                    req, field, warm=traj,
                    # 对手侧验算：候选必须先证明"对同批次其他航迹无冲突"，
                    # 否则最便宜的小档让路（8 m）会被一律接受、冲突永远消不掉
                    others=tuple(current[k] for k in sorted(current) if k != uav_id),
                    detection=detection,
                )
                if out.ok and out.trajectory is not None:
                    current[uav_id] = out.trajectory
                    replanned.append(uav_id)
                    moves[str(int(uav_id))] = str(out.label)
                else:
                    failed.append(uav_id)
                    last_reason = out.reason or FailureCode.NO_LOCAL_SOLUTION

        after_report = detect_conflicts(
            tuple(current[k] for k in sorted(current)), detection
        )
        last_report = after_report
        records.append(RoundRecord(
            round_index=round_index, conflicts_before=before,
            conflicts_after=len(after_report.conflicts),
            replanned=tuple(sorted(replanned)), failed=tuple(sorted(failed)),
        ))
        if not after_report.conflicts:
            return CoordinationOutcome(
                ok=True, trajectories=dict(current), rounds=tuple(records),
                report=after_report,
                detail={"rounds_used": round_index, "moves": dict(moves)},
            )
        if not replanned:
            # 一条都改不动：再跑多少轮都只是重复同样的失败
            break

    return CoordinationOutcome(
        ok=False, trajectories=dict(current), rounds=tuple(records),
        reason=last_reason or FailureCode.CONFLICT_UNRESOLVED,
        report=last_report,
        detail={"rounds_used": len(records), "moves": dict(moves),
                "unresolved": len(last_report.conflicts) if last_report else 0},
    )


def _polyline_of(traj: Trajectory, step_m: float = 5.0) -> np.ndarray:
    """把当前轨迹**按固定步长采样**成折线，作为局部重规划的初值几何。

    为什么不用 ``trajectory_nodes``：一条单段轨迹的节点只有两个端点，而端点必须
    冻结——于是让路候选里没有任何可动节点，局部重规划会**永远**返回
    ``NO_LOCAL_SOLUTION``（表现为"冲突怎么改都改不掉"）。按 5 m 采样可保证局部域
    内有足够节点，余弦权重才有作用对象。

    为什么用"当前轨迹"而不是原始几何：局部重规划是**迭代**的，第二轮必须以
    "上一轮已经让开的那条线"为起点，否则每轮都从同一原点出发、改完又撞回去。
    """
    step = max(float(step_m), 1e-6)
    chunks: list[np.ndarray] = []
    for seg in traj.segments:
        n = max(2, int(np.ceil(seg.chord_m / step)) + 1)
        chunks.append(segment_position(seg, np.linspace(0.0, 1.0, n)))
    return np.vstack(chunks) if chunks else np.zeros((0, 3))


def _other_id(pair, uav_id: int) -> int:
    return pair.uav_b if pair.uav_a == uav_id else pair.uav_a


def _fallback_region(traj: Trajectory, params: PlanParams) -> LocalRegion:
    p = traj.start_position
    return LocalRegion(
        center_xy=(p[0], p[1]), radius_m=float(params.local_region_margin_m),
        t_cpa=traj.t0, time_pad_s=0.0,
    )


def _limits_from(nlp: NlpConfig, traj: Trajectory) -> VerificationLimits:
    """复核限值必须与内层 NLP **同源**：净空、半宽、走廊、端点豁免。"""
    return VerificationLimits(
        clearance_m=float(nlp.clearance_m),
        half_width_m=None if nlp.half_width_m is None else float(nlp.half_width_m),
        corridor_normal_xy=nlp.corridor_normal_xy,
        corridor_pivot_xy=nlp.corridor_pivot_xy,
        endpoint_exempt_segments=int(nlp.endpoint_exempt_segments),
        sample_step_m=2.0,
        expected_start=traj.start_position,
        expected_end=traj.end_position,
    )
