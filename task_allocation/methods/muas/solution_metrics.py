# -*- coding: utf-8 -*-
"""solution_metrics.py — 解的指标计算（F1/F2/F3 与航次代价）。

职责（回答）：一个解的收益、路径代价、最坏机航迹时间是多少？

SolutionMetricsMixin 混入 :class:`~task_allocation.methods.muas.problem.SelectiveMUASProblem`。
只算指标，不做可行性判定（见 feasibility）也不做标量化（见 fitness）。
"""

from __future__ import annotations

from task_allocation.common.types import AllocationSolution, UAVTour


class SolutionMetricsMixin:
    """解指标计算（f1_reward / path_cost / f2_cost / tour_time / F3）。"""

    def f1_reward(self, solution: AllocationSolution) -> float:
        # 有效奖励 = Σ R_j × 折减系数；折减系数按每任务迟到秒数线性衰减
        # （late_reward_decay_seconds=W 时 max(0, 1 − L/W)）。迟到的量取自
        # check_feasible 透出的 lateness 表（时间窗游标的唯一模拟源）。
        total = 0.0
        # 覆盖门禁模式（D5）：F1 回归纯完成计数——迟到折减砍掉，迟到只走
        # w_late·L 单一定价（T2），消「迟到双计」。
        decay = self.config.late_reward_decay_seconds
        if self.config.lexicographic_coverage:
            decay = None
        feas = solution.meta.get("feasibility") or {}
        lateness = feas.get("lateness") or {}
        for tid in solution.executed_task_ids:
            t = self.task_by_id.get(tid)
            if t is None:
                continue
            r = max(t.reward, 0.0)
            if decay is not None and float(decay) > 0:
                late = float(lateness.get(int(tid), 0.0) or 0.0)
                if late > 0.0:
                    r *= max(0.0, 1.0 - late / float(decay))
            total += r
        return float(total)

    def tour_path_cost(self, tour: UAVTour) -> float:
        if not tour.task_ids and tour.end_nest_id is None:
            return 0.0
        cost = 0.0
        prev = -(1 + tour.uav_id)
        for tid in tour.task_ids:
            cost += self.costs.cost(prev, tid)
            prev = tid
        if tour.end_nest_id is not None:
            cost += self.costs.cost(prev, self.costs.nest_key(tour.end_nest_id))
        elif not tour.task_ids:
            return 0.0
        return float(cost)

    def path_cost(self, solution: AllocationSolution) -> float:
        """纯路径代价（不含约束惩罚），供适应度做量纲解耦。"""
        return float(sum(self.tour_path_cost(t) for t in solution.tours))

    def f2_cost(self, solution: AllocationSolution, penalty: float = 0.0) -> float:
        path = sum(self.tour_path_cost(t) for t in solution.tours)
        return float(path + self.config.w_penalty * penalty)

    def violation_penalty(self, solution: AllocationSolution) -> float:
        """解当前的约束惩罚**总量**（硬+软；legacy 加权和口径消费）。

        优先取 :meth:`evaluate` 实际计入 ``f2_cost`` 的惩罚（``meta["penalty"]``），
        否则回落到可行性报告里的惩罚值。四层适应度不消费本方法——
        硬违约走 :meth:`hard_stats`（T0），软违约走 :meth:`soft_violation`（T2）。
        """
        meta = solution.meta or {}
        if "penalty" in meta:
            return float(meta["penalty"])
        feas = meta.get("feasibility") or {}
        return float(feas.get("penalty", 0.0))

    def tour_time(self, tour: UAVTour) -> float:
        """单架 UAV 航迹时间（含飞行 + 等待 + 服务 + 返巢），用于最坏机瓶颈项 F3。

        逐航次模拟到达游标（与 ``coop_constraints.check_time_window_constraint``
        的游标语义一致，但不对迟到计惩罚）：从 UAV 起点出发，按访问顺序累加段飞行
        时间（= 段长 / speed），到点若早于 ``earliest`` 则等待至 ``earliest``，
        离开时累加上 ``default_service_time``，末段返巢到终点机巢。
        返回相对 ``current_time`` 的航迹时长（秒）。

        段长口径：**统一走 DEM 三维航程**（``costs.travel``），不用欧氏直线——
        此前 F3 用直线而代价矩阵用地形代价，同一航次两套长度，F3 与 ETA 系统性
        低估（P1-1）。

        对应 Ming et al. 2017 Eq.1 的 Σ_i Σ_j t(i,j)·x(i,j) 单 UAV 飞行时间；
        F3 = max_k T_k 即其中的 a·max(·) 最坏机项。
        """
        if not tour.task_ids and tour.end_nest_id is None:
            return 0.0
        u = self.uav_by_id.get(tour.uav_id)
        if u is None:
            return 0.0
        speed = max(float(getattr(u, "speed", 0.0) or 0.0), 1e-6)
        t_cursor = float(self.config.current_time)
        prev = tour.start_position
        for tid in tour.task_ids:
            task = self.task_by_id.get(tid)
            if task is None:
                continue
            dist = self.costs.travel(prev, task.position)
            t_cursor += dist / speed
            if task.earliest is not None and t_cursor < task.earliest:
                t_cursor = task.earliest
            t_cursor += float(self.config.default_service_time)
            prev = task.position
        if tour.end_nest_id is not None:
            nest = self.nest_by_id.get(tour.end_nest_id)
            if nest is not None:
                dist = self.costs.travel(prev, nest.position)
                t_cursor += dist / speed
        return float(t_cursor - self.config.current_time)

    def bottleneck_time(self, solution: AllocationSolution) -> float:
        """最坏机航迹时间 F3 = max_k T_k（论文 §5(3) 负载均衡项）。

        空解（无航次）返回 0；若存在非空航次则取各航次 ``tour_time`` 的最大值。
        最小化该项使任务在 UAV 间更均衡，避免单架机成为执行瓶颈。
        """
        if not solution.tours:
            return 0.0
        return max(self.tour_time(t) for t in solution.tours)
