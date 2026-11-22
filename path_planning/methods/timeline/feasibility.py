# -*- coding: utf-8 -*-
"""独立可行性验证器：对任意 ``Trajectory`` 复核运动与净空（**不依赖优化器**）。

职责：逐项复核速度三项（口径 B）、加速度、推力、净空、走廊半宽、端点、时间步、
      弧长—时间相容性；返回带失败码的违规清单，供编排层决定"可交付 / 降级 / 不可行"。
边界：不求解、不修轨迹、不生成轨迹；只做判定与度量。
相关：``docs/time_mapping.md`` §2/§4/§5.2（三项速度、弧长相容性、三条断言）、
      ``splines.py``（解析速度/加速度/弧长）、``failure_codes.py``（失败码分级）。

**为什么必须有这一份**（改造方案 §5.5）：避免"优化器说可行就可行"。优化器与被检
对象若共用同一份约束代码，约束写错时两边一起错、检查不出来。这里只用**解析**的
Hermite 导数与 SDF 读数复核，与被检对象的构造路径无关。

容忍度：``_TOL_*`` 是为了吸收 IPOPT 的 ``solver_tol``（默认 1e-4）与浮点误差，
**不是**留给"近似可行"的余地。任何一条超差不该被更大的容差掩盖——参数化容差等于
把判据调松，会静默放行真超限。

加速度口径：Hermite 只 C¹，``p''`` 在节点两侧不等（C² 不成立），故逐段取
``s=0`` 与 ``s=1`` 的**单边**加速度分别判；把相邻段拼成"节点加速度"会漏掉跳变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import Trajectory, Vec3
from path_planning.common.vehicle_spec import GRAVITY_MPS2, VehicleSpec
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.splines import (
    segment_acceleration,
    segment_arc_length,
    segment_position,
    segment_velocity,
    trajectory_arc_length,
)

__all__ = ["Violation", "VerificationResult", "VerificationLimits", "verify_trajectory"]

#: 速度超差容忍（m/s）：吸收 solver_tol 与浮点累加
_TOL_SPEED = 1e-3
#: 加速度/推力超差容忍（m/s²）
_TOL_ACC = 1e-3
#: 净空超差容忍（m）
_TOL_CLEARANCE = 1e-3
#: 位置/走廊超差容忍（m）
_TOL_POS = 1e-3
#: 时间超差容忍（s）
_TOL_TIME = 1e-6


@dataclass(frozen=True)
class VerificationLimits:
    """复核所需的限值（逐机 + 本次规划口径）。

    Attributes:
        clearance_m:            净空阈值（与优化器同一口径）
        half_width_m:           走廊半宽；None = 不作为硬约束
        corridor_normal_xy:     走廊法向（水平单位向量）
        corridor_pivot_xy:      走廊轴上的基准点（起点水平坐标）
        endpoint_exempt_segments: 首末豁免的**段数**（与优化器必须一致）
        sample_step_m:          净空/走廊沿段采样间隔（米）
        expected_start/expected_end: 期望端点位置；None = 不校
        arc_subintervals:       弧长 Gauss 子区间数
        alt_range_m:            绝对 z 飞行带；None = 不校（与优化器同缘）
    """

    clearance_m: float
    half_width_m: float | None = None
    corridor_normal_xy: tuple[float, float] | None = None
    corridor_pivot_xy: tuple[float, float] | None = None
    endpoint_exempt_segments: int = 1
    sample_step_m: float = 2.0
    expected_start: Vec3 | None = None
    expected_end: Vec3 | None = None
    arc_subintervals: int = 8
    alt_range_m: tuple[float, float] | None = None

    @classmethod
    def from_params(
        cls,
        params: PlanParams,
        *,
        clearance_m: float,
        half_width_m: float | None = None,
        corridor_normal_xy: tuple[float, float] | None = None,
        corridor_pivot_xy: tuple[float, float] | None = None,
        endpoint_exempt_segments: int = 1,
        expected_start: Vec3 | None = None,
        expected_end: Vec3 | None = None,
    ) -> VerificationLimits:
        return cls(
            clearance_m=float(clearance_m),
            half_width_m=None if half_width_m is None else float(half_width_m),
            corridor_normal_xy=corridor_normal_xy,
            corridor_pivot_xy=corridor_pivot_xy,
            endpoint_exempt_segments=int(endpoint_exempt_segments),
            sample_step_m=float(params.clearance_sample_step_m),
            expected_start=expected_start,
            expected_end=expected_end,
            arc_subintervals=int(params.arc_subintervals),
        )


@dataclass(frozen=True)
class Violation:
    """一条违规：失败码 + 中文说明 + 定位（时刻/段号）。"""

    code: FailureCode
    detail: str
    segment: int | None = None
    time_s: float | None = None


@dataclass(frozen=True)
class VerificationResult:
    """复核结果。``ok=False`` 时 ``violations`` 非空。"""

    ok: bool
    violations: tuple[Violation, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def codes(self) -> tuple[FailureCode, ...]:
        return tuple(v.code for v in self.violations)

    def first(self) -> Violation | None:
        return self.violations[0] if self.violations else None


def verify_trajectory(
    traj: Trajectory,
    vehicle: VehicleSpec,
    field: SdfField | WorldField,
    limits: VerificationLimits,
    *,
    params: PlanParams | None = None,
) -> VerificationResult:
    """全项复核。一次收集**全部**违规后统一返回（不因第一条失败就短路）。

    ``params`` 给出时额外校 Δt 区间（``dt_min_s``/``dt_max_s``）；不给时跳过该项。
    只调用 ``field.at``，故窗口场与全域场（z-major 存储）都能直接用。
    """
    viol: list[Violation] = []
    metrics: dict[str, Any] = {
        "segments": len(traj.segments),
        "duration_s": traj.duration,
        "path_len_m": trajectory_arc_length(
            traj, subintervals=limits.arc_subintervals
        ),
    }

    # ① 自洽（时间连续、位置连续、悬停段合法）
    try:
        traj.validate()
    except ValueError as exc:
        viol.append(Violation(FailureCode.KINEMATIC_INCONSISTENT, str(exc)))

    n = len(traj.segments)
    k = max(0, int(limits.endpoint_exempt_segments))

    # ② 逐段：速度三项、加速度、推力、净空、走廊
    vmax_h = vehicle.v_max_plan_mps
    vmax2 = vehicle.v_max_mps ** 2
    amax2 = vehicle.a_max_mps2 ** 2
    thrust2 = vehicle.thrust_max_per_mass ** 2
    az_min = vehicle.thrust_min_per_mass - GRAVITY_MPS2
    worst_clear = float("inf")
    worst_lateral = 0.0

    for i, seg in enumerate(traj.segments):
        n_samples = max(2, int(np.ceil(seg.chord_m / max(limits.sample_step_m, 1e-6))) + 1)
        ss = np.linspace(0.0, 1.0, n_samples)
        pts = segment_position(seg, ss)
        vel = segment_velocity(seg, ss)
        acc = segment_acceleration(seg, ss)

        # --- 口径 B 三速度（逐点）---
        v_h = np.linalg.norm(vel[:, :2], axis=1)
        if float(v_h.max()) > vmax_h + _TOL_SPEED:
            viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                           f"水平速度 {float(v_h.max()):.4f} > v_max_plan={vmax_h:.4f}"))
        if float(vel[:, 2].max()) > vehicle.v_up_mps + _TOL_SPEED:
            viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                           f"爬升率 {float(vel[:, 2].max()):.4f} > v_up={vehicle.v_up_mps:.4f}"))
        if float(-vel[:, 2].max()) > vehicle.v_dn_mps + _TOL_SPEED:
            viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                           f"下降率 {float(-vel[:, 2].min()):.4f} > v_dn={vehicle.v_dn_mps:.4f}"))
        if float(np.sum(vel * vel, axis=1).max()) > vmax2 + _TOL_SPEED:
            viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                           f"合速度 {float(np.linalg.norm(vel, axis=1).max()):.4f} > v_max"))

        # --- 加速度模长 ---
        a2 = np.sum(acc * acc, axis=1)
        if float(a2.max()) > amax2 + _TOL_ACC:
            viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                           f"加速度模长 {float(np.sqrt(a2.max())):.4f} > a_max"))

        # --- 推力：力平衡 ‖a+g·ê_z‖ ≤ f_max/m，且 a_z ≥ f_min/m − g ---
        thrust = np.sum(acc * acc, axis=1) + 2.0 * GRAVITY_MPS2 * acc[:, 2] + GRAVITY_MPS2 ** 2
        if float(thrust.max()) > thrust2 + _TOL_ACC:
            viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                           f"比推力 {float(np.sqrt(thrust.max())):.4f} > f_max/m"))
        if float(acc[:, 2].min()) < az_min - _TOL_ACC:
            viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                           f"竖直加速度 {float(acc[:, 2].min()):.4f} < f_min/m−g={az_min:.4f}"
                           "（推力无法托住重力）"))

        # --- 绝对 z 飞行带（全部段，无豁免）---
        if limits.alt_range_m is not None:
            lo, hi = (float(v) for v in limits.alt_range_m)
            zmin, zmax = float(pts[:, 2].min()), float(pts[:, 2].max())
            if zmin < lo - _TOL_POS or zmax > hi + _TOL_POS:
                viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                               f"竖直 z∈[{zmin:.2f}, {zmax:.2f}] 越出飞行带 "
                               f"[{lo:.2f}, {hi:.2f}]"))

        # --- 走廊半宽（全部段，无豁免）---
        if limits.half_width_m is not None and limits.corridor_normal_xy and limits.corridor_pivot_xy:
            cn = np.asarray(limits.corridor_normal_xy, dtype=np.float64)
            cp = np.asarray(limits.corridor_pivot_xy, dtype=np.float64)
            lateral = np.abs((pts[:, :2] - cp) @ cn)
            worst_lateral = max(worst_lateral, float(lateral.max()))
            if float(lateral.max()) > limits.half_width_m + _TOL_POS:
                viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                               f"走廊侧向 {float(lateral.max()):.3f} > 半宽 "
                               f"{limits.half_width_m:.3f}"))

        # --- 净空（首末爬升段豁免，与优化器同一豁免）---
        if i < k or i >= n - k:
            continue
        sdf_vals = field.at(pts)
        worst_clear = min(worst_clear, float(sdf_vals.min()))
        if float(sdf_vals.min()) < limits.clearance_m - _TOL_CLEARANCE:
            viol.append(_v(FailureCode.CLEARANCE_VIOLATION, i, seg,
                           f"净空 {float(sdf_vals.min()):.3f} < clearance "
                           f"{limits.clearance_m:.3f}（SDF 口径）"))

    metrics["worst_clearance_m"] = None if worst_clear == float("inf") else worst_clear
    metrics["worst_lateral_m"] = worst_lateral

    # ③ 端点位置与端点速度
    if limits.expected_start is not None:
        d = _dist(traj.start_position, limits.expected_start)
        if d > _TOL_POS:
            viol.append(Violation(FailureCode.KINEMATIC_INCONSISTENT,
                                  f"起点偏离 {d:.4f} m", time_s=traj.t0))
    if limits.expected_end is not None:
        d = _dist(traj.end_position, limits.expected_end)
        if d > _TOL_POS:
            viol.append(Violation(FailureCode.KINEMATIC_INCONSISTENT,
                                  f"终点偏离 {d:.4f} m", time_s=traj.t_end))
    v_head = segment_velocity(traj.segments[0], 0.0)
    v_tail = segment_velocity(traj.segments[-1], 1.0)
    if float(np.linalg.norm(v_head)) > _TOL_SPEED:
        viol.append(Violation(FailureCode.KINEMATIC_INCONSISTENT,
                              f"起点速度 {float(np.linalg.norm(v_head)):.4f} 非零", time_s=traj.t0))
    if float(np.linalg.norm(v_tail)) > _TOL_SPEED:
        viol.append(Violation(FailureCode.KINEMATIC_INCONSISTENT,
                              f"终点速度 {float(np.linalg.norm(v_tail)):.4f} 非零", time_s=traj.t_end))

    # ④ Δt 区间（给了参数才校）
    if params is not None:
        for i, seg in enumerate(traj.segments):
            if seg.is_hover:
                continue
            if seg.duration < params.dt_min_s - _TOL_TIME:
                viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                               f"段时长 {seg.duration:.4f} < dt_min={params.dt_min_s}"))
            if seg.duration > params.dt_max_s + _TOL_TIME:
                viol.append(_v(FailureCode.KINEMATIC_INCONSISTENT, i, seg,
                               f"段时长 {seg.duration:.4f} > dt_max={params.dt_max_s}"))

    # ⑤ 弧长—时间相容性：L/T ≤ v_max_plan（严格不等式，只有下界）
    flight = traj.flight_duration
    arc = metrics["path_len_m"]
    if flight > 0.0 and arc > vmax_h * flight + _TOL_POS:
        viol.append(Violation(
            FailureCode.KINEMATIC_INCONSISTENT,
            f"弧长 {arc:.3f} m / 飞行时长 {flight:.3f} s = {arc / flight:.4f} m/s "
            f"> v_max_plan={vmax_h:.4f}（弧长或时间不自洽）",
        ))
    metrics["mean_speed_mps"] = None if flight <= 0.0 else arc / flight

    return VerificationResult(ok=not viol, violations=tuple(viol), metrics=metrics)


def _v(code: FailureCode, index: int, seg, detail: str) -> Violation:
    """构造带段号与起点的违规（段号定位到具体第几段）。"""
    return Violation(code, detail, segment=index, time_s=seg.t0)


def _dist(a: Vec3, b: Vec3) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))
