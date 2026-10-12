# -*- coding: utf-8 -*-
"""pipeline.py — 动态任务选择流水线。

顺序：基础优先级 → 能力上界计算 cap(t) → 候选预筛选 → [事件驱动强制纳入]
      → 初步可行性过滤 → 边际收益选择 → 候选集检查 → 淘汰原因诊断

规模决策原则：候选/选择规模 = 合成(机数名额, 机队能力预算可承载数 cap(t))，
合成方式由 :mod:`.capability` 的 ``mode`` 决定（:func:`.capability.effective_cap`）：

    intersect（默认）：min{名额, cap(t)} —— 能力只能**收紧**，绝不超名额。
        航程紧张时按 cap(t) 提前收编；航程充裕时仍以机数名额为硬天花板，
        "欠选"问题依旧存在。
    primary：以 cap(t) 为主定规模、名额降为兜底（可按 max_expand_factor 封顶）。
        紧张时收编、充裕时允许**超名额**多选，双向失真同时修正。

名额定义：候选数 = ⌈α·K_avail⌉；选择数 = ⌈max_ratio·K_avail⌉（或 max_selected）。
注意：事件驱动强制纳入（``forced_task_ids``）会**绕过**上述规模上界追加候选。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from task_allocation.common.types import SelectionResult, Task, UAV
from task_allocation.methods.selection.capability import (
    CapabilityScaleConfig,
    capability_task_cap,
    coarse_task_costs,
    effective_cap,
    fleet_range_budget,
)
from task_allocation.methods.selection.feasibility import (
    FeasibilityConfig,
    postcheck,
    prefilter,
)
from task_allocation.methods.selection.marginal_gain import (
    MarginalConfig,
    select_by_marginal_gain,
)
from task_allocation.methods.selection.priority import (
    PriorityConfig,
    compute_priorities,
    top_alpha_preselect,
)


@dataclass
class SelectionConfig:
    priority: PriorityConfig = field(default_factory=PriorityConfig)
    feasibility: FeasibilityConfig = field(default_factory=FeasibilityConfig)
    marginal: MarginalConfig = field(default_factory=MarginalConfig)
    # 能力驱动规模：候选/选择数 = min(机数名额, 能力预算可承载数)。
    # enabled=False 时退回纯机数名额（消融对比）。
    capability: CapabilityScaleConfig = field(default_factory=CapabilityScaleConfig)
    current_time: float = 0.0
    # 事件驱动强制纳入：紧急 / 故障释放的任务绕过 Top-αK 预筛（仍过可行性过滤）
    forced_task_ids: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.priority.current_time = self.current_time
        self.feasibility.current_time = self.current_time
        self.marginal.current_time = self.current_time


def run(
    tasks: list[Task],
    uavs: list[UAV],
    config: SelectionConfig | None = None,
    cost_hint: dict[int, float] | None = None,
    pairwise_cost: dict[tuple[int, int], float] | None = None,
) -> SelectionResult:
    cfg = config or SelectionConfig()
    n_avail = sum(1 for u in uavs if u.is_available)
    diagnostics: dict[str, Any] = {
        "n_tasks_in": len(tasks),
        "n_uav_avail": n_avail,
        "current_time": cfg.current_time,
    }

    scored_all = compute_priorities(tasks, uavs, cfg.priority, cost_hint=cost_hint)

    # 能力驱动规模：B(t) 与粗代价分位 → cap(t)，同时约束候选数与选择数
    capability_cap: int | None = None
    if cfg.capability.enabled and n_avail > 0:
        budget = fleet_range_budget(
            uavs,
            cfg.feasibility.budget_factor * cfg.capability.budget_scale,
            sortie_budget=cfg.capability.sortie_budget,
            max_participating_uavs=cfg.capability.max_participating_uavs,
        )
        costs = coarse_task_costs(tasks, uavs, cost_hint)
        capability_cap = capability_task_cap(costs, budget, cfg.capability)
        diagnostics["capability_budget"] = round(budget, 3)
        diagnostics["capability_cap"] = capability_cap
    diagnostics["capability_enabled"] = cfg.capability.enabled

    # 候选数 = 合成(机数名额 ⌈α·K⌉, 能力上界 cap(t))；两种 mode 见 capability.effective_cap
    alpha_quota = (
        max(1, int(math.ceil(cfg.priority.alpha * n_avail))) if n_avail > 0 else 0
    )
    preselect_k = effective_cap(alpha_quota, capability_cap, cfg.capability)
    diagnostics["capability_preselect_k"] = preselect_k

    preselected = top_alpha_preselect(
        scored_all,
        n_avail_uavs=n_avail,
        alpha=cfg.priority.alpha,
        hard_cap=capability_cap,
        k_override=preselect_k,
    )

    # 事件驱动重规划：强制纳入紧急 / 故障释放任务（绕过 Top-αK 预筛上限）
    if cfg.forced_task_ids:
        pre_ids = {s.task.id for s in preselected}
        forced_in = 0
        for s in scored_all:
            if s.task.id in cfg.forced_task_ids and s.task.id not in pre_ids:
                preselected.append(s)
                pre_ids.add(s.task.id)
                forced_in += 1
        diagnostics["n_forced_in"] = forced_in

    diagnostics["n_after_priority"] = len(preselected)

    pre_ids = {s.task.id for s in preselected}
    for s in scored_all:
        if s.task.id not in pre_ids and s.filter_reason is None:
            s.filter_reason = "not_in_top_alpha"

    passed, rejected_pre = prefilter(
        preselected, uavs, cfg.feasibility, cost_hint=cost_hint
    )
    diagnostics["n_after_prefilter"] = len(passed)
    diagnostics["n_rejected_prefilter"] = len(rejected_pre)

    # 选择数 = 合成(机数名额 max_selected 或 ⌈max_ratio·K⌉, 能力上界 cap(t))
    if cfg.marginal.max_selected is not None:
        marginal_quota = int(cfg.marginal.max_selected)
    else:
        marginal_quota = (
            max(1, int(math.ceil(cfg.marginal.max_ratio * n_avail))) if n_avail > 0 else 0
        )
    marginal_limit = effective_cap(marginal_quota, capability_cap, cfg.capability)
    diagnostics["capability_marginal_limit"] = marginal_limit

    chosen = select_by_marginal_gain(
        passed,
        uavs,
        cfg.marginal,
        pairwise_cost=pairwise_cost,
        capability_cap=capability_cap,
        limit_override=marginal_limit,
    )
    diagnostics["n_after_marginal"] = len(chosen)

    final_kept, dropped_post = postcheck(chosen, uavs, cfg.feasibility, cost_hint=cost_hint)
    diagnostics["n_selected"] = len(final_kept)
    diagnostics["n_dropped_postcheck"] = len(dropped_post)

    # 淘汰原因直方图：用于把「物理上不可能完成」与「算法主动放弃」分开统计。
    # 全域撒点后会出现一批 miss_time_window / exceed_remaining_range 的结构性
    # 不可达任务，完成率下降必须能被这张表解释清楚，否则会被质疑数据合理性。
    rejected_reasons: dict[str, int] = {}
    for s in scored_all:
        if s.filter_reason:
            rejected_reasons[s.filter_reason] = rejected_reasons.get(s.filter_reason, 0) + 1
    if rejected_reasons:
        diagnostics["filter_reasons"] = dict(sorted(rejected_reasons.items()))

    selected_tasks = [s.task for s in final_kept]
    for s in final_kept:
        s.selected = True

    by_id = {s.task.id: s for s in scored_all}
    for s in final_kept + dropped_post + rejected_pre + chosen:
        if s.task.id in by_id:
            base = by_id[s.task.id]
            base.priority = s.priority or base.priority
            base.marginal_score = s.marginal_score or base.marginal_score
            base.approx_delta_cost = s.approx_delta_cost or base.approx_delta_cost
            base.filter_reason = s.filter_reason
            base.selected = s.selected

    return SelectionResult(
        selected=selected_tasks,
        scored=list(by_id.values()),
        diagnostics=diagnostics,
    )
