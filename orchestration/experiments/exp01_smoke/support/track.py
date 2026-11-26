# -*- coding: utf-8 -*-
"""航迹采样：把 Trajectory 压成绘图用的折线（只落盘，不参与任何判据）。

职责：按固定时间步取位置，输出 ``t / x / y / z``；数值只保留 2 位小数。
边界：采样不改变航迹、不回算里程与时长——那两个数由 ticket 直接给。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from path_planning.methods.timeline.splines import position_at


def sample(traj: Any, *, step_s: float = 1.0, ndigits: int = 2) -> dict[str, Any]:
    """等时间采样一条航迹（含悬停段），点数为 ``duration/step + 1``。"""
    if step_s <= 0.0:
        raise ValueError(f"采样步长必须为正：{step_s}")
    t0, t_end = float(traj.t0), float(traj.t_end)
    span = max(t_end - t0, 0.0)
    n = max(int(np.ceil(span / step_s)), 1)
    times = np.linspace(t0, t_end, n + 1)
    pts = np.asarray([position_at(traj, float(t)) for t in times], dtype=float)
    return {
        "t0": round(t0, 3),
        "t_end": round(t_end, 3),
        "t": [round(float(t), 3) for t in times],
        "x": [round(float(v), ndigits) for v in pts[:, 0]],
        "y": [round(float(v), ndigits) for v in pts[:, 1]],
        "z": [round(float(v), ndigits) for v in pts[:, 2]],
    }
