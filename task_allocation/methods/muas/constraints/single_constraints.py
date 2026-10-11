# -*- coding: utf-8 -*-
"""single_constraints.py — 单机飞行性能约束（论文 §3.3 / §5(4)）。

单机维度的硬约束，作用于单条 UAVTour：

    * 航程约束：航次总代价 ≤ 剩余航程（``remaining_range``）
    * 飞行时间约束（可选能力）：航次耗时 ≤ 最大飞行时间（``UAV.max_time``）

与 :mod:`coop_constraints` / :mod:`nest_capacity` 一起构成 constraints 包的
三类约束，统一汇入 :mod:`evaluator` 的 :class:`ConstraintReport`。

参考借鉴 Experiments-For-Dissertation 的 single_constraints 设计
（range / time 两类单机约束 + 不可变违背量数据类）。
"""

from __future__ import annotations

from dataclasses import dataclass

from task_allocation.common.types import UAV


@dataclass(frozen=True)
class SingleConstraintViolation:
    """单机约束违背量。

    Attributes:
        range_violation: 航程约束违背量（米）。
        time_violation:  飞行时间约束违背量（秒）。
    """

    range_violation: float = 0.0
    time_violation: float = 0.0

    @property
    def total(self) -> float:
        return self.range_violation + self.time_violation


def check_range_constraint(
    uav: UAV,
    assigned_distance: float,
    *,
    margin: float = 1.0,
) -> float:
    """航程约束违背量（论文 §5(4) 航程约束）。

    当 ``assigned_distance > margin * uav.remaining_range`` 时返回超出量。
    默认 ``margin=1.0``，即与原 ``problem.check_feasible`` 中的
    ``pc > u.remaining_range`` 等价，便于平滑替换、零行为差异。

    Args:
        uav: UAV 实体（取 ``remaining_range``）。
        assigned_distance: 该航次总代价（起点→各任务→终点机巢的代价和）。
        margin: 航程安全裕度。与 ``selection.feasibility.range_margin`` 同类量；
                此处默认 1.0 = 不打折；需要保守时可传 0.95。

    Returns:
        违背量（非负值）。0 表示满足约束。
    """
    limit = margin * float(getattr(uav, "remaining_range", float("inf")))
    if assigned_distance > limit + 1e-6:
        return assigned_distance - limit
    return 0.0


def check_time_constraint(
    uav: UAV,
    assigned_distance: float,
) -> float:
    """最大飞行时间约束违背量（可选能力）。

    当 UAV 携带 ``max_time`` 属性、且以当前速度飞行总耗时仍超出时返回超出秒数。
    本仓库 ``UAV`` 当前未设 ``max_time``，此检查自动退化为 0（不引入新行为）；
    一旦数据/配置提供 ``max_time``，本函数即自动生效。

    Args:
        uav: UAV 实体（取 ``speed`` 与可选的 ``max_time``）。
        assigned_distance: 航次总代价（米）。

    Returns:
        违背量（非负秒）。0 表示满足约束或该能力未启用。
    """
    max_time = getattr(uav, "max_time", None)
    if max_time is None:
        return 0.0
    speed = max(float(getattr(uav, "speed", 1e-6)), 1e-6)
    flight_time = assigned_distance / speed
    if flight_time > float(max_time):
        return flight_time - float(max_time)
    return 0.0
