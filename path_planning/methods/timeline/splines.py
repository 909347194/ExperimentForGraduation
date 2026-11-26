# -*- coding: utf-8 -*-
"""Hermite 轨迹求值与弧长积分（交付轨迹的数学定义）。

职责：对 ``HermiteSegment`` / ``Trajectory`` 做位置、速度求值与**弧长 Gauss
      重积分**；提供按绝对时刻查位置/速度的入口，以及供优化器热启动的节点提取。
边界：不做可行性判定（``feasibility.py``）、不做优化（``trajectory_optimizer.py``）、
      不构造航迹（优化器与降级路径各自构造）。
相关：``common/plan_types.py``（段与航迹的数据定义）、
      ``docs/time_mapping.md`` §4（为什么必须重积分、Gauss 公式与系数）。

两条口径（time_mapping §4，做错航程判据会系统性乐观）：

1. **弧长 ≠ 弦长，也 ≠ 平均速度×时长**，两者都低估（弯道城区可低估 5–15%）。
   必须对 Hermite 解析导数做 Gauss-Legendre 积分：M 个等长子区间、每区间 k 点
   （默认 8×2）。系数是 ``1/(2M)`` 不是 ``1/2``——写成端点均值×区间长就退化成
   梯形公式，代数精度从 3 掉到 1，弯道上重新引入低估。
2. ``p'(s)`` 是对参数 s 的导数（量纲米），真实速度 ``ṗ = p'/Δt``；弧长
   ``L = ∫₀¹‖p'(s)‖ds``，**不再乘 Δt**。文档公式里的 Δt 属于"对时间求导"的
   参数化，两种写法别混。

时间参数化是线性的：``t = t0 + Δt·s``，所以 ``s = (t-t0)/Δt`` 精确成立，
按时刻查位置不需要 Newton 迭代（time_mapping §11 的疑虑在此消解）。
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import numpy as np

from path_planning.common.plan_types import HermiteSegment, Trajectory, Vec3

__all__ = [
    "segment_position",
    "segment_derivative",
    "segment_velocity",
    "segment_acceleration",
    "segment_command_acceleration",
    "segment_arc_length",
    "trajectory_arc_length",
    "position_at",
    "velocity_at",
    "trajectory_nodes",
]


# ---------------------------------------------------------------------------
# Hermite 基函数（s ∈ [0,1]）
# ---------------------------------------------------------------------------


def _basis(s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``h00, h10, h01, h11``（位置基）。"""
    s2 = s * s
    s3 = s2 * s
    return (
        2.0 * s3 - 3.0 * s2 + 1.0,
        s3 - 2.0 * s2 + s,
        -2.0 * s3 + 3.0 * s2,
        s3 - s2,
    )


