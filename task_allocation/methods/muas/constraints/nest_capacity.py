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

本模块同时提供两种 ``z_ub`` 决策器（设计文档 §7 处理链第 ① 步）：

- :func:`solve_end_nest_assignment`：**方案 A 内层子问题的精确求解器**
  （最小费用流，容量约束下全局最优），默认使用；
- :func:`choose_end_nests`：贪心基线（"最难安置优先"），保留供
  阶段二对比实验（固定终点 / 最近合法巢 / 贪心改派 / 最优分配）使用。
"""

from __future__ import annotations

import math
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
    "solve_end_nest_assignment",
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
    excluded_nest_ids: Iterable[int] | None = None,
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

    # 事件驱动：被标记不可用的机巢（即便 available 偶发未翻转）一律不可选为终点
    excluded = set(excluded_nest_ids) if excluded_nest_ids else set()
    if excluded:
        nest_list = [n for n in nest_list if n.id not in excluded]

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


# ---------------------------------------------------------------------------
# 方案 A 内层子问题：终点机巢分配的精确求解（最小费用流）
# ---------------------------------------------------------------------------


class _MinCostFlow:
    """连续最短路（Bellman-Ford）最小费用流。

    终点机巢分配子问题规模极小（P 个航次 × B 个机巢），朴素实现足够，
    不引入 scipy 依赖。
    """

    def __init__(self, n: int) -> None:
        self._n = n
        # 邻接表元素：[to, cap, cost, rev]
        self._g: list[list[list]] = [[] for _ in range(n)]

    def add_edge(self, fr: int, to: int, cap: int, cost: float) -> None:
        self._g[fr].append([to, cap, cost, len(self._g[to])])
        self._g[to].append([fr, 0, -cost, len(self._g[fr]) - 1])

    def min_cost_flow(self, s: int, t: int, maxf: int) -> tuple[int, float]:
        """从 s 到 t 发送至多 maxf 单位流，返回 ``(实际流量, 总费用)``。"""
        res = 0.0
        flow = 0
        while flow < maxf:
            dist = [float("inf")] * self._n
            prev_v = [-1] * self._n
            prev_e = [-1] * self._n
            dist[s] = 0.0
            updated = True
            while updated:
                updated = False
                for v in range(self._n):
                    if dist[v] == float("inf"):
                        continue
                    for i, e in enumerate(self._g[v]):
                        if e[1] > 0 and dist[e[0]] > dist[v] + e[2] + 1e-12:
                            dist[e[0]] = dist[v] + e[2]
                            prev_v[e[0]] = v
                            prev_e[e[0]] = i
                            updated = True
            if dist[t] == float("inf"):
                break  # 无法再增广（对本问题不应发生，防御性退出）
            d = maxf - flow
            v = t
            while v != s:
                d = min(d, self._g[prev_v[v]][prev_e[v]][1])
                v = prev_v[v]
            v = t
            while v != s:
                e = self._g[prev_v[v]][prev_e[v]]
                e[1] -= d
                self._g[v][e[3]][1] += d
                v = prev_v[v]
            flow += d
            res += d * dist[t]
        return flow, res


def solve_end_nest_assignment(
    uavs: Sequence[UAV],
    nests: Sequence[Nest],
    last_node_cost: Mapping[int, Mapping[int, float]],
    *,
    planning_uav_ids: Iterable[int] | None = None,
    excluded_nest_ids: Iterable[int] | None = None,
    overflow_penalty: float = DEFAULT_CAPACITY_UNIT_PENALTY,
) -> dict[int, int | None]:
    """精确求解「给定任务路线后的终点机巢分配」子问题（方案 A 内层）。

    数学模型（容量口径与 :func:`check_nest_capacity` 完全一致，按周期末归属）::

        min  Σ_u Σ_b c_ub · z_ub
        s.t. Σ_b z_ub = 1          （每个有航次的 UAV 恰好一个终点机巢）
             Σ_u z_ub ≤ Q_b        （末态泊位：起飞让出名额 + 降落占位）
             z_ub = 0              （b 被禁用 / 不可用 / 末节点不可达）

    其中 c_ub 为「该航次末节点（末任务或 UAV 起点）→ 机巢 b」的返航代价。
    起飞让名额：参与规划的 UAV 先从当前归属巢让出泊位，再统一按终点占位——
    与容量判定的 ``occupied_after = now - departures + arrivals`` 口径严格等价。

    实现：最小费用流（源 → 航次节点 → 机巢节点 → 汇）。容量为**软约束**：
    每个机巢除正常泊位边外另有一条溢出边，单位代价 ``overflow_penalty``
    （默认与 :data:`DEFAULT_CAPACITY_UNIT_PENALTY` 同量纲）。因此：

    - 存在容量可行解时，返回值即硬约束下的**全局最优**终点分配；
    - 全部超容时，返回值即「返航代价 + 罚」意义下的最优解，
      由 :func:`check_nest_capacity` 计罚兜底（设计文档 §7 ②）。

    Args:
        uavs:              全部无人机（占用从 ``nest_id`` 推导）。
        nests:             机巢列表（提供 ``capacity`` 与 ``available``）。
        last_node_cost:    ``{uav_id: {nest_id: cost}}``，各航次末节点到机巢的代价。
        planning_uav_ids:  参与本周期规划（有航次）的 UAV；省略取 ``last_node_cost`` 键。
        excluded_nest_ids: 事件驱动禁用的机巢（即便 ``available`` 未翻转也不可选）。
        overflow_penalty:  每超 1 架的惩罚，须与容量判定同量纲。

    Returns:
        ``{uav_id: end_nest_id | None}``；无任何候选代价的 UAV 退化为
        原归属巢（与 :func:`choose_end_nests` 的兜底语义一致）。
    """
    planning = (
        list(planning_uav_ids)
        if planning_uav_ids is not None
        else list(last_node_cost)
    )
    if not planning:
        return {}

    excluded = set(excluded_nest_ids) if excluded_nest_ids else set()
    # 候选机巢：未禁用 + 可用 + 有泊位容量语义（capacity>0），与改派判据一致
    cand_nests = [
        n for n in nests
        if n.id not in excluded and n.available and n.capacity > 0
    ]
    if not cand_nests:
        return {uid: None for uid in planning}

    uav_by_id = {u.id: u for u in uavs}

    # 1) 起飞让出名额（与 choose_end_nests / check_nest_capacity 同口径）
    occ = fleet_occupancy(list(uavs))
    for uid in planning:
        uav = uav_by_id.get(uid)
        if uav is not None and uav.nest_id is not None:
            occ[uav.nest_id] = occ.get(uav.nest_id, 0) - 1

    # 2) 拆分：有可行候选的进优化；无候选的退化为原归属巢（并回占泊位）
    result: dict[int, int | None] = {}
    solvable: list[int] = []
    candidates: dict[int, dict[int, float]] = {}
    nest_ids = {n.id for n in cand_nests}
    for uid in planning:
        costs = {
            b: float(c)
            for b, c in (last_node_cost.get(uid) or {}).items()
            if b in nest_ids and math.isfinite(c)
        }
        if costs:
            solvable.append(uid)
            candidates[uid] = costs
        else:
            uav = uav_by_id.get(uid)
            home = uav.nest_id if uav is not None else None
            result[uid] = home
            if home is not None:
                occ[home] = occ.get(home, 0) + 1  # 原地不动：回占泊位

    if not solvable:
        return result

    # 3) 末态泊位（让出后基线 + 降落占位 ≤ capacity）
    nest_list = sorted(cand_nests, key=lambda n: n.id)
    b_index = {n.id: k for k, n in enumerate(nest_list)}

    p = len(solvable)
    b_count = len(nest_list)
    src, sink = 0, 1 + p + b_count
    mcf = _MinCostFlow(sink + 1)

    for i, uid in enumerate(solvable):
        mcf.add_edge(src, 1 + i, 1, 0.0)
        for b, c in sorted(candidates[uid].items()):
            mcf.add_edge(1 + i, 1 + p + b_index[b], 1, float(c))

    for n in nest_list:
        node = 1 + p + b_index[n.id]
        slots = max(0, n.capacity - occ.get(n.id, 0))
        if slots > 0:
            mcf.add_edge(node, sink, slots, 0.0)
        # 溢出边：容量软化，单位代价与 check_nest_capacity 罚则同量纲
        mcf.add_edge(node, sink, p, float(overflow_penalty))

    mcf.min_cost_flow(src, sink, p)

    # 4) 读出分配（每条 UAV → 机巢满流边即一个 z_ub = 1）
    for i, uid in enumerate(solvable):
        chosen: int | None = None
        for e in mcf._g[1 + i]:
            to, cap, _cost, _rev = e
            if 1 + p <= to < 1 + p + b_count and cap == 0:
                # add_edge 后容量为 0 即已被满流占用（容量 1 的边）
                chosen = nest_list[to - (1 + p)].id
                break
        if chosen is None:
            # 防御性兜底：不应发生（每个可解 UAV 至少一条候选边 + 溢出边）
            chosen = min(candidates[uid].items(), key=lambda kv: kv[1])[0]
        result[uid] = chosen

    return result
