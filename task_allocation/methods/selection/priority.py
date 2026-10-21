# -*- coding: utf-8 -*-
"""priority.py — 基础优先级评价与 Top-α 预筛选（论文 3.2–3.3）。

候选规模的完整规则（论文 3.3）：

    n_cand = min(⌈α · K⌉, cap(t))

即"机数名额"与"能力预算可承载数"（:mod:`.capability`）取交；
``hard_cap`` 为 None 时退回纯机数名额（消融对比用）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from task_allocation.common.types import ScoredTask, Task, UAV
from task_allocation.methods.selection.capability import coarse_task_costs


@dataclass
class PriorityWeights:
    w_r: float = 0.30
    w_u: float = 0.25
    w_w: float = 0.15
    w_s: float = 0.15
    w_c: float = 0.15

    def normalized(self) -> PriorityWeights:
        s = self.w_r + self.w_u + self.w_w + self.w_s + self.w_c
        if s <= 0:
            return PriorityWeights()
        return PriorityWeights(
            w_r=self.w_r / s,
            w_u=self.w_u / s,
            w_w=self.w_w / s,
            w_s=self.w_s / s,
            w_c=self.w_c / s,
        )


@dataclass
class PriorityConfig:
    alpha: float = 2.5
    weights: PriorityWeights | None = None
    synergy_radius: float = 500.0
    wait_scale: float = 3600.0
    current_time: float = 0.0

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = PriorityWeights()
        self.weights = self.weights.normalized()


def _minmax_norm(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    vmin = float(np.min(values))
    vmax = float(np.max(values))
    if abs(vmax - vmin) < 1e-12:
        return np.full_like(values, 0.5, dtype=float)
    return (values - vmin) / (vmax - vmin)


def _urgency(task: Task, now: float) -> float:
    if task.latest is None:
        return 0.0
    remain = task.latest - now
    if remain <= 0:
        return 1.0
    span = (task.latest - task.earliest) if task.earliest is not None else max(task.latest, 1.0)
    span = max(span, 1e-6)
    return float(np.clip(1.0 - remain / span, 0.0, 1.0))


def _wait_compensation(task: Task, now: float, scale: float) -> float:
    waited = max(0.0, now - task.arrival_time)
    scale = max(scale, 1e-6)
    return float(1.0 - np.exp(-waited / scale))


def _synergy_gain(task: Task, others: list[Task], radius: float) -> float:
    if radius <= 0 or not others:
        return 0.0
    tx, ty, tz = task.position
    total = 0.0
    weight = 0.0
    for o in others:
        if o.id == task.id:
            continue
        dist = ((o.x - tx) ** 2 + (o.y - ty) ** 2 + (o.z - tz) ** 2) ** 0.5
        if dist <= radius:
            w = 1.0 - dist / radius
            total += w * max(o.reward, 0.0)
            weight += w
    if weight <= 0:
        return 0.0
    return total / weight


def compute_priorities(
    tasks: list[Task],
    uavs: list[UAV],
    config: PriorityConfig | None = None,
    cost_hint: dict[int, float] | None = None,
) -> list[ScoredTask]:
    cfg = config or PriorityConfig()
    w = cfg.weights
    assert w is not None
    if not tasks:
        return []

    R = np.array([max(t.reward, 0.0) for t in tasks], dtype=float)
    U = np.array([_urgency(t, cfg.current_time) for t in tasks], dtype=float)
    W = np.array(
        [_wait_compensation(t, cfg.current_time, cfg.wait_scale) for t in tasks],
        dtype=float,
    )
    G = np.array([_synergy_gain(t, tasks, cfg.synergy_radius) for t in tasks], dtype=float)
    # C_i(t) 与 capability.coarse_task_costs 同口径：cost_hint 优先，否则最近可用 UAV 距离
    C = np.array(coarse_task_costs(tasks, uavs, cost_hint), dtype=float)

    Rn, Un, Wn, Gn, Cn = map(_minmax_norm, (R, U, W, G, C))
    scores = w.w_r * Rn + w.w_u * Un + w.w_w * Wn + w.w_s * Gn - w.w_c * Cn
    out = [ScoredTask(task=tasks[i], priority=float(scores[i])) for i in range(len(tasks))]
    out.sort(key=lambda s: s.priority, reverse=True)
    return out


def top_alpha_preselect(
    scored: list[ScoredTask],
    n_avail: int,
    alpha: float = 2.5,
    hard_cap: int | None = None,
    n_cand_override: int | None = None,
) -> list[ScoredTask]:
    """Top-α 候选预筛选（论文 3.3）。

    候选任务数 ``n_cand = ⌈α·K⌉``，其中 ``K`` 为可用 UAV 数（``n_avail``）；
    ``alpha`` 是**机数倍率**，与任务总数无关。

    Args:
        hard_cap: 能力预算可承载数 cap(t)（:func:`capability.capability_task_cap`）。
            非 None 时候选数取 ``min(⌈α·K⌉, cap(t))``（intersect 口径，
            能力只能收紧）；None 时退回纯机数名额。
        n_cand_override: 直接指定候选数 n_cand，优先级高于 ``alpha`` 与
            ``hard_cap``。供 :func:`capability.effective_cap` 在
            ``mode="primary"`` 下使用，使规模得以**超过**机数名额
            （解决"能力充裕时欠选"）。
    """
    if n_avail <= 0 or not scored:
        return []
    if n_cand_override is not None:
        n_cand = max(1, int(n_cand_override))
    else:
        n_cand = max(1, int(np.ceil(alpha * n_avail)))
        if hard_cap is not None:
            n_cand = max(1, min(n_cand, int(hard_cap)))
    return scored[: min(n_cand, len(scored))]
