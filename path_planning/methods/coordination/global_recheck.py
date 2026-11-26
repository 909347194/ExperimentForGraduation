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

**可动侧规则**：默认同一对冲突里只改**机号较大**的那台（等价于"后来的让先到的"）。
跨周期场景必须显式传 ``movable_ids``（本批次新机的机号）：旧航迹由 Registry
提供、属于"已发布的时空障碍"，**旧侧冻结**不能靠"新机机号恰好更大"这种约定
——一旦旧机机号更大就会改错对象。显式白名单下，一对里只有新机可动时改新机，
两台都可动时仍取机号大者，两台都不可动则记为不可消解（不许偷偷改旧侧）。
"谁可动"只出现在编排层，检测层依然只看几何与时间。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

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
from path_planning.methods.coordination.leg_stitching import legs_of
from path_planning.methods.coordination.local_region import LocalRegion, region_from_conflict
from path_planning.methods.coordination.local_replan import (
    LocalReplanRequest,
    replan_locally,
)
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.feasibility import VerificationLimits
from path_planning.methods.timeline.trajectory_optimizer import NlpConfig

__all__ = ["RoundRecord", "CoordinationOutcome", "coordinate_conflicts"]


def _movable_of_pair(pair: tuple[int, int], movable_ids: frozenset[int]) -> int | None:
    """显式白名单下，一对冲突里该改哪台；返回 None = 两台都不可动。

    两台都可动沿用"机号大者让路"；只有一台可动时**不管机号顺序**都改它。
    """
    a, b = pair
    a_ok, b_ok = int(a) in movable_ids, int(b) in movable_ids
    if not a_ok and not b_ok:
        return None
    if a_ok and b_ok:
        return max(int(a), int(b))
    return int(a) if a_ok else int(b)


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
        pre_report:   第一轮改动前的检测报告（新—旧冲突的触发证据；无冲突时也有）
    """

    ok: bool
    trajectories: dict[int, Trajectory]
    rounds: tuple[RoundRecord, ...] = ()
    reason: FailureCode | None = None
    report: DetectionReport | None = None
    detail: dict[str, object] = field(default_factory=dict)
    pre_report: DetectionReport | None = None


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
    movable_ids: frozenset[int] | None = None,
    sdf_cache: dict | None = None,
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
        movable_ids:  允许改几何的机号白名单。None = 默认"机号大者让路"；
                      跨周期传本批次新机机号，Registry 里的旧航迹一律冻结。
        sdf_cache:    由门面传入的插值器缓存；同一场对象跨候选复用。

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
    pre_report: DetectionReport | None = None
    last_reason: FailureCode | None = None
    blocked_pairs: list[tuple[int, int]] = []
    frozen: tuple[int, ...] = (
        () if movable_ids is None
        else tuple(sorted(int(uid) for uid in current if int(uid) not in movable_ids))
    )

    for round_index in range(1, rounds_limit + 1):
        trajs = tuple(current[k] for k in sorted(current))
        report = detect_conflicts(trajs, detection)
        last_report = report
        if pre_report is None:
            pre_report = report
        before = len(report.conflicts)
        if before == 0:
            records.append(RoundRecord(round_index, 0, 0, ()))
            return CoordinationOutcome(
                ok=True, trajectories=dict(current), rounds=tuple(records),
                report=report,
                detail={"rounds_used": round_index - 1, "moves": {},
                        "frozen_ids": list(frozen), "unresolvable_pairs": []},
                pre_report=pre_report,
            )

        # 同域一起改：域内冲突必须协同处理，逐个独立让路会来回振荡（§8.3）
        clusters = cluster_conflicts(p.ids for p in report.conflicts)
        replanned: list[int] = []
        failed: list[int] = []
        for cluster in clusters:
            # 默认每个冲突对只改机号较大的一台；显式白名单下"旧侧冻结"优先于机号序。
            # 注意 ``cluster.pairs`` 是 ``(id_a, id_b)` 元组，不是 ``ConflictPair``。
            if movable_ids is None:
                movable = sorted({max(pair) for pair in cluster.pairs})
            else:
                chosen: set[int] = set()
                for pair in cluster.pairs:
                    uid = _movable_of_pair((int(pair[0]), int(pair[1])), movable_ids)
                    if uid is None:
                        blocked_pairs.append((int(pair[0]), int(pair[1])))
                    else:
                        chosen.add(uid)
                movable = sorted(chosen)
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
                # 按航段重解：只改与冲突时刻相关的那一段，其余段与悬停原样保留。
                # 旧实现把整条 tour 压成一条折线交给一次 NLP，多航段航次会被压成单段。
                req = LocalReplanRequest(
                    uav_id=uav_id, t0=traj.t0, nlp=nlp, limits=limits,
                    region=region, params=params, vehicle=vehicle,
                    clearance_fix_m=params.local_replan_clearance_fix_m,
                    prisms=prisms, base=traj, legs=legs_of(traj),
                    target_time=(
                        None if partner is None or partner.cpa_time is None
                        else float(partner.cpa_time)
                    ),
                )
                out = replan_locally(
                    req, field,
                    # 对手侧验算：候选必须先证明"对同批次其他航迹无冲突"，
                    # 否则最便宜的小档让路（8 m）会被一律接受、冲突永远消不掉
                    others=tuple(current[k] for k in sorted(current) if k != uav_id),
                    detection=detection, sdf_cache=sdf_cache,
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
                detail={"rounds_used": round_index, "moves": dict(moves),
                        "frozen_ids": list(frozen),
                        "unresolvable_pairs": [list(p) for p in blocked_pairs]},
                pre_report=pre_report,
            )
        if not replanned:
            # 一条都改不动：再跑多少轮都只是重复同样的失败
            break

    if blocked_pairs and last_reason is None:
        last_reason = FailureCode.CONFLICT_UNRESOLVED
    return CoordinationOutcome(
        ok=False, trajectories=dict(current), rounds=tuple(records),
        reason=last_reason or FailureCode.CONFLICT_UNRESOLVED,
        report=last_report,
        detail={"rounds_used": len(records), "moves": dict(moves),
                "unresolved": len(last_report.conflicts) if last_report else 0,
                "frozen_ids": list(frozen),
                "unresolvable_pairs": [list(p) for p in blocked_pairs]},
        pre_report=pre_report,
    )


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

