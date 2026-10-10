# -*- coding: utf-8 -*-
"""horizon.py — 滚动时域主循环：单周期编排与多周期推进（论文 §6）。

一个滚动周期的完整链路::

    T_t = remain ∪ new ∪ release
      → 当前可用 UAV 确定  U_t^avail / K_avail(t)
      → 第一阶段 动态任务选择                → T_t^sel
      → 第二阶段 选择性多机巢 MUAS          → T_t^exec, U_t^exec
      → 回写状态（任务池 + 机队）
      → 执行推进与完成反馈
      → T_{t+1}

本层是**唯一的串联点**：``task_pool`` 与 ``uav_state`` 两个状态机互不 import，
故障 → 释放、解 → 绑定 这些跨模块的状态流转全部在此显式完成。

论文的两个包含关系在本层保证：

    T_t^exec ⊆ T_t^sel ⊆ T_t
    U_t^exec ⊆ U_t^avail

未纳入本周期执行计划的任务不会被删除，而是保留在任务池中，
在下一周期以 ``REMAIN`` 身份重新参与分配。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

import numpy as np

from task_allocation.common.types import AllocationSolution, Nest, Task, UAV
from task_allocation.methods.muas.constraints.nest_capacity import CapacityReport
from task_allocation.methods.muas.cost.cost_matrix import (
    CostMatrixBuildResult,
    build_pairwise_costs,
)
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
from task_allocation.methods.muas.cost.vertical_section import VerticalSectionCostEstimator
from task_allocation.methods.muas.problem import MUASProblemConfig
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASStageConfig,
    MUASStageResult,
    run_selective_muas,
)
from task_allocation.methods.rolling.event_trigger import (
    EventTriggerConfig,
    detect_events,
)
from task_allocation.methods.selection.pipeline import run as run_selection
from task_allocation.methods.selection.feasibility import FeasibilityConfig
from task_allocation.methods.selection.marginal_gain import MarginalConfig
from task_allocation.methods.selection.priority import PriorityConfig, PriorityWeights
from task_allocation.methods.selection.pipeline import SelectionConfig
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

__all__ = [
    "HorizonConfig",
    "CycleRecord",
    "RollingHorizon",
]


@dataclass
class HorizonConfig:
    """滚动时域参数（全部可由实验 yaml 覆盖）。"""

    n_cycles: int = 1
    cycle_length: float = 600.0          # 每周期推进的时间（秒）
    # 周期末把执行中的任务判为完成（简化：不建模飞行过程）
    complete_after_cycle: bool = True
    # 降落后在机巢换电，剩余航程恢复满值
    recharge_at_nest: bool = True
    uav_max_range: float = 60000.0
    # 故障注入：{周期号(1-based): [uav_id, ...]}，用于验证 T_t^release 链路。
    # 注入时机是「本周期解已回写、任务尚在执行中」，即 UAV 真正带着任务时，
    # 这样 mark_fault 才有未完成任务可释放（若在周期起点注入，上一周期的任务
    # 已全部完成解绑，无任务可释放，链路观察不到）。
    faults: dict[int, list[int]] = field(default_factory=dict)
    # 落地期容量硬校验：True 时超容直接抛异常
    strict_capacity: bool = False


@dataclass
class CycleRecord:
    """单周期的可机读记录（直接并进 metrics.json）。"""

    cycle: int
    current_time: float
    pool_counts: dict[str, int] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    muas: dict[str, Any] = field(default_factory=dict)
    executed_ids: list[int] = field(default_factory=list)
    deferred_ids: list[int] = field(default_factory=list)
    completed_ids: list[int] = field(default_factory=list)
    released_ids: list[int] = field(default_factory=list)
    expired_ids: list[int] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    nest_occupancy: dict[str, int] = field(default_factory=dict)
    capacity_violations: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "current_time": round(self.current_time, 3),
            "pool_counts": self.pool_counts,
            "selection": self.selection,
            "muas": self.muas,
            "n_executed": len(self.executed_ids),
            "n_deferred": len(self.deferred_ids),
            "n_completed": len(self.completed_ids),
            "n_released": len(self.released_ids),
            "executed_ids": self.executed_ids,
            "deferred_ids": self.deferred_ids,
            "completed_ids": self.completed_ids,
            "released_ids": self.released_ids,
            "expired_ids": self.expired_ids,
            "events": self.events,
            "nest_occupancy": self.nest_occupancy,
            "capacity_violations": self.capacity_violations,
        }


def _subset_cost_build(
    cb: CostMatrixBuildResult,
    all_tasks: Sequence[Task],
    selected: Sequence[Task],
) -> CostMatrixBuildResult:
    """把全量代价矩阵裁到候选子集（避免重复估算，O(n²) 只算一次）。

    ``provider.pairwise`` 是全量字典，子集查询命中同样成立，因此直接复用。

    机巢块（``task_nest`` / ``uav_nest``）的**列维是机巢、不随任务子集变化**，
    只裁任务行；终点机巢进编码依赖这两个块，不能丢。
    """
    if not selected:
        return CostMatrixBuildResult(
            provider=cb.provider,
            uav_target=np.zeros((cb.uav_target.shape[0], 0)),
            task_task=np.zeros((0, 0)),
            task_nest=np.zeros((0, getattr(cb, "n_nests", 0))),
            uav_nest=getattr(cb, "uav_nest", np.zeros((0, 0))),
            n_nests=getattr(cb, "n_nests", 0),
        )
    pos = {t.id: i for i, t in enumerate(all_tasks)}
    rows = [pos[t.id] for t in selected]
    return CostMatrixBuildResult(
        provider=cb.provider,
        uav_target=cb.uav_target[:, rows],
        task_task=cb.task_task[np.ix_(rows, rows)],
        task_nest=cb.task_nest[rows, :] if cb.task_nest.shape[0] else cb.task_nest,
        uav_nest=cb.uav_nest,
        n_nests=cb.n_nests,
    )


class RollingHorizon:
    """滚动时域编排器。

    典型用法::

        horizon = RollingHorizon(pool, fleet, nests, dem=terrain, estimator=est, ...)
        records = horizon.run(n_cycles=3, arrivals={2: [task_a, task_b]})
    """

    def __init__(
        self,
        pool: TaskPool,
        fleet: UAVFleet,
        nests: Sequence[Nest],
        *,
        dem: DEMTerrain | None = None,
        estimator: VerticalSectionCostEstimator | None = None,
        horizon_config: HorizonConfig | None = None,
        selection_config: SelectionConfig | None = None,
        problem_config: MUASProblemConfig | None = None,
        stage_config: MUASStageConfig | None = None,
        solver_config: DMDEConfig | None = None,
        event_config: EventTriggerConfig | None = None,
        nest_offset: int = 1_000_000,
    ) -> None:
        self.pool = pool
        self.fleet = fleet
        self.nests = list(nests)
        self.dem = dem
        self.estimator = estimator
        self.hcfg = horizon_config or HorizonConfig()
        self.sel_cfg = selection_config or SelectionConfig()
        self.problem_cfg = problem_config or MUASProblemConfig()
        self.stage_cfg = stage_config or MUASStageConfig()
        self.solver_cfg = solver_config or DMDEConfig()
        self.event_cfg = event_config or EventTriggerConfig()
        self.nest_offset = nest_offset

        self._nest_by_id = {n.id: n for n in self.nests}
        self._time = float(pool.current_time)
        self._known_unavailable = {n.id for n in self.nests if not n.available}
        self.records: list[CycleRecord] = []

    # ------------------------------------------------------------------
    # 单周期
    # ------------------------------------------------------------------

    def run_cycle(
        self,
        new_tasks: Sequence[Task] | None = None,
        *,
        advance: bool = True,
    ) -> CycleRecord:
        """推进一个滚动周期。"""
        cfg = self.hcfg
        if advance:
            self._time += float(cfg.cycle_length)

        # 1) 周期开始：T_t = remain ∪ new ∪ release
        snap = self.pool.begin_cycle(new_tasks=new_tasks, current_time=self._time)
        tasks = snap.tasks

        record = CycleRecord(
            cycle=snap.cycle,
            current_time=self._time,
            pool_counts=dict(snap.counts),
            expired_ids=list(snap.expired_ids),
        )

        # 2) 事件检测（只识别，不自动改状态）
        events = detect_events(
            self.fleet.all(),
            self.pool.active_tasks(),
            self.nests,
            self.event_cfg,
            current_time=self._time,
            known_unavailable=self._known_unavailable,
        )
        record.events = [e.as_dict() for e in events]
        self._known_unavailable = {n.id for n in self.nests if not n.available}

        avail = self.fleet.available()
        record.selection = {
            "n_tasks_in": len(tasks),
            "n_uav_avail": len(avail),
            "current_time": self._time,
        }

        # 4) 退化：无任务或无可用的 UAV → 全部任务保留至下一周期
        if not tasks or not avail:
            record.muas = {
                "n_tasks": len(tasks),
                "n_uav_avail": len(avail),
                "reason": "no_tasks" if not tasks else "no_available_uav",
                "n_executed": 0,
                "n_deferred": len(tasks),
            }
            record.deferred_ids = [t.id for t in tasks]
            self._fill_occupancy(record)
            self.records.append(record)
            return record

        # 5) 三维地理代价（O(n²)，本周期只算一次）
        cb = build_pairwise_costs(
            tasks,
            self.fleet.all(),
            nests=self.nests,
            dem=self.dem,
            estimator=self.estimator,
            nest_offset=self.nest_offset,
        )

        # 6) 第一阶段：动态任务选择 → T_t^sel
        sel_config = SelectionConfig(
            priority=self.sel_cfg.priority,
            feasibility=self.sel_cfg.feasibility,
            marginal=self.sel_cfg.marginal,
            current_time=self._time,
        )
        cost_hint = self._cost_hint(tasks, avail, cb)
        sel = run_selection(
            tasks,
            self.fleet.all(),
            config=sel_config,
            cost_hint=cost_hint,
            pairwise_cost=cb.provider.pairwise,
        )
        record.selection = dict(sel.diagnostics)

        selected = sel.selected
        if not selected:
            record.muas = {
                "n_tasks": len(tasks),
                "n_selected": 0,
                "n_executed": 0,
                "n_deferred": len(tasks),
                "reason": "selection_empty",
            }
            record.deferred_ids = [t.id for t in tasks]
            self._fill_occupancy(record)
            self.records.append(record)
            return record

        self.pool.mark_selected(sel.selected_ids)

        # 7) 第二阶段：选择性多机巢 MUAS → T_t^exec
        cb_sel = _subset_cost_build(cb, tasks, selected)
        stage: MUASStageResult = run_selective_muas(
            selected,
            self.fleet.all(),
            self.nests,
            cb_sel,
            fleet_uavs=self.fleet.all(),
            problem_config=replace(self.problem_cfg, current_time=self._time),
            stage_config=self.stage_cfg,
            solver_config=self.solver_cfg,
            nest_offset=self.nest_offset,
            current_time=self._time,
        )
        solution = stage.solution
        record.muas = dict(stage.diagnostics)
        record.muas["model_type"] = stage.model_type
        record.muas["solve_seconds"] = round(stage.elapsed_seconds, 4)
        record.muas["n_evals"] = stage.n_evals
        record.muas["n_selected"] = len(selected)
        feas = solution.meta.get("feasibility", {}) or {}
        record.muas["violations"] = list(feas.get("violations", []))
        record.muas["penalty"] = float(feas.get("penalty", 0.0))

        # 8) 回写：任务池 + 机队（含终点机巢 z_ub）
        violations: list[CapacityReport] = []
        apply_res = self.pool.apply_solution(solution)
        try:
            self.fleet.apply_solution(
                solution,
                self.nests,
                strict_capacity=self.hcfg.strict_capacity,
                on_violation=violations.append,
            )
        except Exception:
            # 落地期拒绝：回滚任务池的执行中状态，任务保留至下一周期
            self.pool.release(apply_res.executing_ids, reason="capacity_rejected")
            raise

        executed = set(apply_res.executing_ids)
        record.executed_ids = [int(i) for i in apply_res.executing_ids]
        record.deferred_ids = [t.id for t in tasks if t.id not in executed]
        record.capacity_violations = [v.as_dict() for v in violations]

        # 8.5) 执行中故障注入 → 未完成任务释放回任务池（T_t^release）
        # 时机必须在本周期解回写之后、完成反馈之前：此时 UAV 才真正带着任务。
        self._inject_faults(snap.cycle, record)

        # 9) 执行推进：位置 / 航程 / 完成反馈
        self._advance_execution(solution, record)

        self._fill_occupancy(record)
        self.records.append(record)
        return record

    # ------------------------------------------------------------------
    # 多周期
    # ------------------------------------------------------------------

    def run(
        self,
        n_cycles: int | None = None,
        arrivals: Mapping[int, Sequence[Task]] | None = None,
    ) -> list[CycleRecord]:
        """连续推进多个周期。

        Args:
            n_cycles: 周期数；省略取 :class:`HorizonConfig` 的 ``n_cycles``。
            arrivals: ``{周期号(1-based): [Task, ...]}``，该周期开始时到达的任务。
        """
        n = int(n_cycles if n_cycles is not None else self.hcfg.n_cycles)
        arrivals = dict(arrivals or {})
        for step in range(1, n + 1):
            self.run_cycle(new_tasks=arrivals.get(step))
        return self.records

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _inject_faults(self, cycle: int, record: CycleRecord) -> None:
        """执行中注入 UAV 故障，把其未完成任务释放回任务池。

        ``fleet.mark_fault`` 与 ``pool.release`` 两个状态机互不 import，
        由本层显式串联（论文 §6 的 T_t^release）。
        """
        for uav_id in self.hcfg.faults.get(cycle, []):
            if uav_id not in self.fleet:
                continue
            stranded = self.fleet.mark_fault(uav_id)
            if not stranded:
                continue
            self.pool.release(stranded, reason="uav_fault")
            record.released_ids.extend(int(t) for t in stranded)

    def _cost_hint(
        self,
        tasks: Sequence[Task],
        avail: Sequence[UAV],
        cb: CostMatrixBuildResult,
    ) -> dict[int, float]:
        """第一阶段用的粗粒度执行代价 C_i(t)：最近可用 UAV 到该任务的三维代价。"""
        hint: dict[int, float] = {}
        for t in tasks:
            best = float("inf")
            for u in avail:
                c = cb.provider.cost(-(1 + u.id), t.id)
                if c < best:
                    best = c
            hint[t.id] = best if np.isfinite(best) else 1e6
        return hint

    def _advance_execution(self, solution: AllocationSolution, record: CycleRecord) -> None:
        """按航次推进位置与剩余航程，并在周期末判定完成。"""
        cfg = self.hcfg
        for tour in solution.tours:
            if not tour.task_ids:
                continue
            uav = self.fleet.find(tour.uav_id)
            if uav is None:
                continue
            # 航程消耗
            self.fleet.consume_range(tour.uav_id, max(float(tour.est_cost), 0.0))
            nest = self._nest_by_id.get(tour.end_nest_id)
            if nest is not None:
                self.fleet.update_position(tour.uav_id, nest.x, nest.y, nest.z)
            if cfg.recharge_at_nest:
                uav.remaining_range = float(cfg.uav_max_range)

        if not cfg.complete_after_cycle:
            return

        for tour in solution.tours:
            if not tour.task_ids:
                continue
            bound = self.fleet.bound_tasks(tour.uav_id)
            if not bound:
                continue
            # 记录完成时刻，供 avg_wait 等指标使用
            for tid in bound:
                task = self.pool.find(int(tid))
                if task is not None:
                    task.meta["completed_at"] = float(self._time)
            self.fleet.complete_tasks(tour.uav_id, bound)
            self.pool.complete(bound)
            record.completed_ids.extend(int(t) for t in bound)

    def _fill_occupancy(self, record: CycleRecord) -> None:
        occ = self.fleet.nest_occupancy(self.nests)
        record.nest_occupancy = {str(k): int(v) for k, v in sorted(occ.items())}

    # ------------------------------------------------------------------
    # 便捷构造
    # ------------------------------------------------------------------

    @staticmethod
    def selection_config_from_dict(d: Mapping[str, Any] | None) -> SelectionConfig:
        """从 yaml 的 ``selection:`` 段构造 :class:`SelectionConfig`。"""
        d = dict(d or {})
        weights = PriorityWeights(**d.get("weights", {})) if d.get("weights") else None
        priority = PriorityConfig(
            alpha=float(d.get("alpha", 2.5)),
            weights=weights,
            synergy_radius=float(d.get("synergy_radius", 500.0)),
            wait_scale=float(d.get("wait_scale", 3600.0)),
        )
        feas_d = dict(d.get("feasibility", {}) or {})
        feasibility = FeasibilityConfig(
            range_margin=float(feas_d.get("range_margin", 0.95)),
            check_time_window=bool(feas_d.get("check_time_window", True)),
            budget_factor=float(feas_d.get("budget_factor", 1.0)),
        )
        marg_d = dict(d.get("marginal", {}) or {})
        marginal = MarginalConfig(
            eps=float(marg_d.get("eps", 1.0)),
            max_selected=marg_d.get("max_selected", None),
            max_ratio=float(marg_d.get("max_ratio", 2.0)),
            min_marginal_ratio=float(marg_d.get("min_marginal_ratio", 0.0)),
        )
        return SelectionConfig(
            priority=priority, feasibility=feasibility, marginal=marginal
        )
