# -*- coding: utf-8 -*-
"""统一数据结构（任务分配子系统共用）。

selection / muas / rolling / experiments 均从此处导入实体类型，
避免各模块重复定义导致字段漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    SELECTED = "selected"
    EXECUTING = "executing"
    DONE = "done"
    EXPIRED = "expired"
    UNREACHABLE = "unreachable"


class UAVStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    CHARGING = "charging"
    FAULT = "fault"


@dataclass
class Task:
    id: int
    x: float
    y: float
    z: float = 0.0
    reward: float = 1.0
    earliest: float | None = None
    latest: float | None = None
    arrival_time: float = 0.0
    status: TaskStatus = TaskStatus.PENDING
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class UAV:
    id: int
    x: float
    y: float
    z: float = 0.0
    remaining_range: float = 1e9
    speed: float = 10.0
    nest_id: int | None = None
    status: UAVStatus = UAVStatus.IDLE
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def is_available(self) -> bool:
        return self.status == UAVStatus.IDLE


@dataclass
class Nest:
    id: int
    x: float
    y: float
    z: float = 0.0
    capacity: int = 10
    available: bool = True

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class ScoredTask:
    task: Task
    priority: float = 0.0
    marginal_score: float = 0.0
    approx_delta_cost: float = 0.0
    filter_reason: str | None = None
    selected: bool = False


@dataclass
class SelectionResult:
    selected: list[Task]
    scored: list[ScoredTask]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def selected_ids(self) -> list[int]:
        return [t.id for t in self.selected]
