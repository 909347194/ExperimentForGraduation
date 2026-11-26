# -*- coding: utf-8 -*-
"""单机航迹的"质量报告"：整条 + 逐 leg 的净空/裕度/时间账，外加五项判据。

职责：给审计用——把 ``trajectory_margins``（无净空）与场/棱柱净空拼起来，输出
      (a) 可打印的逐 leg 明细行，(b) 五项判据：时间来源、分段边界（给了停留点才算）、
      运动学裕度、稠密净空（剔端点豁免球）、端点豁免（球内按更低的阈值单独判）。
边界：只读落盘航迹与场；不放宽任何阈值、不重跑优化器。

**为什么逐 leg 单独报**：一条"巢→任务1→任务2→…→巢"的航迹，总账的净空最小值
可能落在任意一段；调整某一段的权重时会先抬高别段、把最小值挪走，整条看不出是谁
在贴边。逐 leg 才能回答"哪一段紧、紧在哪个约束上"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from path_planning.common.plan_types import Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from .audit_checks import Check
from .trajectory_margins import (
    LegSlice,
    kinematic_margins,
    leg_trajectory,
    split_legs,
    time_quality,
)
from path_planning.methods.planning.leg_segments import sampled_positions
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.splines import segment_position

__all__ = ["QualityReport", "trajectory_quality"]

_UTIL_TOL = 1e-3  # 17 点采样算出的峰值可比优化器约束值高 3–4 个 1e-4（离散采样误差，非越界）
DENSE_STEP_M = 0.2


@dataclass(frozen=True)
class QualityReport:
    checks: list[Check]
    lines: list[str]


def trajectory_quality(
    uid: int,
    traj: Trajectory,
    *,
    vehicle: VehicleSpec,
    field: WorldField,
    clearance_m: float,
    cfg_step_m: float,
    time_source: str | None = None,
    failure_code: str | None = None,
    stops: list[tuple[tuple[float, float, float], float]] | None = None,
    endpoint_exempt_radius_m: float = 0.0,
    endpoint_clearance_m: float | None = None,
    dense_step_m: float = DENSE_STEP_M,
) -> QualityReport:
    """一条航迹的完整质量报告。

    ``stops``：该航次的停留点 ``[(enu, service_time_s), …]``（不含起点）。给了就
    校验"leg 边界落在停留点上"——这是多段航迹最要紧的结构判据：每一处停留必须
    真的被到达，段数必须与"服务时长 > 0 的停留点"对得上。

    ``endpoint_exempt_radius_m``：起/终点豁免半径。机巢处 SDF 只有 +1.63 / −0.63 /
    +1.47，按 6 m 判会死在端点上（见 ``docs/architecture.md`` §4）。豁免区内的点
    单独按 ``endpoint_clearance_m`` 判，**不**混进"本体净空"的最小值里——否则
    一条只在起飞处贴地的航迹会被报成"全程 1.6 m"，把真正的中段风险盖掉。

    ``failure_code``：规划阶段返回的失败码。只有它非空时 ``estimate`` 才被接受为
    **降级但可解释**的交付（见 ``PlanResult`` 的状态矩阵）：时间不可信 → 不判运动
    学、不判预算，但净空与分段结构照判（几何仍须安全）。
    """
    source = str(time_source or traj.time_source.value)
    degraded = source == "estimate" and failure_code is not None
    checks: list[Check] = []
    lines: list[str] = []

    checks.append(Check(
        f"时间来源/机{uid}",
        source in ("trajectory", "planned") or degraded,
        f"time_source={source}"
        + (f"，降级交付（失败码 {failure_code}）——时间不可用于冲突判定，只保证几何安全"
           if degraded else ""),
    ))
    if degraded:
        checks.append(Check(
            f"降级剖面/机{uid}", traj.meta.get("fallback_profile") == "cruise_profile",
            f"剖面 {traj.meta.get('fallback_profile')}（定速 v_max/1.2，保守；"
            "段端点速度沿边、C1 不连续是这一路的已知近似）",
        ))

    legs = split_legs(traj)
    if stops:
        checks.append(_boundary_check(uid, legs, stops))

    if not degraded:
        kin = kinematic_margins(traj, vehicle)
        used = max(kin["util_v_h"], kin["util_v_up"], kin["util_v_dn"],
                   kin["util_v_norm"], kin["util_acc"], kin["util_thrust"])
        checks.append(Check(
            f"运动学裕度/机{uid}", used <= 1.0 + _UTIL_TOL,
            f"水平 {kin['util_v_h']:.6f} / 爬升 {kin['util_v_up']:.6f} / "
            f"下降 {kin['util_v_dn']:.6f} / 合速 {kin['util_v_norm']:.6f} / "
            f"加速度 {kin['util_acc']:.6f} / 推力 {kin['util_thrust']:.6f}"
            f"（1.0 = 贴边）；竖直加速度余量 {kin['az_headroom_mps2']:.3f} m/s²",
        ))

    dense_pts = sampled_positions(traj, dense_step_m)
    cfg_pts = sampled_positions(traj, cfg_step_m)
    radius = float(endpoint_exempt_radius_m)
    interior = _interior_mask(traj, dense_pts, radius)
    if not interior.any():
        interior = np.ones(len(dense_pts), dtype=bool)
    dense_min = float(np.min(field.at(dense_pts[interior])))
    cfg_min = float(np.min(field.at(cfg_pts[_interior_mask(traj, cfg_pts, radius)])))
    checks.append(Check(
        f"稠密净空/机{uid}", dense_min >= clearance_m - 1e-6,
        f"{dense_step_m:g} m 采样 min={dense_min:.2f} m，"
        f"配置 {cfg_step_m:g} m 采样 min={cfg_min:.2f} m，阈值 {clearance_m:.2f} m"
        + (f"（已扣除端点豁免 {radius:g} m 内 {int((~interior).sum())} 个点）"
           if radius > 0.0 else ""),
    ))
    if radius > 0.0 and endpoint_clearance_m is not None:
        in_zone = ~interior
        zone_min = float(np.min(field.at(dense_pts[in_zone]))) if in_zone.any() else float("inf")
        checks.append(Check(
            f"端点豁免/机{uid}", zone_min >= float(endpoint_clearance_m) - 1e-6,
            f"豁免区内 min={zone_min:.2f} m，阈值 {float(endpoint_clearance_m):.2f} m"
            f"（{int(in_zone.sum())} 个点）",
        ))

    lines.extend(_leg_lines(uid, traj, legs, vehicle, field, clearance_m, dense_step_m,
                            radius))
    # 多段的绕行比必须比"逐段直线之和"：起终点直线（或往返重合）都没意义。
    direct = (
        float(sum(_dist(leg.start, leg.end) for leg in legs)) if len(legs) > 1 else None
    )
    tq = time_quality(traj, vehicle, direct_m=direct)
    detour = tq["detour_ratio"]
    direct_label = "逐段直线" if direct is not None else "直线"
    lines.append(
        f"[INFO] 总/机{uid}：{tq['duration_s']:.2f} s"
        f"（飞行 {tq['flight_s']:.2f} + 悬停 {tq['hover_s']:.1f}），"
        f"下界 {tq['lower_bound_plan_s']:.2f} s ⇒ 比值 {tq['ratio_to_plan_bound']:.3f}"
        f"（按 v_max 下界 {tq['ratio_to_vmax_bound']:.3f}）；"
        f"弧长 {tq['path_len_m']:.1f} m"
        + (f"，{direct_label} {tq['direct_m']:.1f} m ⇒ 绕行比 {detour:.4f}" if detour is not None
           else "，起终点重合（绕行比不适用）")
        + f"，均速 {tq['mean_speed_mps']:.2f} m/s"
    )
    return QualityReport(checks=checks, lines=lines)


def _boundary_check(
    uid: int,
    legs: tuple[LegSlice, ...],
    stops: list[tuple[tuple[float, float, float], float]],
) -> Check:
    """leg 边界必须逐一落在停留点上；段数由"哪些停留点真的停过"决定。"""
    name = f"分段边界/机{uid}"
    expected = 1 + sum(1 for _, svc in stops[:-1] if svc > 0.0)
    head = f"{len(legs)} 段 / 期望 {expected} 段，{len(stops)} 个停留点"
    if len(legs) != expected:
        return Check(name, False, head)
    i = 0
    for k, (pos, svc) in enumerate(stops[:-1]):
        if svc <= 0.0:
            continue
        d = _dist(legs[i].end, pos)
        if d > 1e-3:
            return Check(name, False, f"leg{i} 终点偏离停留点{k} {d:.4f} m（{head}）")
        i += 1
    d = _dist(legs[-1].end, stops[-1][0])
    if d > 1e-3:
        return Check(name, False, f"末段终点偏离最后停留点 {d:.4f} m（{head}）")
    return Check(name, True, f"{head}；各边界与停留点吻合（≤1 mm）")


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)))


def _leg_lines(uid: int, traj: Trajectory, legs: Iterable[LegSlice],
               vehicle: VehicleSpec, field: WorldField, clearance_m: float,
               step_m: float, exempt_radius_m: float = 0.0) -> list[str]:
    out: list[str] = []
    for leg in legs:
        sub = leg_trajectory(traj, leg)
        km = kinematic_margins(sub, vehicle)
        tq = time_quality(sub, vehicle)
        pts = _leg_points(traj, leg, step_m)
        mask = _interior_mask(traj, pts, exempt_radius_m)
        if not mask.any():
            mask = np.ones(len(pts), dtype=bool)
        sdf_min = float(np.min(field.at(pts[mask])))
        detour = tq["detour_ratio"]
        out.append(
            f"[INFO] leg{leg.index}/机{uid}：{leg.flight_s:.2f} s 飞行"
            f" + {leg.hover_s:.1f} s 悬停，弧长 {leg.path_len_m:.1f} m"
            + (f"（直线 {tq['direct_m']:.1f} ⇒ {detour:.4f}）" if detour is not None
               else "（起终点重合）")
            + f"，净空 {sdf_min:.2f} m（阈值 {clearance_m:.2f}）"
            f"，水平 {km['util_v_h']:.3f} / 爬升 {km['util_v_up']:.3f}"
            f" / 合速 {km['util_v_norm']:.3f} / 加速度 {km['util_acc']:.3f}"
            f" / 推力 {km['util_thrust']:.3f}"
        )
    return out


def _interior_mask(traj: Trajectory, pts: np.ndarray, radius: float) -> np.ndarray:
    """去掉落在起/终点豁免球内的采样点（``radius=0`` 时全保留）。"""
    pts = np.atleast_2d(np.asarray(pts, dtype=np.float64))
    if radius <= 0.0:
        return np.ones(len(pts), dtype=bool)
    start = np.asarray(traj.start_position, dtype=np.float64)
    end = np.asarray(traj.end_position, dtype=np.float64)
    d_start = np.linalg.norm(pts - start, axis=1)
    d_end = np.linalg.norm(pts - end, axis=1)
    return (d_start > radius) & (d_end > radius)


def _leg_points(traj: Trajectory, leg: LegSlice, step_m: float) -> np.ndarray:
    chunks: list[np.ndarray] = []
    for i in range(leg.first_seg, leg.last_seg + 1):
        seg = traj.segments[i]
        if seg.is_hover:
            continue
        n = max(2, int(np.ceil(seg.chord_m / max(step_m, 1e-6))) + 1)
        chunks.append(
            np.atleast_2d(segment_position(seg, np.linspace(0.0, 1.0, n)))
        )
    if not chunks:
        raise ValueError(f"leg{leg.index} 没有可采样点")
    return np.vstack(chunks)
