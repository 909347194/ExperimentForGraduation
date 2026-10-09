# -*- coding: utf-8 -*-
"""统一数据结构（任务分配子系统共用）。

分层约定：
    实体层：Task / UAV / Nest 及状态枚举
    选择层：ScoredTask / SelectionResult（论文 3.2 → T_t^sel）
    解层：  UAVTour / AllocationSolution（论文 3.3 → y, x, π, z）

算法内部基因（Gene / Individual）仍放在 muas/representation，不在此定义。
SolverResult.extra["solution"] 建议挂载 AllocationSolution，与 best_assignment 扁平表并存。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 实体层
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """巡检任务。"""

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
    """无人机当前状态快照。起点由实际状态决定，终点机巢在解中决策。"""

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
    """机巢。"""

    id: int
    x: float
    y: float
    z: float = 0.0
    capacity: int = 10
    available: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


# ---------------------------------------------------------------------------
# 选择层（3.2）
# ---------------------------------------------------------------------------


@dataclass
class ScoredTask:
    """任务选择阶段的评分与诊断。"""

    task: Task
    priority: float = 0.0
    marginal_score: float = 0.0
    approx_delta_cost: float = 0.0
    filter_reason: str | None = None
    selected: bool = False


@dataclass
class SelectionResult:
    """selection.run 输出：本周期候选集 T_t^sel。"""

    selected: list[Task]
    scored: list[ScoredTask]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def selected_ids(self) -> list[int]:
        return [t.id for t in self.selected]


# ---------------------------------------------------------------------------
# 解层（3.3）：航次 + 分配解
# ---------------------------------------------------------------------------


@dataclass
class UAVTour:
    """单架 UAV 在一个规划周期内的航次。

    对应：
        B_start → T_u1 → T_u2 → … → T_un → B_end

    - start_*：由 UAV 当前实际状态确定（已知）
    - task_ids：访问顺序 π_u；空列表表示本周期空闲（n_u = 0）
    - end_nest_id：终点机巢决策 z_ub；None 表示未指定/不返航到登记机巢
    """

    uav_id: int
    task_ids: list[int] = field(default_factory=list)
    end_nest_id: int | None = None
    # 起点快照（规划时刻）
    start_x: float = 0.0
    start_y: float = 0.0
    start_z: float = 0.0
    start_nest_id: int | None = None
    # 可选评价信息（由 cost / constraints 回填）
    est_cost: float = 0.0
    est_reward: float = 0.0
    feasible: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_idle(self) -> bool:
        return len(self.task_ids) == 0

    @property
    def start_position(self) -> tuple[float, float, float]:
        return (self.start_x, self.start_y, self.start_z)

    @classmethod
    def from_uav(
        cls,
        uav: UAV,
        task_ids: list[int] | None = None,
        end_nest_id: int | None = None,
    ) -> UAVTour:
        """用 UAV 当前状态填充起点。"""
        return cls(
            uav_id=uav.id,
            task_ids=list(task_ids or []),
            end_nest_id=end_nest_id,
            start_x=uav.x,
            start_y=uav.y,
            start_z=uav.z,
            start_nest_id=uav.nest_id,
        )


@dataclass
class AllocationSolution:
    """单周期选择性多机巢 MUAS 解。

    与论文决策变量的对应关系：
        y_j = 1  iff  task j ∈ executed_task_ids
        x_uj = 1 iff  task j 出现在 uav u 的 tour.task_ids 中
        π_u      = tour.task_ids
        z_ub     = tour.end_nest_id

    约定：
        - candidate_task_ids：进入 MUAS 的 T_t^sel（选择层输出）
        - executed_task_ids：本周期最终执行的 T_t^exec ⊆ T_t^sel（允许 y_j=0）
        - tours：覆盖参与规划的 UAV；空闲 UAV 用 task_ids=[] 的 tour 表示
        - 同一 task_id 至多出现在一个 tour 中（一任务一机）
    """

    tours: list[UAVTour] = field(default_factory=list)
    candidate_task_ids: list[int] = field(default_factory=list)
    executed_task_ids: list[int] = field(default_factory=list)
    # 目标值（可选，由评价器填充）
    f1_reward: float = 0.0
    f2_cost: float = 0.0
    feasible: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    def assignment_pairs(self) -> list[tuple[int, int]]:
        """扁平 (uav_id, task_id) 列表，顺序按各 tour 内访问顺序；对接 SolverResult.best_assignment。"""
        pairs: list[tuple[int, int]] = []
        for tour in self.tours:
            for tid in tour.task_ids:
                pairs.append((tour.uav_id, tid))
        return pairs

    def task_to_uav(self) -> dict[int, int]:
        """task_id → uav_id。"""
        mapping: dict[int, int] = {}
        for tour in self.tours:
            for tid in tour.task_ids:
                mapping[tid] = tour.uav_id
        return mapping

    def tour_of(self, uav_id: int) -> UAVTour | None:
        for tour in self.tours:
            if tour.uav_id == uav_id:
                return tour
        return None

    @property
    def active_uav_ids(self) -> list[int]:
        return [t.uav_id for t in self.tours if not t.is_idle]

    @property
    def idle_uav_ids(self) -> list[int]:
        return [t.uav_id for t in self.tours if t.is_idle]

    def validate_unique_tasks(self) -> bool:
        """检查任务是否被唯一分配。"""
        seen: set[int] = set()
        for tour in self.tours:
            for tid in tour.task_ids:
                if tid in seen:
                    return False
                seen.add(tid)
        return True
