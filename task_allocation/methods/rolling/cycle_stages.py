# -*- coding: utf-8 -*-
"""周期三段式：``prepare`` / ``solve`` / ``commit``（``run_cycle`` 的实现体）。

职责：把"一个滚动周期"拆成互不写状态的三段——
      ``prepare`` 冻结时间与事件上下文并组装一张代价视图；
      ``solve`` 在同一视图上求解（可重复调用，不推进时间、不写任务池/机队）；
      ``commit`` 把当前解**一次性**回写任务池、机队并落 ``CycleRecord``。
边界：不算代价口径（``FactCostView`` 管取数）、不排航迹、不改滚动接口。
相关：``rolling/horizon.py``（对外薄包装与状态持有）、``muas/edge_keys.py``、
      ``muas/cost/feasibility_cost_view.py``。

**为什么必须拆**：周期内重解要求"同一快照再解一次"。旧 ``run_cycle`` 把推进时间、
选解、回写、执行推进压成一个不可分割的动作，重解就等于把任务判完成两次、把机位
挪回巢两次。拆开后 ``solve`` 只依赖 ``PreparedCycle``（冻结的 ``t_start/t_end``、
任务集、可用机集、事件上下文、代价视图），因此第二版解与第一版解在同一时间锚点
上可比。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Sequence

from task_allocation.common.types import Task, UAV
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs
from task_allocation.methods.muas.cost.fact_cost_view import FactCostView
from task_allocation.methods.muas.cost.lazy_pairwise import make_lazy_pairwise
from task_allocation.methods.muas.solvers.muas_stage import run_selective_muas
from task_allocation.methods.rolling.event_trigger import (
    detect_events,
    should_replan,
    summarize_events,
)
from task_allocation.methods.selection.capability import merge_caps
from task_allocation.methods.selection.pipeline import SelectionConfig, run as run_selection

if TYPE_CHECKING:  # pragma: no cover
    from task_allocation.methods.rolling.horizon import CycleRecord, RollingHorizon

__all__ = ["CycleStages", "PreparedCycle", "SolvedCycle"]


@dataclass
class PreparedCycle:
    """一个周期的**冻结**输入（重解不许改这里的任何字段）。"""

    cycle: int
    t_start: float
    t_end: float
    record: "CycleRecord"
    #: 本周期送进选择层的候选（已剔除静态不可达 / 转休眠 / 顺序组不齐）；
    #: 也是 ``deferred_ids`` 的记账基准
    tasks: list[Task]
    #: 粗代价提示的取数基准（原 ``cost_hint`` 的输入，含已转休眠任务）
    hint_tasks: list[Task]
    avail: list[UAV]
    view: FactCostView
    replan_ctx: Any
    forced_ids: set[int]
    excluded_nest_ids: set[int]
    sel_config: SelectionConfig
    tasks_for_selection: list[Task] = field(default_factory=list)
    seq_deferred: list[int] = field(default_factory=list)
    #: 退化原因（``no_tasks`` / ``no_available_uav``）；非空 = 本周期不求解
    degenerate: str = ""
    #: 本快照已重解次数（0 = 只有首解）
    resolves: int = 0
    #: 代价版本标签（``base`` / ``cycle:1`` …）
    cost_version: str = "base"
    snapshot_id: str = ""

    @property
    def cycle_length(self) -> float:
        return float(self.t_end - self.t_start)


@dataclass
class SolvedCycle:
    """一版解（未提交）。``solution is None`` = 本版没有可执行航次。"""

    prepared: PreparedCycle
    selected: list[Task]
    solution: Any
    stage: Any
    attempt: int = 0
    cost_version: str = "base"

    @property
    def record(self) -> "CycleRecord":
        return self.prepared.record

    @property
    def executed_ids(self) -> tuple[int, ...]:
        if self.solution is None:
            return ()
        return tuple(int(i) for i in self.solution.executed_task_ids)


class CycleStages:
    """三段式实现（持有 ``RollingHorizon``，所有状态都在它身上）。"""

    def __init__(self, horizon: "RollingHorizon") -> None:
        self.h = horizon

    # ------------------------------------------------------------------
    # prepare
    # ------------------------------------------------------------------
    def prepare(
        self, new_tasks: Sequence[Task] | None = None, *, advance: bool = True
    ) -> PreparedCycle:
        """冻结本周期：时间、任务集、可用机、事件上下文、代价视图。"""
        h = self.h
        cfg = h.hcfg
        if advance:
            h._time += float(cfg.cycle_length)
        t_end = float(h._time)
        t_start = t_end - float(cfg.cycle_length)

        # 0) 服务休眠唤醒：条件恢复即唤醒回 PENDING，本周期重新进入 T_t。
        woken_ids = h._wake_dormant()

        # 1) 周期开始：T_t = remain ∪ new ∪ release
        snap = h.pool.begin_cycle(new_tasks=new_tasks, current_time=t_end)
        tasks: list[Task] = list(snap.tasks)

        record = h.make_record(
            cycle=int(snap.cycle),
            current_time=t_end,
            pool_counts=dict(snap.counts),
            overdue_ids=list(snap.overdue_ids),
        )
        record.woken_ids = list(woken_ids)

        # 2) 静态物理不可达筛选（每周期满电 ⇒ 判定与周期状态无关，一次出池）
        unreachable = h._screen_unreachable(tasks)
        if unreachable:
            record.unreachable_ids = [t.id for t in unreachable]
            dropped = {t.id for t in unreachable}
            tasks = [t for t in tasks if t.id not in dropped]

        # 3) 事件注入 → 检测 → 事件驱动重规划决策（上下文在此**锁定**）
        h._apply_nest_closures(snap.cycle)
        events = detect_events(
            h.fleet.all(), h.pool.active_tasks(), h.nests, h.event_cfg,
            current_time=t_end, known_unavailable=h._known_unavailable,
        )
        h._known_unavailable = {n.id for n in h.nests if not n.available}
        fresh_events = h._fresh_events(events)
        record.events = [e.as_dict() for e in fresh_events]
        if cfg.event_replan:
            h._rehome_stranded_uavs()
        replan_ctx = summarize_events(events, h.event_cfg)
        replan = cfg.event_replan and should_replan(fresh_events, h.event_cfg)
        replan_ctx.replan = replan
        record.replan = replan
        record.replan_context = replan_ctx.as_dict()

        avail = h.fleet.available()
        record.selection = {
            "n_tasks_in": len(tasks),
            "n_uav_avail": len(avail),
            "current_time": t_end,
        }

        forced_ids: set[int] = set()

        # 4) 退化：无任务或无可用的 UAV → 全部任务保留至下一周期
        #    注意：强制集（``_pending_forced``）**此时不得消费**——退化周期没有
        #    选择层可跑，释放任务必须顺延到下个真正求解的周期（F4）。
        if not tasks or not avail:
            reason = "no_tasks" if not tasks else "no_available_uav"
            record.muas = {
                "n_tasks": len(tasks), "n_uav_avail": len(avail), "reason": reason,
                "n_executed": 0, "n_deferred": len(tasks),
            }
            record.deferred_ids = [t.id for t in tasks]
            return self._prepare_result(
                record, snap.cycle, t_start, t_end,
                tasks=tasks, hint_tasks=tasks, avail=avail,
                view=None, replan_ctx=replan_ctx, forced_ids=forced_ids,
                excluded_nest_ids=set(), sel_config=h.sel_cfg,
                degenerate=reason,
            )

        if cfg.event_replan:
            forced_ids = set(replan_ctx.urgent_task_ids) | set(h._pending_forced)
        h._pending_forced.clear()

        # 5) 一阶段前建惰性代价表：视图 = base + 实测覆盖 + 不可飞边（本周期唯一一份）
        hint_tasks = list(tasks)
        window = h.build_cost_view(hint_tasks, avail)

        # 6) 机队服务不可达转休眠（在选择前，避免"进候选又被拒"的空转）
        newly_dormant = h._update_service_stall(tasks)
        if newly_dormant:
            record.dormant_ids = [t.id for t in newly_dormant]
            dorm_ids = {t.id for t in newly_dormant}
            tasks = [t for t in tasks if t.id not in dorm_ids]

        # 6.1) 顺序组准入闸门：成员不齐的组整组暂缓（只收窄送进选择层的副本）
        tasks_for_selection, seq_deferred = h._seq_admission_gate(tasks)

        cap_cfg = replace(
            h.sel_cfg.capability,
            sortie_budget=merge_caps(
                h.sel_cfg.capability.sortie_budget, h.stage_cfg.sortie_budget
            ),
            max_participating_uavs=merge_caps(
                h.sel_cfg.capability.max_participating_uavs,
                h.stage_cfg.max_participating_uavs,
            ),
        )
        sel_config = SelectionConfig(
            priority=h.sel_cfg.priority,
            feasibility=h.sel_cfg.feasibility,
            marginal=h.sel_cfg.marginal,
            capability=cap_cfg,
            anti_starvation=h.sel_cfg.anti_starvation,
            current_time=t_end,
            forced_task_ids=forced_ids,
        )
        excluded = (
            set(replan_ctx.unavailable_nest_ids) if cfg.event_replan else set()
        )
        return self._prepare_result(
            record, snap.cycle, t_start, t_end,
            tasks=tasks, hint_tasks=hint_tasks, avail=avail,
            view=window, replan_ctx=replan_ctx, forced_ids=forced_ids,
            excluded_nest_ids=excluded, sel_config=sel_config,
            tasks_for_selection=tasks_for_selection, seq_deferred=seq_deferred,
        )

    # ------------------------------------------------------------------
    # solve
    # ------------------------------------------------------------------
    def solve(self, prep: PreparedCycle, *, attempt: int = 0) -> SolvedCycle:
        """在同一快照上解一版（**不写**任务池/机队、不推进时间）。"""
        h = self.h
        record = prep.record
        if prep.degenerate:
            return SolvedCycle(prep, selected=[], solution=None, stage=None,
                               attempt=attempt, cost_version=prep.cost_version)
        cost_hint = h._cost_hint(prep.hint_tasks, prep.avail, prep.view)
        # 走 horizon.select 间接层：测试可替换模块级 run_selection（见其 docstring）
        sel = h.select(
            prep.tasks_for_selection,
            prep.sel_config,
            cost_hint=cost_hint,
            pairwise=prep.view,
        )
        record.selection = dict(sel.diagnostics)
        record.selection["replan"] = bool(record.replan)
        if prep.seq_deferred:
            record.selection["seq_group_admission_deferred"] = list(prep.seq_deferred)
        record.selection["selected_ids"] = sorted(sel.selected_ids)
        if prep.forced_ids:
            record.selection["forced_task_ids"] = sorted(prep.forced_ids)

        if not sel.selected:
            record.muas = {
                "n_tasks": len(prep.tasks), "n_selected": 0, "n_executed": 0,
                "n_deferred": len(prep.tasks), "reason": "selection_empty",
            }
            record.deferred_ids = [t.id for t in prep.tasks]
            return SolvedCycle(prep, selected=[], solution=None, stage=None,
                               attempt=attempt, cost_version=prep.cost_version)

        cb_sel = build_pairwise_costs(
            sel.selected, h.fleet.all(), nests=h.nests, dem=h.dem,
            estimator=h.estimator, nest_offset=h.nest_offset,
            distance_fn=h._distance_fn, pairwise_source=prep.view,
        )
        stage_cfg = replace(h.stage_cfg, excluded_nest_ids=set(prep.excluded_nest_ids))
        stage = run_selective_muas(
            sel.selected, h.fleet.all(), h.nests, cb_sel,
            fleet_uavs=h.fleet.all(),
            problem_config=replace(h.problem_cfg, current_time=float(h._time)),
            stage_config=stage_cfg,
            solver_config=h.solver_cfg,
            nest_offset=h.nest_offset,
            current_time=float(h._time),
            solver=(h.solver_factory() if h.solver_factory is not None else None),
        )
        solution = stage.solution
        record.muas = h.stage_diagnostics(stage, selected=sel.selected,
                                         replan=bool(record.replan),
                                         excluded=prep.excluded_nest_ids)
        # 预览账（最终口径仍以 commit 的 apply_solution 为准）
        record.tours = [h.snapshot_of(t) for t in solution.tours if t.task_ids]
        record.cost_history = [float(v) for v in stage.cost_history]
        record.executed_ids = [int(i) for i in solution.executed_task_ids]
        return SolvedCycle(prep, selected=list(sel.selected), solution=solution,
                           stage=stage, attempt=attempt,
                           cost_version=prep.cost_version)

    # ------------------------------------------------------------------
    # commit
    # ------------------------------------------------------------------
    def commit(self, prep: PreparedCycle, solved: SolvedCycle) -> "CycleRecord":
        """把最终版一次性提交：任务池、机队、执行推进、记账。"""
        h = self.h
        record = prep.record
        solution = solved.solution
        if solution is None:
            h._fill_occupancy(record)
            h.records.append(record)
            return record

        # 7.1) 顺序组交付闸门：只装载半个组的解整组撤下（改 tour.task_ids 后才回写）
        seq_drop = h._seq_delivery_gate(solution)
        if seq_drop:
            record.muas["seq_group_deferred_ids"] = list(seq_drop)
            record.muas["n_executed"] = len(solution.executed_task_ids)
            record.muas["n_deferred"] = len(solved.selected) - len(
                solution.executed_task_ids
            )
        record.tours = [h.snapshot_of(t) for t in solution.tours if t.task_ids]
        record.cost_history = [float(v) for v in solved.stage.cost_history]

        # 8) 回写：任务池 + 机队（含终点机巢 z_ub）
        # ``mark_selected`` 挪到这里：重解期间不得反复改任务状态。
        h.pool.mark_selected([t.id for t in solved.selected])
        violations: list[Any] = []
        apply_res = h.pool.apply_solution(solution)
        try:
            h.fleet.apply_solution(
                solution, h.nests,
                strict_capacity=h.hcfg.strict_capacity,
                on_violation=violations.append,
            )
        except Exception:
            h.pool.release(apply_res.executing_ids, reason="capacity_rejected")
            raise

        executed = set(apply_res.executing_ids)
        record.executed_ids = [int(i) for i in apply_res.executing_ids]
        record.deferred_ids = [t.id for t in prep.tasks if t.id not in executed]
        record.capacity_violations = [v.as_dict() for v in violations]

        # 9) 执行中故障注入 → 未完成任务释放回任务池（在解回写之后、完成反馈之前）
        h._inject_faults(int(record.cycle), record)

        # 10) 执行推进：位置 / 航程 / 完成反馈
        h._advance_execution(solution, record)

        h._fill_occupancy(record)
        h.records.append(record)
        return record

    # ------------------------------------------------------------------
    def _prepare_result(
        self,
        record: "CycleRecord",
        cycle: int,
        t_start: float,
        t_end: float,
        *,
        tasks: list[Task],
        hint_tasks: list[Task],
        avail: list[UAV],
        view: FactCostView | None,
        replan_ctx: Any,
        forced_ids: set[int],
        excluded_nest_ids: set[int],
        sel_config: SelectionConfig,
        tasks_for_selection: list[Task] | None = None,
        seq_deferred: list[int] | None = None,
        degenerate: str = "",
    ) -> PreparedCycle:
        h = self.h
        return PreparedCycle(
            cycle=int(cycle),
            t_start=float(t_start),
            t_end=float(t_end),
            record=record,
            tasks=list(tasks),
            hint_tasks=list(hint_tasks),
            avail=list(avail),
            view=view,
            replan_ctx=replan_ctx,
            forced_ids=set(forced_ids),
            excluded_nest_ids=set(excluded_nest_ids),
            sel_config=sel_config,
            tasks_for_selection=list(tasks_for_selection or []),
            seq_deferred=list(seq_deferred or []),
            degenerate=degenerate,
            cost_version=str(h._cost_version),
            snapshot_id=f"{int(cycle)}:{h._cost_version}",
        )
