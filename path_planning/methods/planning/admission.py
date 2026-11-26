# -*- coding: utf-8 -*-
"""单航段准入：先用几何下界判"这条航段飞不了"，再决定要不要花时间解 NLP。

职责：给一条航段算**必要条件下的下界**（最短可能时长、最短可能航程），
      与剩余预算比对，给出失败码；不建走廊、不跑 RRT、不进 NLP、不判净空。
边界：只做预算口径的必要条件；净空/走廊/运动学仍由空间层与时间层判。
相关：``tour_planner.py``（在候选初值生成之后调用）、``common/flight_budget.py``
      （交付后的预算判定，口径与这里互补）。

为什么需要这一层：NLP 的失败态几乎恒为 ``Maximum_Iterations_Exceeded``，而本项目把
这个状态当成"再给点预算就能解"，于是触发节点阶梯 + 预算 ×3。一条预算根本不够的
航段（exp01_smoke 机7：剩余续航 6 s、直线就要 250 m）会因此被反复证明不可行：
实测单种子 3794 s 里有 ~2000 s 花在这一条上。下界判据是 0 秒的，先判先退。

判据（都用**物理上限**算，保证是"必要条件"而不是"够不够用"的充分判断）：

- 时长下界 ``t_lb = max(dh/v_max, dz⁺/v_up, dz⁻/v_dn)`` —— 水平与垂直分量必须
  同时完成，取较大者；三条都用机体物理上限，不掺安全系数，否则会把"勉强够"的
  航段误拒。
- 航程下界 ``l_lb = ‖Δp‖`` —— 任何曲线弧长都 ≥ 端点直线距离。

与 ``TIME_EXCEEDS_BUDGET`` 的分工：那个码指**求解预算**（wall-clock/迭代数）超限，
本层判的是**飞行预算**，所以超时给 ``ENDURANCE_EXCEEDED``、超距给 ``RANGE_EXCEEDED``。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.vehicle_spec import VehicleSpec

__all__ = ["admit_leg", "leg_lower_bounds"]


def leg_lower_bounds(
    vehicle: VehicleSpec, start: np.ndarray, goal: np.ndarray
) -> dict[str, float]:
    """航段的时长/航程下界（米、秒）。"""
    dx, dy, dz = (float(goal[i] - start[i]) for i in range(3))
    dh = math.hypot(dx, dy)
    d3 = math.hypot(dh, dz)
    t_h = dh / float(vehicle.v_max_mps)
    t_v = dz / float(vehicle.v_up_mps) if dz >= 0.0 else -dz / float(vehicle.v_dn_mps)
    return {
        "dh_m": dh,
        "dz_m": dz,
        "t_lb_s": max(t_h, t_v),
        "l_lb_m": d3,
    }


def admit_leg(
    vehicle: VehicleSpec,
    start: np.ndarray,
    goal: np.ndarray,
    *,
    remaining_s: float,
    remaining_m: float,
) -> tuple[FailureCode | None, dict[str, Any]]:
    """航段准入：返回 ``(失败码, 诊断)``；``None`` 表示下界仍有余量，可以真解。

    两个下界都没有余量时按"更差的那一项"给码（占用率大者优先），与本项目交付后
    预算判定（``flight_budget.check``）的取向一致。
    """
    lb = leg_lower_bounds(vehicle, np.asarray(start, dtype=float), np.asarray(goal, dtype=float))
    t_ratio = lb["t_lb_s"] / remaining_s if remaining_s > 0.0 else float("inf")
    l_ratio = lb["l_lb_m"] / remaining_m if remaining_m > 0.0 else float("inf")
    diag: dict[str, Any] = {
        "t_lb_s": lb["t_lb_s"], "l_lb_m": lb["l_lb_m"],
        "remaining_s": float(remaining_s), "remaining_m": float(remaining_m),
        "t_ratio": t_ratio, "l_ratio": l_ratio,
    }
    code: FailureCode | None = None
    if t_ratio > 1.0 or l_ratio > 1.0:
        code = (
            FailureCode.ENDURANCE_EXCEEDED if t_ratio >= l_ratio
            else FailureCode.RANGE_EXCEEDED
        )
        diag["reason"] = (
            f"下界已超预算：时长 ≥ {lb['t_lb_s']:.2f} s（剩 {remaining_s:.2f} s）、"
            f"航程 ≥ {lb['l_lb_m']:.2f} m（剩 {remaining_m:.2f} m）"
        )
    return code, diag
