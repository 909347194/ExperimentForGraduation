# -*- coding: utf-8 -*-
"""事实回灌：把下层逐段事实折成一张 ``CostUpdate``（实测长度 + 不可飞边）。

职责：从交付解的逐段事实里取两类事实——
      ① 已飞航段的**实测长度**（按同一权重口径重定该边代价）；
      ② **几何上不可飞**的边（写成一个极大数值，由任务分配内部航程检查判死）；
      另附逐机残差给审计（不参与任何决策）。
边界：不校准估计倍率、不留跨周期缓存、不判断"要不要重解"（``LoopDriver`` 只看空单）。
相关：``protocol.CostUpdate``、``task_allocation/muas/cost/fact_cost_view.py``。

**为什么同周期写实测、跨周期不写**：同一冻结快照里，实测长度描述的正是这张矩阵
上的那批边，没有"迁移"这一步，所以它是事实；换个周期任务点就变了，同一批 OD
往往不再出现，"旧边的实测"没有可迁移的对象——那是校准，已被删掉。

**为什么不可飞边只取几何失败码**：``RANGE_EXCEEDED`` / ``ENDURANCE_EXCEEDED`` 是整条
航次的预算判定（换个任务组合就可能成立），记成"这条边不可飞"会误杀可行 OD；
``OPT_FAILED`` 是求解器偶发失败，不是几何事实。白名单见 :data:`INFEASIBLE_CODES`。
"""

from __future__ import annotations

from typing import Mapping

from .protocol import CostUpdate, TourTicket

__all__ = [
    "INFEASIBLE_CODES",
    "build_cost_update",
    "flown_legs_of",
    "infeasible_edges_of",
    "nest_ended_edges_of",
    "per_uav_residuals",
]

#: 可以作为"这条边几何上不可飞"的失败码（码表分级必须为 ``infeasible``）。
INFEASIBLE_CODES: frozenset[str] = frozenset({
    "CORRIDOR_TOO_NARROW",
    "CORRIDOR_OUT_OF_DOMAIN",
    "RRT_NO_PATH",
    "CLEARANCE_VIOLATION",
    "ENDPOINT_OUT_OF_BAND",
})


def _is_infeasible(leg: object) -> bool:
    """一条逐段事实是否构成"这条边几何上不可飞"。"""
    code = getattr(leg, "failure_code", None)
    if code is None or str(getattr(leg, "severity", "") or "") != "infeasible":
        return False
    return str(code) in INFEASIBLE_CODES


def infeasible_edges_of(
    tickets: Mapping[int, TourTicket],
) -> frozenset[tuple[int, int]]:
    """几何不可飞的边（只看白名单码，且分级必须是 ``infeasible``）。"""
    return frozenset(
        (int(leg.from_id), int(leg.to_id))
        for ticket in tickets.values()
        for leg in ticket.leg_costs
        if _is_infeasible(leg)
    )


def flown_legs_of(tickets: Mapping[int, TourTicket]) -> dict[tuple[int, int], float]:
    """已飞航段的实测长度（米）：``(from, to) → length``。

    只收**真的量出了长度**的段（``leg_costs`` 里 ``length_m`` 非空且没有硬失败）；
    同一条边被多机飞出不同长度时取较大者——不许把"实测"压成一条比实际更短的值，
    否则超航程的边会被这层覆盖"救"回来。
    """
    out: dict[tuple[int, int], float] = {}
    for ticket in tickets.values():
        for leg in ticket.leg_costs:
            if leg.length_m is None or _is_infeasible(leg):
                continue
            edge = (int(leg.from_id), int(leg.to_id))
            length = float(leg.length_m)
            if length <= 0.0:
                continue
            out[edge] = max(out.get(edge, 0.0), length)
    return out


def nest_ended_edges_of(
    tickets: Mapping[int, TourTicket],
) -> frozenset[tuple[int, int]]:
    """``facts ∪ infeasible`` 里**终点落在机巢**的那几条（返航边）。

    上下层共用一套整数编号（机巢 0/1/2 与任务 1..30 撞号），所以上层不能靠 id
    数值猜角色；角色由航次结构给出：只有"末任务 → 报上来的终点机巢"这一段是返航边。
    """
    out: set[tuple[int, int]] = set()
    for ticket in tickets.values():
        if ticket.end_nest_id is None or not ticket.task_ids:
            continue
        last = int(ticket.task_ids[-1])
        for leg in ticket.leg_costs:
            if int(leg.from_id) == last and int(leg.to_id) == int(ticket.end_nest_id):
                out.add((last, int(ticket.end_nest_id)))
    return frozenset(out)


def build_cost_update(
    tickets: Mapping[int, TourTicket], *, attempt: int = 0
) -> CostUpdate:
    """构造回灌单：实测长度 + 不可飞边；两类皆空 = 不重解。"""
    facts = flown_legs_of(tickets)
    infeasible = infeasible_edges_of(tickets)
    if not facts and not infeasible:
        return CostUpdate(source_attempt=int(attempt))
    return CostUpdate(
        facts=facts,
        infeasible=infeasible,
        nest_ends=nest_ended_edges_of(tickets) & (set(facts) | set(infeasible)),
        source_attempt=int(attempt),
    )


def per_uav_residuals(
    tickets: Mapping[int, TourTicket], est_cost: Mapping[int, float]
) -> dict[int, float]:
    """逐机残差 = 实测航程 − 上层估计（米），**仅供审计**。

    失败航次记 0：它没有"飞了多远"，只有"断在哪"；把失败当 0 会让残差系统性偏负。
    没有任何决策读这个数——它只进 ``ResolveAttempt.residuals`` 供事后核对
    "上层这把尺子偏了多少"。
    """
    out: dict[int, float] = {}
    for uid, ticket in tickets.items():
        real = 0.0
        for leg in ticket.leg_costs:
            if leg.length_m is not None and leg.time_source != "failed":
                real += float(leg.length_m)
        est = float(est_cost.get(int(uid), 0.0) or 0.0)
        out[int(uid)] = 0.0 if not ticket.ok else float(real - est)
    return out
