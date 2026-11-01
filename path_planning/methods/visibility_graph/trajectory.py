# -*- coding: utf-8 -*-
"""path → 匀速 ETA 占位（仅供可视化的时间色标辅助，**非 claimed 轨迹生成**）。

time-optimal 时间最优轨迹（Lin2021 §2.4 前向推进 + 反向回溯速度剖面）**不在本研究范围**，
由下游 UAV 飞行控制器完成；本方法只做最简单的**匀速占位**供可视化使用：
每段弧长 / 巡航速度 = 该段飞行时间，times 从出发时刻 t_dep 累计，speeds 恒为 cruise_speed。

trajectory 与 path 严格分存（多一维时间），不可合并——这是 route / path /
trajectory 三者必须区分的铁律（见 common/types.py 与 module_map.md §3）。
此处 trajectory 仅为 ETA 辅助字段，不承诺任何动力学/速度剖面正确性。
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from path_planning.common.types import Trajectory


def make_trajectory(
    path_pts: Sequence,
    cruise_speed_mps: float = 15.0,
    t_dep: float = 0.0,
) -> Trajectory:
    """path 三维采样点 → 匀速时间参数化 Trajectory。

    参数
    ----
    path_pts        (M,3) 平滑后的空间曲线采样点
    cruise_speed_mps  匀速巡航速度（米/秒）；≤0 退化为零时刻占位
    t_dep          出发时刻（秒）

    返回
    ----
    Trajectory(pts, times, speeds)，times[0]=t_dep；若点数 < 2 则零长度占位。
    """
    pts = np.asarray(path_pts, dtype=float)
    if len(pts) < 2 or cruise_speed_mps <= 0:
        return Trajectory(
            pts=pts,
            times=np.zeros(len(pts)),
            speeds=np.zeros(len(pts)),
        )
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.cumsum(seg / cruise_speed_mps)
    times = np.concatenate([[t_dep], t_dep + cum])
    speeds = np.full(len(pts), float(cruise_speed_mps))
    return Trajectory(pts=pts, times=times, speeds=speeds)
