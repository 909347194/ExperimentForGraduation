# -*- coding: utf-8 -*-
"""平滑后二次碰撞校验与偏移修正（README §4.3.4）。

对平滑后的折线逐段调用 CollisionQuery（建筑 + 静态/动态禁飞）；发生冲突时
按 shrink_factor 收缩平滑偏差 d 并回调 dubins_smoother 重平滑，直至全部可行
或超过迭代上限（返回 False 由上层标记失败）。

复核口径：
    无时间：link_feasible（建筑 + 静态禁飞）
    带时间：polyline_occupancy_free（时刻按路径弧长比例分配，design §8）
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from path_planning.common.config import OnlineParams
from path_planning.methods.control_network_planning.constraints.query import CollisionQuery
from path_planning.methods.control_network_planning.online.dubins_smoother import smooth_route


def check_polyline(query: CollisionQuery, pts: np.ndarray, margin: float) -> bool:
    return query.polyline_feasible(pts, margin=margin)


def smooth_and_check(
    route_pts: Sequence[np.ndarray],
    query: CollisionQuery,
    onp: OnlineParams,
    total_time: Optional[float] = None,
    t_dep: float = 0.0,
    times_out: Optional[list] = None,
    check_dynamic: bool = True,
) -> tuple[Optional[np.ndarray], float, int]:
    """平滑 + 复核，必要时收缩偏差重试（Fig.14）。

    复核**同时**覆盖：
        无时间：建筑 + 静态禁飞（link_feasible）
        带时间：动态禁飞（时刻按弧长比例分配，逐段切段求交）
    只查前者会漏掉"平滑曲线偏离走廊蹭进临时禁飞区"这一类冲突。

    返回 (路径采样点 or None, 实际使用的偏差 d, 复核轮次)。
    """
    d = float(onp.smooth_deviation)
    for it in range(onp.max_shrink_iters + 1):
        pts, _ = smooth_route(route_pts, d, onp)
        times = _assign_times(pts, total_time, t_dep)
        if not check_polyline(query, pts, 0.0):
            d *= onp.shrink_factor
            continue
        if check_dynamic and not query.polyline_occupancy_free(pts, times):
            d *= onp.shrink_factor  # 收缩平滑偏差再平滑（Fig.14 的收缩机制）
            continue
        if times_out is not None:
            times_out.append(times)
        return pts, d, it + 1
    return None, d, onp.max_shrink_iters + 1


def _assign_times(pts: np.ndarray, total_time: Optional[float], t_dep: float) -> np.ndarray:
    """按弧长比例分配到达时刻（时间最优轨迹 time_optimal.py 的前置占位口径）。"""
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = s[-1] if s[-1] > 0 else 1.0
    T = float(total_time) if total_time and np.isfinite(total_time) else total / 15.0
    return t_dep + s / total * T
