# -*- coding: utf-8 -*-
"""NLP 初值：几何折线 → 定维数的 ``(v, a, Δt)`` 猜测。

职责：把 RRT 折线按弧长等距重采样到固定节点数；由折线猜逐节点速度、加速度、
      步长，尽量落进口径 B 的可行域（**只是初值，不保证可行**）。
边界：不做优化、不判可行性（``feasibility.py``）、不建 SDF。
相关：``trajectory_optimizer.py``（消费本模块产物）、``common/vehicle_spec.py``
      （口径 B 三速度上限）、``docs/time_mapping.md`` §2/§3。

为什么单独成文件：让 ``trajectory_optimizer`` 只装模型与求解策略（行数纪律）。

与参考实现（单一标量 Δt）的差别：本项目 Δt 是**逐节点**向量（10N=600 决策），
所以初值也逐段给——``dt_i = clip(段长_i / v_cruise, dt_min, dt_max)``，中心差分
用**真实时间间隔** ``t_{i+1}-t_{i-1}`` 而不是 ``2·dt``（非均匀步长下后者是错的）。
"""

from __future__ import annotations

import numpy as np

from path_planning.common.plan_params import PlanParams
from path_planning.common.vehicle_spec import VehicleSpec

__all__ = ["resample_path", "initial_guess", "initial_guess_from_limits"]

#: 初值放大循环的最大轮数（dt ×1.5，最多 8 轮）
_MAX_INFLATE_ROUNDS = 8
_INFLATE_FACTOR = 1.5
#: 初值可行性判断的相对容差（初值不必精确可行，留一点余量给求解器）
_TOL = 1e-6


def resample_path(path_enu: np.ndarray, n_nodes: int) -> np.ndarray:
    """按**弧长**等距重采样到 ``n_nodes`` 个点（含两端）。

    RRT 原始节点数随环境漂移（实测 3–40 点），决策变量维度不定就没法比多初值；
    固定 N 就是这一步。一维线性插值够用——这里只要一个落在走廊里的初值，
    形状由 NLP 决定。
    """
    path = np.asarray(path_enu, dtype=np.float64)
    if path.ndim != 2 or path.shape[1] != 3 or len(path) < 2:
        raise ValueError(f"path_enu 必须是 (K≥2, 3)，收到 {path.shape}")
    if n_nodes < 2:
        raise ValueError(f"n_nodes 至少 2：{n_nodes}")
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(s[-1])
    if total <= 0.0:
        raise ValueError("path_enu 长度为 0，无法重采样")
    target = np.linspace(0.0, total, int(n_nodes))
    return np.stack([np.interp(target, s, path[:, d]) for d in range(3)], axis=1)


def initial_guess(
    path: np.ndarray, vehicle: VehicleSpec, params: PlanParams
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """由重采样折线猜 ``(v, a, dt)``（机体规格 + 参数入口）。"""
    return initial_guess_from_limits(
        path,
        v_cruise_mps=vehicle.v_max_plan_mps,
        v_max_mps=vehicle.v_max_mps,
        v_up_mps=vehicle.v_up_mps,
        v_dn_mps=vehicle.v_dn_mps,
        a_max_mps2=vehicle.a_max_mps2,
        dt_min_s=params.dt_min_s,
        dt_max_s=params.dt_max_s,
    )


def initial_guess_from_limits(
    path: np.ndarray,
    *,
    v_cruise_mps: float,
    v_max_mps: float,
    v_up_mps: float,
    v_dn_mps: float,
    a_max_mps2: float,
    dt_min_s: float,
    dt_max_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """由重采样折线猜 ``(v, a, dt)``，形状 ``(N,3)/(N,3)/(N,)``（限值入口）。

    优化器只持有 ``NlpConfig``（没有 ``VehicleSpec`` 对象），故提供按限值入参
    的版本；``initial_guess`` 是它的机体规格包装。

    步骤：先按"别超速"逐段定 dt，再用真实时间间隔做中心差分得 v/a；若违反
    口径 B 三速度或加速度上限，把 dt 整体 ×1.5 重算（最多 8 轮）。dt 顶到
    ``dt_max`` 仍不可行就原样返回——初值不可行是常态，求解器会修。
    """
    path = np.asarray(path, dtype=np.float64)
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    dt = np.clip(seg / max(v_cruise_mps, 1e-9), dt_min_s, dt_max_s)
    dt = np.concatenate([dt, dt[-1:]])  # 镜像末位，凑齐 N（与优化器布局一致）

    v = np.zeros_like(path)
    a = np.zeros_like(path)
    for _ in range(_MAX_INFLATE_ROUNDS):
        t = np.concatenate([[0.0], np.cumsum(dt[:-1])])
        span = t[2:] - t[:-2]  # 中心差分用真实时间间隔（非均匀步长）
        v[1:-1] = (path[2:] - path[:-2]) / span[:, None]
        a[1:-1] = (v[2:] - v[:-2]) / span[:, None]
        if _guess_feasible(v, a, v_max_mps, v_up_mps, v_dn_mps, a_max_mps2, v_cruise_mps):
            break
        dt = np.clip(dt * _INFLATE_FACTOR, dt_min_s, dt_max_s)
    return v, a, dt


def _guess_feasible(
    v: np.ndarray,
    a: np.ndarray,
    v_max_mps: float,
    v_up_mps: float,
    v_dn_mps: float,
    a_max_mps2: float,
    v_h_max_mps: float,
) -> bool:
    """口径 B 三速度 + 加速度上限（逐节点，含端点）。"""
    v_h = np.linalg.norm(v[:, :2], axis=1)
    v_norm = np.linalg.norm(v, axis=1)
    a_norm = np.linalg.norm(a, axis=1)
    return bool(
        np.all(v_h <= v_h_max_mps + _TOL)
        and np.all(v[:, 2] <= v_up_mps + _TOL)
        and np.all(v[:, 2] >= -v_dn_mps - _TOL)
        and np.all(v_norm <= v_max_mps + _TOL)
        and np.all(a_norm <= a_max_mps2 + _TOL)
    )
