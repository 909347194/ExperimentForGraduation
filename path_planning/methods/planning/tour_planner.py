# -*- coding: utf-8 -*-
"""单航次编排：按停靠点串段 → 降级 → 全域交付闸 → 预算判定。

职责：把一个 ``PlanRequest``（起点 + 停靠点序列）走完整条流水线，产出可直接交付的
      ``PlanResult``（一条带时间的 ``Trajectory`` + 诊断 + 失败码）。
边界：**单段怎么解**在 ``planning/leg_planner.py``；多机协调在 ``coordination``；
      不写 Registry（``trajectory_registry``）、不做对外契约（``api.py``）、
      不做批处理（``planning/batch.py``）。
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

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable

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
from path_planning.methods.planning.delivery_gate import global_gate
from path_planning.methods.planning.failure_report import failure_detail, prefix_report
from path_planning.methods.planning.leg_planner import plan_leg
from path_planning.methods.planning.leg_recorder import LegRecorder, origin_id_of
from path_planning.methods.planning.leg_segments import hover_segment, vertical_leg
from path_planning.methods.planning.od_contract import od_violation_of
from path_planning.methods.spatial.corridor_window import CorridorRule
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.sdf_casadi import SdfOnGrid
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.fallback import cruise_profile

__all__ = ["TourPlanningContext", "plan_tour"]

#: 水平距离小于此值视为"同一点"（退化航段，走纯竖向管道）
_MIN_LEG_HORIZONTAL_M = 1.0

#: 允许走"定速巡航剖面"降级交付的失败码：优化失败，或准入层用下界就判死的两类预算不足
_FALLBACK_CODES = frozenset({
    FailureCode.OPT_FAILED,
    FailureCode.ENDURANCE_EXCEEDED,
    FailureCode.RANGE_EXCEEDED,
})


@dataclass(frozen=True)
class TourPlanningContext:
    """一次编排所需的全部外部依赖（与请求解耦，便于同一场景多次复用）。

    Attributes:
        field:            全域场（窗口按格点取样，见 ``docs/architecture.md`` §2）
        params:           参数体系
        corridor_rule:    走廊半宽规则（交付硬约束的来源）
        alt_range_m:      绝对 z 飞行带 ``(min, max)``（低空口径：上限为绝对高度）
        prisms:           真几何障碍；给定时每段都过真几何净空闸
        seed:             RRT 基础种子（多初值取 ``seed + i``，可复现）
        endpoint_clearance_m: 端点豁免圈内的净空阈值（机巢实测 SDF 低，必须放宽）
        endpoint_exempt_radius_m: 端点豁免圈半径（0 = 不豁免）
        clearance_fix_m:  真几何净空不达标时的抬阈值量；None = ``params.local_replan_clearance_fix_m``
        agl_range_m:      端点准入的真高带 ``(min, max)``（米，AGL）；None = 不判
        ground_height:    局部地面高程 ``f(x, y) -> z``；给定时端点真高按**逐点**地面算
        sdf_quant_tolerance_m: SDF 竖直量化容差（米）：求解阈值 = ``clearance_m`` + 本值，
                          交付与审计仍按 ``clearance_m`` 判（两条口径必须分开）
        max_od_length_m:   建场契约允许的单段最大水平长度（米）；None = 不判（兼容
                          不建全城场的实验）。非 None 时在请求期逐段准入，超限报
                          ``OD_TOO_LONG``，不让底层抛 ``WindowOutsideField``。
                          与建场 pad 同源：由 ``required_pad_m`` 从同一个上限派生。
    """

    field: WorldField
    params: PlanParams
    corridor_rule: CorridorRule
    alt_range_m: tuple[float, float]
    prisms: PrismClearance | None = None
    seed: int = 0
    endpoint_clearance_m: float | None = None
    endpoint_exempt_radius_m: float = 0.0
    clearance_fix_m: float | None = None
    agl_range_m: tuple[float, float] | None = None
    ground_height: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None
    sdf_quant_tolerance_m: float = 0.0
    max_od_length_m: float | None = None
    #: CasADi 插值器缓存（按场对象 + 栅格契约）。挂在装配上，跨候选/跨让路复用；
    #: 用 mutable 默认值是刻意的——本对象 frozen，但缓存需要在同一装配内累积。
    sdf_cache: dict[tuple, SdfOnGrid] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.alt_range_m[1] <= self.alt_range_m[0]:
            raise ValueError(f"alt_range_m 退化：{self.alt_range_m}")
        if self.max_od_length_m is not None and float(self.max_od_length_m) <= 0.0:
            raise ValueError(
                f"max_od_length_m 必须为正：{self.max_od_length_m}"
            )


def _validate_endpoints(request: PlanRequest, ctx: TourPlanningContext) -> str | None:
    """输入点契约验收：methods 侧不解析高度，但必须拒绝不合法的三维点。

    高度策略（巢 +15 / 任务 +10）属于任务产生侧，**不在本模块做**；本模块只验收
    交给它的点是否合法，不合格返回可读错误信息（由 ``plan_tour`` 转成
    ``ENDPOINT_OUT_OF_BAND`` 失败），让问题在入口就暴露，而不是静默流进 RRT/NLP。

    逐点检查：
    1. 坐标必须是三维有限数（维度错 / NaN / Inf 一律拒）；
    2. 绝对高度 ``z`` 落在 ``ctx.alt_range_m`` 内（NLP 用的同一条硬上界，
       例：屋顶 115 m + 净空 10 m = 125 m 超出上界就在这里被拦）；
    3. 真高落在 ``ctx.agl_range_m`` 内（若配置），按**逐点**局部地面算——
       航线穿越高地形时，用全域最低地面会把"贴山顶飞"误判为合格。

    返回 ``None`` 表示全部合格。
    """
    named = [("起点", request.start_position)] + [
        (f"停靠点 {i}", s.position) for i, s in enumerate(request.stops, 1)
    ]
    pts: list[np.ndarray] = []
    for tag, raw in named:
        try:
            arr = np.asarray(raw, dtype=np.float64)
        except (TypeError, ValueError):
            return f"{tag} 坐标不是数值：{raw!r}"
        if arr.shape != (3,):
            return f"{tag} 坐标必须是三维 (x,y,z)，实际 shape={arr.shape}：{raw!r}"
        if not np.all(np.isfinite(arr)):
            return f"{tag} 坐标含 NaN/Inf，不是有限值：{raw!r}"
        pts.append(arr)

    pts_arr = np.asarray(pts, dtype=np.float64)

    abs_lo, abs_hi = (float(v) for v in ctx.alt_range_m)
    for (tag, _), p in zip(named, pts_arr):
        z = float(p[2])
        if not (abs_lo - 1e-9 <= z <= abs_hi + 1e-9):
            return (f"{tag} 绝对高度 z={z:.1f} m 越出飞行带 [{abs_lo:g}, {abs_hi:g}] m"
                    "（任务产生侧应换点或调整净空，不得交给规划器兜底）")

    if ctx.agl_range_m is not None and ctx.ground_height is not None:
        ground = np.asarray(
            ctx.ground_height(pts_arr[:, 0], pts_arr[:, 1]), dtype=np.float64
        )
        agl = pts_arr[:, 2] - ground
        lo, hi = (float(v) for v in ctx.agl_range_m)
        for k, value in enumerate(agl):
            if not (lo - 1e-9 <= float(value) <= hi + 1e-9):
                tag = named[k][0]
                z = float(pts_arr[k, 2])
                return (f"{tag} z={z:.1f} m、地面 {z - float(value):.1f} m、"
                        f"真高 {float(value):.1f} m 越出 [{lo:g}, {hi:g}] m")
    return None


def plan_tour(request: PlanRequest, ctx: TourPlanningContext) -> PlanResult:
    """规划一条 tour；任何一段不可交付即整条失败（``RRT_NO_PATH`` 之类）。

    **模型假设 A4**：机巢作业（地面→巡航高度的垂直起降、悬停装卸）不在本模块规划
    域内。``request.t0`` 的语义是"已离巢并稳定悬停于起点"的时刻，规划区间为
    ``[t0, t0+T]`` 内自起点悬停点至终点悬停点的空中航段；起降安全由机巢选址
    （巢周净空、巢高落真高带内）与离巢时序（上层时隙）保证。

    Args:
        request: 规划请求（含逐机机体、冻结 ``t0``、停靠点、预算）
        ctx:     场景侧依赖

    Returns:
        ``PlanResult``：成功时 ``trajectory`` 为整条 tour（含停靠悬停段）；
        失败时 ``failure`` 给码，``detail`` 指明在第几段、什么原因。
    """
    od_violation = od_violation_of(
        request.start_position, request.stops, ctx.max_od_length_m
    )
    if od_violation is not None:
        return PlanResult.failed(
            request.uav_id, FailureCode.OD_TOO_LONG, detail=od_violation,
            diagnostics={"max_od_length_m": ctx.max_od_length_m},
        )
    violation = _validate_endpoints(request, ctx)
    if violation is not None:
        return PlanResult.failed(
            request.uav_id, FailureCode.ENDPOINT_OUT_OF_BAND, detail=violation,
            diagnostics={"agl_range_m": ctx.agl_range_m, "alt_range_m": ctx.alt_range_m},
        )
    stops: tuple[PlanStop, ...] = request.stops
    segments: list[HermiteSegment] = []
    cursor = float(request.t0)
    prev = np.asarray(request.start_position, dtype=np.float64)
    total_len = 0.0
    hover_total = 0.0
    degraded = False
    diagnostics: dict[str, Any] = {"legs": [], "leg_costs": []}
    # 逐段账：首段起点取 ``meta["origin_id"]``（UAV 起点键 -(1+机号)），没带就记 -1；
    # 所有 return 路径都经 ``_emit`` 出口，禁止只更新局部列表就返回。
    recorder = LegRecorder(origin_id_of(request))

    def _emit(result_diag: dict[str, Any]) -> PlanResult:
        result_diag["leg_costs"] = recorder.as_dicts()
        return result_diag

    for index, stop in enumerate(stops):
        from_id = recorder.from_id(index, stops)
        goal = np.asarray(stop.position, dtype=np.float64)
        leg_start_t = cursor
        remaining_s = max(request.budget.endurance_remaining_s - (cursor - request.t0), 1e-3)
        remaining_m = max(request.budget.range_remaining_m - total_len, 1e-3)
        leg_diag: dict[str, Any] = {}
        is_vertical = (
            float(np.hypot(goal[0] - prev[0], goal[1] - prev[1])) < _MIN_LEG_HORIZONTAL_M
        )
        if is_vertical:
            leg = vertical_leg(request.uav_id, request.vehicle, prev, goal, leg_start_t)
            failure = None
            # 竖向退化段不进 ``legs``（那是水平段求解诊断的位置），但它是**真实航段**，
            # 必须进逐段账——返航段的竖直下降常常正是最贵的一段。
            recorder.planned(from_id, int(stop.id), leg)
        else:
            leg, failure, leg_diag = plan_leg(
                request, ctx, prev, goal, leg_start_t, remaining_s, remaining_m
            )
            diagnostics["legs"].append(leg_diag)
        if leg is None:
            # 降级交付：只在显式声明的航次上启用，且只对"几何已被空间层判过无碰撞"这一路。
            # 触发面有三种：NLP 失败，以及准入层用下界就判死的两类预算不足。
            if not (request.allow_estimate_fallback
                    and failure in _FALLBACK_CODES
                    and leg_diag.get("fallback_path") is not None):
                # 失败航次**只报告、不交付**：把已解好的前 index 段几何与失败段的空间
                # 初值（fallback_path）一并写进诊断，供失败复盘与 PP01 绘图区分
                # "能飞的段"（实线）与"断在哪一段"（虚线）。不改本航次的交付语义：
                # 整航次仍失败，prefix 不当作交付物，也不删点（删点是上层职权）。
                diagnostics.update(
                    prefix_report(segments, index, stop, leg_diag, cursor)
                )
                # 断掉的那一段单独进账：失败码 + 码表分级，长度留空（没有实测就不猜）。
                recorder.failed(from_id, int(stop.id), failure or FailureCode.OPT_FAILED)
                return PlanResult.failed(
                    request.uav_id, failure or FailureCode.OPT_FAILED,
                    detail=failure_detail(index, stop, leg_diag),
                    diagnostics=_emit(diagnostics),
                )
            leg = cruise_profile(
                leg_diag["fallback_path"], request.vehicle,
                uav_id=request.uav_id, t0=leg_start_t,
            )
            degraded = True
            diagnostics.setdefault("fallback", []).append(
                {"leg": index, "stop": int(stop.id),
                 "reason": f"{failure.value} → 定速巡航剖面（time_source=estimate）"}
            )
            recorder.estimated(from_id, int(stop.id), leg)
        elif not is_vertical:
            recorder.planned(from_id, int(stop.id), leg)
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
        time_source=TimeSource.ESTIMATE if degraded else TimeSource.TRAJECTORY,
        hover_weight=float(ctx.params.hover_weight),
        meta={"stops": tuple(s.id for s in stops), "degraded": degraded,
              "fallback_profile": "cruise_profile" if degraded else None},
    )
    # 交付闸：整条航迹在**全域场**上按 0.2 m 稠密量净空，再叠全域真几何。
    # 段内复核只看各段自己的走廊窗口（值被平滑 ~0.1 m），段间接缝与任务点折返处的低点会漏过。
    gate_worst, gate_msg = global_gate(ctx, trajectory, ctx.prisms)
    diagnostics["global_gate"] = {
        "ok": gate_msg is None, "worst_clearance_m": gate_worst, "first": gate_msg,
    }
    if gate_msg is not None:
        # 交付闸失败也要带逐段账（与 _emit / 预算分支同口径）：失败票靠它定位
        # 断在哪一段、并把已飞段的实测长度回灌给上层。
        diagnostics["leg_costs"] = recorder.as_dicts()
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
    diagnostics["leg_costs"] = recorder.as_dicts()
    if not verdict.ok:
        # 预算超限仍然交付（带着既有航迹比"什么都不给"更有用），但必须带码
        return PlanResult.failed(
            request.uav_id, verdict.failure or FailureCode.TIME_EXCEEDS_BUDGET,
            detail=f"预算超限：续航占用 {verdict.time_ratio:.2f} / 航程占用 {verdict.range_ratio:.2f}",
            trajectory=trajectory,
            diagnostics=diagnostics,
        )
    return PlanResult.success(trajectory, diagnostics=diagnostics)

