# -*- coding: utf-8 -*-
"""实验指标：逐机航迹质量 + 协调周期记录。

职责：把"跑完了"变成可入表的数——时长/飞行时长/弧长/最小净空（SDF 与真几何两套）/
      复核结论/预算占用，以及冲突消解的周期与遗留冲突。
边界：不改航迹、不规划、不做统计显著性（那是 ``metrics.py`` 上层的事）。
相关：``methods/timeline/feasibility.py``、``methods/spatial/prism_clearance.py``、
      ``methods/planning/leg_segments.py``。

两套净空必须都记：SDF 是优化器看到的量（离散、偏乐观），真几何是交付判据；
只记 SDF 会出现"表里 6.3 m、实际 4.1 m"的对不上账。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np

from path_planning.common.flight_budget import Budget, BudgetVerdict
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import PlanRequest, Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.planning.leg_segments import sampled_positions
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.feasibility import VerificationLimits, verify_trajectory

__all__ = ["budget_check", "fleet_metrics", "trajectory_metrics"]


def trajectory_metrics(
    traj: Trajectory,
    *,
    vehicle: VehicleSpec,
    field: WorldField,
    prisms: PrismClearance,
    params: PlanParams,
    alt_range_m: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """单条航迹的质量指标（SDF 净空、真几何净空、独立复核、预算）。"""
    step = float(params.clearance_sample_step_m)
    pts = sampled_positions(traj, step)
    sdf_min = float(np.min(field.at(pts)))
    prism_min = float(prisms.min_clearance(pts, step))

    limits = VerificationLimits(
        clearance_m=float(params.clearance_m),
        endpoint_exempt_segments=1,
        sample_step_m=step,
        expected_start=traj.start_position,
        expected_end=traj.end_position,
        alt_range_m=alt_range_m,
    )
    verdict = verify_trajectory(traj, vehicle, field, limits, params=params)
    budget = budget_check(traj, vehicle)
    speeds = [
        float(np.linalg.norm(v))
        for seg in traj.segments
        for v in (seg.v0, seg.v1)
        if not seg.is_hover
    ]
    return {
        "duration_s": traj.duration,
        "flight_duration_s": traj.flight_duration,
        "hover_duration_s": traj.hover_duration,
        "weighted_duration_s": traj.weighted_duration,
        "path_len_m": traj.path_len_m,
        "mean_speed_mps": 0.0 if traj.flight_duration <= 0.0
        else traj.path_len_m / traj.flight_duration,
        "max_speed_mps": max(speeds, default=0.0),
        "sdf_min_m": sdf_min,
        "prism_min_m": prism_min,
        "clearance_margin_m": prism_min - float(params.clearance_m),
        "verified": bool(verdict.ok),
        "violations": list(verdict.codes()) if hasattr(verdict, "codes") else [],
        "budget": None if budget is None else {
            "ok": budget.ok,
            "binding": budget.binding.value,
            "time_ratio": budget.time_ratio,
            "range_ratio": budget.range_ratio,
            "headroom_s": budget.headroom_s,
            "headroom_m": budget.headroom_m,
        },
        "segments": len(traj.segments),
    }


def fleet_metrics(
    trajectories: Mapping[int, Trajectory],
    *,
    vehicles: Mapping[int, VehicleSpec],
    requests: Iterable[PlanRequest],
    field: WorldField,
    prisms: PrismClearance,
    params: PlanParams,
    outcome,
    changed_ids: Iterable[int] = (),
    alt_range_m: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """机队级指标：逐机质量 + 协调周期 + 遗留冲突。"""
    per_uav: dict[str, Any] = {}
    for uid, traj in sorted(trajectories.items()):
        per_uav[str(uid)] = trajectory_metrics(
            traj, vehicle=vehicles[uid], field=field, prisms=prisms,
            params=params, alt_range_m=alt_range_m,
        )

    req_list = list(requests)
    iterations = []
    if outcome is not None:
        iterations = [
            {
                "iteration": int(r.round_index),
                "conflicts_before": int(r.conflicts_before),
                "conflicts_after": int(r.conflicts_after),
                "replanned": list(r.replanned),
                "failed": list(r.failed),
            }
            for r in outcome.rounds
        ]
    report = None if outcome is None else outcome.report
    return {
        "per_uav": per_uav,
        "totals": {
            "uavs": len(per_uav),
            "path_len_m": sum(m["path_len_m"] for m in per_uav.values()),
            "duration_s": sum(m["duration_s"] for m in per_uav.values()),
            "min_prism_m": min((m["prism_min_m"] for m in per_uav.values()), default=None),
        },
        "coordination": {
            "ok": None if outcome is None else bool(outcome.ok),
            "iterations": iterations,
            # 让路留痕：``{"2": "vertical_up_16m"}``。只记标签，不记 trial 全文——
            # 复现失败时要的是"哪一档被接受"与"周期/耗时"，全量 trials 进结果目录反而
            # 让 metrics 变成日志。失败的逐条理由仍在 ``LocalReplanResult.trials``。
            "moves": {} if outcome is None else dict(outcome.detail.get("moves", {})),
            "remaining_conflicts": 0 if report is None else len(report.conflicts),
            # 判过的对（含安全对）与冲突对分开记：``conflict_ids`` 是**涉及机号集合**，
            # 一对冲突就是 2 个机号，拿它当"对数"会把 1 对读成 2 对。
            "pairs_detected": 0 if report is None else len(report.pairs),
            "certificates": {} if report is None else dict(report.certificate_counts),
            "elapsed_s": None if report is None else report.elapsed_s,
            "changed": sorted(int(uid) for uid in changed_ids),
        },
        "requests": [
            {"uav_id": int(r.uav_id), "t0": r.t0, "stops": len(r.stops)} for r in req_list
        ],
    }


def budget_check(traj: Trajectory, vehicle: VehicleSpec) -> BudgetVerdict:
    """按机体名义能力（``max_time_s`` / ``max_range_m``）复核一次预算。

    清单里的**剩余量**是另一套口径，结果文件里分开记；这里只回答"这条航迹本身
    是否超出该机名义能力"。
    """
    budget = Budget(
        endurance_remaining_s=float(vehicle.max_time_s),
        range_remaining_m=float(vehicle.max_range_m),
    )
    return budget.check(
        traj.duration, traj.path_len_m, hover_weight=float(traj.hover_weight)
    )

