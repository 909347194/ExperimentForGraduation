# -*- coding: utf-8 -*-
"""uav_state.py - 无人机运行状态管理(可用识别、状态更新、故障 / 释放)。

对应论文 3.1(2) 与第 6 节的闭环反馈:

    U_t^avail = { u ∈ U : u 处于空闲且可用 }     K_avail(t) = |U_t^avail|

状态机(common.types.UAVStatus)::

    IDLE ──assign──► BUSY ──任务全部完成──► IDLE
      │                │
      ├──mark_charging─► CHARGING ──restore──► IDLE
      └──mark_fault────► FAULT ──restore────► IDLE
                           │
                           └── 未完成任务释放回任务池(论文 T_t^release)

与相邻模块的衔接:

    available()        ->  methods.selection.run / muas.SelectiveMUASProblem
                          (两者都自带 u.is_available 过滤,传全量列表即可)
    apply_solution()   <-  common.types.AllocationSolution / UAVTour
    mark_fault()       ->  返回未完成 task_id,交给 methods.task_pool.TaskPool.release()

与 ``task_pool`` 不互相 import:故障→释放任务这一步由滚动层(methods/rolling)
或实验脚本显式串联,避免两个状态机互相耦合。
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator, Sequence

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    UAV,
    UAVStatus,
)
from task_allocation.methods.muas.constraints.nest_capacity import (
    CapacityReport,
    check_nest_capacity,
    fleet_occupancy,
)

__all__ = [
    "available_uavs",
    "count_available",
    "UAVFleet",
    "CapacityViolation",
]


class CapacityViolation(RuntimeError):
    """落地期机巢容量硬约束被违反（design_nest_state.md §6.2）。

    规划期的解在执行反馈后可能失效，落地期是最后一道防线：
    超容时拒绝回写并告警，不得静默接受。
    """

    def __init__(self, report: CapacityReport) -> None:
        self.report = report
        super().__init__(
            "机巢容量超容，拒绝回写解："
            + ", ".join(
                f"nest {nid} 末态 {r['occupied_after']}/{r['capacity']}"
                for nid, r in report.per_nest.items()
                if nid in report.violations
            )
        )


# ---------------------------------------------------------------------------
# 无状态便捷函数(只做 U_t^avail 识别)
# ---------------------------------------------------------------------------


def available_uavs(uavs: Iterable[UAV]) -> list[UAV]:
    """当前可用无人机集合 U_t^avail(空闲且可用)。"""
    return [u for u in uavs if u.is_available]


def count_available(uavs: Iterable[UAV]) -> int:
    """当前可用无人机规模 K_avail(t)。"""
    return sum(1 for u in uavs if u.is_available)


# ---------------------------------------------------------------------------
# 有状态管理
# ---------------------------------------------------------------------------


class UAVFleet:
    """无人机机队状态管理。

    典型用法(一个滚动周期)::

        fleet = UAVFleet(uavs)
        avail = fleet.available()                 # U_t^avail -> selection / MUAS
        fleet.apply_solution(solution)            # 求解结果回写：BUSY + 绑定任务
        fleet.complete_tasks(uid, done_ids)       # 反馈：完成（全清自动回 IDLE）
        stranded = fleet.mark_fault(uid)          # 反馈：故障，返回未完成 task_id
        pool.release(stranded, reason="uav_fault")  # 释放回任务池

    绑定关系（UAV ↔ 执行中的任务）只在本类维护；任务池侧用
    :meth:`task_pool.TaskPool.uav_of` 查同一映射。

    注意：本类**不持有** Task 对象，故障 / 释放相关方法返回的都是
    ``task_id``；真实任务对象由 ``TaskPool.release()`` / ``complete()`` 取出。
    """

    def __init__(
        self,
        uavs: Iterable[UAV] | None = None,
        *,
        current_time: float = 0.0,
    ) -> None:
        self._uavs: dict[int, UAV] = {}
        self._bindings: dict[int, list[int]] = {}  # uav_id -> [task_id, ...]
        self._current_time = float(current_time)
        if uavs is not None:
            self.register(uavs)

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------

    def register(self, uavs: Iterable[UAV]) -> list[UAV]:
        """登记 UAV(id 重复报错)。"""
        added: list[UAV] = []
        for uav in uavs:
            if uav.id in self._uavs:
                raise ValueError(f"UAV id 重复:{uav.id}")
            self._uavs[uav.id] = uav
            self._bindings.setdefault(uav.id, [])
            added.append(uav)
        return added

    # ------------------------------------------------------------------
    # 只读查询
    # ------------------------------------------------------------------

    @property
    def current_time(self) -> float:
        return self._current_time

    def all(self) -> list[UAV]:
        """全部 UAV。"""
        return list(self._uavs.values())

    def available(self) -> list[UAV]:
        """当前可用无人机集合 U_t^avail。"""
        return [u for u in self._uavs.values() if u.is_available]

    def count_available(self) -> int:
        """当前可用规模 K_avail(t)。"""
        return sum(1 for u in self._uavs.values() if u.is_available)

    def by_status(self, status: UAVStatus) -> list[UAV]:
        return [u for u in self._uavs.values() if u.status == status]

    def busy(self) -> list[UAV]:
        return self.by_status(UAVStatus.BUSY)

    def charging(self) -> list[UAV]:
        return self.by_status(UAVStatus.CHARGING)

    def faulty(self) -> list[UAV]:
        return self.by_status(UAVStatus.FAULT)

    def get(self, uav_id: int) -> UAV:
        try:
            return self._uavs[uav_id]
        except KeyError:
            raise KeyError(f"UAV {uav_id} 未登记") from None

    def find(self, uav_id: int) -> UAV | None:
        return self._uavs.get(uav_id)

    def bound_tasks(self, uav_id: int) -> list[int]:
        """该 UAV 当前绑定的 task_id(执行中),按绑定顺序。"""
        self.get(uav_id)  # 校验存在
        return list(self._bindings.get(uav_id, ()))

    def uav_of_task(self, task_id: int) -> int | None:
        """执行该任务的 UAV id;无人绑定返回 None。"""
        for uav_id, task_ids in self._bindings.items():
            if task_id in task_ids:
                return uav_id
        return None

    def stats(self) -> dict[str, int]:
        """机队状态统计,可直接并入 metrics.json。"""
        counts = {
            "n_uav": len(self._uavs),
            "n_available": self.count_available(),
        }
        for status in UAVStatus:
            counts[f"n_{status.value}"] = sum(
                1 for u in self._uavs.values() if u.status == status
            )
        counts["n_bound_tasks"] = sum(len(v) for v in self._bindings.values())
        return counts

    def __len__(self) -> int:
        return len(self._uavs)

    def __contains__(self, uav_id: int) -> bool:
        return uav_id in self._uavs

    def __iter__(self) -> Iterator[UAV]:
        return iter(self._uavs.values())

    # ------------------------------------------------------------------
    # 状态流转
    # ------------------------------------------------------------------

    def assign(self, uav_id: int, task_ids: Sequence[int]) -> UAV:
        """分配任务给 UAV:置 BUSY 并绑定任务序列。

        只允许 IDLE 的 UAV 接受新任务(论文 3.1(2):执行中的无人机
        不参与当前周期的新任务分配)。
        """
        uav = self.get(uav_id)
        if uav.status != UAVStatus.IDLE:
            raise ValueError(
                f"UAV {uav_id} 状态为 {uav.status.value},只有 IDLE 才能接受新任务"
            )
        self._bindings[uav_id] = list(task_ids)
        uav.status = UAVStatus.BUSY
        return uav

    def bind_tasks(self, uav_id: int, task_ids: Sequence[int]) -> list[int]:
        """只更新绑定关系,不改状态;返回绑定的 task_id。"""
        self.get(uav_id)
        self._bindings[uav_id] = list(task_ids)
        return list(task_ids)

    def mark_busy(self, uav_id: int) -> UAV:
        uav = self.get(uav_id)
        uav.status = UAVStatus.BUSY
        return uav

    def mark_charging(self, uav_id: int) -> UAV:
        uav = self.get(uav_id)
        uav.status = UAVStatus.CHARGING
        return uav

    def mark_idle(self, uav_id: int) -> list[int]:
        """释放 UAV，回到 IDLE。

        解绑并返回仍挂在它名下的 task_id——正常流程下应为空（任务已
        complete）；若非空，调用方应把它们交给 ``TaskPool.release()``，
        避免任务悬空。
        """
        uav = self.get(uav_id)
        uav.status = UAVStatus.IDLE
        return self._unbind_and_return(uav_id)

    def mark_fault(self, uav_id: int) -> list[int]:
        """UAV 故障（FAULT）。

        按论文 3.1(2)，其尚未完成的任务需要重新释放至动态任务池：
        本方法解绑并返回这些 **task_id**，由调用方执行
        ``TaskPool.release(ids, reason="uav_fault")``。

        对已处于 FAULT 且无绑定任务的 UAV 重复调用返回空列表（幂等）。
        """
        uav = self.get(uav_id)
        if uav.status == UAVStatus.FAULT and not self._bindings.get(uav_id):
            return []
        uav.status = UAVStatus.FAULT
        return self._unbind_and_return(uav_id)

    def restore(self, uav_id: int) -> UAV:
        """维修 / 充电完成,恢复到 IDLE。"""
        uav = self.get(uav_id)
        if uav.status == UAVStatus.BUSY:
            raise ValueError(f"UAV {uav_id} 正在执行任务,不能直接恢复为 IDLE")
        uav.status = UAVStatus.IDLE
        return uav

    def complete_tasks(self, uav_id: int, task_ids: Sequence[int]) -> list[int]:
        """记录 UAV 完成部分任务，解除对应绑定。

        当绑定清空时自动回到 IDLE（对应“任务全部完成 -> IDLE”）。
        返回本次完成的 task_id。
        """
        uav = self.get(uav_id)
        bound = self._bindings.get(uav_id, [])
        unknown = [tid for tid in task_ids if tid not in bound]
        if unknown:
            raise ValueError(f"UAV {uav_id} 未绑定这些任务：{unknown}")

        done_ids = set(task_ids)
        self._bindings[uav_id] = [tid for tid in bound if tid not in done_ids]
        if not self._bindings[uav_id] and uav.status == UAVStatus.BUSY:
            uav.status = UAVStatus.IDLE
        return list(task_ids)

    # ------------------------------------------------------------------
    # 状态更新(位置 / 航程)
    # ------------------------------------------------------------------

    def set_time(self, current_time: float) -> None:
        self._current_time = float(current_time)

    def update_position(
        self,
        uav_id: int,
        x: float,
        y: float,
        z: float | None = None,
        *,
        remaining_range: float | None = None,
    ) -> UAV:
        """更新 UAV 位置(与剩余航程)。"""
        uav = self.get(uav_id)
        uav.x, uav.y = float(x), float(y)
        if z is not None:
            uav.z = float(z)
        if remaining_range is not None:
            uav.remaining_range = float(remaining_range)
        return uav

    def consume_range(self, uav_id: int, distance: float) -> float:
        """扣减剩余航程,返回扣减后的剩余航程(不小于 0)。"""
        uav = self.get(uav_id)
        if distance < 0:
            raise ValueError(f"distance 不能为负:{distance}")
        uav.remaining_range = max(0.0, float(uav.remaining_range) - float(distance))
        return uav.remaining_range

    # ------------------------------------------------------------------
    # 与 MUAS 解对接
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 机巢归属（nest_id 的三个写点之二/三；占用一律从 nest_id 推导）
    # ------------------------------------------------------------------

    def nest_occupancy(self, nests: Sequence[Nest] | None = None) -> dict[int, int]:
        """各机巢当前归属架数（含飞行中、含未降落）。

        给出 ``nests`` 时为所有机巢补 0，便于直接并进 metrics。
        """
        occ = fleet_occupancy(self._uavs.values())
        if nests is not None:
            for n in nests:
                occ.setdefault(n.id, 0)
        return occ

    def reassign_nest(self, uav_id: int, nest_id: int | None) -> UAV:
        """显式改归属（分配决策 / 回收 / 转场）；触发容量变化。

        这是 ``nest_id`` 的**唯一写点**之一（另一个是 :meth:`decommission`）：
        起飞与降落一律不改归属（design_nest_state.md §5）。
        """
        uav = self.get(uav_id)
        uav.nest_id = nest_id
        return uav

    def decommission(self, uav_id: int) -> UAV:
        """退役：清空归属，释放名额。"""
        uav = self.get(uav_id)
        uav.nest_id = None
        uav.status = UAVStatus.FAULT
        self._bindings[uav_id] = []
        return uav

    # ------------------------------------------------------------------
    # 解回写
    # ------------------------------------------------------------------

    def apply_solution(
        self,
        solution: AllocationSolution,
        nests: Sequence[Nest] | None = None,
        *,
        strict_capacity: bool = True,
        on_violation: Callable[[CapacityReport], None] | None = None,
    ) -> list[int]:
        """把 MUAS 解回写到机队：终点机巢 ``z_ub`` + 任务绑定 + 状态。

        顺序严格为**先校验、后写入**（design_nest_state.md §9.3）：

        1. 落地期硬校验：按周期末归属算容量，超容则拒绝并告警；
        2. 写入 ``nest_id``（唯一写点）；
        3. 绑定任务并置 BUSY（空航次回到 IDLE）。

        Args:
            solution:        MUAS 解。
            nests:           机巢列表；给出则执行容量硬校验。
            strict_capacity: True 时超容直接抛 :class:`CapacityViolation`；
                             False 时仅通过 ``on_violation`` 告警并继续写入。
            on_violation:    容量违反的回调（用于记日志 / metrics）。

        Returns:
            本周期进入 BUSY 的 uav_id。
        """
        if not solution.validate_unique_tasks():
            raise ValueError("解中存在重复分配的任务,无法回写机队状态")

        # 1) 硬校验（与规划期共用同一份判定，避免口径漂移）
        if nests:
            assignments = {t.uav_id: t.end_nest_id for t in solution.tours}
            report = check_nest_capacity(list(self._uavs.values()), nests, assignments)
            if report.violations:
                if on_violation is not None:
                    on_violation(report)
                if strict_capacity:
                    raise CapacityViolation(report)

        # 2) 写入终点机巢（唯一写点）
        for tour in solution.tours:
            if tour.end_nest_id is not None:
                self.reassign_nest(tour.uav_id, tour.end_nest_id)

        # 3) 状态与绑定
        busy_ids: list[int] = []
        for tour in solution.tours:
            if tour.task_ids:
                self.assign(tour.uav_id, tour.task_ids)
                busy_ids.append(tour.uav_id)
            else:
                if self.get(tour.uav_id).status == UAVStatus.IDLE:
                    self.mark_idle(tour.uav_id)
        return busy_ids

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _unbind_and_return(self, uav_id: int) -> list[int]:
        """解绑并返回被释放的 task_id。"""
        task_ids = self._bindings.get(uav_id, [])
        self._bindings[uav_id] = []
        return list(task_ids)

    # ------------------------------------------------------------------
    # 产物
    # ------------------------------------------------------------------

    def snapshot_json(self) -> dict[str, object]:
        """机队状态的可机读切面(用于 results/*.json)。"""
        return {
            "current_time": self._current_time,
            "stats": self.stats(),
            "nest_occupancy": {
                str(k): v for k, v in sorted(self.nest_occupancy().items())
            },
            "uavs": [
                {
                    "id": u.id,
                    "status": u.status.value,
                    "x": round(u.x, 3),
                    "y": round(u.y, 3),
                    "z": round(u.z, 3),
                    "remaining_range": round(float(u.remaining_range), 3),
                    "nest_id": u.nest_id,
                    "bound_task_ids": self._bindings.get(u.id, []),
                }
                for u in self._uavs.values()
            ],
        }
