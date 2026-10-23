# -*- coding: utf-8 -*-
"""feasibility.py — 可行性校验编排（论文 §5(4) 约束处理）。

职责（回答）：一个解物理可行吗？违了哪些约束、罚多少？

结构/单机/终点机巢/机巢容量/协同五组约束的判定与惩罚编排；细粒度判定在
``constraints`` 包，本模块只负责按 :class:`ConstraintConfig` 的开关组卷并
汇入 ``ConstraintReport``。FeasibilityMixin 混入 SelectiveMUASProblem。
"""

from __future__ import annotations

import math
from typing import Any

from task_allocation.common.types import AllocationSolution
from task_allocation.methods.muas.constraints.nest_capacity import check_nest_capacity
from task_allocation.methods.muas.constraints.single_constraints import (
    check_range_constraint,
    check_time_constraint,
)
from task_allocation.methods.muas.constraints.coop_constraints import (
    check_sequence_constraint,
    check_sync_constraint,
    check_time_window_constraint,
)
from task_allocation.methods.muas.constraints.evaluator import ConstraintReport


class FeasibilityMixin:
    """可行性校验与约束惩罚编排（check_feasible）。"""

    def check_feasible(
        self, solution: AllocationSolution
    ) -> tuple[bool, dict[str, Any]]:
        """校验解的可行性并累加约束惩罚（论文 §5(4) 约束处理）。

        实现已迁至 ``constraints`` 包：单机约束 → ``single_constraints``、
        协同约束 → ``coop_constraints``、机巢容量 → ``nest_capacity``，最终统一
        汇入 ``evaluator.ConstraintReport``。:meth:`ConstraintReport.as_details`
        透出的 ``violations`` / ``penalty`` / ``nest_capacity`` 与历史口径完全一致
        （供滚动层 horizon 与指标 metrics 消费）。
        """
        cons = self.config.constraints
        report = ConstraintReport()

        # ---- 结构类（misc）----
        if cons.duplicate_task:
            if not solution.validate_unique_tasks():
                report.add_misc("duplicate_task_assignment", cons.w_misc * 1e3)

            rel = self.relations_from_solution(solution)
            if not rel["sum_x_eq_y"]:
                report.add_misc("sum_x_neq_y", cons.w_misc * 1e3)

            cand = set(solution.candidate_task_ids) or set(self.task_by_id)
            for tid in solution.executed_task_ids:
                if tid not in cand and tid not in self.task_by_id:
                    report.add_misc(f"unknown_task_{tid}", cons.w_misc * 1e3)

        if cons.require_all_tasks and not self.config.allow_unassigned:
            missing = [
                t.id for t in self.tasks if t.id not in set(solution.executed_task_ids)
            ]
            if missing:
                report.add_misc("unassigned_not_allowed", 100.0 * len(missing))

        if cons.require_no_idle and not self.config.allow_idle_uav:
            for tour in solution.tours:
                if tour.is_idle:
                    report.add_misc(f"idle_uav_{tour.uav_id}", 100.0)

        # ---- 单机约束（single_constraints）----
        for tour in solution.tours:
            u = self.uav_by_id.get(tour.uav_id)
            if u is None:
                report.add_misc(f"unknown_uav_{tour.uav_id}", cons.w_misc * 1e3)
                continue
            pc = self.tour_path_cost(tour)
            tour_ok = True
            if cons.range:
                rv = check_range_constraint(u, pc, margin=1.0)
                if rv > 0:
                    # excess=rv：超限米数单独进 T0 层内 tie-break（不受
                    # w_range 缩放影响），使不可行解之间也有「少超一点
                    # 更好」的梯度。
                    report.add_range(
                        f"range_uav_{tour.uav_id}", cons.w_range * rv, excess=rv
                    )
                    tour_ok = False
            if cons.endurance:
                tv = check_time_constraint(u, pc)
                if tv > 0:
                    report.add_time(
                        f"time_uav_{tour.uav_id}", cons.w_endurance * tv
                    )
            tour.feasible = tour_ok

        # ---- 终点机巢：合法性 + 可用性 + 可达性 ----
        for tour in solution.tours:
            if tour.end_nest_id is None:
                if tour.task_ids:
                    # 有航次却无终点机巢：返航义务未落账（否则返航零成本、
                    # 容量不计账，适应度被系统性低估，恰是方案 A 要避免的
                    # 「进化不考虑返航」陷阱的残余缺口）→ 计违约使缺口在
                    # 评价中可见。空航次（不飞）无返航义务，不计。
                    report.add_misc(
                        f"end_nest_missing_{tour.uav_id}",
                        cons.w_end_nest_reachability * 1e3,
                    )
                    tour.feasible = False
                continue
            nest = self.nest_by_id.get(tour.end_nest_id)
            if nest is None:
                if cons.nest_availability:
                    report.add_misc(
                        f"bad_nest_{tour.end_nest_id}",
                        cons.w_nest_availability * 1e3,
                    )
                continue
            if cons.nest_availability and not nest.available:
                report.add_misc(
                    f"nest_unavailable_{tour.end_nest_id}",
                    cons.w_nest_availability * 100.0,
                )
            # 末任务 → 终点机巢不可达（地形遮挡 / 禁飞区，代价 inf）→ 判违约。
            # 与 muas_stage._choose_end_nests 的改派过滤共用同一判据。
            if cons.end_nest_reachability and tour.task_ids:
                c = self.costs.cost(
                    tour.task_ids[-1], self.costs.nest_key(nest.id)
                )
                if not math.isfinite(float(c)):
                    report.add_misc(
                        f"end_nest_unreachable_{tour.end_nest_id}",
                        cons.w_end_nest_reachability * 1e3,
                    )
                    tour.feasible = False

        # ---- 机巢容量约束（nest_capacity）----
        if cons.nest_capacity and self.nests:
            assignments = {t.uav_id: t.end_nest_id for t in solution.tours}
            fleet = self.fleet_uavs if self.fleet_uavs is not None else self.uavs
            cap_report = check_nest_capacity(fleet, self.nests, assignments)
            nest_tags = [
                f"nest_capacity_{nid}"
                for nid in cap_report.violations
                if cap_report.per_nest[nid]["overflow"] > 0
            ]
            report.add_nest(
                nest_tags,
                cons.w_nest_capacity * cap_report.penalty,
                capacity=cap_report.as_dict(),
            )

        # ---- 协同约束（coop_constraints）----
        if cons.time_window:
            w_pen, w_tags, w_late = check_time_window_constraint(
                solution.tours,
                self.task_by_id,
                self.uav_by_id,
                current_time=float(self.config.current_time),
                service_time=float(self.config.default_service_time),
                distance_fn=self.costs.travel,
            )
            if w_pen > 0:
                report.add_window(
                    w_tags, cons.w_window * w_pen, lateness=w_late
                )
                # 【任务级时间窗不可行 · 2026-10-12 改造】
                # 不再把「含迟到任务的整条 tour」置 feasible=False。
                # tw_task_* 标签已写入 violations（add_window），horizon._violated_task_ids
                # 对其走任务级归因（仅迟到任务自身被拦），同 tour 的准时任务照常完成。
                # 整 tour 兜底（horizon 中 tour.feasible==False 分支）仅对 range/endurance/
                # 终点机巢等真正「飞行不可行」的情形触发，时间窗违约只影响迟到任务本身。
        if cons.sequence:
            s_pen, s_tags = check_sequence_constraint(solution.tours, self.task_by_id)
            if s_pen > 0:
                report.add_seq(s_tags, cons.w_seq * s_pen)
        if cons.sync:
            sy_pen, sy_tags = check_sync_constraint(solution.tours, self.task_by_id)
            if sy_pen > 0:
                report.add_sync(sy_tags, cons.w_sync * sy_pen)

        return report.is_feasible, report.as_details()
