# -*- coding: utf-8 -*-
"""单机航迹的"裕度账"：约束用到几分、时间离下界多远。

职责：只读一条已交付航迹，给出**利用率**（不是通过/不通过的布尔值）：
      水平速度、爬升率、下降率、合速度、加速度模长、比推力相对各自上限的比例，
      以及时间账（相对"弧长÷速度上限 + 悬停"下界的比值、弧长弯曲余量）。
边界：不算净空（那是 SDF/棱柱的事）、不判可行性（那是独立验证器的事）。

**为什么要单列一份"裕度"**：验证器报的是 violations=[]，那是二值；论文里要说明
"这条航迹离撞墙/超速有多远"，就需要利用率。两者用同一批公式，但**用途不同**：
验证器守底线，裕度账回答"优化把约束用到了多满"。数值不一致时以验证器为准。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from path_planning.common.plan_types import Trajectory
from path_planning.common.vehicle_spec import GRAVITY_MPS2, VehicleSpec
from path_planning.methods.timeline.splines import (
    segment_acceleration,
    segment_command_acceleration,
    segment_arc_length,
    segment_velocity,
)

__all__ = [
    "LegSlice",
    "kinematic_margins",
    "leg_trajectory",
    "split_legs",
    "time_quality",
]

_SAMPLES_PER_SEGMENT = 17


@dataclass(frozen=True)
class LegSlice:
    """一条航迹里"飞一段"的切片：相邻两处停留之间的飞行段。

    悬停段**不单独成 leg**——它属于"到达那一点之后的等待"，计入所在 leg 的
    ``hover_s``，但不占用 leg 的弧长。没有悬停的航迹就是 1 条 leg。
    """

    index: int
    first_seg: int
    last_seg: int
    t0: float
    t1: float
    path_len_m: float
    hover_s: float
    start: tuple[float, float, float]
    end: tuple[float, float, float]

    @property
    def flight_s(self) -> float:
        """纯飞行时长。``[t0, t1]`` 只覆盖飞行段，所以悬停**不**从这里扣。"""
        return self.t1 - self.t0


def split_legs(traj: Trajectory) -> tuple[LegSlice, ...]:
    """按悬停段切 leg；末尾悬停归到最后一个 leg，开头悬停归到第一个 leg。"""
    legs: list[LegSlice] = []
    start_i: int | None = None
    hover_acc = 0.0
    for i, seg in enumerate(traj.segments):
        if seg.is_hover:
            hover_acc += seg.duration
            if start_i is not None:
                _close(legs, traj, start_i, i - 1, hover_acc)
                start_i = None
                hover_acc = 0.0
            continue
        if start_i is None:
            start_i = i
    if start_i is not None:
        _close(legs, traj, start_i, len(traj.segments) - 1, hover_acc)
    if not legs:
        raise ValueError("航迹里没有任何飞行段：全是悬停不构成 leg")
    return tuple(legs)


def _close(legs: list[LegSlice], traj: Trajectory, first: int, last: int,
           hover_s: float) -> None:
    segs = traj.segments[first:last + 1]
    legs.append(LegSlice(
        index=len(legs),
        first_seg=first,
        last_seg=last,
        t0=float(segs[0].t0),
        t1=float(segs[-1].t1),
        path_len_m=float(sum(segment_arc_length(s) for s in segs)),
        hover_s=float(hover_s),
        start=tuple(segs[0].p0),
        end=tuple(segs[-1].p1),
    ))


def leg_trajectory(traj: Trajectory, leg: LegSlice) -> Trajectory:
    """把 leg 切片还原成独立 ``Trajectory``，好复用同一套裕度/时间账。"""
    return Trajectory(
        uav_id=traj.uav_id,
        segments=traj.segments[leg.first_seg:leg.last_seg + 1],
        path_len_m=leg.path_len_m,
        time_source=traj.time_source,
        hover_weight=traj.hover_weight,
        meta={"leg_index": leg.index},
    )



def kinematic_margins(traj: Trajectory, vehicle: VehicleSpec) -> dict[str, float]:
    """各约束的峰值与利用率（1.0 = 贴边，>1 = 越界，但本函数不抛）。"""
    vmax_h = float(vehicle.v_max_plan_mps)
    amax = float(vehicle.a_max_mps2)
    fmax_m = float(vehicle.thrust_max_per_mass)
    az_floor = float(vehicle.thrust_min_per_mass) - GRAVITY_MPS2

    peak = {
        "v_h": 0.0, "v_up": 0.0, "v_dn": 0.0, "v_norm": 0.0,
        "a_norm": 0.0, "thrust_per_mass": 0.0, "az_min": float("inf"),
    }
    for seg in traj.segments:
        ss = np.linspace(0.0, 1.0, _SAMPLES_PER_SEGMENT)
        vel = np.atleast_2d(segment_velocity(seg, ss))
        acc = np.atleast_2d(segment_command_acceleration(seg, ss))
        peak["v_h"] = max(peak["v_h"], float(np.linalg.norm(vel[:, :2], axis=1).max()))
        peak["v_up"] = max(peak["v_up"], float(vel[:, 2].max()))
        peak["v_dn"] = max(peak["v_dn"], float(-vel[:, 2].min()))
        peak["v_norm"] = max(peak["v_norm"], float(np.linalg.norm(vel, axis=1).max()))
        peak["a_norm"] = max(peak["a_norm"], float(np.linalg.norm(acc, axis=1).max()))
        # 比推力模长 = ‖a + g·ê_z‖（与验证器同一式子，只是这里取峰值而非判阈值）
        thrust2 = np.sum(acc * acc, axis=1) + 2.0 * GRAVITY_MPS2 * acc[:, 2] + GRAVITY_MPS2 ** 2
        peak["thrust_per_mass"] = max(
            peak["thrust_per_mass"], float(np.sqrt(float(thrust2.max())))
        )
        peak["az_min"] = min(peak["az_min"], float(acc[:, 2].min()))

    return {
        "peak_v_h_mps": peak["v_h"],
        "peak_v_up_mps": peak["v_up"],
        "peak_v_dn_mps": peak["v_dn"],
        "peak_v_norm_mps": peak["v_norm"],
        "peak_acc_mps2": peak["a_norm"],
        "peak_thrust_per_mass_n_per_kg": peak["thrust_per_mass"],
        "min_az_mps2": peak["az_min"],
        # 利用率：>1 表示越界（验证器此时应已报 KINEMATIC_INCONSISTENT）
        "util_v_h": peak["v_h"] / vmax_h,
        "util_v_up": peak["v_up"] / float(vehicle.v_up_mps),
        "util_v_dn": peak["v_dn"] / float(vehicle.v_dn_mps),
        "util_v_norm": peak["v_norm"] / float(vehicle.v_max_mps),
        "util_acc": peak["a_norm"] / amax,
        "util_thrust": peak["thrust_per_mass"] / fmax_m,
        # 竖直方向不是"用满多少"，而是"离托不住重力还差多少"（az 下限为负值）
        "az_headroom_mps2": peak["az_min"] - az_floor,
    }


def time_quality(
    traj: Trajectory,
    vehicle: VehicleSpec,
    *,
    direct_m: float | None = None,
) -> dict[str, float]:
    """时间账：实际时长 vs 两个下界，以及弧长相对"直线弦长和"的弯曲余量。

    ``direct_m``：绕行比的参照距离。缺省用起终点三维直线；**多段航迹必须传
    "逐段直线的和"**——否则巢→A→B→巢 的起终点直线没意义，绕行比会虚高一倍。
    """
    chord_sum = float(sum(seg.chord_m for seg in traj.segments))
    hover = float(traj.hover_duration)
    flight = float(traj.flight_duration)
    lb_plan = traj.path_len_m / float(vehicle.v_max_plan_mps) + hover
    lb_peak = traj.path_len_m / float(vehicle.v_max_mps) + hover
    if direct_m is None:
        direct = float(np.linalg.norm(np.subtract(traj.end_position, traj.start_position)))
    else:
        direct = float(direct_m)
    return {
        "duration_s": float(traj.duration),
        "flight_s": flight,
        "hover_s": hover,
        "lower_bound_plan_s": lb_plan,
        "lower_bound_vmax_s": lb_peak,
        "ratio_to_plan_bound": float(traj.duration) / lb_plan if lb_plan > 0 else float("inf"),
        "ratio_to_vmax_bound": float(traj.duration) / lb_peak if lb_peak > 0 else float("inf"),
        "path_len_m": float(traj.path_len_m),
        "chord_sum_m": chord_sum,
        # 弧长 − 各段弦长和：分段越细、越"直"，这个差越小；它衡量**离散化弯曲**，
        # 不是绕行距离（绕行要看 OD 直线距离，往返航次里 OD 距离为 0，用它算不出来）
        "arc_chord_gap_m": float(traj.path_len_m) - chord_sum,
        # 与起终点三维直线的比：>1 的部分就是为避障/爬升多飞的路。往返航次
        # （起终点重合）时无意义，返回 None 而不是 inf。
        "direct_m": direct,
        "detour_ratio": (float(traj.path_len_m) / direct) if direct > 1e-6 else None,
        "mean_speed_mps": traj.path_len_m / flight if flight > 0 else 0.0,
    }
