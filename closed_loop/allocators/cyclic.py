# -*- coding: utf-8 -*-
"""定点投放 + 最近空闲机的多周期上层（``AllocatorPort`` 的一个具体实现）。

职责：给出"本拍有哪些任务、哪几台机空着、把任务配给谁"，并产出冻结 ``t0`` 的
      ``TourOrder``。每个任务点带 ``release_cycle``，只在那一拍投放。
边界：不排航迹、不改几何、不猜机体能力；机在飞就跳过（``busy_of``）。任务点由
      调用方从配置读好后构造 ``LoopTask``——本模块**不 import 两层算法代码**。
相关：``closed_loop/protocol.py``（``AllocatorPort``）、``closed_loop/orchestrator.py``
      （驱动）、``path_planning/experiments/exp06_cycles/run.py``（使用者）。

**为什么不直接接 ``task_allocation.RollingHorizon``**：那套上层带 DMDE 任务选择，
它的解依赖一批手工估价值；用它验"多周期航迹"会把两件事混在一起——到底是规划错了
还是分配错了。这里用"定点投放 + 最近空闲机"把分配侧压成确定性的，环里唯一的不确定
量就只剩真实航迹本身。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from closed_loop.protocol import CycleSnapshot, TourOrder, TourStop

__all__ = ["CyclicTaskAllocator", "LoopTask", "TourAssignment"]

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class LoopTask:
    """一个按周期投放的任务点。``release_cycle`` 从 1 起。"""

    id: int
    position: Vec3
    service_time_s: float = 0.0
    release_cycle: int = 1

    def __post_init__(self) -> None:
        if len(self.position) != 3:
            raise ValueError(f"任务 {self.id} 的位置必须是三维：{self.position}")
        if self.service_time_s < 0.0:
            raise ValueError(f"任务 {self.id} 的服务时长不能为负：{self.service_time_s}")
        if int(self.release_cycle) < 1:
            raise ValueError(f"任务 {self.id} 的 release_cycle 从 1 起：{self.release_cycle}")


@dataclass(frozen=True)
class TourAssignment:
    """一次派活的账（用于落盘与审计，不进 ``CycleSnapshot``）。"""

    cycle: int
    uav_id: int
    task_id: int
    est_cost_m: float


class CyclicTaskAllocator:
    """定点投放 + 最近空闲机的多周期上层。

    Args:
        tasks:        ``LoopTask`` 序列（每个任务点带 ``release_cycle``）
        homes:        机号 → 起飞/回巢点（机巢正上方 ``takeoff_agl_m``），同时充当机号全集
        home_nest:    机号 → 机巢号（航次末项写它，环里才能校验"末项必须是巢"）
        cycle_length_s / n_cycles: 周期长度与周期数
        busy_of:      机号 → 忙到何时（秒）；``<= t_start`` 即视为空闲。缺省全空闲
        service_time_of: 覆盖任务自带的服时长的显式映射
    """

    def __init__(
        self,
        tasks: Sequence[LoopTask],
        homes: Mapping[int, Vec3],
        home_nest: Mapping[int, int],
        *,
        cycle_length_s: float,
        n_cycles: int,
        busy_of: Callable[[int], float] | None = None,
        service_time_of: Mapping[int, float] | None = None,
    ) -> None:
        if cycle_length_s <= 0.0:
            raise ValueError(f"周期长度必须为正：{cycle_length_s}")
        if n_cycles <= 0:
            raise ValueError(f"周期数必须为正：{n_cycles}")
        if not homes:
            raise ValueError("机队为空：没有任何起飞点")
        ids = {int(t.id) for t in tasks}
        if len(ids) != len(tasks):
            raise ValueError("loop.tasks 里有重复的任务 id")
        self.tasks = tuple(tasks)
        self.homes = {int(k): tuple(float(v) for v in p) for k, p in homes.items()}
        self.home_nest = {int(k): int(v) for k, v in home_nest.items()}
        missing = sorted(set(self.homes) - set(self.home_nest))
        if missing:
            raise ValueError(f"机 {missing} 缺机巢号：航次末项必须回巢")
        self.cycle_length_s = float(cycle_length_s)
        self.n_cycles = int(n_cycles)
        self.busy_of = busy_of or (lambda _uid: 0.0)
        self.service_time_of = dict(service_time_of or {})
        self._cycle = 0
        self.assignments: list[TourAssignment] = []
        #: 本周期没派出去的任务（机不够用），按周期留痕
        self.unassigned: dict[int, tuple[int, ...]] = {}

    # ------------------------------------------------------------------
    def step(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        """推进一个周期。``new_tasks`` 只接受额外的 ``LoopTask``。"""
        if self._cycle >= self.n_cycles:
            raise ValueError(f"周期已跑满 {self.n_cycles} 拍，不能再 step")
        self._cycle += 1
        cycle = self._cycle
        t_start = (cycle - 1) * self.cycle_length_s
        t_end = t_start + self.cycle_length_s

        if new_tasks:
            extra = tuple(new_tasks)
            if not all(isinstance(t, LoopTask) for t in extra):
                raise ValueError("new_tasks 只接受 LoopTask：任务点由配置投放")
            self.tasks = self.tasks + extra

        released = sorted((t for t in self.tasks if t.release_cycle == cycle), key=lambda t: t.id)
        free = sorted(uid for uid in self.homes if float(self.busy_of(uid)) <= t_start)

        orders: list[TourOrder] = []
        est_cost: dict[int, float] = {}
        executed: list[int] = []
        leftovers: list[int] = []
        for task in released:
            if not free:
                leftovers.append(int(task.id))
                continue
            uid = min(free, key=lambda u: _dist(self.homes[u], task.position))
            free.remove(uid)
            order, cost = self._order(uid, task, t_start, cycle)
            orders.append(order)
            est_cost[uid] = cost
            executed.append(int(task.id))
            self.assignments.append(TourAssignment(cycle, uid, int(task.id), cost))
        if leftovers:
            self.unassigned[cycle] = tuple(leftovers)

        return CycleSnapshot(
            cycle=cycle,
            t_start=t_start,
            t_end=t_end,
            orders=tuple(orders),
            executed_ids=tuple(executed),
            est_cost=est_cost,
        )

    # ------------------------------------------------------------------
    def _order(self, uid: int, task: LoopTask, t0: float,
               cycle: int) -> tuple[TourOrder, float]:
        home = self.homes[uid]
        nest_id = self.home_nest[uid]
        service = float(self.service_time_of.get(int(task.id), task.service_time_s))
        stops = (
            TourStop(id=int(nest_id), kind="nest", position=home, service_time_s=0.0),
            TourStop(id=int(task.id), kind="task", position=tuple(task.position),
                     service_time_s=service),
            TourStop(id=int(nest_id), kind="nest", position=home, service_time_s=0.0),
        )
        cost = _dist(home, task.position) * 2.0
        return (
            TourOrder(
                uav_id=uid,
                t0=float(t0),
                start_position=home,
                stops=stops,
                meta={"release_cycle": cycle, "task_id": int(task.id)},
                task_ids=(int(task.id),),
            ),
            cost,
        )


def _dist(a: Vec3, b: Vec3) -> float:
    return math.dist(tuple(float(v) for v in a), tuple(float(v) for v in b))
