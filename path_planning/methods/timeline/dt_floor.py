# -*- coding: utf-8 -*-
"""NLP 时间步下界的自适应收缩：防止固定节点数把 ``min ΣΔt`` 压成常数。

职责：按航段长度给 ``dt_min_s`` 算一个「只降不升」的有效值，供
      ``trajectory_optimizer.optimize_trajectory``（求解侧）与
      ``feasibility.verify_trajectory``（复核侧）**共用同一份公式**。
边界：不改目标函数（仍为 ``min ΣΔt``）、不改节点数与段内采样密度、不碰几何场。
相关：``common/plan_params.py``（``dt_min_s`` 的数值护栏定义）、
      ``orchestration/experiments/exp01_smoke/support/diag/dt_floor.py``（诊断依据）。
"""

from __future__ import annotations

import math

import numpy as np

from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.timeline.nlp_config import NlpConfig

__all__ = ["adaptive_dt_min_s", "effective_dt_min_s", "floor_from_length"]


#: 时长地板占「静止→静止乐观最短时间」的上限比例。
#:
#: ``dt_min_s`` 只是数值护栏，但固定节点数 n 下 ``(n−1)·dt_min`` 会变成航段时长的
#: 硬地板。地板一旦高于物理最短时间，``min ΣΔt`` 就被下界钉住、对形状失去敏感度，
#: z 方向自由漂移（Hermite 段内插放大成拱形）。exp01_smoke 的 302→202 单航段诊断：
#: L=17.5 m、a_max=3 → 乐观下界 2√(L/a)=4.83 s，实测无退化解 ΣΔt=4.846 s；
#: 地板 5.950 s（比值 1.23）→ ΣΔt=5.951 s、弧长 19.615 m、z 拱出端点 2.106 m；
#: 地板 2.380 s（比值 0.49）→ ΣΔt=4.847 s、弧长 17.608 m、z 不越界。
#: 取 0.5 留一半余量，保证地板恒低于物理最短时间。
_DT_FLOOR_TIME_RATIO = 0.5


def floor_from_length(
    length_m: float,
    *,
    a_max_mps2: float,
    v_max_plan_mps: float,
    n_nodes: int,
    dt_min_s: float,
) -> float:
    """给「航段直线长度 + 节点数」算有效 ``dt_min``（只降不升）。

    求解侧（``optimize_trajectory``）与复核侧（``verify_trajectory``）**必须**调这
    一个函数：任何一边自己再写一遍公式，短航段就会出现"求解器按 0.032 s 解出、
    复核按 0.05 s 判死"的口径分裂——exp01_smoke seed0 的 202→102（L=44 m、100 段、
    每段 0.0496 s）就是这样被整条航次判死的。
    """
    length = float(length_m)
    n = max(2, int(n_nodes))
    if not math.isfinite(length) or length <= 0.0:
        return float(dt_min_s)
    a = max(float(a_max_mps2), 1e-9)
    v = max(float(v_max_plan_mps), 1e-9)
    if math.sqrt(a * length) <= v:
        t_lower = 2.0 * math.sqrt(length / a)
    else:
        t_lower = length / v + v / a
    cap = _DT_FLOOR_TIME_RATIO * t_lower / (n - 1)
    # 极端短航段仍保留 1 ms 下限，避免节点挤到一起让 IPOPT 病态。
    return min(float(dt_min_s), max(1e-3, cap))


def adaptive_dt_min_s(path_enu: np.ndarray, cfg: NlpConfig) -> float:
    """按航段长度收缩 ``dt_min``，使时长地板顶不到物理最短时间。

    **只降不升**：长航段算出的上限大于配置值时原样返回，故 km 级航段的离散精度
    与段内净空采样密度完全不变。用端点直线距离（而非 RRT 折线弧长）算下界——
    直线是最短可能路径，据此得到的地板最小，最安全。

    速度剖面取加速度受限的乐观下界（忽略爬升、净空绕行与竖直限速）：
    三角形峰值 ``√(a·L) ≤ v`` 时 ``t=2√(L/a)``，否则梯形 ``t=L/v+v/a``。
    它是真实最短时间的下界，乘 0.5 后作为地板上限有双重余量。
    """
    pts = np.atleast_2d(np.asarray(path_enu, dtype=float))
    if len(pts) < 2:
        return float(cfg.dt_min_s)
    return floor_from_length(
        float(np.linalg.norm(pts[-1] - pts[0])),
        a_max_mps2=float(cfg.a_max_mps2),
        v_max_plan_mps=float(cfg.v_max_plan_mps),
        n_nodes=int(cfg.n_nodes),
        dt_min_s=float(cfg.dt_min_s),
    )


def effective_dt_min_s(
    traj: Trajectory, params: PlanParams, vehicle: VehicleSpec
) -> float:
    """复核侧的有效 ``dt_min``：同一条轨迹按同一 ``floor_from_length`` 复算。

    节点数取**这条轨迹实际的飞行段数 + 1**（求解侧无论走到哪一级退让，交付的段数
    就是它当时用的 n），长度取首末飞行段的端点直线距离——与 ``adaptive_dt_min_s``
    同口径，故不会出现"求解放宽、复核仍按配置值判死"。
    """
    flight = [seg for seg in traj.segments if not seg.is_hover]
    if len(flight) < 2:
        return float(params.dt_min_s)
    p0, p1 = np.asarray(flight[0].p0, dtype=float), np.asarray(flight[-1].p1, dtype=float)
    return floor_from_length(
        float(np.linalg.norm(p1 - p0)),
        a_max_mps2=float(vehicle.a_max_mps2),
        v_max_plan_mps=float(vehicle.v_max_plan_mps),
        n_nodes=len(flight) + 1,
        dt_min_s=float(params.dt_min_s),
    )
