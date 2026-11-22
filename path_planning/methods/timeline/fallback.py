# -*- coding: utf-8 -*-
"""降级巡航剖面：优化失败时仍给出**带时间**的可交付轨迹（``time_source=estimate``）。

职责：把纯几何折线按"定速巡航 + 保守放大"折算成 Hermite 段序列，供上层继续
      排程；时间口径是估算，不是优化结果。
边界：不求解、不修正几何（几何必须已被空间层判为无碰撞）、不参与冲突消解。
相关：``trajectory_optimizer.py``（正常路径）、``failure_codes.py`` 的
      ``TIME_EXCEEDS_BUDGET`` / ``OPT_FAILED`` 分级口径、``docs/time_mapping.md`` §5。

**为什么位置严格贴折线**：取 ``v0 = v1 = (p1-p0)/Δt`` 时 Hermite 退化为线性
参数化，曲线与折线**逐点重合**，不会出现"节省时间式切角"。这样降级产物虽然时间
偏保守，几何仍然可信；若在这里切角，会让上层以为拿到了更短路径，而冲突检测又按
这条曲线去判距，等于**悄悄改了几何**。

**保守 1.2 施加在时长上，不施加在距离上**：把速度降到 ``v_cruise/κ`` 得到更长
的 Δt（``κ=1.2``），于是 `T_est` 偏保守；位置与弧长不变。反过来放大距离会让航程
口径（米）虚增，而上层是按米扣 ``remaining_range`` 的，会误扣航程。

减速器（deceleration）被省掉了：段内定速意味着节点处速度方向突变（C⁰ 折线），
这是估算产物可以接受的粗糙度，但**不能**直接喂给冲突检测的椭球判定做背书——上层
须按 ``time_source=estimate`` 走保守分支。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from path_planning.common.plan_types import HermiteSegment, TimeSource, Trajectory, Vec3
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.timeline.splines import trajectory_arc_length

__all__ = ["cruise_profile", "CONSERVATIVE_FACTOR"]

#: 估算时间的保守放大系数（1.2 = 在估算时长上多留 20%）
CONSERVATIVE_FACTOR = 1.2


def cruise_profile(
    path_enu: Sequence[Vec3],
    vehicle: VehicleSpec,
    *,
    uav_id: int = 0,
    t0: float = 0.0,
    service_time_s: float = 0.0,
    speed_mps: float | None = None,
    conservative: float = CONSERVATIVE_FACTOR,
) -> Trajectory:
    """按定速巡航把折线折算成 ``Trajectory``（``time_source=estimate``）。

    Args:
        path_enu:       几何折线（ENU，至少 1 点；重复点会被压成零长段跳过）
        vehicle:        机体规格（取 ``v_max_plan_mps`` 作参考巡航速度）
        t0:             出发时刻
        service_time_s: 末端服务时长（悬停段，计入 ``busy_until`` 但不计入飞行时长）
        speed_mps:      巡航速度；None = ``vehicle.v_max_plan_mps``
        conservative:   >1 的保守系数，施加在**时长**上

    Raises:
        ValueError: 折线点数 < 1、速度为 0/负、保守系数 ≤ 1
    """
    pts = np.asarray(path_enu, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] < 1 or pts.shape[1] != 3:
        raise ValueError(f"path_enu 必须是 (n≥1, 3) 的 ENU 点列：{pts.shape}")
    v_cruise = float(vehicle.v_max_plan_mps if speed_mps is None else speed_mps)
    if not np.isfinite(v_cruise) or v_cruise <= 0.0:
        raise ValueError(f"巡航速度必须为正有限值：{v_cruise}")
    if conservative <= 1.0:
        # 系数 ≤1 等于把估算时间报得比定速飞行还短，那是**乐观**偏差，不能叫降级
        raise ValueError(f"保守系数必须 >1：{conservative}")

    eff_speed = v_cruise / float(conservative)
    segments: list[HermiteSegment] = []
    t = float(t0)

    for a, b in zip(pts[:-1], pts[1:]):
        edge = b - a
        length = float(np.linalg.norm(edge))
        if length <= 1e-9:
            continue
        dt = length / eff_speed
        u = edge / length
        segments.append(
            HermiteSegment(
                t0=t, t1=t + dt,
                p0=tuple(float(x) for x in a), p1=tuple(float(x) for x in b),
                v0=tuple(float(x) * eff_speed for x in u),
                v1=tuple(float(x) * eff_speed for x in u),
            )
        )
        t += dt

    if service_time_s > 0.0:
        last = pts[-1]
        segments.append(
            HermiteSegment(
                t0=t, t1=t + float(service_time_s),
                p0=tuple(float(x) for x in last), p1=tuple(float(x) for x in last),
                v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0), is_hover=True,
            )
        )
        t += float(service_time_s)

    if not segments:
        # 单点折线：整个航次就是原地悬停（时长取 1 s 占位，避免零长 Trajectory）
        p = tuple(float(x) for x in pts[0])
        segments.append(
            HermiteSegment(t0=float(t0), t1=float(t0) + 1.0, p0=p, p1=p,
                           v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0), is_hover=True)
        )
        t = float(t0) + 1.0

    traj = Trajectory(
        uav_id=int(uav_id),
        segments=tuple(segments),
        path_len_m=trajectory_arc_length(segments) if segments else 0.0,
        time_source=TimeSource.ESTIMATE,
        hover_weight=1.0,
        meta={
            "fallback": "cruise_profile",
            "cruise_speed_mps": v_cruise,
            "effective_speed_mps": eff_speed,
            "conservative_factor": float(conservative),
            "service_time_s": float(service_time_s),
            "time_source_note": "估算时间：未做速度-时间联立优化，按保守系数放大",
        },
    )
    return traj
