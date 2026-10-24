# -*- coding: utf-8 -*-
"""marginal_gain.py — 边际收益增量选择（论文 3.4）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from task_allocation.common.types import ScoredTask, Task, UAV


@dataclass
class MarginalConfig:
    eps: float = 1.0  # 代价地板，与距离同量纲
    max_selected: int | None = None
    max_ratio: float = 2.0
    min_marginal_ratio: float = 0.0
    current_time: float = 0.0
    # Fix① 防饥饿：等待时长对接受判据的软加权（乘性，单位无关）。
    # eff = ratio * (1 + age_gamma * age)，age∈[0,1] 由 waited/wait_scale 得。
    # 0 = 关闭（向后兼容）。
    age_gamma: float = 0.0
    wait_scale: float = 3600.0
    # Fix② 事件/老化强制纳入的任务 id：在终刀获得强优先（forced_bonus），
    # 保证不会被名额或几何代价无限期挤出。
    forced_task_ids: set[int] = field(default_factory=set)
    forced_bonus: float = 1.0e6


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5)


def insert_delta_cost(
    task: Task,
    selected: list[Task],
    uavs: list[UAV],
    pairwise_cost: dict[tuple[int, int], float] | None = None,
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
) -> float:
    """把任务插入当前选择集的最小代价增量。

    ``distance_fn`` 为统一距离口径（DEM 三维航程）时，``pairwise_cost`` 缺项
    的兜底长度也走地形航程，而不是欧氏直线 —— 否则同一条边在「有代价条目」
    和「无代价条目」两种情况下长度不同，边际比不可比。
    """

    def fallback(pi, pj) -> float:
        return float(distance_fn(pi, pj)) if distance_fn is not None else _dist(pi, pj)

    def c_ids(i: int, j: int, pi, pj) -> float:
        if pairwise_cost is not None and (i, j) in pairwise_cost:
            return float(pairwise_cost[(i, j)])
        if pairwise_cost is not None and (j, i) in pairwise_cost:
            return float(pairwise_cost[(j, i)])
        return fallback(pi, pj)

    depots = [u.position for u in uavs if u.is_available]
    if not depots:
        return 1e6
    ti = task.position
    if not selected:
        return min(fallback(d, ti) for d in depots)

    nodes = [(t.id, t.position) for t in selected]
    nearest_depot = min(depots, key=lambda d: fallback(d, ti))
    nodes_with_ends = [(-1, nearest_depot)] + nodes
    best = float("inf")
    for idx in range(len(nodes_with_ends)):
        for jdx in range(idx + 1, len(nodes_with_ends)):
            id_a, pa = nodes_with_ends[idx]
            id_b, pb = nodes_with_ends[jdx]
            cab = (
                c_ids(id_a, id_b, pa, pb)
                if id_a >= 0 and id_b >= 0
                else fallback(pa, pb)
            )
            cai = c_ids(id_a, task.id, pa, ti) if id_a >= 0 else fallback(pa, ti)
            cib = c_ids(task.id, id_b, ti, pb) if id_b >= 0 else fallback(ti, pb)
            best = min(best, cai + cib - cab)
    best = min(best, min(fallback(d, ti) for d in depots))
    return float(max(best, 0.0))


def _delta_benefit(
    task: Task,
    selected: list[Task],
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
) -> float:
    """任务 i 加入当前选择集的边际收益 ΔB_i。

    ``ΔB_i = max(R_i, 0) · (1 + 0.2 · exp(−d_min/500))``：以任务收益为基底，
    乘一个**邻近加权**——与已选集合最近距离 ``d_min`` 越小加成越高（上限
    1.2 倍），与插入式 ΔC 的空间邻近效应同向。两个常数（0.2 加成幅度 /
    500 m 尺度）为实现内定值，与 ``priority.synergy_radius`` 不同源。
    """
    base = max(task.reward, 0.0)
    if not selected:
        return base
    if distance_fn is not None:
        dmin = min(
            float(distance_fn(task.position, s.position)) for s in selected
        )
    else:
        dmin = min(_dist(task.position, s.position) for s in selected)
    proximity = np.exp(-dmin / 500.0) if dmin < 1e6 else 0.0
    return base * (1.0 + 0.2 * float(proximity))


def select_by_marginal_gain(
    candidates: list[ScoredTask],
    uavs: list[UAV],
    config: MarginalConfig | None = None,
    pairwise_cost: dict[tuple[int, int], float] | None = None,
    capability_cap: int | None = None,
    n_sel_override: int | None = None,
    forced_task_ids: set[int] | None = None,
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
) -> list[ScoredTask]:
    """边际收益增量选择（论文 3.4）。

    选择任务数 ``n_sel``：``n_sel_override`` > ``max_selected`` >
    ``⌈max_ratio·K⌉``（K = 可用 UAV 数）；仅在无 ``n_sel_override`` 时才与
    ``capability_cap`` 取交——override 路径的规模合成已在 ``pipeline`` 的
    :func:`capability.effective_cap` 完成。

    Args:
        capability_cap: 能力预算可承载数 cap(t)（:mod:`.capability`）。
            仅在无 ``n_sel_override`` 时生效：选择数上限取
            ``min(⌈max_ratio·K⌉, cap(t))``（intersect 口径）——选择规模
            跟着机队能力走（候选池不受 cap 约束，见 pipeline）。
        n_sel_override: 直接指定选择数上限 n_sel，优先级高于 ``max_selected`` 与
            ``capability_cap``。由 :func:`pipeline.run` 传入
            :func:`capability.effective_cap` 合成后的 n_sel；该路径不再与 cap
            取交，且可**超过**机数名额 ⌈max_ratio·K⌉（mode="primary"）。
        forced_task_ids: 强制纳入的任务 id（Fix②）。非 None 时优先于
            ``config.forced_task_ids``；命中者在终刀获得 ``forced_bonus`` 强优先，
            保证不被名额/几何代价无限期挤出（不停饿兜底）。
        distance_fn: 统一距离口径（DEM 三维航程）。用于 ``pairwise_cost``
            缺项与「最近机/最近邻任务」的兜底长度；None → 欧氏直线。
    """
    cfg = config or MarginalConfig()
    n_avail = sum(1 for u in uavs if u.is_available)
    if n_sel_override is not None:
        n_sel = max(1, int(n_sel_override))
    else:
        n_sel = cfg.max_selected
        if n_sel is None:
            n_sel = (
                max(1, int(np.ceil(cfg.max_ratio * max(n_avail, 1))))
                if n_avail > 0
                else 0
            )
        if capability_cap is not None and n_sel > 0:
            n_sel = max(1, min(n_sel, int(capability_cap)))
    if n_sel <= 0 or not candidates:
        for s in candidates:
            s.selected = False
        return []

    forced = set(forced_task_ids) if forced_task_ids else set(cfg.forced_task_ids)
    remaining = {s.task.id: s for s in candidates}
    selected_tasks: list[Task] = []
    selected_scored: list[ScoredTask] = []

    while remaining and len(selected_tasks) < n_sel:
        best_id = None
        best_eff = float("-inf")
        best_ratio = -1.0
        best_age = 0.0
        best_delta_c = 0.0
        best_delta_b = 0.0
        best_forced = False
        for tid, s in remaining.items():
            db = _delta_benefit(s.task, selected_tasks, distance_fn)
            dc = insert_delta_cost(
                s.task, selected_tasks, uavs, pairwise_cost, distance_fn
            )
            dc_safe = max(float(dc), cfg.eps)
            ratio = db / dc_safe
            # Fix①：等待时长 -> 接受判据的乘性软加权（单位无关、有界）
            age = 0.0
            if cfg.wait_scale and cfg.wait_scale > 0:
                waited = max(
                    0.0, cfg.current_time - float(getattr(s.task, "arrival_time", 0.0) or 0.0)
                )
                age = min(max(1.0 - math.exp(-waited / cfg.wait_scale), 0.0), 1.0)
            is_forced = tid in forced
            # Fix②：强制纳入任务获得强优先，保证不停饿
            eff = ratio * (1.0 + cfg.age_gamma * age) + (
                cfg.forced_bonus if is_forced else 0.0
            )
            if eff > best_eff:
                best_eff = eff
                best_id = tid
                best_ratio = ratio
                best_age = age
                best_delta_c = dc
                best_delta_b = db
                best_forced = is_forced
        # 终止：最佳候选基础边际比过低且非强制 -> 再选无意义
        if best_id is None or (best_ratio < cfg.min_marginal_ratio and not best_forced):
            break
        s = remaining.pop(best_id)
        s.marginal_score = float(best_eff)
        s.approx_delta_cost = float(best_delta_c)
        s.selected = True
        s.task.meta = dict(s.task.meta)
        s.task.meta["delta_benefit"] = best_delta_b
        s.task.meta["age_bonus"] = round(float(cfg.age_gamma * best_age), 4)
        selected_tasks.append(s.task)
        selected_scored.append(s)

    for s in remaining.values():
        s.selected = False
    return selected_scored
