# -*- coding: utf-8 -*-
"""冲突聚类：把冲突对并成连通域，决定"局部单点改"还是"多点同时改"。

职责：并查集连通冲突对涉及的所有机号，返回连通域列表与每域内部的冲突对。
边界：不改轨迹、不排序优先级、不做重规划。
相关：``docs/conflict_model.md`` §8.3（为什么必须整体协同）、
      ``conflict_detector.py``（冲突对来源）。

**为什么必须成域处理**：若 A—B 冲突、改开 A 后又与 C 冲突，则 A/B/C 同属一域。
逐个独立让路会让 A 让开 B 就撞上 C、再让 C 又撞回 B，来回振荡且永不收敛；
只有把同一域内的相关航迹**一起**改，冲突消解才可能单调收敛（§12 终止条件）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = ["ConflictCluster", "cluster_conflicts"]


@dataclass(frozen=True)
class ConflictCluster:
    """一个冲突连通域。

    Attributes:
        uav_ids: 域内全部机号（升序）
        pairs:   域内的冲突对（``(id_a, id_b)``，升序元组，去重）
    """

    uav_ids: tuple[int, ...]
    pairs: tuple[tuple[int, int], ...]

    @property
    def size(self) -> int:
        return len(self.uav_ids)


def cluster_conflicts(
    pairs: Iterable[tuple[int, int]],
    *,
    ids: Sequence[int] | None = None,
) -> list[ConflictCluster]:
    """把冲突对聚成连通域。

    Args:
        pairs: 冲突对 ``(id_a, id_b)``；自环 ``(x, x)`` 视为无效并丢弃
        ids:   额外要纳入的机号（例如"本轮新规划的机"），保证只含孤立机也成域？

    ``ids`` 的语义：只用于**校验**（若给定，则输出域内的机号必须都在 ids 里），
    不会凭空造出孤立域——没有冲突的机不该成为聚类结果，否则协调层会去"改"
    一条本无冲突的航迹。
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    edges: list[tuple[int, int]] = []
    for a, b in pairs:
        if a == b:
            continue
        edges.append((min(a, b), max(a, b)))
        find(a)
        find(b)
        union(a, b)

    if ids is not None:
        allowed = set(ids)
        unknown = {x for e in edges for x in e} - allowed
        if unknown:
            raise ValueError(f"冲突对里出现未声明的机号：{sorted(unknown)}")

    groups: dict[int, set[int]] = {}
    for node in parent:
        groups.setdefault(find(node), set()).add(node)

    clusters: list[ConflictCluster] = []
    for root, members in groups.items():
        member_pairs = tuple(sorted(e for e in edges if find(e[0]) == root))
        clusters.append(
            ConflictCluster(uav_ids=tuple(sorted(members)), pairs=member_pairs)
        )
    # 大域优先：协调层按序处理时先解决影响面最大的
    clusters.sort(key=lambda c: (-c.size, c.uav_ids))
    return clusters
