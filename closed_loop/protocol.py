# -*- coding: utf-8 -*-
r"""跨层消息契约：闭环一周里上下层实际交换的东西。

职责：定义环内消息类型与两个端口的形状（``AllocatorPort`` / ``PlannerPort``），
      以及环产出的轨迹账（``CyclePlan`` / ``LoopTrace``）。
边界：不含任何处理逻辑，也不 import 两侧的算法模块——只有类型与 Protocol。
相关：``orchestrator.py``（环驱动）、``adapters/``（两侧适配）。

**为什么不复用两侧的 ``PlanRequest`` / ``UAVTour``**：那两者分别是各层的内部
口径（``PlanRequest`` 带机体规格与预算、``UAVTour`` 带 est_cost 与机巢决策），
直接互传等于让一层依赖另一层的字段语义。环里只走"这条航次去哪、什么时候走"
和"这条航次真实用多久"，转换在 ``adapters/`` 完成。

**``overrun`` 为什么要在契约里显式表达**：上层当前的执行模型是"周期末即完成"
（``_advance_execution``），它假定每条航次都在本周期内飞完。一旦真实
\(T_{\mathrm{traj}}\) 超过周期长度，上层仍会把任务判完成、把机位挪回机巢。
这个矛盾必须由环**当场报出来**，不能靠"通常不会超"来掩盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

Vec3 = tuple[float, float, float]


# ---------------------------------------------------------------------------
# 交换物
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TourStop:
    """一条航次里的一个停留点（起点/任务点/终点机巢统一形状）。"""

    id: int
    kind: str  # "nest" | "task"
    position: Vec3
    service_time_s: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in ("nest", "task"):
            raise ValueError(f"stop.kind 只能是 nest/task：{self.kind}")
        if len(self.position) != 3:
            raise ValueError(f"stop.position 必须是三维：{self.position}")
        if self.service_time_s < 0.0:
            raise ValueError(f"service_time_s 不能为负：{self.service_time_s}")


@dataclass(frozen=True)
class TourOrder:
    """上层 → 下层：本周期决定派出去的一条航次。

    ``t0`` 是**冻结的出发时刻**（上层排程定的），下层只按它排、不改它。
    ``stops`` 首项是起飞点、末项是终点机巢（与 ``PlanRequest.stops`` 同构）。
    ``task_ids`` 是这条航次真正承载的任务 id——失败回池时靠它认身份，
    不从 ``stops`` 里反推（机巢停留点也在里面）。
    """

    uav_id: int
    t0: float
    start_position: Vec3
    stops: tuple[TourStop, ...]
    meta: Mapping[str, Any] = field(default_factory=dict)
    task_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.stops:
            raise ValueError(f"机 {self.uav_id} 的航次没有任何停留点")
        if self.stops[-1].kind != "nest":
            raise ValueError(f"机 {self.uav_id} 的航次末项必须是终点机巢")


@dataclass(frozen=True)
class TourTicket:
    """下层 → 上层：一条航次的真实代价（不是估计）。

    ``time_source``：``planned`` = 时空联合优化的航迹；``estimate`` = 退化到
    定速估算（下层已声明降级）；``failed`` = 无交付航迹，``failure`` 给失败码。

    ``gate``：下层交付闸的三元组（``ok`` / ``worst_clearance_m`` / ``first``），取自
    ``PlanResult.diagnostics["global_gate"]``。有了它，"这条航迹凭什么能交付"不必
    回头翻日志；失败票也能说清是稠密净空还是真几何拦下的。
    """

    uav_id: int
    ok: bool
    t0: float
    t_end: float = 0.0
    duration_s: float = 0.0
    path_len_m: float = 0.0
    time_source: str = "planned"
    failure: str | None = None
    gate: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.ok and self.failure is not None:
            raise ValueError(f"机 {self.uav_id}：成功票不得带失败码")
        if not self.ok and self.time_source != "failed":
            raise ValueError(f"机 {self.uav_id}：失败票的 time_source 必须是 failed")


@dataclass(frozen=True)
class PlannerReport:
    """一次"排一批"的结果（下层对本周期的答卷）。"""

    tickets: Mapping[int, TourTicket]
    moved: Mapping[int, str] = field(default_factory=dict)
    conflicts_before: int = 0
    conflicts_after: int = 0
    elapsed_s: float = 0.0
    #: 有单但没拿到航迹的机号（与 ``tickets`` 里 ``ok=False`` 同集，显式留一份便于对账）
    unplanned: tuple[int, ...] = ()


@dataclass(frozen=True)
class CycleSnapshot:
    """上层一个周期的决定（运行完一个周期后取到的切面）。"""

    cycle: int
    t_start: float
    t_end: float
    orders: tuple[TourOrder, ...]
    executed_ids: tuple[int, ...] = ()
    est_cost: Mapping[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class CyclePlan:
    """环对一个周期的记账：上层派了什么、下层排了多久、谁超框。"""

    cycle: int
    t_start: float
    t_end: float
    report: PlannerReport
    est_cost: Mapping[int, float]
    busy_until: Mapping[int, float]
    # 上层本周期判为"已派出"的任务（``CycleSnapshot.executed_ids`` 原样带过来）
    executed_ids: tuple[int, ...] = ()
    # t_end 晚于周期结束的机号：上层"周期末即完成"的模型盖不住它们
    overruns: tuple[int, ...] = ()
    pruned: tuple[int, ...] = ()
    # 本拍失败回池的任务 id（来自失败票的 ``TourOrder.task_ids``）：下一拍可重投
    rolled_back: tuple[int, ...] = ()


@dataclass(frozen=True)
class LoopTrace:
    """整轮闭环的账。``overrun_cycles`` 非空 = 上层执行模型与本模块口径不合。"""

    cycles: tuple[CyclePlan, ...]
    executed_ids: tuple[int, ...]
    overrun_cycles: tuple[int, ...]
    failures: Mapping[int, str] = field(default_factory=dict)

    @property
    def planned_duration_s(self) -> float:
        return sum(
            t.duration_s for c in self.cycles for t in c.report.tickets.values() if t.ok
        )


# ---------------------------------------------------------------------------
# 端口（两侧适配器要满足的形状）
# ---------------------------------------------------------------------------


@runtime_checkable
class AllocatorPort(Protocol):
    """上层端口：推进一个滚动周期，给出本周期派出的航次。"""

    def step(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        ...


@runtime_checkable
class PlannerPort(Protocol):
    """下层端口：按一批冻结 t0 的航次排真实航迹，并维护已发布航迹。"""

    def plan_cycle(self, orders: Sequence[TourOrder]) -> PlannerReport:
        ...

    def busy_until(self, uav_id: int) -> float:
        ...

    def prune(self, now: float) -> tuple[int, ...]:
        ...
