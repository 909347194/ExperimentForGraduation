# -*- coding: utf-8 -*-
r"""环驱动：每周期"上层派航次 → 下层排真实航迹 → 事实回灌 → 记账"。

职责：把 ``AllocatorPort`` 与 ``PlannerPort`` 串成一轮周期推进，并把"哪条航次超框、
哪台机还在飞、哪些航迹已过期"记进 ``LoopTrace``。
边界：不含算法、不改上层执行语义、不改下层几何；两侧差异全在 ``adapters/``。

**时序（启用周期内重解时）**：

```
prepare(冻结快照) → plan_fleet(S0) → 取逐段事实
      → apply_cost_update → resolve(同快照重解) → plan_fleet(S1) → commit(只提交 S1)
```

四条纪律写死在这里：

1. **重解只有一个理由**：``S0`` 的交付解里出现了几何不可飞的边
   （``feedback.INFEASIBLE_CODES``）。残差大、估计不准都不构成重解理由——上层那把尺子
   只负责排序，不负责精确。已飞航段的实测长度会随单一并写进代价（同快照事实，
   不是跨周期校准），但**它本身不触发重解**。
2. **至多重解一次**（``max_in_cycle`` 默认 1）：这是剔除动作，不是轮询；重解后仍有
   不可飞边就如实记进 ``unresolved_infeasible_edges``，交给下一周期的任务池处理。
3. **只有最终版算完成/不可行/机位**；中间版失败只进 ``ResolveAttempt``，否则
   "S0 断在 T、S1 飞成 T"会被同时记成失败一次、完成一次。
4. **重解不推进时间、不写任务池/机队**（由上层三段式保证）；环这边只需保证重解前
   把上一版已发布的航迹从注册表撤下，否则第二版会躲自己上一版的航迹。

不启用重解（``max_in_cycle=0`` 或端口能力不全）时走 ``_step_once``：一拍完成，与旧
实现逐句一致。**能力不全就不假装能重解**：:meth:`LoopDriver.resolve_blocker` 是唯一
判据，实验层据此在启动时直接报错，而不是跑到中途才发现退化了。

异常纪律：重解这段是**可选增强**，它失败不得把本周期拖成"没提交"。故 resolve 分支
整体包在 try 里，失败即退回首解交付（``CyclePlan.resolve_error`` 如实落账、审计判
FAIL），``commit`` 无论如何都会执行一次。
"""

from __future__ import annotations

from typing import Any, Sequence

