# -*- coding: utf-8 -*-
"""Δt 区间判据：逐飞行段校「段时长 ∈ [有效地板, ``dt_max``]」。

职责：给复核侧产出 Δt 越界的段清单（段号 + 段对象 + 中文说明）。
边界：只判不改；不求解、不重采样、不碰几何。
相关：``dt_floor.py``（有效地板的**唯一**公式源）、``feasibility.py``（调用方）。

**为什么下界不是配置里的 ``dt_min_s``**：那是 NLP 决策步长的数值护栏，短航段求解
时会按长度自适应收缩（``dt_floor.floor_from_length``）。复核若仍按配置值判，求解器
按 0.032 s 解出的 100 段会被整段误报 ``KINEMATIC_INCONSISTENT``——exp01_smoke seed0 周期 2
机 5 的 202→102（L=44 m）就是这样把整条 10 站航次判死、最终 0/30 的。
"""

from __future__ import annotations

from typing import Any

from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.timeline.dt_floor import effective_dt_min_s

__all__ = ["dt_range_issues"]

#: 时间超差容忍（s）：只吸收浮点累加
_TOL_TIME = 1e-6


def dt_range_issues(
    traj: Trajectory, params: PlanParams, vehicle: VehicleSpec
) -> list[tuple[int, Any, str]]:
    """返回 ``[(段号, 段, 说明), ...]``；空表 = Δt 区间合规。悬停段不参与。"""
    floor = effective_dt_min_s(traj, params, vehicle)
    out: list[tuple[int, Any, str]] = []
    for i, seg in enumerate(traj.segments):
        if seg.is_hover:
            continue
        if seg.duration < floor - _TOL_TIME:
            out.append((i, seg, f"段时长 {seg.duration:.4f} < 有效地板 {floor:.4f}"))
        elif seg.duration > float(params.dt_max_s) + _TOL_TIME:
            out.append((i, seg, f"段时长 {seg.duration:.4f} > dt_max={params.dt_max_s}"))
    return out
