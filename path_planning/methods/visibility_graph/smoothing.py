# -*- coding: utf-8 -*-
"""可见性图折线路径 → 平滑可飞曲线（先简后繁；Dubins 二期）。

route（图节点折线，纯几何走廊）→ path（平滑曲线，纯几何、无时间）：

    * 简（默认）：Chaikin 角点削减（corner cutting）→ 自然圆角，端点保持；
    * 繁：Catmull-Rom 样条插值（同样保持端点，曲线经过所有控制点）。

平滑后做碰撞复核：在障碍（SimplifiedObstacles，已含 d_build 外扩）与禁飞
（NoFlyField）上对密集采样段逐段检查。若平滑曲线侵入任一约束 → **回退到原始
折线**（绝不输出不可行 path；route 走廊始终保留，供上层降级使用）。

不负责：时间维（trajectory.py 负责）、最优控制（time_optimal 二期）、
建图与搜索（pipeline.py / edges.py / search.py）。
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np


def _as_points(pts: Sequence) -> list[np.ndarray]:
    return [np.asarray(p, dtype=float) for p in pts]


def chaikin(points: Sequence, iterations: int = 2) -> np.ndarray:
    """Chaikin 角点削减：每条线段两端各取 1/4、3/4 处，迭代生成圆角折线。

    端点严格保持（首末点不动）；段数指数增长（迭代 k 次 → 约 1 + n·2^k 点）。
    纯几何、无碰撞语义，碰撞由 recheck 把关。
    """
    pts = _as_points(points)
    if len(pts) < 3:
        return np.array(pts)
    for _ in range(max(0, iterations)):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for i in range(len(pts) - 1):
            p0, p1 = pts[i], pts[i + 1]
            out.append(p0 * 0.75 + p1 * 0.25)
            out.append(p0 * 0.25 + p1 * 0.75)
        out.append(pts[-1])
        pts = out
    return np.array(pts)


def catmull_rom(points: Sequence, samples_per_seg: int = 6) -> np.ndarray:
    """Catmull-Rom 样条：曲线经过所有控制点（含端点），C1 连续。

    端点用镜像复制扩展，故首尾点保持不动；samples_per_seg 控制每段的采样密度。
    """
    pts = _as_points(points)
    if len(pts) < 3:
        return np.array(pts)
    out: list[np.ndarray] = [pts[0]]
    P = [pts[0]] + pts + [pts[-1]]  # 端点镜像扩展
    n = max(2, int(samples_per_seg))
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0.0, 1.0, n + 1)[1:]:
            t2 = t * t
            t3 = t2 * t
            v = 0.5 * (
                2.0 * p1
                + (-p0 + p2) * t
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
            )
            out.append(v)
    out.append(pts[-1])
    return np.array(out)


def _dense(poly: np.ndarray, step: float) -> np.ndarray:
    poly = np.asarray(poly, dtype=float)
    if len(poly) < 2:
        return poly
    out = [poly[0]]
    for a, b in zip(poly[:-1], poly[1:]):
        d = float(np.linalg.norm(b - a))
        k = max(1, int(np.ceil(d / max(step, 1e-6))))
        for j in range(1, k + 1):
            out.append(a + (b - a) * (j / k))
    return np.array(out)


def recheck(
    poly: np.ndarray,
    obstacles,
    nofly,
    step: float = 2.0,
    margin: float = 0.0,
) -> bool:
    """平滑曲线是否仍无碰撞（建筑棱柱 + 禁飞体）。

    对加密后的采样段逐段调用 obstacles.segment_clear / nofly.blocked。
    任一不通过即返回 False（须回退原始折线）。
    """
    poly = np.asarray(poly, dtype=float)
    if len(poly) < 2:
        return True
    dense = _dense(poly, step)
    for i in range(len(dense) - 1):
        if not obstacles.segment_clear(dense[i], dense[i + 1], margin=margin):
            return False
        if nofly.blocked(dense[i], dense[i + 1], margin=margin):
            return False
    return True


def smooth_polyline(
    poly: Sequence,
    method: str = "chaikin",
    obstacles=None,
    nofly=None,
    *,
    step: float = 2.0,
    margin: float = 0.0,
    iterations: int = 2,
    samples_per_seg: int = 6,
) -> tuple[np.ndarray, bool]:
    """图节点折线 → 平滑曲线（纯几何）。

    参数
    ----
    poly        折线（world 坐标，list[np.ndarray] 或 (M,3) ndarray）
    method      'chaikin'（简，默认）| 'catmull'（繁）| 'none'（不平滑）
    obstacles   SimplifiedObstacles（用于碰撞复核；None → 跳过建筑复核）
    nofly       NoFlyField（用于碰撞复核；None → 跳过禁飞复核）
    step        复核采样步长（米）
    margin      复核余量（米）；建议传图建图时的 edge_margin 以等口径
    iterations / samples_per_seg  两种方法的精度参数

    返回
    ----
    (pts, applied)  pts=(M',3) ndarray；applied=True 表示输出了平滑曲线，
                    False 表示平滑后侵入约束、已回退原始折线（route 走廊仍可用）。
    """
    raw = np.asarray(_as_points(poly), dtype=float)
    if len(raw) < 3 or method in ("none", "raw"):
        return raw, False

    if method == "chaikin":
        sm = chaikin(raw, iterations=iterations)
    elif method == "catmull":
        sm = catmull_rom(raw, samples_per_seg=samples_per_seg)
    else:
        raise ValueError(f"未知平滑方法 {method!r}（可选 chaikin/catmull/none）")

    if obstacles is None and nofly is None:
        return sm, True

    # 平滑曲线通过 → 采用；否则先尝试更弱平滑，仍失败则回退原始折线。
    if recheck(sm, obstacles, nofly, step=step, margin=margin):
        return sm, True
    if method == "chaikin" and iterations > 1:
        sm2 = chaikin(raw, iterations=max(1, iterations - 1))
        if recheck(sm2, obstacles, nofly, step=step, margin=margin):
            return sm2, True
    # 回退：原始折线（保证 route/path 不致输出不可行曲线）
    return raw, False
