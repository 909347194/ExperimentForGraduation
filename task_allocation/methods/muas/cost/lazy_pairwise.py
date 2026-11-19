# -*- coding: utf-8 -*-
"""lazy_pairwise.py — 按需计算并缓存的 pairwise 代价表。

职责：LazyPairwiseCost 以 Mapping[(id_a,id_b)]->float 提供代价，未命中键按有序对现算并
缓存；make_lazy_pairwise 由 tasks/uavs/nests 装配位置注册表。
边界：值口径与全量建表一致；缓存仅在单周期内有效，跨周期复用由调用方自持。
相关：muas/cost/cost_matrix.py（惰性源）、vertical_section.py（底层估算器）、
rolling/horizon.py。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable, Iterable, Iterator, Sequence

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.cost.nest_keys import DEFAULT_NEST_OFFSET
from task_allocation.methods.muas.cost.vertical_section import (
    VerticalSectionCostEstimator,
)

__all__ = ["LazyPairwiseCost", "make_lazy_pairwise"]


class LazyPairwiseCost(Mapping):
    """惰性 pairwise 代价表：``__getitem__`` 按需估算并缓存。

    与全量 eager 字典的语义差异只有一处：``__contains__`` 对**位置注册
    表内**的任意 id 对返回 True（值可现算），而 eager 字典只对已建条目
    返回 True。所有既有消费方（``CostProvider.cost`` / ``insert_delta_cost``）
    都是「先 ``in`` 再取」，行为因此等价。
    """

    def __init__(
        self,
        positions: Mapping[int, Sequence[float]],
        estimator: VerticalSectionCostEstimator | None = None,
        *,
        weight_of: Callable[[int], float] | None = None,
    ) -> None:
        """
        Args:
            positions: id → (x, y, z)。覆盖任务 id、机巢键（nest_offset+id）、
                UAV 起点键（-(1+uav_id)）；缺 id 的查询走 KeyError
                （与 eager 字典缺项一致，交由调用方回退）。
            estimator: 垂直切面估算器；None → 无 DEM 的三维直线口径。
            weight_of: id → 代价权重（与 eager 建表的 ``weight_of(目标)``
                同口径：只对**第二个** id 生效）。None → 恒 1.0。
        """
        self._positions = dict(positions)
        self._est = (
            estimator
            if estimator is not None
            else VerticalSectionCostEstimator()
        )
        self._weight_of = weight_of or (lambda _id: 1.0)
        self._store: dict[tuple[int, int], float] = {}

    # -- Mapping 协议 ---------------------------------------------------

    def __getitem__(self, key: tuple[int, int]) -> float:
        try:
            return self._store[key]
        except KeyError:
            pass
        id_a, id_b = key
        pa = self._positions.get(id_a)
        pb = self._positions.get(id_b)
        if pa is None or pb is None:
            raise KeyError(key)
        val = float(self._est.estimate(pa, pb, weight=self._weight_of(id_b)).cost)
        self._store[key] = val
        return val

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        if key in self._store:
            return True
        id_a, id_b = key
        return id_a in self._positions and id_b in self._positions

    def __iter__(self) -> Iterator[tuple[int, int]]:
        return iter(self._store)

    def __len__(self) -> int:
        return len(self._store)

    def __setitem__(self, key: tuple[int, int], value: float) -> None:
        self._store[key] = float(value)

    def get(self, key: tuple[int, int], default: float | None = None):
        try:
            return self[key]
        except KeyError:
            return default

    # -- 检视 -----------------------------------------------------------

    @property
    def n_computed(self) -> int:
        """已现算/缓存的条目数（诊断：验证「只算了几条边」）。"""
        return len(self._store)

    def known_ids(self) -> set[int]:
        """位置注册表内的全部 id（任务 / 机巢键 / UAV 起点键）。"""
        return set(self._positions)


def make_lazy_pairwise(
    tasks: Iterable[Task],
    uavs: Iterable[UAV],
    nests: Sequence[Nest] | None = None,
    estimator: VerticalSectionCostEstimator | None = None,
    *,
    nest_offset: int = DEFAULT_NEST_OFFSET,
    task_weight_from_reward: bool = False,
) -> LazyPairwiseCost:
    """装配一阶段用的惰性代价表（位置注册表 = 全部节点，值按需算）。

    ``task_weight_from_reward=True`` 时目标任务按其收益加权，与
    :func:`build_pairwise_costs` 的 ``weight_of`` 逐位同口径。
    """
    task_list = list(tasks)
    uav_list = list(uavs)
    nest_list = list(nests or [])
    positions: dict[int, Sequence[float]] = {}
    reward_by_id: dict[int, float] = {}
    for t in task_list:
        positions[t.id] = t.position
        reward_by_id[t.id] = max(float(t.reward), 1e-6)
    for n in nest_list:
        positions[nest_offset + n.id] = n.position
    for u in uav_list:
        positions[-(1 + u.id)] = u.position

    if task_weight_from_reward:
        def weight_of(node_id: int) -> float:
            return reward_by_id.get(node_id, 1.0)
    else:
        weight_of = lambda _node_id: 1.0  # noqa: E731

    return LazyPairwiseCost(positions, estimator, weight_of=weight_of)
