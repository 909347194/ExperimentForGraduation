# -*- coding: utf-8 -*-
"""cost_provider.py — 节点间代价与统一距离口径。

职责（回答）：两节点之间代价多少、物理航程多少？

- :class:`CostProvider`：pairwise 优先、欧氏兜底的节点代价；``travel`` 为
  DEM 统一航程（物理长度，供 ETA / F3 换算时间）；
- :func:`build_cost_provider`：由任务/机巢/UAV 实体装配代价视图。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from task_allocation.common.types import Nest, Task, UAV


def _euclid(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    return float(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
    )


@dataclass
class CostProvider:
    """节点间代价：pairwise 优先，否则欧氏；机巢键 = nest_offset + nest_id。

    另承载 **统一距离口径** ``distance_fn``（:func:`make_distance_fn` 生成的
    DEM 三维航程）。凡要算「飞这一段的物理长度/时间」的地方（航迹时间 F3、
    时间窗 ETA）一律走 :meth:`travel`，不得再直接 ``_euclid`` —— 后者是直线，
    会系统性低估飞行时间（docs/full_review_20261012.md P1-1）。
    """

    pairwise: dict[tuple[int, int], float] = field(default_factory=dict)
    positions: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    nest_offset: int = 1_000_000
    # 统一距离口径：``f(xyz_a, xyz_b) -> 米``；None → 回退欧氏（无 DEM 场景）
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None

    def nest_key(self, nest_id: int) -> int:
        return self.nest_offset + nest_id

    def cost(self, id_a: int, id_b: int) -> float:
        if (id_a, id_b) in self.pairwise:
            return float(self.pairwise[(id_a, id_b)])
        if (id_b, id_a) in self.pairwise:
            return float(self.pairwise[(id_b, id_a)])
        pa = self.positions.get(id_a)
        pb = self.positions.get(id_b)
        if pa is not None and pb is not None:
            return _euclid(pa, pb)
        return 1e6

    def travel(self, a: Sequence[float], b: Sequence[float]) -> float:
        """两点间的**物理航程**（米），统一口径：DEM 三维航程优先。

        与 :meth:`cost` 的区别：``cost`` 查的是节点 id → id 的**代价**（可含
        收益加权等模型化口径），``travel`` 是纯粹的空间长度，用于把距离换算成
        飞行时间（ETA / F3）。两者同源（都由同一个 DEM 估算器派生）。
        """
        if self.distance_fn is not None:
            try:
                return float(self.distance_fn(a, b))
            except Exception:  # 防御：估算器异常不得让求解崩，退回直线
                pass
        return _euclid(tuple(a[:3]), tuple(b[:3]))


def build_cost_provider(
    tasks: Sequence[Task],
    nests: Sequence[Nest],
    uavs: Sequence[UAV],
    pairwise: Mapping[tuple[int, int], float] | None = None,
    nest_offset: int = 1_000_000,
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
) -> CostProvider:
    positions: dict[int, tuple[float, float, float]] = {}
    for t in tasks:
        positions[t.id] = t.position
    for n in nests:
        positions[nest_offset + n.id] = n.position
    for u in uavs:
        positions[-(1 + u.id)] = u.position
    if pairwise is None:
        pw: Mapping[tuple[int, int], float] = {}
    elif isinstance(pairwise, dict):
        # 快照：调用方后续改动不影响 provider（历史行为）
        pw = dict(pairwise)
    else:
        # 非 dict 的 Mapping 直通共享。本项目唯一的此类输入是惰性活视图
        # :class:`~task_allocation.methods.muas.cost.lazy_pairwise.LazyPairwiseCost`：
        # ① 其语义是「只增缓存的值视图」，快照无意义；
        # ② dict() 物化会把没问过的边全部算出来（O(n²) 回潮），必须禁止；
        # ③ 缺项查询按其 KeyError/False 语义回落，与普通字典缺项一致。
        # （不 import 具体类：cost 包的 __init__ 会拉 cost_matrix → 回环。）
        pw = pairwise
    return CostProvider(
        pairwise=pw,
        positions=positions,
        nest_offset=nest_offset,
        distance_fn=distance_fn,
    )
