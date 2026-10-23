# -*- coding: utf-8 -*-
"""coop_constraints.py — 任务侧协同约束（论文 §3.3 / §5(4) / §6）。

任务维度的耦合约束，作用于 AllocationSolution 整体：

    * 时间窗约束：任务须在 [earliest, latest] 内被访问（latest 为绝对时刻，
      由滚动周期 ``current_time`` 起的到达游标判定）
    * 时序约束（可选能力）：同一 ``sequence_group`` 内、id 较小的任务须先于
      id 较大的任务被访问
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
    传入 ``distance_fn`` 时改走 **DEM 三维航程**——与代价矩阵、航迹时间 F3
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


def check_sequence_constraint(
    tours: Sequence[UAVTour],
    tasks: Mapping[int, Task],
) -> tuple[float, list[str]]:
    """时序约束违背量（可选能力，依赖 ``Task.sequence_group``）。

    同一 ``sequence_group`` 内、id 较小的任务须先于 id 较大的任务被访问；
    统计每个组内逆序对个数作为违背量。本仓库 ``Task`` 当前未提供
    ``sequence_group``，自动退化为 0（不引入新行为）；数据提供该字段后自动生效。

    Args:
        tours: 各 UAV 航次。
        tasks: ``task_id → Task``。

    Returns:
        ``(seq_penalty, violation_tags)``；无违规时两项均为 0 / 空。
    """
    penalty = 0.0
    tags: list[str] = []
    for tour in tours:
        groups: dict[Any, list[int]] = {}
        for tid in tour.task_ids:
            grp = getattr(tasks.get(tid), "sequence_group", None)
            if grp is not None:
                groups.setdefault(grp, []).append(tid)
        for grp, ids in groups.items():
            inv = 0
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if ids[i] > ids[j]:
                        inv += 1
            if inv:
                penalty += float(inv)
                tags.append(f"seq_group_{grp}")
    return penalty, tags


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
