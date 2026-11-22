# -*- coding: utf-8 -*-
"""上层适配器：``task_allocation.methods.rolling.RollingHorizon`` → ``AllocatorPort``。

职责：推进一个滚动周期，把该周期的航次快照翻成 ``TourOrder``（起飞点、任务点、
       终点机巢、冻结出发时刻），连同 ``est_cost`` 与执行集一起交付给环。
边界：不重排、不改解、不碰 MUAS/DMDE；选择与求解全部留在上层。

**起飞点口径（v1 的硬前提）**：上层在周期末把机位挪回机巢、任务判完成，因此
本适配器要求每个航次都有 ``start_nest_id``——即"上一周期已落地、本周期从巢起飞"。
出现"在飞中改派"（起点在途）时直接报错：那种情形需要上层把指派与结算拆开、
让 ``busy_until`` 接管，不在本适配器的职责内（见 README §5）。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from task_allocation.methods.rolling.horizon import RollingHorizon

from ..protocol import CycleSnapshot, TourOrder, TourStop


class RollingHorizonAdapter:
    """把滚动时域装成 ``AllocatorPort``。"""

    def __init__(self, horizon: RollingHorizon) -> None:
        self.horizon = horizon
        self.cycle_length = float(horizon.hcfg.cycle_length)
        if self.cycle_length <= 0.0:
            raise ValueError(f"cycle_length 必须为正：{self.cycle_length}")

    # ------------------------------------------------------------------
    def step(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        record = self.horizon.run_cycle(new_tasks, advance=True)
        t_end = float(record.current_time)
        orders = tuple(
            self._order(tour, t0=t_end - self.cycle_length, cycle=int(record.cycle))
            for tour in (record.tours or [])
            if tour.get("task_ids")
        )
        return CycleSnapshot(
            cycle=int(record.cycle),
            t_start=t_end - self.cycle_length,
            t_end=t_end,
            orders=orders,
            executed_ids=tuple(int(t) for t in record.executed_ids),
            est_cost={int(t["uav_id"]): float(t.get("est_cost", 0.0))
                      for t in (record.tours or []) if t.get("task_ids")},
        )

    # ------------------------------------------------------------------
    def _order(self, tour: Mapping[str, Any], *, t0: float, cycle: int) -> TourOrder:
        uid = int(tour["uav_id"])
        if tour.get("start_nest_id") is None:
            raise ValueError(
                f"机 {uid} 的航次起点不在机巢（start_nest_id=None）：环 v1 只支持从巢起飞，"
                "在飞中改派需上层先接入 busy_until"
            )
        stops = [self._nest_stop(int(tour["start_nest_id"])), *(
            self._task_stop(int(tid)) for tid in tour["task_ids"]
        )]
        if tour.get("end_nest_id") is None:
            raise ValueError(f"机 {uid} 的航次没有终点机巢（end_nest_id=None）")
        stops.append(self._nest_stop(int(tour["end_nest_id"])))
        return TourOrder(
            uav_id=uid,
            t0=float(t0),
            start_position=(float(tour["start"][0]), float(tour["start"][1]),
                            float(tour["start"][2])),
            stops=tuple(stops),
            meta={"cycle": int(cycle)},
        )

    def _nest_stop(self, nest_id: int) -> TourStop:
        nest = self.horizon._nest_by_id.get(int(nest_id))
        if nest is None:
            raise ValueError(f"未知机巢 {nest_id}：上层的解引用了不存在的巢")
        return TourStop(id=int(nest_id), kind="nest", position=(nest.x, nest.y, nest.z))

    def _task_stop(self, task_id: int) -> TourStop:
        task = self.horizon.pool.find(int(task_id))
        if task is None:
            raise ValueError(f"任务 {task_id} 已不在池中：无法为已出池任务排航次")
        service = float((task.meta or {}).get("service_time_s", 0.0))
        return TourStop(
            id=int(task_id), kind="task", position=(task.x, task.y, task.z),
            service_time_s=service,
        )
