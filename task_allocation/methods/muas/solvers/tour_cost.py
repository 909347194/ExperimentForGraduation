# -*- coding: utf-8 -*-
"""tour_cost.py — 航次代价 / ETA / 时间窗 / 迟到键原语（``TourCostMixin``）。

2026-10-27 自 ``muas_stage.py`` 拆出。解码修复、构造式解码、成本 polish
三组算子**共用**这些原语；口径的唯一来源（ETA = DEM 三维航程 / speed，
早到等待 ``earliest``、离开加服务时间；航程 = 起点→任务链→终点巢），
任何算子不得自行复制一份实现——历史上「判定与实际两套长度」的缺陷
（2026-10-13 统一口径）正是复制实现造成的。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from task_allocation.methods.muas.constraints.coop_constraints import (
    arrival_times,
    task_window,
)


class TourCostMixin:
    """航次代价 / ETA / 时间窗原语（混入 :class:`MUASAssignmentEvaluator`）。"""

    def _task_latest(self, tid: int) -> float | None:
        """任务的 deadline：经 ``coop_constraints.task_window`` 读取（唯一字段口径）。"""
        task = self._task_by_id.get(tid)
        if task is None:
            return None
        return task_window(task)[1]

    def _nearest_nest_cost(self, node_id: int) -> float:
        """到最近可用机巢的代价；终点尚未定稿时用于保守估计返航段。"""
        best = float("inf")
        for nest in self.nests:
            if not nest.available or nest.capacity <= 0:
                continue
            c = float(
                self.problem.costs.cost(node_id, self.problem.costs.nest_key(nest.id))
            )
            if c < best:
                best = c
        return best if np.isfinite(best) else 0.0

    def _return_leg_cost(
        self,
        node_id: int,
        end_nest_id: int | None,
    ) -> float:
        """末节点 → 终点机巢的代价；终点未定时退化为最近机巢（保守）。"""
        if end_nest_id is not None:
            return float(
                self.problem.costs.cost(
                    node_id, self.problem.costs.nest_key(end_nest_id)
                )
            )
        return self._nearest_nest_cost(node_id)

    def _tour_cost(
        self,
        uav_id: int,
        tids: Sequence[int],
        end_nest_id: int | None,
    ) -> float:
        """航次总代价：起点 → 各任务 → 终点机巢。"""
        prev: int = -(1 + uav_id)
        total = 0.0
        for tid in tids:
            total += float(self.problem.costs.cost(prev, tid))
            prev = tid
        if tids:
            total += self._return_leg_cost(tids[-1], end_nest_id)
        return float(total)

    def _arrival_time_of(
        self,
        uav_id: int,
        tids: Sequence[int],
        pos: int,
    ) -> float:
        """航次中第 ``pos`` 个任务的预计到达时刻。

        到达游标委托 :func:`~task_allocation.methods.muas.constraints.coop_constraints.arrival_times`
        （与时间窗惩罚 / 航迹时间 / :meth:`_eta_list` **同一实现**，口径不分叉）：
        **三维航程** / speed（``costs.travel``），早于 ``earliest`` 则等待，
        离开时累加 ``default_service_time``；等待后再判 latest（完成闸门正是
        这样判的，用等待前时刻会比闸门宽松、补进实际会违约的任务）。

        ``pos`` 按 ``tids`` 原始下标计（含缺失任务的空位）；越界或落在缺失
        位上时返回全航次结束时刻（历史语义）。
        """
        uav = self._uav_by_id.get(uav_id)
        speed = max(float(getattr(uav, "speed", 10.0) or 10.0), 1e-6)
        service = float(getattr(self.problem.config, "default_service_time", 0.0))
        start = (
            (float(uav.x), float(uav.y), float(uav.z))
            if uav is not None else (0.0, 0.0, 0.0)
        )
        slots = [self._task_by_id.get(tid) for tid in tids]
        arrivals = arrival_times(
            start,
            slots,
            speed=speed,
            current_time=float(self.config.current_time),
            service_time=service,
            distance_fn=self.problem.costs.travel,
        )
        if 0 <= pos < len(arrivals) and slots[pos] is not None:
            return arrivals[pos]
        last_real = next(
            (i for i in range(len(slots) - 1, -1, -1) if slots[i] is not None),
            None,
        )
        if last_real is None:
            return float(self.config.current_time)
        return arrivals[last_real] + service

    def _window_ok(self, uav_id: int, tids: Sequence[int], pos: int) -> bool:
        """第 ``pos`` 个任务能否在其时间窗内到达（True=不违反约束）。"""
        task = self._task_by_id.get(tids[pos]) if pos < len(tids) else None
        if task is None:
            return True
        latest = task_window(task)[1]
        if latest is None:
            return True
        return self._arrival_time_of(uav_id, tids, pos) <= float(latest) + 1e-6

    def _window_ok_all(self, uav_id: int, tids: Sequence[int]) -> bool:
        """**整条航次**所有任务都满足时间窗（True=无一迟到）。

        与 :meth:`_window_ok` 的差别：后者只校验被插入位置上的任务，
        放行「把同航次后续任务推迟到 deadline 之后」的插入——单点可行、
        全航次违约。Fix⑦ 的换机迁移用它做硬门：可达即执行，但不得把
        已守约的任务搞成迟到（用户方法论：违反约束是硬边界，不是可接受的
        代价；实测 exp01 换机版 95%→85% 正是单点校验放行 3 个新违约所致）。
        """
        for pos in range(len(tids)):
            if not self._window_ok(uav_id, tids, pos):
                return False
        return True

    def _eta_list(self, uav_id: int, tids: Sequence[int]) -> list[float]:
        """单趟算航次各任务的预计到达时刻（与 :meth:`_arrival_time_of` 同口径）。

        ``_arrival_time_of`` 每查一个位置重算整条前缀（O(n²/趟）；
        ``_best_order`` 的 5 候选评估就这么用，搜索期每代每体一次可接受）。
        交付后 polish 的邻域是 O(n²) 个候选，逐个 O(n²) 会到 O(n⁴)——
        这里改成单趟 O(n) 累加（与 ``_polish_routes_monotone.arrivals_of``
        同结构），测试以逐位相等锁死与 ``_arrival_time_of`` 的一致性。
        """
        uav = self._uav_by_id.get(uav_id)
        speed = max(float(getattr(uav, "speed", 10.0) or 10.0), 1e-6)
        service = float(getattr(self.problem.config, "default_service_time", 0.0) or 0.0)
        start = (
            (float(uav.x), float(uav.y), float(uav.z))
            if uav is not None else (0.0, 0.0, 0.0)
        )
        return arrival_times(
            start,
            [self._task_by_id.get(tid) for tid in tids],
            speed=speed,
            current_time=float(self.config.current_time),
            service_time=service,
            distance_fn=self.problem.costs.travel,
        )

    def _late_key(self, uav_id: int, tids: Sequence[int]) -> tuple[int, float]:
        """航次迟到键 ``(迟到任务数, 迟到总量秒)``；无时间窗时恒 ``(0, 0)``。"""
        n_late = 0
        late_total = 0.0
        for tid, t_arr in zip(tids, self._eta_list(uav_id, tids)):
            latest = self._task_latest(tid)
            if latest is not None and t_arr > float(latest) + 1e-6:
                n_late += 1
                late_total += t_arr - float(latest)
        return (n_late, round(late_total, 6))

    def _range_limit(self, uav_id: int) -> float:
        """UAV 航程预算；未知 UAV 返回 +inf（不设限）。"""
        uav = self._uav_by_id.get(uav_id)
        return float(uav.remaining_range) if uav is not None else float("inf")

    def _removal_saving(
        self,
        uav_id: int,
        tids: Sequence[int],
        pos: int,
        end_nest_id: int | None,
    ) -> float | None:
        """移除 ``tids[pos]`` 节省的路径代价（>0 表示值得移除）。"""
        if pos < 0 or pos >= len(tids):
            return None
        tid = tids[pos]
        prev_id = -(1 + uav_id) if pos == 0 else tids[pos - 1]
        is_last = pos == len(tids) - 1
        next_key = self._return_leg_cost(tid, end_nest_id) if is_last else None

        c_prev_t = float(self.problem.costs.cost(prev_id, tid))
        if is_last:
            # 末位任务：移除后由前驱直接返航
            c_t_next = float(next_key)  # type: ignore[arg-type]
            c_prev_next = self._return_leg_cost(prev_id, end_nest_id)
        else:
            next_id = tids[pos + 1]
            c_t_next = float(self.problem.costs.cost(tid, next_id))
            c_prev_next = float(self.problem.costs.cost(prev_id, next_id))
        return float(c_prev_t + c_t_next - c_prev_next)

