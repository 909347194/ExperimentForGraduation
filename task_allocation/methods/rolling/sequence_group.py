"""sequence_group.py — 顺序约束组的调度原子化。

职责：把 sequence_group 任务链作为不可拆调度单元：整组同时完成或同时暂缓；提供组映射、
展开、剔除不可唤醒/不完整组的 helper。
边界：只管调度层的组准入/交付原子性；同一航次内的访问顺序由 muas/constraints/
coop_constraints.py 判定，两者不互相替代。
相关：rolling/horizon.py。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

__all__ = [
    "group_id_of",
    "group_members",
    "has_groups",
    "expand_to_groups",
    "prune_unwakeable_groups",
    "prune_incomplete_groups",
]


def group_id_of(task: Any) -> int | None:
    """任务的顺序组 id；``None`` 表示无约束（所有收口旁路）。"""
    gid = getattr(task, "sequence_group", None)
    return int(gid) if gid is not None else None


def group_members(tasks: Iterable[Any]) -> dict[int, list[int]]:
    """按组聚合本周期 scope 内的成员 id（仅收 ``sequence_group`` 非 None）。"""
    out: dict[int, list[int]] = defaultdict(list)
    for task in tasks:
        gid = group_id_of(task)
        if gid is not None:
            out[gid].append(int(task.id))
    return dict(out)


def has_groups(tasks: Iterable[Any]) -> bool:
    """是否存在顺序组；False ⇒ 调用方应完全跳过组级收口（零行为变化）。"""
    return any(group_id_of(t) is not None for t in tasks)


def expand_to_groups(
    ids: set[int],
    groups: dict[int, list[int]],
) -> set[int]:
    """把命中任一组的 id 扩展到**整组**（原子性：要动就整组一起动）。

    用于「整组一起休眠 / 一起唤醒 / 一起弹回」等方向一致的动作；无组 id
    原样保留。无组时 ``groups`` 为空 ⇒ 返回输入集合的副本（零行为变化）。
    """
    out = set(ids)
    for members in groups.values():
        mset = set(members)
        if out & mset:
            out |= mset
    return out


def prune_unwakeable_groups(
    wakeable_ids: set[int],
    groups: dict[int, list[int]],
) -> set[int]:
    """组内仍有不可唤醒成员时，整组不唤醒 → 返回过滤后的可唤醒集。

    唤醒侧的原子性：只要链里还有一个成员够不着，整条链继续休眠，避免
    「组内部分唤醒、部分休眠」把调度单元拆开。无组时原样返回副本。
    """
    out = set(wakeable_ids)
    for members in groups.values():
        if not set(members).issubset(wakeable_ids):
            out.difference_update(members)
    return out


def prune_incomplete_groups(
    kept_ids: set[int],
    groups: dict[int, list[int]],
) -> set[int]:
    """返回因「组不完整」需要从 ``kept_ids`` 中撤下的成员 id。

    对每个组：完整（全部成员都在 kept）→ 保留；未参与（0 个在 kept）→
    无操作；**部分**（0 < 在 kept 数 < 成员数）→ 把已参与的成员全部撤下，
    使整组一起暂缓。无组时 ``groups`` 为空 ⇒ 立即返回空集。
    """
    drop: set[int] = set()
    for members in groups.values():
        present = [tid for tid in members if tid in kept_ids]
        if 0 < len(present) < len(members):
            drop.update(present)
    return drop
