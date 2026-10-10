# -*- coding: utf-8 -*-
"""event_trigger.py — 事件驱动的提前重规划触发（论文 §6）。

滚动周期之外，以下事件触发**局部/提前**重规划，而不是等下一个周期：

    1. UAV 故障           → 其未完成任务释放回任务池（T_t^release）
    2. 突发高优先级任务   → 立即纳入本周期重新选择
    3. 机巢不可用         → 在途 / 计划降落的 UAV 改降其它机巢

本模块只做**事件识别与判定**，不直接改动状态：
真正的状态流转由 :mod:`methods.rolling.horizon` 显式执行，保持
``task_pool`` 与 ``uav_state`` 两个状态机互不 import。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from task_allocation.common.types import Nest, Task, UAV, UAVStatus

__all__ = [
    "EventType",
    "ReplanEvent",
    "EventTriggerConfig",
    "detect_events",
    "should_replan",
]


class EventType(str, Enum):
    """触发重规划的事件类型。"""

    UAV_FAULT = "uav_fault"
    URGENT_TASK = "urgent_task"
    NEST_UNAVAILABLE = "nest_unavailable"


@dataclass(frozen=True)
class ReplanEvent:
    """一条重规划事件。"""

    type: EventType
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, **self.detail}


@dataclass
class EventTriggerConfig:
    """事件触发阈值（全部可由实验 yaml 覆盖）。"""

    on_uav_fault: bool = True
    on_nest_unavailable: bool = True
    # 紧急任务：收益 >= reward_threshold 或 剩余时间 <= slack_threshold 视为紧急
    on_urgent_task: bool = True
    reward_threshold: float = 8.0
    slack_threshold: float = 900.0


def detect_events(
    uavs: Sequence[UAV],
    tasks: Sequence[Task],
    nests: Sequence[Nest],
    config: EventTriggerConfig | None = None,
    *,
    current_time: float = 0.0,
    known_unavailable: set[int] | None = None,
) -> list[ReplanEvent]:
    """扫描当前状态，返回需要触发重规划的事件列表。

    Args:
        uavs:       全部无人机。
        tasks:      当前任务池活动任务。
        nests:      机巢列表。
        config:     触发阈值。
        current_time: 当前时刻，用于算紧迫度。
        known_unavailable: 上一周期已标记为不可用的机巢，用于识别**新发生**的关闭。
    """
    cfg = config or EventTriggerConfig()
    events: list[ReplanEvent] = []

    if cfg.on_uav_fault:
        for u in uavs:
            if u.status == UAVStatus.FAULT:
                events.append(ReplanEvent(EventType.UAV_FAULT, {"uav_id": u.id}))

    if cfg.on_nest_unavailable:
        known = known_unavailable or set()
        for n in nests:
            if not n.available and n.id not in known:
                events.append(
                    ReplanEvent(
                        EventType.NEST_UNAVAILABLE,
                        {"nest_id": n.id, "reason": n.meta.get("close_reason")},
                    )
                )

    if cfg.on_urgent_task:
        for t in tasks:
            urgent_reward = t.reward >= cfg.reward_threshold
            slack = (t.latest - current_time) if t.latest is not None else None
            urgent_slack = slack is not None and slack <= cfg.slack_threshold
            if urgent_reward or urgent_slack:
                events.append(
                    ReplanEvent(
                        EventType.URGENT_TASK,
                        {
                            "task_id": t.id,
                            "reward": float(t.reward),
                            "slack": None if slack is None else float(slack),
                        },
                    )
                )

    return events


def should_replan(
    events: Sequence[ReplanEvent],
    config: EventTriggerConfig | None = None,
) -> bool:
    """是否需要提前重规划（任一事件命中即触发）。"""
    return len(list(events)) > 0
