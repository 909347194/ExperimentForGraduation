# -*- coding: utf-8 -*-
"""让路候选：按"垂直层切换优先、水平绕行次之"的次序生成局部几何变体。

职责：在局部域内对折线节点施加带过渡权重的位移，生成若干个候选几何（端点冻结）。
边界：只生成几何，不解时间、不判可行性、不挑最优。
相关：``local_region.py``（域与权重）、``local_replan.py``（消费者）、
      ``docs/conflict_model.md`` §10.2/§10.3（自由度与候选集）。

**为什么垂直优先**（§10.1）：椭球把竖直间隔要求降到 15 m（水平 30 m），而两机的
相对竖直速度上限只有 8 m/s，所以"换一层飞"通常是最短、最省弧长的解；水平绕行
必然增加弧长与耗时。候选集按这个次序排，让"先试便宜的"成为**代码顺序**而不是注释。

**端点永不位移**：起点/终点是机巢与任务点，动了就不是这条航次。位移只作用在
域内节点，且权重在域边界衰减到 0，位移在边界处闭合——否则折线会带一个尖角，
内层 NLP 要用极大加速度去追。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from path_planning.methods.coordination.local_region import LocalRegion

__all__ = ["DetourCandidate", "generate_detours", "lateral_direction"]


@dataclass(frozen=True)
class DetourCandidate:
    """一个让路几何候选。

    Attributes:
        path:    位移后的折线 ``(n, 3)``（端点与输入相同）
        label:   可读标签（进日志与 ``meta``）
        kind:    ``"vertical"`` / ``"lateral"``
        offset_m: 中心处名义位移量（米，带符号）
        moved_nodes: 实际被移动的节点数（0 表示域内没有节点，候选无效）
    """

    path: np.ndarray
    label: str
    kind: str
    offset_m: float
    moved_nodes: int

    @property
    def valid(self) -> bool:
        return self.moved_nodes > 0


def lateral_direction(path: np.ndarray, center_xy) -> tuple[float, float]:
    """冲突处折线走向的左法向（水平单位向量）。

    取"离域中心最近的折线段"的走向，再取左法向 ``(-dy, dx)/‖·‖``。竖直段（水平
    投影退化）没有可用走向，退回 ``(0, 1)``——那说明冲突是原地悬停，绕行方向
    本来就任意。
    """
    pts = np.asarray(path, dtype=np.float64)
    if pts.shape[0] < 2:
        return (0.0, 1.0)
    cx, cy = float(center_xy[0]), float(center_xy[1])
    best = None
    for a, b in zip(pts[:-1], pts[1:]):
        mx, my = 0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])
        d = (mx - cx) ** 2 + (my - cy) ** 2
        if best is None or d < best[0]:
            best = (d, b[0] - a[0], b[1] - a[1])
    _, dx, dy = best
    norm = float(np.hypot(dx, dy))
    if norm < 1e-9:
        return (0.0, 1.0)
    return (-dy / norm, dx / norm)


def generate_detours(
    path: np.ndarray,
    region: LocalRegion,
    *,
    vertical_steps_m: tuple[float, ...],
    lateral_steps_m: tuple[float, ...],
    lateral_dir_xy: tuple[float, float] | None = None,
) -> list[DetourCandidate]:
    """生成让路候选，次序 = 垂直各档（先上后下）→ 水平各档（先左后右）。

    Args:
        path: 原折线（ENU，``(n,3)``，含端点）
        region: 局部域（决定哪些节点被移动、移动多少）
        vertical_steps_m: 竖直档（正数，米）
        lateral_steps_m:  水平档（正数，米）
        lateral_dir_xy:   水平绕行方向；None = 按冲突处走向取左法向
    """
    pts = np.asarray(path, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] < 2:
        raise ValueError(f"path 应为 (n>=2, 3)：{pts.shape}")
    dir_xy = lateral_dir_xy or lateral_direction(pts, region.center_xy)
    w = np.array([region.weight(p[:2]) for p in pts], dtype=np.float64)
    # 端点冻结：权重强制 0（域中心恰在起终点附近时也不能动端点）
    w[0] = 0.0
    w[-1] = 0.0
    if not np.any(w > 0.0):
        return []
    moved = int(np.count_nonzero(w > 0.0))

    out: list[DetourCandidate] = []
    for step in vertical_steps_m:
        for sign, name in ((1.0, "up"), (-1.0, "down")):
            cand = pts.copy()
            cand[:, 2] += sign * float(step) * w
            out.append(DetourCandidate(
                path=cand, label=f"vertical_{name}_{step:g}m", kind="vertical",
                offset_m=sign * float(step), moved_nodes=moved,
            ))
    for step in lateral_steps_m:
        for sign, name in ((1.0, "left"), (-1.0, "right")):
            cand = pts.copy()
            cand[:, 0] += sign * float(step) * w * dir_xy[0]
            cand[:, 1] += sign * float(step) * w * dir_xy[1]
            out.append(DetourCandidate(
                path=cand, label=f"lateral_{name}_{step:g}m", kind="lateral",
                offset_m=sign * float(step), moved_nodes=moved,
            ))
    return out
