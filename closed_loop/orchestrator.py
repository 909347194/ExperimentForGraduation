# -*- coding: utf-8 -*-
r"""环驱动：每周期"上层派航次 → 下层排真实航迹 → 记账与复检"。

职责：把 ``AllocatorPort`` 与 ``PlannerPort`` 串成 update–reassign–replan 的
      一拍，并把"哪条航次超框、哪台机还在飞、哪些航迹已过期"记进 ``LoopTrace``。
边界：不含算法、不改上层执行语义、不改下层几何；两侧差异全在 ``adapters/``。

**时序是后置规划（post-hoc），不是"先排再飞"**：上层 ``RollingHorizon`` 在
``run_cycle`` 内部就把解回写、机位挪回机巢、任务判完成。环在周期**结束后**拿到
该周期的航次快照去排航迹，量出来的 \(T_{\mathrm{traj}}\) 是"这一周期真飞会用
多久"，用于回填代价与检查口径，而不是用来阻止上层完成——那需要上层把
"指派"与"结算"拆开（``busy_until`` 接入，尚未做，见 README §5）。

因此超框（\(T_{\mathrm{traj}} + t_0 > 周期终点\)）在 ``LoopTrace.overrun_cycles``
里如实暴露：它说明上层的"周期末即完成"与真实航迹时长不合，而不是环的缺陷。
"""

from __future__ import annotations

from typing import Any, Sequence

from .protocol import (
    AllocatorPort,
    CyclePlan,
    LoopTrace,
    PlannerPort,
    TourOrder,
)


class LoopDriver:
    """闭环驱动。

    Args:
        allocator: 上层端口（滚动时域）
        planner:   下层端口（轨迹规划）
        overrun_tol_s: 超框容差（秒）。0 = 严格；给 1~2 s 是给"正好卡在周期末"
            留的容差，不能拿它抹掉几十秒的超框。
        prune_at_cycle_end: 周期末把已结束航迹从注册表摘掉。上层在周期末就把机
            判回机巢、任务判完成，注册表若继续留着这些航迹，下一周期会把"早已
            落地"的旧航迹当作在飞约束，凭空造出冲突。
    """

    def __init__(
        self,
        allocator: AllocatorPort,
        planner: PlannerPort,
        *,
        overrun_tol_s: float = 0.0,
        prune_at_cycle_end: bool = True,
    ) -> None:
        if overrun_tol_s < 0.0:
            raise ValueError(f"overrun_tol_s 不能为负：{overrun_tol_s}")
        self.allocator = allocator
        self.planner = planner
        self.overrun_tol_s = float(overrun_tol_s)
        self.prune_at_cycle_end = bool(prune_at_cycle_end)

    # ------------------------------------------------------------------
    def step_one(self, new_tasks: Sequence[Any] | None = None) -> CyclePlan:
        """推进一拍：上层一个周期 + 下层一次批量规划。"""
        snap = self.allocator.step(new_tasks)
        report = self.planner.plan_cycle(snap.orders)
        overruns = tuple(
            sorted(
                t.uav_id
                for t in report.tickets.values()
                if t.ok and t.t_end > snap.t_end + self.overrun_tol_s
            )
        )
        busy = {
            int(o.uav_id): float(self.planner.busy_until(int(o.uav_id)) or 0.0)
            for o in snap.orders
        }
        rolled_back = _rolled_back(snap.orders, report)
        pruned: tuple[int, ...] = ()
        if self.prune_at_cycle_end:
            pruned = tuple(int(u) for u in self.planner.prune(snap.t_end))
        return CyclePlan(
            cycle=int(snap.cycle),
            t_start=float(snap.t_start),
            t_end=float(snap.t_end),
            report=report,
            est_cost=dict(snap.est_cost),
            busy_until=busy,
            executed_ids=tuple(int(t) for t in snap.executed_ids),
            overruns=overruns,
            pruned=pruned,
            rolled_back=rolled_back,
        )

    # ------------------------------------------------------------------
    def run(
        self, n_cycles: int, arrivals: Sequence[Sequence[Any]] | None = None
    ) -> LoopTrace:
        """跑满 ``n_cycles`` 拍；``arrivals[i]`` 是第 i 拍新到的任务（可为空）。"""
        if n_cycles <= 0:
            raise ValueError(f"n_cycles 必须为正：{n_cycles}")
        plans: list[CyclePlan] = []
        failures: dict[int, str] = {}
        for i in range(int(n_cycles)):
            batch = arrivals[i] if arrivals is not None and i < len(arrivals) else None
            plan = self.step_one(batch)
            plans.append(plan)
            for uid, ticket in plan.report.tickets.items():
                if not ticket.ok:
                    failures[int(uid)] = ticket.failure or "unknown"
        return LoopTrace(
            cycles=tuple(plans),
            executed_ids=tuple(t for p in plans for t in p.executed_ids),
            overrun_cycles=tuple(p.cycle for p in plans if p.overruns),
            failures=failures,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def format_table(trace: LoopTrace) -> str:
        """把环账打印成表（验收时肉眼扫一遍，不进 JSON）。"""
        lines = [
            "周期  机  估计m  真实s  起点→终点  让路        超框",
        ]
        for plan in trace.cycles:
            for uid in sorted(plan.report.tickets):
                t = plan.report.tickets[uid]
                line = (
                    f"{plan.cycle:>4}  {uid:>2}  {plan.est_cost.get(uid, 0.0):>7.1f}"
                    f"  {t.duration_s:>6.2f}  [{t.t0:>7.1f},{t.t_end:>7.1f}]"
                    f"  {plan.report.moved.get(uid, '-'):<12}"
                    f"  {'✗' if uid in plan.overruns else ''}"
                )
                lines.append(line)
        if trace.failures:
            lines.append(f"失败：{dict(trace.failures)}")
        if trace.overrun_cycles:
            lines.append(
                f"超框周期：{trace.overrun_cycles}（上层周期末即完成，与本模块时长不合）"
            )
        return "\n".join(lines)


def _rolled_back(orders: Sequence[TourOrder], report: Any) -> tuple[int, ...]:
    """本拍失败票承载的任务 id（``task_ids`` 为空的老消息则无从回池，返回空）。"""
    tickets = getattr(report, "tickets", {}) or {}
    ids = {
        int(tid)
        for o in orders
        if not tickets.get(int(o.uav_id)) or not tickets[int(o.uav_id)].ok
        for tid in o.task_ids
    }
    return tuple(sorted(ids))
