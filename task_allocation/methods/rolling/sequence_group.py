"""顺序约束组原子化（口径约定 3「同周期完成，或同时暂缓」）。

带 ``Task.sequence_group`` 的任务链是**不可拆的调度单元**：本周期要么整组
一起进入执行集并同周期完成，要么整组一起暂缓；不允许组内一部分完成、
一部分休眠/暂缓，也不允许把一条链拆开。

两条暂缓路径（**触发条件不同，互不替代、不冲突**）：

1. **服务休眠路径**（能力不足）：组内任一成员连续无可用机在往返航程内
   ⇒ 整组一起转 ``DORMANT``；条件恢复时整组一起唤醒。计入
   ``n_dormant_events`` / ``n_woken`` / ``n_dormant_end``，停机时由完成率
   分母排除项覆盖（口径 A）。见 ``horizon._update_service_stall`` /
   ``horizon._wake_dormant``。
2. **组原子性路径**（调度判定）：本周期候选集或 MUAS 解里只出现组的一部分
   ⇒ 把已出现的成员一并撤下，整组**批次 defer**：留池、状态保持
   ``PENDING``、不打 ``DORMANT`` 标、不计休眠指标，下周期整组重试。见
   ``horizon._seq_admission_gate`` / ``horizon._seq_delivery_gate``；完成闸门
   里组内任一成员物理不可行时整组一起弹回，同属此路径。这是主动的调度
   原子性保护，不是能力不足，故刻意与服务休眠指标分离。

组映射基准：``horizon._seq_groups()`` 取**全池活动任务**（含 DORMANT /
EXECUTING，不含已出池 DONE/UNREACHABLE）——已出池成员不再计入组，故不会
永久阻塞其余成员（避免死锁）；休眠/在途成员仍计入组，从而触发「整组一起
暂缓」，这正是口径 3 要求的语义，不得放行其余成员单独完成。顺序链由
``common.task_scenario._assign_sequence_chains`` 在**单批次内**按空间近邻
分组、共享 ``arrival_time``，不存在组内成员跨周期到达；未进池的任务本就不
在组映射里，天然不参与完整性判断。

无组旁路：既有实验 ``sequence_chain_ratio`` 默认 0.0 ⇒ 全部任务
``sequence_group=None`` ⇒ 所有组级 helper 立即返回、零行为变化。

与 :mod:`task_allocation.methods.muas.constraints.coop_constraints` 的分工：
那里只判**同一航次内**的访问顺序（逆序对硬违约）；本模块负责**调度层**的
组准入/交付原子性，比前者更靠前，两者不互相替代。
"""

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
