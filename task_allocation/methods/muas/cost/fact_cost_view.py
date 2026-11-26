# -*- coding: utf-8 -*-
r"""事实代价视图：把下层逐段事实**写成数值**叠在惰性 pairwise 表上。

只有一个接口——读出来的是代价数，不再有"大 M 集合 + 可行性闸门"第二套语义：

1. **已飞航段**：按实测长度、与底表同一权重口径重定该边代价
   \(c_{ij}=\ell^{real}_{ij}\cdot w_j\)。同一冻结快照、同一批 OD，所以这是事实，
   不是跨周期校准——下一周期清空。
2. **几何不可飞航段**：写入 :data:`INFEASIBLE_COST`（\(10^7\) m 量级）。它同样只是
   "一个数"：判死交给任务分配内部已有的航程检查——含该边的航次必然超出任何机型的
   航程，``tour.feasible=False``，任务回池。不另设可行性闸门、不另存一份边集合。

**为什么是 1e7 而不是 ``inf``**：DE 适应度对候选求和，``inf − inf`` 变 ``NaN``
会污染整个种群；\(10^7\) m 已远超任何可飞航程，且在 float64 内不溢出。

**为什么不跨周期保留**：任务点逐周期变化，同一条边几乎不重复出现；把上一批实测写成
持久覆盖，只会在新任务点上冒充"已知值"。周期末 :meth:`FactCostView.reset` 清空。
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator, Mapping, MutableMapping

__all__ = ["INFEASIBLE_COST", "FactCostView"]

#: 不可飞航段的实现值（米）。语义 = "超出任何航程"，由内部航程检查判死。
INFEASIBLE_COST: float = 1.0e7

Edge = tuple[int, int]


class FactCostView(MutableMapping):
    r"""``base`` + 实测覆盖 + 不可飞边（全部是数值）的活视图，可直接当 pairwise 表用。

    Args:
        base:           底表（通常是 :class:`~muas.cost.lazy_pairwise.LazyPairwiseCost`）
        infeasible_cost: 不可飞边的实现值

    **长度 → 代价的口径**：底表代数为 \(c=\ell\cdot w_j\)（见
    ``vertical_section.py``）。``w_j`` 取**目标节点**的权重，与底表内部
    ``weight_of`` 同一函数；底表没有该函数时按 1.0（无收益加权）。

    **写回语义**：``cost_matrix.build_pairwise_costs`` 会把算出的边写回
    ``pairwise[(a,b)] = c``。底表能算的键直接丢弃这次写入（值本来就由本视图决定），
    只有底表算不出的键（缺位置）才落 ``_extra``。
    """

    def __init__(
        self,
        base: Mapping[Edge, float],
        *,
        infeasible_cost: float = INFEASIBLE_COST,
    ) -> None:
        if infeasible_cost <= 0.0:
            raise ValueError(f"不可飞边的实现值必须为正：{infeasible_cost}")
        self._base = base
        self._infeasible_cost = float(infeasible_cost)
        self._facts: dict[Edge, float] = {}
        self._infeasible: frozenset[Edge] = frozenset()
        self._extra: dict[Edge, float] = {}

    # -- 状态 -----------------------------------------------------------
    @property
    def base(self) -> Mapping[Edge, float]:
        return self._base

    @property
    def facts(self) -> Mapping[Edge, float]:
        """已写入的实测代价（边 → 代价，上层键空间）。"""
        return self._facts

    @property
    def infeasible(self) -> frozenset[Edge]:
        return self._infeasible

    def cost_of_length(self, edge: Edge, length_m: float) -> float:
        """实测长度 → 该边代价（同一权重口径）。"""
        weight_fn: Callable[[int], float] | None = getattr(self._base, "weight_of", None)
        weight = float(weight_fn(int(edge[1]))) if callable(weight_fn) else 1.0
        return float(length_m) * weight

    def apply_facts(
        self,
        facts: Mapping[Edge, float],
        infeasible: Iterable[Edge] = (),
    ) -> None:
        """写入一批事实：``facts`` 是**实测长度（米）**，``infeasible`` 是不可飞边。"""
        for edge, length_m in facts.items():
            key = (int(edge[0]), int(edge[1]))
            self._facts[key] = self.cost_of_length(key, float(length_m))
        if infeasible:
            self._infeasible = self._infeasible | {
                (int(a), int(b)) for a, b in infeasible
            }

    def reset(self) -> None:
        """周期末清空（周期作用域的唯一清理入口）。"""
        self._facts.clear()
        self._infeasible = frozenset()
        self._extra.clear()

    def as_dict(self) -> dict[str, object]:
        return {
            "n_facts": len(self._facts),
            "n_infeasible": len(self._infeasible),
            "infeasible": [f"{a}->{b}" for a, b in sorted(self._infeasible)],
        }

    # -- 取数 -----------------------------------------------------------
    def base_estimate(self, key: Edge) -> float | None:
        """底表原值（未叠事实）：审计口径拿它对照"这条边原本估多少"。"""
        try:
            return float(self._base[key])
        except KeyError:
            return None

    def __getitem__(self, key: Edge) -> float:
        edge = (int(key[0]), int(key[1]))
        if edge in self._infeasible:
            return self._infeasible_cost
        value = self._facts.get(edge)
        if value is not None:
            return float(value)
        try:
            return float(self._base[(key[0], key[1])])
        except KeyError:
            value = self._extra.get(edge)
            if value is None:
                raise
            return float(value)

    def __setitem__(self, key: Edge, value: float) -> None:
        edge = (int(key[0]), int(key[1]))
        try:
            self._base[key]
        except KeyError:
            self._extra[edge] = float(value)
        else:
            self._extra.pop(edge, None)

    def __delitem__(self, key: Edge) -> None:
        edge = (int(key[0]), int(key[1]))
        self._extra.pop(edge, None)
        self._facts.pop(edge, None)
        self._infeasible = self._infeasible - {edge}

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        edge = (int(key[0]), int(key[1]))
        return (
            edge in self._infeasible
            or edge in self._facts
            or edge in self._extra
            or key in self._base
        )

    def __iter__(self) -> Iterator[Edge]:
        seen: set[Edge] = set()
        for edge in self._infeasible:
            seen.add(edge)
            yield edge
        for edge in self._facts:
            if edge not in seen:
                seen.add(edge)
                yield edge
        for edge in self._base:
            if edge not in seen:
                seen.add(edge)
                yield edge
        for edge in self._extra:
            if edge not in seen:
                seen.add(edge)
                yield edge

    def __len__(self) -> int:
        return len(self._infeasible) + len(self._facts) + len(self._extra) + len(self._base)

    def get(self, key: Edge, default: float | None = None):  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default

    # -- 检视 -----------------------------------------------------------
    @property
    def n_computed(self) -> int:
        return int(getattr(self._base, "n_computed", len(self._base)))

    def known_ids(self) -> set[int]:
        fn = getattr(self._base, "known_ids", None)
        if callable(fn):
            return set(fn())
        return {node for edge in self._base for node in edge}