from .feedback import build_cost_update, per_uav_residuals
from .protocol import (
    AllocatorPort,
    CostUpdate,
    CyclePlan,
    CycleSnapshot,
    LoopTrace,
    PlannerPort,
    ResolveAttempt,
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
        max_in_cycle: 周期内**最大重解次数**（默认 1；0 = 关闭，走旧路径）。
            解版号 ``S0`` 是首解，``S1`` 是唯一允许的剔除版。
    """

    def __init__(
        self,
        allocator: AllocatorPort,
        planner: PlannerPort,
        *,
        overrun_tol_s: float = 0.0,
        prune_at_cycle_end: bool = True,
        max_in_cycle: int = 1,
    ) -> None:
        if overrun_tol_s < 0.0:
            raise ValueError(f"overrun_tol_s 不能为负：{overrun_tol_s}")
        if int(max_in_cycle) < 0:
            raise ValueError(f"max_in_cycle 不能为负：{max_in_cycle}")
        self.allocator = allocator
        self.planner = planner
        self.overrun_tol_s = float(overrun_tol_s)
        self.prune_at_cycle_end = bool(prune_at_cycle_end)
        self.max_in_cycle = int(max_in_cycle)

    # ------------------------------------------------------------------
    # 能力探测
    # ------------------------------------------------------------------
    #: 周期内重解要找上层的四个入口（缺一个就退回一拍完成）
    ALLOCATOR_STEPS = ("prepare", "apply_cost_update", "resolve", "commit")

    def resolve_blocker(self) -> str | None:
        """周期内重解**为什么不可用**；``None`` = 能力齐备、可重解。

        这是全链唯一的能力判据：``can_resolve`` 只读它，实验层启动时也读它。
        把"缺什么"用一句话说出来，比返回一个 bool 有用——bool 只会让人
        在日志里看到"本周期没重解"却不知道是配置关的、还是端口缺的。
        """
        if self.max_in_cycle <= 0:
            return "max_in_cycle=0（配置关闭）"
        missing = [
            name for name in self.ALLOCATOR_STEPS
            if not callable(getattr(self.allocator, name, None))
        ]
        if missing:
            return "上层端口缺 " + "/".join(missing)
        # 缺 reset_cycle 时重解会把上一版航迹当成"别人在飞"去躲：解不会改，账却记了一次。
        if not callable(getattr(self.planner, "reset_cycle", None)):
            return "下层端口缺 reset_cycle（重解会躲自己上一版航迹）"
        return None

    def can_resolve(self) -> bool:
        """三段式能力是否齐备（缺一个就退回一拍完成）。"""
        return self.resolve_blocker() is None

    # ------------------------------------------------------------------
    def step_one(self, new_tasks: Sequence[Any] | None = None) -> CyclePlan:
        """推进一个周期：首解 →（可选）写进代价的不可飞边的一版 → 提交最终版。"""
        if not self.can_resolve():
            return self._step_once(new_tasks)
        return self._step_with_resolve(new_tasks)

    # ------------------------------------------------------------------
    # 旧路径：一拍完成
    # ------------------------------------------------------------------
    def _step_once(self, new_tasks: Sequence[Any] | None) -> CyclePlan:
        snap = self.allocator.step(new_tasks)
        report = self.planner.plan_cycle(snap.orders)
        busy = self._busy_of(snap)
        pruned = self._prune(snap.t_end)
        return self._plan(
            snap, report, pruned=pruned, attempts=(), update=None, intermediate=0,
            busy=busy,
        )

    # ------------------------------------------------------------------
    # 新路径：硬不可行的单次剔除
    # ------------------------------------------------------------------
    def _step_with_resolve(self, new_tasks: Sequence[Any] | None) -> CyclePlan:
        snap = self.allocator.prepare(new_tasks)
        report = self.planner.plan_cycle(snap.orders)
        attempts: list[ResolveAttempt] = [self._attempt(snap, report, attempt=0)]
        update: CostUpdate | None = None
        resolve_error = ""
        snap_s0, report_s0 = snap, report

        # 唯一重解触发条件：S0 的交付解里出现了几何不可飞的边。
        # 注意判据是 ``infeasible`` 而不是 ``is_empty``：同一张回灌单里还带着已飞航段的
        # 实测长度，但"量到了多长"不构成重解理由——只有"这条边根本飞不过去"才重解。
        pending = build_cost_update(report.tickets, attempt=1)
        if pending.infeasible:
            try:
                self._drop_previous(report)
                self._apply_update(pending)
                snap = self.allocator.resolve()
                report = self.planner.plan_cycle(snap.orders)
            except Exception as exc:  # noqa: BLE001 - 重解是可选增强，不许拖垮本周期
                # 退回首解交付：账按 S0 记（``report`` 只在规划成功时才被替换，
                # 这里再显式取回 S0 快照），异常本身落进 ``resolve_error``。
                resolve_error = f"{type(exc).__name__}: {exc}"
                snap, report = snap_s0, report_s0
            else:
                update = pending
                attempts.append(self._attempt(snap, report, attempt=1))

        final = self.allocator.commit()
        if final is not None:
            snap = final
        intermediate = self._intermediate_failures(attempts)
        busy = self._busy_of(snap)
        pruned = self._prune(snap.t_end)
        # 额度用完仍有不可飞边：不重试、不隐藏，交给下一周期的任务池。
        leftover = build_cost_update(report.tickets, attempt=len(attempts))
        # 回灌唯一可被证伪的指标：写进代价的不可飞边有没有真的从终版里消失。
        # 两边都取**下层 id**（``pending`` 未翻译、终版事实也未翻译），同键空间直接求交。
        survived = (
            pending.infeasible & leftover.infeasible if update else frozenset()
        )
        return self._plan(
            snap, report, pruned=pruned, attempts=tuple(attempts), update=update,
            intermediate=intermediate, unresolved=leftover.infeasible,
            survived=survived, resolve_error=resolve_error, busy=busy,
        )

    # ------------------------------------------------------------------
    # 零件
    # ------------------------------------------------------------------
    @staticmethod
    def _attempt(snap: CycleSnapshot, report: Any, *, attempt: int) -> ResolveAttempt:
        return ResolveAttempt(
            attempt_id=int(attempt),
            snapshot_id=str(getattr(snap, "snapshot_id", "") or ""),
            tickets=dict(report.tickets),
            est_cost=dict(snap.est_cost),
            residuals=per_uav_residuals(report.tickets, snap.est_cost),
            cost_version=str(getattr(snap, "cost_version", "base") or "base"),
            cycle=int(snap.cycle),
            failures={
                int(uid): str(t.failure or "unknown")
                for uid, t in report.tickets.items()
                if not t.ok
            },
        )

    @staticmethod
    def _intermediate_failures(attempts: list[ResolveAttempt]) -> int:
        """中间版失败、最终版成功的航次数（只计数，不进入完成/不可行口径）。"""
        if len(attempts) < 2:
            return 0
        final = attempts[-1].tickets
        count = 0
        for att in attempts[:-1]:
            for uid, ticket in att.tickets.items():
                if not ticket.ok and uid in final and final[uid].ok:
                    count += 1
        return count

    def _drop_previous(self, report: Any) -> None:
        """重解前撤下上一版已发布航迹（可选能力；无则不动）。"""
        reset = getattr(self.planner, "reset_cycle", None)
        if not callable(reset):
            return
        reset(sorted(int(uid) for uid, t in report.tickets.items() if t.ok))

    def _apply_update(self, pending: CostUpdate) -> None:
        """把回灌单送进上层。

        翻译（下层 id → 上层节点键）是适配器内部的事，**不进账**：本模块记的
        始终是下层原单，与 ``unresolved_infeasible_edges`` / ``infeasible_but_survived``
        同一键空间，免得一张台账里混两套编号。
        """
        self.allocator.apply_cost_update(pending)

    def _plan(
        self,
        snap: CycleSnapshot,
        report: Any,
        *,
        pruned: tuple[int, ...],
        attempts: tuple[ResolveAttempt, ...],
        update: CostUpdate | None,
        intermediate: int,
        unresolved: frozenset[tuple[int, int]] = frozenset(),
        survived: frozenset[tuple[int, int]] = frozenset(),
        resolve_error: str = "",
        busy: dict[int, float] | None = None,
    ) -> CyclePlan:
        overruns = tuple(
            sorted(
                t.uav_id
                for t in report.tickets.values()
                if t.ok and t.t_end > snap.t_end + self.overrun_tol_s
            )
        )
        # busy 必须在 prune 之前取：prune 会把本周期已落地的航迹从注册表清掉，
        # 之后再读必然是 0，审计拿它核 t_end 就会假 FAIL。调用方在 _prune 前采集并传入；
        # None 只留给不走 prune 的旧调用路径。
        if busy is None:
            busy = {
                int(o.uav_id): float(self.planner.busy_until(int(o.uav_id)) or 0.0)
                for o in snap.orders
            }
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
            rolled_back=_rolled_back(snap.orders, report),
            assignments={
                int(o.uav_id): tuple(int(t) for t in o.task_ids) for o in snap.orders
            },
            attempts=attempts,
            n_intermediate_failures=int(intermediate),
            cost_version=str(getattr(snap, "cost_version", "base") or "base"),
            cost_update=update,
            unresolved_infeasible_edges=tuple(sorted(unresolved)),
            infeasible_but_survived=tuple(sorted(survived)),
            resolve_error=str(resolve_error),
        )

    def _busy_of(self, snap: CycleSnapshot) -> dict[int, float]:
        """在 prune 之前取每机占用时刻。"""
        return {
            int(o.uav_id): float(self.planner.busy_until(int(o.uav_id)) or 0.0)
            for o in snap.orders
        }

    def _prune(self, now: float) -> tuple[int, ...]:
        if not self.prune_at_cycle_end:
            return ()
        return tuple(int(u) for u in self.planner.prune(float(now)))

    # ------------------------------------------------------------------
    def run(
        self, n_cycles: int, arrivals: Sequence[Sequence[Any]] | None = None
    ) -> LoopTrace:
        """跑满 ``n_cycles`` 个周期；``arrivals[i]`` 是第 i 个周期新到的任务（可为空）。"""
        if n_cycles <= 0:
            raise ValueError(f"n_cycles 必须为正：{n_cycles}")
        plans: list[CyclePlan] = []
        failures: dict[int, str] = {}
        for i in range(int(n_cycles)):
            batch = arrivals[i] if arrivals is not None and i < len(arrivals) else None
            plan = self.step_one(batch)
            plans.append(plan)
            # 只记**最终版**失败：中间版失败已在 CyclePlan.attempts 里单列，
            # 混进这里会让"重解修好了"的周期看起来仍然失败。
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
            "周期  机  估计m  真实s  起点→终点  让路        超框  解",
        ]
        for plan in trace.cycles:
            for uid in sorted(plan.report.tickets):
                t = plan.report.tickets[uid]
                line = (
                    f"{plan.cycle:>4}  {uid:>2}  {plan.est_cost.get(uid, 0.0):>7.1f}"
                    f"  {t.duration_s:>6.2f}  [{t.t0:>7.1f},{t.t_end:>7.1f}]"
                    f"  {plan.report.moved.get(uid, '-'):<12}"
                    f"  {'✗' if uid in plan.overruns else ''}"
                    f"  {plan.cost_version}"
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
    """本周期失败航次承载的任务 id（``task_ids`` 为空的老消息则无从回池，返回空）。"""
    tickets = getattr(report, "tickets", {}) or {}
    ids = {
        int(tid)
        for o in orders
        if not tickets.get(int(o.uav_id)) or not tickets[int(o.uav_id)].ok
        for tid in o.task_ids
    }
    return tuple(sorted(ids))
