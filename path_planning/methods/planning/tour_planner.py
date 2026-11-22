# -*- coding: utf-8 -*-
"""单航次编排：走廊 → RRT 多初值 → 时空 NLP → 独立复核 → 真几何闸 → 预算判定。

职责：把一个 ``PlanRequest``（起点 + 停靠点序列）走完整条流水线，产出可直接交付的
      ``PlanResult``（一条带时间的 ``Trajectory`` + 诊断 + 失败码）。
边界：不做多机协调（``coordination``）、不写 Registry（``trajectory_registry``）、
      不做对外契约（``api.py``）、不做批处理（``planning/batch.py``）。
相关：``docs/architecture.md`` §1、``docs/time_mapping.md`` §2–§4、
      参考实现 ``core/route_segment_planner.py`` 的流水线次序。

**为什么每段都重跑"走廊 → 场 → RRT"**：走廊窗口是**斜栅格**（轴向对齐该段），
两段的窗口不共享任何格点。把整条 tour 塞进一个窗口会让第二段的走廊半宽按第一段
轴向度量——那是另一个几何问题，解出来的约束与交付契约对不上。

**为什么每段独立优化、再按时间拼接**：段与段之间靠停靠点分隔，前一架的到达时刻
就是后一段的 ``t0``（外加服务时长）。这保持了"``t0`` 冻结"的口径：段内的 ``t0``
由上游给的时刻派生，可预测、可复现，而不是让整条 tour 一起优化（那等于替上层重排）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import (
    HermiteSegment,
    PlanRequest,
    PlanResult,
    PlanStop,
    Trajectory,
    TimeSource,
)
from path_planning.methods.spatial.corridor_window import (
    CorridorRule,
    CorridorWindow,
    corridor_window,
)
from path_planning.methods.spatial.initial_paths import generate_initial_paths
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.rrt_path_search import RRT3D, RRTConfig
from path_planning.methods.planning.delivery_gate import dense_clearance, global_gate
from path_planning.methods.planning.leg_segments import hover_segment, sampled_positions, vertical_leg
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.feasibility import VerificationLimits, verify_trajectory
from path_planning.methods.timeline.nlp_config import NlpConfig
from path_planning.methods.timeline.trajectory_optimizer import optimize_trajectory

__all__ = ["TourPlanningContext", "plan_tour"]

#: 水平距离小于此值视为"同一点"（退化航段，走纯竖向管道）
_MIN_LEG_HORIZONTAL_M = 1.0


@dataclass(frozen=True)
class TourPlanningContext:
    """一次编排所需的全部外部依赖（与请求解耦，便于同一场景多次复用）。

    Attributes:
        field:            全域场（窗口按格点取样，见 ``docs/architecture.md`` §2）
        params:           参数体系
        corridor_rule:    走廊半宽规则（交付硬约束的来源）
        alt_range_m:      绝对 z 飞行带 ``(min, max)``（窗口竖向范围）
        prisms:           真几何障碍；给定时每段都过真几何净空闸
        ground_fn:        地面高程函数（可选，RRT 真高带用）
        agl_range_m:      真高带 ``(min, max)``（需 ``ground_fn`` 同时给）
        seed:             RRT 基础种子（多初值取 ``seed + i``，可复现）
        endpoint_clearance_m: 端点豁免圈内的净空阈值（机巢实测 SDF 低，必须放宽）
        endpoint_exempt_radius_m: 端点豁免圈半径（0 = 不豁免）
        clearance_fix_m:  真几何净空不达标时的抬阈值量；None = ``params.local_replan_clearance_fix_m``
    """

    field: WorldField
    params: PlanParams
    corridor_rule: CorridorRule
    alt_range_m: tuple[float, float]
    prisms: PrismClearance | None = None
    ground_fn: Any = None
    agl_range_m: tuple[float, float] | None = None
    seed: int = 0
    endpoint_clearance_m: float | None = None
    endpoint_exempt_radius_m: float = 0.0
    clearance_fix_m: float | None = None

    def __post_init__(self) -> None:
        if self.alt_range_m[1] <= self.alt_range_m[0]:
            raise ValueError(f"alt_range_m 退化：{self.alt_range_m}")
        if self.agl_range_m is not None and self.ground_fn is None:
            raise ValueError("给了 agl_range_m 却缺 ground_fn：真高带无从换算")


def plan_tour(request: PlanRequest, ctx: TourPlanningContext) -> PlanResult:
    """规划一条 tour；任何一段不可交付即整条失败（``RRT_NO_PATH`` 之类）。

    Args:
        request: 规划请求（含逐机机体、冻结 ``t0``、停靠点、预算）
        ctx:     场景侧依赖

    Returns:
        ``PlanResult``：成功时 ``trajectory`` 为整条 tour（含停靠悬停段）；
        失败时 ``failure`` 给码，``detail`` 指明在第几段、什么原因。
    """
    stops: tuple[PlanStop, ...] = request.stops
    segments: list[HermiteSegment] = []
    cursor = float(request.t0)
    prev = np.asarray(request.start_position, dtype=np.float64)
    total_len = 0.0
    hover_total = 0.0
    diagnostics: dict[str, Any] = {"legs": []}

    for index, stop in enumerate(stops):
        goal = np.asarray(stop.position, dtype=np.float64)
        leg_start_t = cursor
        remaining_s = max(request.budget.endurance_remaining_s - (cursor - request.t0), 1e-3)
        if float(np.hypot(goal[0] - prev[0], goal[1] - prev[1])) < _MIN_LEG_HORIZONTAL_M:
            leg = vertical_leg(request.uav_id, request.vehicle, prev, goal, leg_start_t)
            failure = None
        else:
            leg, failure, leg_diag = _plan_leg(
                request, ctx, prev, goal, leg_start_t, remaining_s
            )
            diagnostics["legs"].append(leg_diag)
        if leg is None:
            return PlanResult.failed(
                request.uav_id, failure or FailureCode.OPT_FAILED,
                detail=f"第 {index} 段（→ stop={stop.id}）规划失败",
                diagnostics=diagnostics,
            )
        segments.extend(leg.segments)
        total_len += leg.path_len_m
        cursor = leg.t_end
        if stop.service_time_s > 0.0:
            hover_seg, cursor = hover_segment(goal, cursor, float(stop.service_time_s))
            segments.append(hover_seg)
            hover_total += float(stop.service_time_s)
        prev = goal

    trajectory = Trajectory(
        uav_id=request.uav_id,
        segments=tuple(segments),
        path_len_m=float(total_len),
        time_source=TimeSource.TRAJECTORY,
        hover_weight=float(ctx.params.hover_weight),
        meta={"stops": tuple(s.id for s in stops)},
    )
    # 交付闸：整条航迹在**全域场**上按 0.2 m 稠密量净空，再叠全域真几何。
    # 段内复核只看各段自己的走廊窗口（值被平滑 ~0.1 m），段间接缝与任务点折返处的低点会漏过。
    gate_worst, gate_msg = global_gate(ctx, trajectory, ctx.prisms)
    diagnostics["global_gate"] = {
        "ok": gate_msg is None, "worst_clearance_m": gate_worst, "first": gate_msg,
    }
    if gate_msg is not None:
        return PlanResult.failed(
            request.uav_id, FailureCode.CLEARANCE_VIOLATION,
            detail=f"全域复核未通过：{gate_msg}（最差净空 {gate_worst:.2f} m）",
            diagnostics=diagnostics,
        )
    verdict = request.budget.check(
        trajectory.duration, trajectory.path_len_m, hover_weight=trajectory.hover_weight
    )
    diagnostics["budget_verdict"] = verdict
    diagnostics["hover_s"] = hover_total
    if not verdict.ok:
        # 预算超限仍然交付（带着既有航迹比"什么都不给"更有用），但必须带码
        return PlanResult.failed(
            request.uav_id, verdict.failure or FailureCode.TIME_EXCEEDS_BUDGET,
            detail=f"预算超限：续航占用 {verdict.time_ratio:.2f} / 航程占用 {verdict.range_ratio:.2f}",
            trajectory=trajectory,
            diagnostics=diagnostics,
        )
    return PlanResult.success(trajectory, diagnostics=diagnostics)


# ---------------------------------------------------------------------------
# 单段
# ---------------------------------------------------------------------------


def _plan_leg(
    request: PlanRequest,
    ctx: TourPlanningContext,
    start: np.ndarray,
    goal: np.ndarray,
    t0: float,
    max_time_s: float,
) -> tuple[Trajectory | None, FailureCode | None, dict[str, Any]]:
    """走廊 → 场 → RRT 多初值 → 逐候选 NLP（含净空重解）→ 复核 → 真几何闸。"""
    window = corridor_window(
        tuple(float(v) for v in start),
        tuple(float(v) for v in goal),
        ctx.corridor_rule,
        tuple(float(v) for v in ctx.field.grid.spacing),
        float(ctx.alt_range_m[0]),
        float(ctx.alt_range_m[1]),
    )
    window_field = SdfField(window.grid, ctx.field.window_sdf(window.grid))
    rrt = RRT3D(
        window_field, window, ctx.alt_range_m,
        RRTConfig.from_params(
            ctx.params, clearance_m=float(ctx.params.clearance_m), seed=int(ctx.seed),
            endpoint_clearance_m=ctx.endpoint_clearance_m,
            endpoint_exempt_radius_m=float(ctx.endpoint_exempt_radius_m),
        ),
        ground_fn=ctx.ground_fn, agl_range_m=ctx.agl_range_m,
    )
    candidates = generate_initial_paths(
        rrt, start, goal,
        initializations=int(ctx.params.rrt_initializations),
        dedup_tol_m=float(ctx.params.rrt_dedup_tol_m),
    )
    diag: dict[str, Any] = {
        "t0": t0, "candidates": len(candidates),
        "alt_m": (float(ctx.alt_range_m[0]), float(ctx.alt_range_m[1])),
    }
    if not candidates:
        return None, FailureCode.RRT_NO_PATH, diag

    fix = (
        float(ctx.params.local_replan_clearance_fix_m)
        if ctx.clearance_fix_m is None else float(ctx.clearance_fix_m)
    )
    tried = 0
    for cand in candidates:
        for add in (0.0,) if fix <= 0.0 else (0.0, fix):
            tried += 1
            cfg = _nlp_config(ctx, window, request.vehicle, max_time_s, add)
            res = optimize_trajectory(
                cand.path_enu, window_field, cfg, uav_id=request.uav_id, t0=t0,
            )
            traj = res.trajectory
            if traj is None:
                continue
            limits = _limits(cfg, traj, float(ctx.params.clearance_m))
            verdict = verify_trajectory(
                traj, request.vehicle, window_field, limits, params=ctx.params
            )
            if not verdict.ok:
                first = verdict.first()
                diag.setdefault("rejected", []).append(
                    (res.solver_status, verdict.codes(),
                     verdict.metrics.get("worst_clearance_m"),
                     None if first is None else first.detail)
                )
                continue
            # 稠密净空与真几何都按**交付口径**判（不按涨阈值后的 cfg）：涨阈值只给求解留余量。
            dense = dense_clearance(ctx, traj)
            if dense < float(ctx.params.clearance_m) - 1e-6:
                diag.setdefault("rejected", []).append(f"dense {dense:.2f}")
                continue
            if ctx.prisms is not None:
                step = float(ctx.params.clearance_sample_step_m)
                clear = ctx.prisms.min_clearance(sampled_positions(traj, step), step)
                if clear < float(ctx.params.clearance_m) - 1e-6:
                    diag.setdefault("rejected", []).append(f"prism {clear:.2f}")
                    continue
            diag["tried"] = tried
            diag["length_m"] = cand.length_m
            diag["solver"] = res.solver_status
            return traj, None, diag

    diag["tried"] = tried
    diag["status"] = "all_candidates_failed"
    return None, FailureCode.OPT_FAILED, diag


def _nlp_config(
    ctx: TourPlanningContext,
    window: CorridorWindow,
    vehicle: Any,
    max_time_s: float,
    extra_clearance: float,
) -> NlpConfig:
    """内层 NLP 配置：净空、半宽、走廊轴与枢轴必须与走廊窗口同源。"""
    return NlpConfig.from_params(
        ctx.params, vehicle,
        clearance_m=float(ctx.params.clearance_m) + float(extra_clearance),
        half_width_m=float(window.half_width_m),
        corridor_normal_xy=(float(window.normal[0]), float(window.normal[1])),
        corridor_pivot_xy=(float(window.start_xy[0]), float(window.start_xy[1])),
        max_time_s=float(max_time_s),
        alt_range_m=ctx.alt_range_m,
    )


def _limits(cfg: NlpConfig, traj: Trajectory, clearance_m: float) -> VerificationLimits:
    """段内复核的判据：净空按**交付口径**（``clearance_m``），其余与内层 NLP 同源。

    求解时可以把净空涨 ``clearance_fix_m`` 留余量（``cfg``里那份），但"能不能收"必须按
    交付阈值判——否则涨阈值那一路永远差 0.1–0.2 m 被判不收，重解等于白跑。
    """
    band = (None if cfg.alt_min_m is None or cfg.alt_max_m is None
            else (float(cfg.alt_min_m), float(cfg.alt_max_m)))
    return VerificationLimits(
        clearance_m=float(clearance_m),
        half_width_m=None if cfg.half_width_m is None else float(cfg.half_width_m),
        corridor_normal_xy=cfg.corridor_normal_xy, corridor_pivot_xy=cfg.corridor_pivot_xy,
        endpoint_exempt_segments=int(cfg.endpoint_exempt_segments), sample_step_m=2.0,
        expected_start=traj.start_position, expected_end=traj.end_position, alt_range_m=band,
    )

