# -*- coding: utf-8 -*-
"""feasibility.py — 快速可行性检查（论文 3.5）。

1. prefilter — 初步过滤（候选预筛选之后、边际选择之前）
2. postcheck — 候选集粗预算检查（边际选择之后）

行进代价口径（两套几何合一）：``travel = cost_hint[t.id]``（三维地理代价，
与基础优先级 C_i(t)、ΔC 插入式估计、postcheck 预算同源）优先，缺省回退
欧氏距离。**航程检查与时间窗 ETA 共用同一 travel**——此前 ETA 用欧氏
距离/速度、其余环节用三维代价，山区里 ETA 系统性低估，会放进必然赶不上
时间窗的任务，徒增完成闸门的违约回池。
"""

from __future__ import annotations

from dataclasses import dataclass

from task_allocation.common.types import ScoredTask, Task, TaskStatus, UAV


@dataclass
class FeasibilityConfig:
    range_margin: float = 0.95
    check_time_window: bool = True
    budget_factor: float = 1.0
    current_time: float = 0.0


def _nearest_avail_uav(task: Task, uavs: list[UAV]) -> tuple[UAV | None, float]:
    avail = [u for u in uavs if u.is_available]
    if not avail:
        return None, float("inf")
    best_u = None
    best_d = float("inf")
    for u in avail:
        d = ((u.x - task.x) ** 2 + (u.y - task.y) ** 2 + (u.z - task.z) ** 2) ** 0.5
        if d < best_d:
            best_d = d
            best_u = u
    return best_u, best_d


def prefilter(
    scored: list[ScoredTask],
    uavs: list[UAV],
    config: FeasibilityConfig | None = None,
    cost_hint: dict[int, float] | None = None,
) -> tuple[list[ScoredTask], list[ScoredTask]]:
    """初步可行性过滤。

    Args:
        cost_hint: ``task_id -> 三维行进代价``（与优先级 C_i(t) 同源）。
            非 None 时航程检查与时间窗 ETA 均以其为 travel；缺省回退欧氏距离。
    """
    cfg = config or FeasibilityConfig()
    passed: list[ScoredTask] = []
    rejected: list[ScoredTask] = []
    avail = [u for u in uavs if u.is_available]
    if not avail:
        for s in scored:
            s.filter_reason = "no_available_uav"
            rejected.append(s)
        return passed, rejected

    for s in scored:
        t = s.task
        reason: str | None = None
        if t.status in (TaskStatus.EXPIRED, TaskStatus.UNREACHABLE, TaskStatus.DONE):
            reason = f"status_{t.status.value}"
        else:
            u, dist = _nearest_avail_uav(t, uavs)
            # 行进代价统一口径：三维代价优先、欧氏回退；航程与时间窗同用 travel
            travel = (
                float(cost_hint[t.id]) if (cost_hint and t.id in cost_hint) else dist
            )
            if u is None:
                reason = "no_available_uav"
            elif travel > cfg.range_margin * max(u.remaining_range, 0.0):
                reason = "exceed_remaining_range"
            elif cfg.check_time_window and t.latest is not None:
                eta = travel / max(u.speed, 1e-6)
                if cfg.current_time + eta > t.latest:
                    reason = "miss_time_window"
        if reason is not None:
            s.filter_reason = reason
            rejected.append(s)
        else:
            s.filter_reason = None
            passed.append(s)
    return passed, rejected


def postcheck(
    candidate: list[ScoredTask],
    uavs: list[UAV],
    config: FeasibilityConfig | None = None,
    cost_hint: dict[int, float] | None = None,
) -> tuple[list[ScoredTask], list[ScoredTask]]:
    cfg = config or FeasibilityConfig()
    if not candidate:
        return [], []
    avail = [u for u in uavs if u.is_available]
    budget = cfg.budget_factor * sum(max(u.remaining_range, 0.0) for u in avail)

    def approx_cost(s: ScoredTask) -> float:
        if cost_hint and s.task.id in cost_hint:
            return float(cost_hint[s.task.id])
        if s.approx_delta_cost > 0:
            return s.approx_delta_cost
        _, d = _nearest_avail_uav(s.task, uavs)
        return d

    ordered = sorted(
        candidate,
        key=lambda s: (s.marginal_score if s.marginal_score else s.priority),
        reverse=True,
    )
    kept: list[ScoredTask] = []
    dropped: list[ScoredTask] = []
    acc = 0.0
    for s in ordered:
        c = approx_cost(s)
        if budget <= 0 or acc + c <= budget + 1e-9:
            acc += c
            kept.append(s)
        else:
            s.filter_reason = "exceed_fleet_budget"
            dropped.append(s)
    kept_ids = {s.task.id for s in kept}
    kept_stable = [s for s in candidate if s.task.id in kept_ids]
    return kept_stable, dropped
