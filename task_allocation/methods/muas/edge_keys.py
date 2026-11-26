# -*- coding: utf-8 -*-
"""边的键空间：上层节点键约定 + 航次边枚举 + 下层 id → 上层键翻译。

职责：把"哪条有向边"这件事收敛成一处口径，代价视图、编排回灌两方共用同一套
      枚举与翻译。
边界：只做键运算，不取代价、不判可行性。
相关：``muas/cost/nest_keys.py``（机巢键偏移）、``muas/cost/fact_cost_view.py``、
      ``rolling/cycle_stages.py``。

键约定（与 ``cost.provider.CostProvider`` 逐字一致）：

- 任务：``task.id``（正数）
- 机巢：``nest_offset + nest.id``
- UAV 起点：``-(1 + uav.id)``（负数，避免与任务 id 撞键）

下层的逐段账只有"任务 id / 机巢 id"，所以回灌前必须经翻译；翻不出来的端点直接
丢边——宁可少回灌一条，也不把边记到错的节点上。
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

__all__ = [
    "Edge",
    "tour_edges",
    "edges_of_tour",
    "make_key_of",
    "translate_edges",
    "translate_by_role",
]

Edge = tuple[int, int]


def tour_edges(
    nest_key_of: Callable[[int], int],
    uav_id: int,
    task_ids: Sequence[int],
    end_nest_id: int | None,
) -> tuple[Edge, ...]:
    """一条航次的有向边：``UAV起点 → 任务链 → 终点机巢``。

    与 ``problem.tour_path_cost`` 的加总口径**逐边同序**；空航次返回空元组
    （不起飞就没有边，不存在"机→巢"幻影腿）。
    """
    task_list = tuple(int(t) for t in task_ids)
    if not task_list or end_nest_id is None:
        return ()
    out: list[Edge] = []
    prev = -(1 + int(uav_id))
    for tid in task_list:
        out.append((prev, tid))
        prev = tid
    out.append((prev, int(nest_key_of(int(end_nest_id)))))
    return tuple(out)


def edges_of_tour(costs: object, tour: object) -> tuple[Edge, ...]:
    """``UAVTour`` → 边序列（``costs`` 需提供 ``nest_key`` 方法）。"""
    return tour_edges(
        costs.nest_key,  # type: ignore[attr-defined]
        int(getattr(tour, "uav_id")),
        tuple(int(t) for t in (getattr(tour, "task_ids", ()) or ())),
        getattr(tour, "end_nest_id", None),
    )


def make_key_of(
    nest_ids: Iterable[int], *, nest_offset: int, task_ids: Iterable[int] | None = None
) -> Callable[[int], int | None]:
    """下层 id → 上层节点键的翻译器（**按集合猜角色**）。

    - 机巢 id（在 ``nest_ids`` 内）→ ``nest_offset + id``；
    - 任务 id（在 ``task_ids`` 内，或未给出任务集时按"正数即任务"）→ 原值；
    - 其余（含 UAV 起点键、未知 id）→ ``None``（该边丢弃）。

    .. warning::
       机巢与任务**共用一套整数编号**（如 ``active_nest_ids=[0,1,2]`` 而任务 id
       从 1 起）。此时一个裸 id 无法判角色，本函数会按"机巢优先"误翻。逐边翻译
       请用 :func:`translate_by_role`——那里的角色由航次结构给定，不靠猜。
    """
    nests = {int(n) for n in nest_ids}
    tasks = None if task_ids is None else {int(t) for t in task_ids}

    def key_of(legacy_id: int) -> int | None:
        raw = int(legacy_id)
        if raw in nests:
            return int(nest_offset) + raw
        if tasks is not None:
            return raw if raw in tasks else None
        return raw if raw > 0 else None

    return key_of


def translate_edges(
    edges: Iterable[Edge], key_of: Callable[[int], int | None]
) -> tuple[Edge, ...]:
    """逐边翻译；任一端翻不出来即丢弃。"""
    out: list[Edge] = []
    for a, b in edges:
        ka, kb = key_of(int(a)), key_of(int(b))
        if ka is None or kb is None:
            continue
        out.append((int(ka), int(kb)))
    return tuple(out)


def translate_by_role(
    edges: Iterable[Edge],
    *,
    task_ids: Iterable[int] | None = None,
    nest_ids: Iterable[int] = (),
    nest_offset: int = 0,
    nest_ends: Iterable[Edge] = (),
) -> tuple[Edge, ...]:
    """按**边在航次里的角色**翻译下层 id（不猜 id 属于哪个集合）。

    ``nest_ends`` 是"终点落在机巢"的那几条边（由下层航次结构给出）：
    它们的终点翻成 ``nest_offset + id``，其余端点一律当任务（起点键为负数，
    两层同构，直接放行）。角色由此确定，机巢与任务编号重叠也不会翻错。

    翻译不出来的边直接丢弃——宁可少禁一条，也不把边记到错的节点上。
    """
    nests = {int(n) for n in nest_ids}
    ends = {(int(a), int(b)) for a, b in nest_ends}
    tasks = None if task_ids is None else {int(t) for t in task_ids}

    def task_key(raw: int) -> int | None:
        if raw < 0:  # UAV 起点键：两层同一套编码
            return raw
        return raw if tasks is None or raw in tasks else None

    out: list[Edge] = []
    for a, b in edges:
        a, b = int(a), int(b)
        ka = task_key(a)
        if (a, b) in ends:
            kb = (int(nest_offset) + b) if (not nests or b in nests) else None
        else:
            kb = task_key(b)
        if ka is None or kb is None:
            continue
        out.append((int(ka), int(kb)))
    return tuple(out)
