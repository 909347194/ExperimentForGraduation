# -*- coding: utf-8 -*-
"""nest_capacity.py — 机巢容量约束（规划期软约束 / 落地期硬约束共用一份判定）

对应论文 §4.3「机巢容量或可用性约束」，实现依据 ``docs/design_nest_state.md``。

三条设计原则（设计文档 §0）：

    1. 机巢只存静态属性 + 可用性，不存占用计数；
    2. "占用"从 ``uav.nest_id`` 推导，不落第二份状态；
    3. ``nest_id`` 的唯一写点是分配决策 ``z_ub``，起飞 / 降落一律不改。

容量语义（设计文档 §3）：

    occupied_b = |{ u : u.nest_id == b }|      # 含飞行中的、含尚未降落的
    约束：occupied_b ≤ capacity_b              # 恒成立

关键结论：**降落与容量无关**。名额在决策时即预占，否则滚动时域会超卖
（设计文档 §1.2）。因此判定必须按「周期末归属」而非「返回架数」：

    occupied_b_after = occupied_b_now - departures_b + arrivals_b

本模块同时提供 capacity-aware 的终点机巢改派（设计文档 §7 处理链第 ① 步），
供 ``z_ub`` 决策使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from task_allocation.common.types import Nest, UAV

__all__ = [
    "CapacityReport",
    "nest_occupancy",
    "fleet_occupancy",
    "free_slots",
    "check_nest_capacity",
    "choose_end_nests",
]

# 惩罚系数（与 problem.MUASProblemConfig 的 penalty 同量纲：米/代价单位）
DEFAULT_CAPACITY_UNIT_PENALTY = 1.0e4   # 每超 1 架
DEFAULT_UNAVAILABLE_PENALTY = 1.0e4     # 降落到不可用机巢


# ---------------------------------------------------------------------------
# 派生查询：占用数（不存状态，随时从 uav.nest_id 推导）
# ---------------------------------------------------------------------------


def nest_occupancy(uavs: Iterable[UAV], nest_id: int) -> int:
    """归属该机巢的 UAV 数（含飞行中、含未降落）。"""
    return sum(1 for u in uavs if u.nest_id == nest_id)


def fleet_occupancy(uavs: Iterable[UAV]) -> dict[int, int]:
    """各机巢当前归属架数；无归属（``nest_id is None``）的 UAV 不计数。"""
    occ: dict[int, int] = {}
    for u in uavs:
        if u.nest_id is None:
            continue
        occ[u.nest_id] = occ.get(u.nest_id, 0) + 1
    return occ


def free_slots(nests: Iterable[Nest], occupancy: Mapping[int, int]) -> dict[int, int]:
    """各机巢剩余泊位数（可为负，表示已超容）。"""
    return {n.id: n.capacity - occupancy.get(n.id, 0) for n in nests}


# ---------------------------------------------------------------------------
# 容量判定（两道校验共用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapacityReport:
    """容量校验结果。

    attributes:
        per_nest:  {nest_id: {"capacity", "occupied_now", "arrivals",
                    "departures", "occupied_after", "overflow"}}
        violations: 超容或不可用但仍被选为终点的 nest_id
        penalty:    待计入 ``f2`` 的惩罚值
    """

    per_nest: dict[int, dict[str, int]] = field(default_factory=dict)
    violations: list[int] = field(default_factory=list)
    penalty: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, object]:
        return {
            "per_nest": self.per_nest,
            "violations": list(self.violations),
            "penalty": self.penalty,
        }


def check_nest_capacity(
    uavs: Sequence[UAV],
    nests: Sequence[Nest],
    assignments: Mapping[int, int | None],
    *,
    strict: bool = True,
    capacity_unit_penalty: float = DEFAULT_CAPACITY_UNIT_PENALTY,
    unavailable_penalty: float = DEFAULT_UNAVAILABLE_PENALTY,
) -> CapacityReport:
    """按终点机巢分配方案校验容量（设计文档 §6）。

    Args:
        uavs:    **全部**无人机（含 BUSY / FAULT），占用从 ``nest_id`` 推导。
        nests:   机巢列表（提供 ``capacity`` 与 ``available``）。
        assignments: ``{uav_id: end_nest_id}``，即决策变量 ``z_ub`` 的落地形态。
        strict:  为 True 时额外把「机巢不可用」计入 violation。
        capacity_unit_penalty: 每超 1 架的惩罚。
        unavailable_penalty:   降落到不可用机巢的惩罚。

    Returns:
        :class:`CapacityReport`
    """
    uav_list = list(uavs)
    occupied_now = fleet_occupancy(uav_list)
    uav_by_id = {u.id: u for u in uav_list}

    # 统计各巢的 arrivals / departures
    arrivals: dict[int, int] = {}
    departures: dict[int, int] = {}
    for uav_id, end_nest_id in assignments.items():
        uav = uav_by_id.get(uav_id)
        if uav is None:
            continue
        src = uav.nest_id
        if end_nest_id is None:
            # 不返航到任何机巢：视为离开原归属
            if src is not None:
                departures[src] = departures.get(src, 0) + 1
            continue
        if src == end_nest_id:
            continue  # 原地不动，不增不减
        if src is not None:
            departures[src] = departures.get(src, 0) + 1
        arrivals[end_nest_id] = arrivals.get(end_nest_id, 0) + 1

    per_nest: dict[int, dict[str, int]] = {}
    violations: list[int] = []
    penalty = 0.0

    for nest in nests:
        now = occupied_now.get(nest.id, 0)
        arr = arrivals.get(nest.id, 0)
        dep = departures.get(nest.id, 0)
        after = now - dep + arr
        overflow = max(0, after - nest.capacity)
        per_nest[nest.id] = {
            "capacity": int(nest.capacity),
            "occupied_now": int(now),
            "arrivals": int(arr),
            "departures": int(dep),
            "occupied_after": int(after),
            "overflow": int(overflow),
        }
        if overflow > 0:
            violations.append(nest.id)
            penalty += capacity_unit_penalty * overflow
        if strict and not nest.available and (arr > 0 or after > 0 and now > 0):
            # 不可用机巢被选作终点（有新降落）→ 记违反
            if arr > 0 and nest.id not in violations:
                violations.append(nest.id)
            if arr > 0:
                penalty += unavailable_penalty

    return CapacityReport(
        per_nest=per_nest,
        violations=sorted(set(violations)),
        penalty=float(penalty),
    )


# ---------------------------------------------------------------------------
# capacity-aware 终点机巢改派（设计文档 §7 处理链第 ① 步）
# ---------------------------------------------------------------------------


def choose_end_nests(
    uavs: Sequence[UAV],
    nests: Sequence[Nest],
    last_node_cost: Mapping[int, Mapping[int, float]],
    *,
    planning_uav_ids: Iterable[int] | None = None,
) -> dict[int, int | None]:
    """为每架参与规划的 UAV 选终点机巢 ``z_ub``（容量感知的贪心）。

    策略（对应设计文档 §7 ① repair 改派）：

    1. 先让参与规划的 UAV 从当前归属巢「让出」名额（它们即将起飞）；
    2. 按「最难安置优先」（到最近可行巢的代价降序）依次分配，
       每次选代价最小且末态不超容的可用机巢；
    3. 若无任何可行巢（所有巢均满 / 不可用），仍取代价最小的巢，
       由 :func:`check_nest_capacity` 计入 penalty（设计文档 §7 ② 惩罚兜底）。

    Args:
        uavs:             全部无人机（用于推导占用）。
        nests:            机巢列表。
        last_node_cost:   ``{uav_id: {nest_id: cost}}``，从该 UAV 航次
                          最后一个节点（末任务或起点）到机巢的代价。
        planning_uav_ids: 参与本周期规划的 UAV；省略则取 ``last_node_cost`` 的键。

    Returns:
        ``{uav_id: end_nest_id}``；无机巢可选时为 ``None``。
    """
    nest_list = list(nests)
    if not nest_list:
        return {uid: None for uid in (planning_uav_ids or last_node_cost)}

    uav_by_id = {u.id: u for u in uavs}
    planning = list(planning_uav_ids) if planning_uav_ids is not None else list(last_node_cost)

    # 1) 起飞让出名额
    tentative = fleet_occupancy(uavs)
    for uid in planning:
        uav = uav_by_id.get(uid)
        if uav is not None and uav.nest_id is not None:
            tentative[uav.nest_id] = tentative.get(uav.nest_id, 0) - 1

    # 2) 最难安置优先：按「最近可行巢代价」降序
    def best_cost(uid: int) -> float:
        costs = last_node_cost.get(uid) or {}
        if not costs:
            return float("-inf")
        return min(costs.values())

    order = sorted(planning, key=best_cost, reverse=True)

    result: dict[int, int | None] = {}
    for uid in order:
        costs = last_node_cost.get(uid) or {}
        if not costs:
            uav = uav_by_id.get(uid)
            result[uid] = uav.nest_id if uav is not None else None
            continue

        ranked = sorted(costs.items(), key=lambda kv: kv[1])
        chosen: int | None = None
        for nest_id, _cost in ranked:
            nest = next((n for n in nest_list if n.id == nest_id), None)
            if nest is None or not nest.available or nest.capacity <= 0:
                continue
            if tentative.get(nest_id, 0) < nest.capacity:
                chosen = nest_id
                break
        if chosen is None:
            # 全部超容：仍取代价最小的可用巢，交由 penalty 兜底
            for nest_id, _cost in ranked:
                nest = next((n for n in nest_list if n.id == nest_id), None)
                if nest is not None and nest.available:
                    chosen = nest_id
                    break
            if chosen is None:
                chosen = ranked[0][0] if ranked else None

        if chosen is not None:
            tentative[chosen] = tentative.get(chosen, 0) + 1
        result[uid] = chosen

    return result
