# -*- coding: utf-8 -*-
"""coop_constraints.py — 任务侧协同约束（论文 §3.3 / §5(4) / §6）。

任务维度的耦合约束，作用于 AllocationSolution 整体：

    * 时间窗约束：任务须在 [earliest, latest] 内被访问（latest 为绝对时刻，
      由滚动周期 ``current_time`` 起的到达游标判定）
    * 时序约束（2026-10-29 落地）：同一 ``sequence_group`` 内、``sequence_order``
      小的任务须先于大的被访问（**当前只判同一航次内**）
    * 同时到达约束（本模型恒为 0）：一任务一机，无多机协作攻击，故不适用

参考借鉴 Experiments-For-Dissertation 的 coop_constraints 设计
（seq / window / sync 三类协同约束），并按本 MUAS 模型裁剪成
「时间窗 + 时序」两类可激活约束。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from task_allocation.common.types import Task, UAV, UAVTour


@dataclass(frozen=True)
class CoopConstraintViolation:
    """协同约束违背量。

    Attributes:
        seq_violation:   时序约束违背量。
        window_violation: 时间窗约束违背量（秒）。
        sync_violation:  同时到达约束违背量（本模型恒为 0）。
    """

    seq_violation: float = 0.0
    window_violation: float = 0.0
    sync_violation: float = 0.0

    @property
    def total(self) -> float:
        return self.seq_violation + self.window_violation + self.sync_violation


def _task_window(task: Task) -> tuple[float | None, float | None]:
    """任务时间窗：(earliest, latest)。

    优先读参考项目约定的 ``time_window`` 元组，回退到本仓库的
    ``earliest`` / ``latest`` 单字段；二者皆无则视为无窗约束。
    """
    tw = getattr(task, "time_window", None)
    if isinstance(tw, (tuple, list)) and len(tw) == 2:
        return (None if tw[0] is None else float(tw[0])), (
            None if tw[1] is None else float(tw[1])
        )
    return task.earliest, task.latest


def check_time_window_constraint(
    tours: Sequence[UAVTour],
    tasks: Mapping[int, Task],
    uavs: Mapping[int, UAV],
    *,
    current_time: float = 0.0,
    service_time: float = 0.0,
    speed_default: float = 10.0,
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
) -> tuple[float, list[str], dict[int, float]]:
    """时间窗约束违背量（同时返回违规标签与每任务迟到量）。

    逐航次模拟到达游标：从 UAV 起点出发，按访问顺序累加段飞行时间，到点执行
    （若早于 ``earliest`` 则等待至 ``earliest``），离开时累加上 ``service_time``。
    到达时刻晚于 ``latest`` 的，惩罚 = 到达 − latest，并记 ``tw_task_{tid}``。

    段长口径（2026-10-13 统一）：默认仍为欧氏直线（保持既有单测基线），但
    传入 ``distance_fn`` 时改走 **DEM 三维航程**——与代价矩阵、航迹时间
    同源。旧实现恒用直线 ⇒ ETA 系统性低估飞行时间：地形代价算出来的
    航程比直线长，时间窗却在用直线判「来不来得及」，于是放进来不及的任务。

    Args:
        tours: 各 UAV 航次。
        tasks: ``task_id → Task``。
        uavs:  ``uav_id → UAV``（取 ``speed`` 与起点位置）。
        current_time: 本周期规划起始时刻（滚动时域下必须跟随周期）。
        service_time: 单任务作业时长（秒）。
        speed_default: UAV 缺 ``speed`` 时的兜底速度。
        distance_fn: 统一距离口径 ``f(xyz_a, xyz_b) -> 米``；None → 欧氏直线。

    Returns:
        ``(window_penalty, violation_tags, lateness)``；无违规时三项均为
        0 / 空 / 空。``lateness`` = ``task_id → 迟到秒数``（仅迟到任务），
        供 F1 奖励折减（迟到不是废飞，按迟到时长折减有效奖励）与滚动层
        记录迟到交付使用。
    """
    penalty = 0.0
    tags: list[str] = []
    lateness: dict[int, float] = {}
    for tour in tours:
        if not tour.task_ids:
            continue
        uav = uavs.get(tour.uav_id)
        if uav is None:
            continue
        speed = max(float(getattr(uav, "speed", speed_default)), 1e-6)
        t_cursor = float(current_time)
        prev = tour.start_position
        for tid in tour.task_ids:
            task = tasks.get(tid)
            if task is None:
                continue
            if distance_fn is not None:
                dist = float(distance_fn(prev, task.position))
            else:
                dist = math.dist(prev, task.position)
            t_cursor += dist / speed
            est, lst = _task_window(task)
            if est is not None and t_cursor < est:
                t_cursor = est
            if lst is not None and t_cursor > lst + 1e-6:
                late = float(t_cursor - lst)
                penalty += late
                lateness[int(tid)] = late
                tags.append(f"tw_task_{tid}")
            t_cursor += float(service_time)
            prev = task.position
    return penalty, tags, lateness


def _seq_order_of(task: Any) -> float:
    """任务的链内序号；``sequence_order`` 缺失时回退任务 id（历史约定）。

    历史实现直接拿 **任务 id 的大小** 当顺序，这与 id 的分配方式（到达
    批次、偏移）耦合，id 一旦不是按链内先后分配就判不出真实顺序。改为
    显式 ``sequence_order`` 后，id 只作兜底。
    """
    order = getattr(task, "sequence_order", None)
    if order is None:
        return float(getattr(task, "id", 0))
    return float(order)


def check_sequence_constraint(
    tours: Sequence[UAVTour],
    tasks: Mapping[int, Task],
) -> tuple[float, list[str]]:
    """时序（执行顺序）约束违背量，依赖 ``Task.sequence_group``。

    同一 ``sequence_group`` 的任务落在**同一条航次**内时，必须按
    ``sequence_order`` 升序被访问；违背量 = 组内逆序对个数。
    违约经 :meth:`ConstraintReport.add_seq` 进 **硬违约桶**：时序是物理
    执行顺序，违反即为「纸面可行、执行弹回」，与航程/续航同级判废
    （2026-10-29 口径）。

    **当前只判同一航次内**（用户 2026-10-29 裁定的范围）。同一链的任务
    被分到不同 UAV 或不同周期时本函数不判违约——那是跨机/跨周期口径，
    须按绝对完成时刻比对，尚未实现，勿误读为「已支持」。

    ``Task`` 未提供 ``sequence_group``（默认 ``None``）时自动退化为 0，
    **不引入任何新行为**——这正是 2026-10-29 之前 ``sequence: true``
    开关恒开着却从未生效的原因：有开关、无数据字段。

    Args:
        tours: 各 UAV 航次。
        tasks: ``task_id → Task``。

    Returns:
        ``(seq_penalty, violation_tags)``；无违规时两项均为 0 / 空。
    """
    penalty = 0.0
    tags: list[str] = []
    for tour in tours:
        groups: dict[Any, list[float]] = {}
        for tid in tour.task_ids:
            task = tasks.get(tid)
            if task is None:
                continue
            grp = getattr(task, "sequence_group", None)
            if grp is not None:
                groups.setdefault(grp, []).append(_seq_order_of(task))
        for grp, orders in groups.items():
            inv = 0
            for i in range(len(orders)):
                for j in range(i + 1, len(orders)):
                    if orders[i] > orders[j]:
                        inv += 1
            if inv:
                penalty += float(inv)
                tags.append(f"seq_group_{grp}")
    return penalty, tags


def sequence_violation_pairs(
    tour_task_ids: Sequence[int],
    tasks: Mapping[int, Task],
) -> list[tuple[int, int]]:
    """单条航次内的时序逆序对（供解码侧自审与测试用）。

    返回 ``[(先出现的高序号任务, 后出现的低序号任务), ...]``，空列表 =
    该航次满足时序。与 :func:`check_sequence_constraint` 同判据，只是
    给出具体对子便于定位与修复。
    """
    groups: dict[Any, list[tuple[int, float]]] = {}
    for tid in tour_task_ids:
        task = tasks.get(tid)
        if task is None:
            continue
        grp = getattr(task, "sequence_group", None)
        if grp is not None:
            groups.setdefault(grp, []).append((int(tid), _seq_order_of(task)))
    pairs: list[tuple[int, int]] = []
    for _grp, members in groups.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if members[i][1] > members[j][1]:
                    pairs.append((members[i][0], members[j][0]))
    return pairs


def check_sync_constraint(
    tours: Sequence[UAVTour],
    tasks: Mapping[int, Task],
) -> tuple[float, list[str]]:
    """同时到达约束（本 MUAS 模型恒为 0，保留签名以便将来启用）。

    参考项目用于「多机协作攻击同一目标须同时到达」。本 MUAS 为一任务一机
    （``x_uj ∈ {0,1}``、``Σ_u x_uj = y_j``），不存在多机到达同一任务，故约束
    恒满足。若将来引入协作攻击任务（一目标分配多机），再在此实现到达时刻区间
    交集判定即可，调用点无需改动。
    """
    return 0.0, []
