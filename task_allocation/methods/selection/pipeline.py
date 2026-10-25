# -*- coding: utf-8 -*-
"""pipeline.py — 动态任务选择流水线。

顺序：基础优先级 → 能力上界计算 cap(t) → 候选预筛选（与 cap 解耦）→ [事件驱动 / 防饥饿强制纳入]
      → 初步可行性过滤 → 边际收益选择（n_sel 卡 cap）→ 候选集检查 → 淘汰原因诊断

规模决策原则（候选/选择**解耦**，论文 3.3 修订）：

  * 候选池 n_cand（机制3 搜索输入）**该富**：n_cand = ⌈α·K⌉，只受计算预算
    ``capability.max_candidate_pool`` 与任务总数 N 约束；**cap(t) 既不收紧也不
    扩张**。富池缓解排序误差、给边际增益发现协同留空间；机队越紧张、cap(t) 越小，
    相对富余越大（纠正旧版"紧张时 n_cand 塌成 cap"的倒挂）。cap 对候选的唯一
    耦合是可行性下限 n_cand ≥ n_sel（见 pipeline 实现）。
  * 选择数 n_sel（真实承诺）**该紧**：n_sel = 合成(⌈max_ratio·K⌉, cap(t))，
    合成方式由 :mod:`.capability` 的 ``mode`` 决定、且**只作用于 n_sel**：
        intersect（默认）：min{名额, cap(t)} —— 能力只能收紧，绝不超派；
        primary：以 cap(t) 为主定规模、名额降为兜底（防 c̃ 过小失控）。
    这里才是能力约束真正该卡住的地方。

符号口径（全文统一）：**K** = 可用 UAV 数，**N** = 任务数，
**n_cand** = 候选任务数，**n_sel** = 选择任务数，**cap(t)** = 能力承载数。
名额定义：n_cand = ⌈α·K⌉（能力无关）；n_sel = ⌈max_ratio·K⌉（或 ``max_selected``）。
cap(t) 已是吞吐上界（⌊Σ remaining_range / 单任务中位代价⌋，见 :mod:`.capability`），
**仅约束 n_sel**；``capability.fallback_when_disabled=True`` 时即便关闭能力模块
也按此上界收敛（Fix③）。
注意：事件驱动（``forced_task_ids``）与防饥饿（:class:`AntiStarvationConfig`）的
强制纳入都会**绕过**上述规模上界追加候选，并在终刀获强优先（Fix②，保证不停饿）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

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
class AntiStarvationConfig:
    """硬性不停饿兜底（Fix②）。

    即便优先级/边际选择因名额或几何代价把某任务反复挤出，只要它等待超过
    ``force_after_seconds`` 或逼近 ``latest`` 时间窗（剩余 ≤ ``deadline_slack``），
    就强制纳入候选集（绕过 n_cand 预筛）并在终刀获得强优先，保证不会被无限期推迟。

    ``enabled`` 默认 ``False`` —— 仅显式开启时改变选择行为，保持既有实验/测试基线。
    """

    enabled: bool = False
    force_after_seconds: float = 1200.0
    deadline_slack: float = 300.0


@dataclass
class SelectionConfig:
    priority: PriorityConfig = field(default_factory=PriorityConfig)
    feasibility: FeasibilityConfig = field(default_factory=FeasibilityConfig)
    marginal: MarginalConfig = field(default_factory=MarginalConfig)
    # 能力驱动规模：仅约束选择数 n_sel = 合成(⌈max_ratio·K⌉, cap(t))；
    # 候选池 n_cand 已与 cap(t) 解耦（见 pipeline 顶部说明）。
    # enabled=False 时 n_sel 退回纯机数名额（消融对比）。
    capability: CapabilityScaleConfig = field(default_factory=CapabilityScaleConfig)
    # 硬性不停饿兜底（Fix②）：老化/临期任务强制纳入，保证不无限期推迟。
    anti_starvation: AntiStarvationConfig = field(default_factory=AntiStarvationConfig)
    current_time: float = 0.0
    # 事件驱动强制纳入：紧急 / 故障释放的任务绕过 n_cand 预筛（仍过可行性过滤）
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
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
    nests: Sequence[Any] | None = None,
    max_range: float | None = None,
) -> SelectionResult:
    """运行选择流水线。

    Args:
        distance_fn: 统一距离口径（DEM 三维航程），由滚动层从代价估算器注入。
            贯穿预筛（最近可用机）、边际收益（插入代价/空间邻近兜底）与候选
            集预算检查，保证第一阶段与第二阶段用同一把尺子量距离。
        nests / max_range: 几何可达性判据（与 ``static_unreachable`` 同源的
            「最近机巢往返 ≤ 满电航程」）。透传给预筛，把往返超限分两类归因：
            ``exceed_round_trip_range``（真几何不可达）与
            ``no_uav_in_range``（几何可达但当前无可用机——机队漂移空巢）。
    """
    cfg = config or SelectionConfig()
    n_avail = sum(1 for u in uavs if u.is_available)
    diagnostics: dict[str, Any] = {
        "n_tasks_in": len(tasks),
        "n_uav_avail": n_avail,
        "current_time": cfg.current_time,
    }

    # Fix②：老化 / 临期任务强制纳入集合（与事件驱动的 forced_task_ids 取并），
    # 绕过 n_cand 预筛，并在终刀获得强优先，保证不会无限期推迟。
    forced_ids = set(cfg.forced_task_ids)
    if cfg.anti_starvation.enabled:
        asv = cfg.anti_starvation
        for t in tasks:
            waited = max(0.0, cfg.current_time - float(getattr(t, "arrival_time", 0.0) or 0.0))
            deadline_risk = (t.latest is not None) and (
                float(t.latest) - cfg.current_time <= asv.deadline_slack
            )
            if waited >= asv.force_after_seconds or deadline_risk:
                forced_ids.add(t.id)
    # 用**局部副本**承载本周期的强制集与 wait_scale，绝不就地写 cfg.marginal：
    # horizon 每周期以共享的 ``sel_cfg.marginal`` 构造 SelectionConfig（见
    # horizon.py:376），就地写入会让 forced_task_ids 跨周期累积 —— 上周期被强制
    # 过的任务永久保持强制（即便已完成、或本已不再满足老化/临期判据），使防饥饿
    # 退化为「全员强制」，并泄漏到 anti_starvation 关闭的消融路径上。
    # 滚动时域下每周期应独立重算，故此处必须按周期隔离状态。
    marginal = replace(
        cfg.marginal,
        forced_task_ids=forced_ids,
        wait_scale=cfg.priority.wait_scale,
    )

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
    elif cfg.capability.fallback_when_disabled and n_avail > 0:
        # Fix③：能力模块关闭时，仍按吞吐上界收敛候选规模，
        # 避免 "2.5×机数" 永远不瓶颈、aging 排序失效。
        costs = coarse_task_costs(tasks, uavs, cost_hint)
        budget = fleet_range_budget(uavs, 1.0)
        capability_cap = capability_task_cap(
            costs, budget, CapabilityScaleConfig(quantile=0.5, min_cap=1)
        )
        diagnostics["capability_budget"] = round(budget, 3)
        diagnostics["capability_cap"] = capability_cap
        diagnostics["capability_fallback"] = True
    diagnostics["capability_enabled"] = cfg.capability.enabled

    # ── 选择数 n_sel（真实承诺，该紧）：cap(t) 在此真正卡住 ───────────────
    # intersect（默认）：min{名额, cap(t)}，能力只能收紧、绝不超派；
    # primary：cap(t) 可超过机数名额（宽松承诺）。两种 mode 见 capability.effective_cap。
    if marginal.max_selected is not None:
        marginal_quota = int(marginal.max_selected)
    else:
        marginal_quota = (
            max(1, int(math.ceil(marginal.max_ratio * n_avail))) if n_avail > 0 else 0
        )
    n_sel = effective_cap(marginal_quota, capability_cap, cfg.capability)
    diagnostics["capability_n_sel"] = n_sel

    # ── 候选池 n_cand（机制3 搜索输入，该富）：与 cap(t) 解耦 ─────────────
    # n_cand = ⌈α·K⌉，只受计算预算上限 max_candidate_pool 与任务总数 N 约束；
    # cap(t) 不收紧也不扩张。唯一的 cap 耦合是可行性下限 n_cand ≥ n_sel
    # （primary 模式 cap 超过机数名额时触发），保证候选池装得下承诺数；
    # 默认 intersect 模式 n_sel ≤ ⌈max_ratio·K⌉ < ⌈αK⌉，下限自然满足，cap
    # 完全不触碰候选——纠正了旧版"机队紧张时 n_cand 塌成 cap"的倒挂。
    alpha_quota = (
        max(1, int(math.ceil(cfg.priority.alpha * n_avail))) if n_avail > 0 else 0
    )
    n_cand = alpha_quota
    if n_avail > 0:
        n_cand = max(n_cand, n_sel)
    if cfg.capability.max_candidate_pool is not None:
        n_cand = min(n_cand, int(cfg.capability.max_candidate_pool))
    n_cand = min(n_cand, len(scored_all))
    diagnostics["capability_n_cand"] = n_cand
    diagnostics["candidate_cap_independent"] = capability_cap is None or n_cand >= alpha_quota

    preselected = top_alpha_preselect(
        scored_all,
        n_avail=n_avail,
        alpha=cfg.priority.alpha,
        hard_cap=None,
        n_cand_override=n_cand,
    )

    # 事件驱动 + 防饥饿强制纳入（绕过 Top-αK 预筛上限）；终刀里再获强优先
    if forced_ids:
        pre_ids = {s.task.id for s in preselected}
        forced_in = 0
        for s in scored_all:
            if s.task.id in forced_ids and s.task.id not in pre_ids:
                preselected.append(s)
                pre_ids.add(s.task.id)
                forced_in += 1
        diagnostics["n_forced_in"] = forced_in

    diagnostics["n_after_priority"] = len(preselected)

    pre_ids = {s.task.id for s in preselected}
    for s in scored_all:
        if s.task.id not in pre_ids and s.filter_reason is None:
            s.filter_reason = "not_in_n_cand"

    passed, rejected_pre = prefilter(
        preselected,
        uavs,
        cfg.feasibility,
        cost_hint=cost_hint,
        distance_fn=distance_fn,
        nests=nests,
        max_range=max_range,
    )
    diagnostics["n_after_prefilter"] = len(passed)
    diagnostics["n_rejected_prefilter"] = len(rejected_pre)

    # n_sel 已在候选阶段前计算（pipeline 顶部）：cap(t) 是唯一能力卡口，
    # 候选池 n_cand 仅受可行性下限 n_cand ≥ n_sel 抬升，详见上方注释。

    chosen = select_by_marginal_gain(
        passed,
        uavs,
        marginal,
        pairwise_cost=pairwise_cost,
        capability_cap=capability_cap,
        n_sel_override=n_sel,
        distance_fn=distance_fn,
    )
    diagnostics["n_after_marginal"] = len(chosen)

    final_kept, dropped_post = postcheck(
        chosen, uavs, cfg.feasibility, cost_hint=cost_hint, distance_fn=distance_fn
    )
    diagnostics["n_selected"] = len(final_kept)
    diagnostics["n_dropped_postcheck"] = len(dropped_post)

    # 淘汰原因直方图：用于把「物理上不可能完成」与「算法主动放弃」分开统计。
    # 全域撒点后会出现一批 exceed_round_trip_range 的结构性不可达任务
    # （往返航程超限，静态判定后由 horizon 标记 UNREACHABLE 出池），
    # 完成率下降必须能被这张表解释清楚，否则会被质疑数据合理性。
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
