# -*- coding: utf-8 -*-
"""marginal_gain.py — 边际收益增量选择（论文 3.2.2）。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from task_allocation.common.types import ScoredTask, Task, UAV


@dataclass
class MarginalConfig:
    eps: float = 1.0  # 代价地板，与距离同量纲
    max_selected: int | None = None
    max_ratio: float = 2.0
    min_marginal_ratio: float = 0.0
    current_time: float = 0.0


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5)


def insert_delta_cost(
    task: Task,
    selected: list[Task],
    uavs: list[UAV],
    pairwise_cost: dict[tuple[int, int], float] | None = None,
) -> float:
    def c_ids(i: int, j: int, pi, pj) -> float:
        if pairwise_cost is not None and (i, j) in pairwise_cost:
            return float(pairwise_cost[(i, j)])
        if pairwise_cost is not None and (j, i) in pairwise_cost:
            return float(pairwise_cost[(j, i)])
        return _dist(pi, pj)

    depots = [u.position for u in uavs if u.is_available]
    if not depots:
        return 1e6
    ti = task.position
    if not selected:
        return min(_dist(d, ti) for d in depots)

    nodes = [(t.id, t.position) for t in selected]
    nearest_depot = min(depots, key=lambda d: _dist(d, ti))
    nodes_with_ends = [(-1, nearest_depot)] + nodes
    best = float("inf")
    for idx in range(len(nodes_with_ends)):
        for jdx in range(idx + 1, len(nodes_with_ends)):
            id_a, pa = nodes_with_ends[idx]
            id_b, pb = nodes_with_ends[jdx]
            cab = c_ids(id_a, id_b, pa, pb) if id_a >= 0 and id_b >= 0 else _dist(pa, pb)
            cai = c_ids(id_a, task.id, pa, ti) if id_a >= 0 else _dist(pa, ti)
            cib = c_ids(task.id, id_b, ti, pb) if id_b >= 0 else _dist(ti, pb)
            best = min(best, cai + cib - cab)
    best = min(best, min(_dist(d, ti) for d in depots))
    return float(max(best, 0.0))


def _delta_benefit(task: Task, selected: list[Task]) -> float:
    base = max(task.reward, 0.0)
    if not selected:
        return base
    dmin = min(_dist(task.position, s.position) for s in selected)
    proximity = np.exp(-dmin / 500.0) if dmin < 1e6 else 0.0
    return base * (1.0 + 0.2 * float(proximity))


def select_by_marginal_gain(
    candidates: list[ScoredTask],
    uavs: list[UAV],
    config: MarginalConfig | None = None,
    pairwise_cost: dict[tuple[int, int], float] | None = None,
) -> list[ScoredTask]:
    cfg = config or MarginalConfig()
    n_avail = sum(1 for u in uavs if u.is_available)
    limit = cfg.max_selected
    if limit is None:
        limit = max(1, int(np.ceil(cfg.max_ratio * max(n_avail, 1)))) if n_avail > 0 else 0
    if limit <= 0 or not candidates:
        for s in candidates:
            s.selected = False
        return []

    remaining = {s.task.id: s for s in candidates}
    selected_tasks: list[Task] = []
    selected_scored: list[ScoredTask] = []

    while remaining and len(selected_tasks) < limit:
        best_id = None
        best_ratio = -1.0
        best_delta_c = 0.0
        best_delta_b = 0.0
        for tid, s in remaining.items():
            db = _delta_benefit(s.task, selected_tasks)
            dc = insert_delta_cost(s.task, selected_tasks, uavs, pairwise_cost)
            dc_safe = max(float(dc), cfg.eps)
            ratio = db / dc_safe
            if ratio > best_ratio:
                best_ratio = ratio
                best_id = tid
                best_delta_c = dc
                best_delta_b = db
        if best_id is None or best_ratio < cfg.min_marginal_ratio:
            break
        s = remaining.pop(best_id)
        s.marginal_score = float(best_ratio)
        s.approx_delta_cost = float(best_delta_c)
        s.selected = True
        s.task.meta = dict(s.task.meta)
        s.task.meta["delta_benefit"] = best_delta_b
        selected_tasks.append(s.task)
        selected_scored.append(s)

    for s in remaining.values():
        s.selected = False
    return selected_scored
