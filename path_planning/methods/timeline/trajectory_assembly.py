# -*- coding: utf-8 -*-
"""节点解 → 交付曲线：三次 Hermite 段装配与 z 带采样点。

职责：把 CasADi 解出的 ``(x, v, a, dt)`` 装配成 ``Trajectory``；提供"与交付段同一
      基函数"的单轴取值，供约束层在**交付曲线**上采样（而不是只钉节点）。
边界：不含求解（``trajectory_optimizer.py``）、不含判据（``feasibility.py``）。
相关：``splines.py``（求值/弧长/加速度）、``docs/time_mapping.md`` §3。

为什么交付端点要记 ``a0/a1``：``a`` 是本问题里真正被约束的指令加速度，判据层
直接读它就不必从三次曲线反推 ``p''``——反推有 ``O(δ/Δt²)`` 的离散差（exp01_smoke
实测 3.016 vs 3.000），会给出假超限。交付曲线本身仍是三次（升阶实测会把段内速度
顶过 ``v_max``，见 ``segment.py`` 的说明）。
"""

from __future__ import annotations

import numpy as np

from path_planning.common.plan_types import HermiteSegment, Trajectory
from path_planning.methods.timeline.splines import trajectory_arc_length

__all__ = ["Z_BAND_SAMPLES", "build_trajectory", "hermite_axis"]

#: z 飞行带在**交付曲线**上的采样位置（段内归一化参数）。中点 0.5 正是
#: "两端 z 相等、两个 z 速度反向"时 Hermite 拱顶的位置；0.2/0.4/0.6/0.8 兜住
#: 非对称情形（节点框内但曲线外凸）。
Z_BAND_SAMPLES: tuple[float, ...] = (0.2, 0.4, 0.6, 0.8)


def hermite_axis(p0, v0, p1, v1, dt, s: float):
    """单轴三次 Hermite 在段内参数 ``s`` 处的取值（与交付段同一基函数）。"""
    s2 = s * s
    s3 = s2 * s
    h00 = 2.0 * s3 - 3.0 * s2 + 1.0
    h10 = s3 - 2.0 * s2 + s
    h01 = -2.0 * s3 + 3.0 * s2
    h11 = s3 - s2
    return h00 * p0 + h10 * dt * v0 + h01 * p1 + h11 * dt * v1


def build_trajectory(opti, x, v, a, dt, n, uav_id, t0, stats) -> Trajectory:
    """把节点解装配成三次 Hermite 段序列（弧长由 Gauss 重积分算出）。

    段端点顺带记下节点加速度 ``a_i``：它是本问题里真正被约束的指令加速度，
    判据层直接读它，不必从三次曲线反推 ``p''``（反推会差 O(δ/Δt²)，实测 3.016
    vs 3.000）。交付曲线本身仍是三次，位置/速度端点不变。
    """
    pv = np.asarray(opti.value(x), dtype=float).T
    vv = np.asarray(opti.value(v), dtype=float).T
    av = np.asarray(opti.value(a), dtype=float).T
    dtv = np.asarray(opti.value(dt), dtype=float).ravel()
    times = t0 + np.concatenate([[0.0], np.cumsum(dtv[: n - 1])])
    segs = tuple(
        HermiteSegment(
            float(times[i]), float(times[i + 1]),
            tuple(pv[i]), tuple(pv[i + 1]), tuple(vv[i]), tuple(vv[i + 1]),
            a0=tuple(av[i]), a1=tuple(av[i + 1]),
        )
        for i in range(n - 1)
    )
    return Trajectory(
        uav_id=uav_id,
        segments=segs,
        path_len_m=trajectory_arc_length(segs),
        meta={
            "solver_status": str(stats.get("return_status", "Solve_Succeeded")),
            "iterations": int(stats.get("iter_count", 0) or 0),
            "n_nodes": n,
        },
    )