def _basis_derivative(s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``h00', h10', h01', h11'``（对 s 求导）。"""
    s2 = s * s
    return (
        6.0 * s2 - 6.0 * s,
        3.0 * s2 - 4.0 * s + 1.0,
        -6.0 * s2 + 6.0 * s,
        3.0 * s2 - 2.0 * s,
    )


def _basis_second_derivative(s: np.ndarray) -> tuple[np.ndarray, ...]:
    """``h00'', h10'', h01'', h11''``（对 s 的二阶导）。"""
    return (
        12.0 * s - 6.0,
        6.0 * s - 4.0,
        -12.0 * s + 6.0,
        6.0 * s - 2.0,
    )

def _as_s(s) -> tuple[np.ndarray, bool]:
    arr = np.atleast_1d(np.asarray(s, dtype=np.float64))
    return arr, np.ndim(s) == 0


def _combine(
    seg: HermiteSegment, coeffs: tuple[np.ndarray, ...], *, scale_v: float
) -> np.ndarray:
    """基函数 × 端点量 → ``(n,3)``。``scale_v`` 乘在速度项上（Δt 或 1）。"""
    c00, c10, c01, c11 = coeffs
    p0 = np.asarray(seg.p0, dtype=np.float64)
    p1 = np.asarray(seg.p1, dtype=np.float64)
    v0 = np.asarray(seg.v0, dtype=np.float64) * scale_v
    v1 = np.asarray(seg.v1, dtype=np.float64) * scale_v
    return (
        c00[:, None] * p0
        + c10[:, None] * v0
        + c01[:, None] * p1
        + c11[:, None] * v1
    )


# ---------------------------------------------------------------------------
# 求值
# ---------------------------------------------------------------------------


def segment_position(seg: HermiteSegment, s) -> np.ndarray:
    """段内参数 ``s``（标量或 ``(n,)``）处的位置；标量入参返回 ``(3,)``。"""
    s_arr, scalar = _as_s(s)
    out = _combine(seg, _basis(s_arr), scale_v=seg.duration)
    return out[0] if scalar else out


def segment_derivative(seg: HermiteSegment, s) -> np.ndarray:
    """``dp/ds``（量纲米，**不是速度**）；标量入参返回 ``(3,)``。"""
    s_arr, scalar = _as_s(s)
    out = _combine(seg, _basis_derivative(s_arr), scale_v=seg.duration)
    return out[0] if scalar else out


def segment_velocity(seg: HermiteSegment, s) -> np.ndarray:
    """真实速度 ``ṗ = (dp/ds)/Δt``（m/s）；标量入参返回 ``(3,)``。

    必须由 ``dp/ds`` 整体除以 Δt 得到——位置项 ``p0,p1`` 也要除。若只把速度项
    的 Δt 约掉而位置项不除，端点处恰好正确（``h00',h01'`` 在端点为 0），
    内部点却会错，端点测试抓不到。
    """
    return segment_derivative(seg, s) / seg.duration


# ---------------------------------------------------------------------------
# 弧长（Gauss-Legendre 重积分）
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _gauss_nodes_weights(k: int) -> tuple[np.ndarray, np.ndarray]:
    """k 点 Gauss-Legendre 节点/权重（``[-1,1]`` 上）。"""
    if k < 2:
        raise ValueError(f"gauss_points 至少 2（1 点退化为矩形公式）：{k}")
    return np.polynomial.legendre.leggauss(k)


def segment_acceleration(seg: HermiteSegment, s) -> np.ndarray:
    """**插值曲线**的二阶导 ``p''/Δt²``（m/s²）；标量入参返回 ``(3,)``。

    三次 Hermite 只在 C¹，``p''`` 在节点两侧一般**不相等**（C² 不成立），所以取
    端点要各自当单边极限看，别把相邻两点拼成"节点加速度"再判一次。

    这条量与指令加速度不是同一个东西：``p''`` 由位置/速度拼出（端点
    ``p''(0)=6(p0−p1)/Δt²+(4v0+2v1)/Δt``），与优化器节点 ``a`` 有 ``O(δ/Δt²)``
    的离散差。判"飞得动吗"用 ``segment_command_acceleration``；这条只作插值形状
    的参考量（画曲率、诊断隆起）。
    """
    s_arr, scalar = _as_s(s)
    basis = _basis_second_derivative(s_arr)
    p0 = np.asarray(seg.p0, dtype=np.float64)
    p1 = np.asarray(seg.p1, dtype=np.float64)
    v0 = np.asarray(seg.v0, dtype=np.float64) * seg.duration
    v1 = np.asarray(seg.v1, dtype=np.float64) * seg.duration
    out = (
        basis[0][:, None] * p0
        + basis[1][:, None] * v0
        + basis[2][:, None] * p1
        + basis[3][:, None] * v1
    ) / (seg.duration * seg.duration)
    return out[0] if scalar else out


def segment_command_acceleration(seg: HermiteSegment, s) -> np.ndarray:
    """**指令**加速度（m/s²）：段端点带 ``a0/a1`` 时按端点线性插值，否则退到 ``p''``。

    这是判据该用的量。``a0/a1`` 就是优化器的决策变量（``opti.variable(3, n)``，
    已被 ``a ≤ a_max``、推力上下界直接约束过），所以拿它判"飞得动吗"是**同源**的，
    不会出现"约束算节点、复核算 p''"的 0.5% 假超限。段内线性插值只是采样手段：
    模长、比推力都对 ``a`` 凸，最大值必在端点取到，插值不会漏判。
    """
    if seg.a0 is None:
        return segment_acceleration(seg, s)
    s_arr, scalar = _as_s(s)
    a0 = np.asarray(seg.a0, dtype=np.float64)
    a1 = np.asarray(seg.a1, dtype=np.float64)
    out = (1.0 - s_arr)[:, None] * a0 + s_arr[:, None] * a1
    return out[0] if scalar else out


def segment_arc_length(
    seg: HermiteSegment, *, subintervals: int = 8, gauss_points: int = 2
) -> float:
    """段弧长 ``L = ∫₀¹‖dp/ds‖ds``（米），M 子区间 × k 点 Gauss。

    悬停段（``is_hover``）弧长恒 0：位置不变、速度为 0，纯时间项。
    """
    if seg.is_hover:
        return 0.0
    if subintervals < 1:
        raise ValueError(f"subintervals 至少 1：{subintervals}")
    nodes, weights = _gauss_nodes_weights(int(gauss_points))
    m = int(subintervals)
    # 子区间 [(j/m), ((j+1)/m)]，把 [-1,1] 节点映射进去
    centers = (np.arange(m, dtype=np.float64) + 0.5) / m
    half = 0.5 / m
    s_all = (centers[:, None] + half * nodes[None, :]).ravel()
    w_all = np.broadcast_to(half * weights[None, :], (m, len(nodes))).ravel()
    deriv = _combine(seg, _basis_derivative(s_all), scale_v=seg.duration)
    speed = np.linalg.norm(deriv, axis=1)
    return float(np.dot(w_all, speed))


def trajectory_arc_length(
    segments: Sequence[HermiteSegment] | Trajectory,
    *,
    subintervals: int = 8,
    gauss_points: int = 2,
) -> float:
    """整条航迹弧长（各段之和）。接受 ``Trajectory`` 或段序列。"""
    segs = segments.segments if isinstance(segments, Trajectory) else segments
    return float(
        sum(
            segment_arc_length(s, subintervals=subintervals, gauss_points=gauss_points)
            for s in segs
        )
    )


# ---------------------------------------------------------------------------
# 按绝对时刻查询
# ---------------------------------------------------------------------------


def _locate(traj: Trajectory, t: float) -> tuple[HermiteSegment, float]:
    """定位时刻所在段并给出段内参数 s（越界夹到端点）。"""
    t = float(t)
    if t <= traj.t0:
        return traj.segments[0], 0.0
    if t >= traj.t_end:
        return traj.segments[-1], 1.0
    ends = np.array([s.t1 for s in traj.segments], dtype=np.float64)
    idx = int(np.searchsorted(ends, t, side="left"))
    seg = traj.segments[idx]
    s = (t - seg.t0) / seg.duration
    return seg, float(np.clip(s, 0.0, 1.0))


def position_at(traj: Trajectory, t: float) -> Vec3:
    """绝对时刻 ``t`` 处的位置（越界夹到首/末端点）。"""
    seg, s = _locate(traj, t)
    p = segment_position(seg, s)
    return (float(p[0]), float(p[1]), float(p[2]))


def velocity_at(traj: Trajectory, t: float) -> Vec3:
    """绝对时刻 ``t`` 处的速度（越界夹到首/末端点）。"""
    seg, s = _locate(traj, t)
    v = segment_velocity(seg, s)
    return (float(v[0]), float(v[1]), float(v[2]))


# ---------------------------------------------------------------------------
# 节点提取（供热启动）
# ---------------------------------------------------------------------------


def trajectory_nodes(traj: Trajectory) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """从航迹还原优化器节点 ``(p, v, dt)``，形状 ``(N,3)/(N,3)/(N,)``。

    ``N = 段数 + 1``。``dt[i]`` 是第 i 段时长（i < N-1）；``dt[N-1]`` 镜像
    ``dt[N-2]``——与优化器"Δt 向量长 N、动力学只用前 N-1 个"的布局一致
    （见 ``trajectory_optimizer`` 模块说明），保证热启动维度对得上。
    """
    segs = traj.segments
    p = np.array([s.p0 for s in segs] + [segs[-1].p1], dtype=np.float64)
    v = np.array([s.v0 for s in segs] + [segs[-1].v1], dtype=np.float64)
    dt = np.array([s.duration for s in segs], dtype=np.float64)
    dt = np.concatenate([dt, dt[-1:]])
    return p, v, dt
