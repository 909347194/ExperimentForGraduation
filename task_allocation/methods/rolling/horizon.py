# -*- coding: utf-8 -*-
"""horizon.py — 滚动时域主循环：单周期编排与多周期推进（论文 §6）。

一个滚动周期的完整链路::

    T_t = remain ∪ new ∪ release
      → 当前可用 UAV 确定  U_t^avail / K_avail(t)
      → 事件检测（UAV 故障 / 突发高优先级任务 / 机巢不可用）
      → 若命中事件：事件驱动「本期重规划」
          - 紧急 / 故障释放任务强制纳入第一阶段选择（绕过 Top-αK 预筛）
          - 不可用机巢从终点候选集剔除（强制异巢终止）
      → 第一阶段 动态任务选择                → T_t^sel
      → 第二阶段 选择性多机巢 MUAS          → T_t^exec, U_t^exec
      → 回写状态（任务池 + 机队）
      → 执行推进与完成反馈（含执行中故障注入）
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
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
from task_allocation.methods.muas.constraints.nest_capacity import (
    CapacityReport,
    free_slots,
)
from task_allocation.methods.muas.cost.cost_matrix import (
    build_pairwise_costs,
)
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
from task_allocation.methods.muas.cost.lazy_pairwise import make_lazy_pairwise
from task_allocation.methods.muas.cost.vertical_section import (
    VerticalSectionCostEstimator,
    make_distance_fn,
)
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
    should_replan,
    summarize_events,
)
from task_allocation.methods.selection.pipeline import (
    AntiStarvationConfig,
    run as run_selection,
)
from task_allocation.methods.selection.capability import (
    CapabilityScaleConfig,
    merge_caps,
)
from task_allocation.methods.selection.feasibility import (
    FeasibilityConfig,
    static_unreachable,
)
from task_allocation.methods.selection.marginal_gain import MarginalConfig
from task_allocation.methods.selection.priority import PriorityConfig, PriorityWeights
from task_allocation.methods.selection.pipeline import SelectionConfig
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

__all__ = [
    "HorizonConfig",
    "CycleRecord",
    "RollingHorizon",
    "tour_snapshot",
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
    # 完成反馈的可行性闸门（论文 §6 完成反馈的判据）：
    # True 时，周期末只把「本周期解中未**物理**违约」的任务判为完成；
    # 被 ``check_feasible`` 判为物理不可行（航程 / 续航 / 终点机巢不可达 /
    # 结构类）的任务不顺延完成，而是解绑回任务池、下一周期重新纳入选择，
    # 并记入 ``record.infeasible_ids``（废飞指标只统计这一类）。
    # 2026-10-15 协议纠正「迟到不是废飞」：时间窗违约（tw_task）不再是硬
    # 违约——晚到任务正常完成（计入完成/吞吐，有效奖励已在适应度里按
    # 迟到折减），迟到秒数记入 ``record.late_ids`` / ``lateness_by_task``。
    # 置 False 则回到历史行为（一律标完成），用于消融对比。
    completion_feasibility_gate: bool = True
    # 事件驱动重规划：检测到 UAV 故障 / 突发高优先级任务 / 机巢不可用时，
    # 在本周期计划内做反应式调整（强制纳入紧急任务、剔除不可用机巢）。
    # 置 False 则事件仅被记录、不触发本期重规划（供消融对比关停该机制）。
    event_replan: bool = True
    # 收敛早停（论文长程吞吐口径）：到达全部注入后，若池内未完成任务数
    # len(pool) 连续 stable_patience 个周期不变，说明可完成的都已交付、剩余为
    # 物理不可达/淘汰的死任务，提前停止（周期数本身不定）。n_cycles 仅作安全上限。
    stop_when_stable: bool = False
    stable_patience: int = 4


def tour_snapshot(tour: UAVTour) -> dict[str, Any]:
    """航次的可机读切面（写入实验的 ``results/solution.json``）。

    只保留画分配图与论文取数需要的字段；启发式内部量留在 ``tour.meta``。
    """
    return {
        "uav_id": int(tour.uav_id),
        "task_ids": [int(i) for i in tour.task_ids],
        "start_nest_id": (
            None if tour.start_nest_id is None else int(tour.start_nest_id)
        ),
        "end_nest_id": None if tour.end_nest_id is None else int(tour.end_nest_id),
        "start": [
            round(float(tour.start_x), 3),
            round(float(tour.start_y), 3),
            round(float(tour.start_z), 3),
        ],
        "est_cost": round(float(tour.est_cost), 3),
        "est_reward": round(float(tour.est_reward), 4),
        "feasible": bool(tour.feasible),
    }


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
    # 事件驱动重规划：是否在本周期触发了反应式重规划
    replan: bool = False
    replan_context: dict[str, Any] = field(default_factory=dict)
    # 本周期因 UAV 故障释放回任务池的任务（供下一周期强制纳入选择）
    released_by_fault: list[int] = field(default_factory=list)
    # 完成闸门拦截的违约交付：解被判**物理不可行**（航程/续航/终点机巢/
    # 结构类），本周期未真正交付的任务。不顺延完成，解绑回任务池并在
    # 下一周期重新选择（区别于 completed_ids）。
    # 2026-10-15 协议纠正：迟到（tw_task）不再是硬违约——晚到任务正常
    # 完成（奖励已在适应度里按迟到折减），只有物理不可行才弹回。
    infeasible_ids: list[int] = field(default_factory=list)
    # 违约任务的归因标签：{task_id: [violation_tag, ...]}，供指标与论文取数
    infeasible_tags: dict[str, list[str]] = field(default_factory=dict)
    # 迟到交付：本周期完成但到达晚于 latest 的任务及迟到秒数。
    # 「迟到不是废飞」——计入完成与吞吐，按时率/平均迟到单独统计。
    late_ids: list[int] = field(default_factory=list)
    lateness_by_task: dict[str, float] = field(default_factory=dict)
    # 静态物理不可达（「过于偏远」）：所有机巢往返航程都超过满电可及
    # 范围，入池判定一次性出池。用户原则（2026-10-15）下唯一合法的
    # 未完成原因，单列计数，不混入淘汰。
    unreachable_ids: list[int] = field(default_factory=list)
    # 分配结构与求解轨迹：不进 as_dict()/metrics.json（数值指标保持精简），
    # 由实验编排写入 results/solution.json，供分配图 / 收敛图取用。
    tours: list[dict[str, Any]] = field(default_factory=list)
    cost_history: list[float] = field(default_factory=list)

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
            "replan": self.replan,
            "replan_context": self.replan_context,
            "released_by_fault": self.released_by_fault,
            "n_infeasible": len(self.infeasible_ids),
            "infeasible_ids": self.infeasible_ids,
            "infeasible_tags": self.infeasible_tags,
            "n_late": len(self.late_ids),
            "late_ids": self.late_ids,
            "lateness_by_task": self.lateness_by_task,
            "n_unreachable": len(self.unreachable_ids),
            "unreachable_ids": self.unreachable_ids,
        }


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
        solver_factory: Callable[[], Any] | None = None,
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
        # 对比求解器注入（exp05_baselines）：None = 默认 DMDE（主算法，
        # 行为逐位不变）；非 None 时每周期调用工厂取一个求解器实例传给
        # run_selective_muas（同一滚动外壳 + 每周期调用基线，见 exp05 notes）。
        self.solver_factory = solver_factory
        # 统一距离口径（DEM 三维航程）：整场运行**共用一个记忆化缓存**——
        # 任务与机巢位置固定，跨周期、跨阶段重复查同一条边时直接命中，
        # 避免每周期重算 O(n²) 段长（垂直切面 0.37 ms/段，不复用会线性放大）。
        self._distance_cache: dict = {}
        self._distance_fn = make_distance_fn(self.estimator, cache=self._distance_cache)

        self._nest_by_id = {n.id: n for n in self.nests}
        self._time = float(pool.current_time)
        self._known_unavailable = {n.id for n in self.nests if not n.available}
        self.records: list[CycleRecord] = []
        # 收敛早停状态：到达阶段（cycle <= 最后到达周期）不计数；之后监测
        # 池内未完成任务数，连续 stable_patience 周期不变即判稳态。
        self._conv_enabled = bool(self.hcfg.stop_when_stable)
        self._conv_patience = int(self.hcfg.stable_patience)
        self._conv_streak = 0
        self._conv_last_len: int | None = None

    # ------------------------------------------------------------------
    # 单周期
    # ------------------------------------------------------------------

    def _screen_unreachable(self, tasks: Sequence[Task]) -> list[Task]:
        """静态物理不可达筛选：往返航程超满电可及范围的任务出池。

        判据用 ``hcfg.uav_max_range``（与 ``recharge_at_nest`` 恢复的满电
        航程同源；本实验机队同构，满电值唯一）。margin 取**物理上限 1.0**
        （与完成闸门同口径）——规划保守度（range_margin）是 stage 内部
        的参与/provisioning 决策，用作静态边界会误杀物理可达的远站任务。
        距离走运行级记忆化的 DEM 三维 ``_distance_fn``，跨周期命中缓存。
        """
        if not tasks:
            return []
        hit = [
            t
            for t in tasks
            if static_unreachable(
                t,
                self.nests,
                self.hcfg.uav_max_range,
                distance_fn=self._distance_fn,
            )
        ]
        if hit:
            self.pool.mark_unreachable([t.id for t in hit])
        return hit

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

        # 2.5) 静态物理不可达筛选（用户原则 2026-10-15：任务不被完成
        # 只有物理约束被违反）。任务/巢位固定 + 每周期满电 ⇒ 判定与周期
        # 状态无关：所有机巢往返航程都超过满电可及范围的任务一次性出池
        # （UNREACHABLE），不留在池里空转——否则它每周期进选择、被规划器
        # 丢弃、直到超期，把「过于偏远」误导记成「淘汰」。
        unreachable = self._screen_unreachable(tasks)
        if unreachable:
            record.unreachable_ids = [t.id for t in unreachable]
            dropped = {t.id for t in unreachable}
            tasks = [t for t in tasks if t.id not in dropped]

        # 2) 事件检测 + 事件驱动重规划决策（论文 §6）
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
        # 机巢不可用 → 其**全部**归属 UAV 强制改降（含闲置机：合并修复可能
        # 让参与机零任务停在原巢）——归属巢退出运行后不得再有 UAV 挂靠。
        if self.hcfg.event_replan:
            self._rehome_stranded_uavs()

        # 把事件解析为本期重规划上下文；replan 决定后续是否做反应式调整
        replan_ctx = summarize_events(events, self.event_cfg)
        replan = self.hcfg.event_replan and should_replan(events, self.event_cfg)
        # 上下文里的 replan 标志需与本期真实决策一致（event_replan 关停时记为 False）
        replan_ctx.replan = replan
        record.replan = replan
        record.replan_context = replan_ctx.as_dict()

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

        # 5) 第一阶段前的惰性代价表（延迟构建，2026-10-20）：
        # 旧流程在这里对**全量** T_t 建 O(n²) 三维矩阵，而第一阶段只触碰
        # O(n·K + k²) 条边——exp02 实测 N=200 时全量建矩阵占端到端
        # 85.6%（9752/11391 ms），Top-αK 省下的搜索时间被它吃光。
        # 改为按需计算 + 缓存的活视图：值与全量 eager 建表逐位一致
        # （同估算器/同坐标/同方向），一阶段用完再只对候选子集建矩阵。
        lazy = make_lazy_pairwise(
            tasks,
            self.fleet.all(),
            nests=self.nests,
            estimator=self.estimator,
            nest_offset=self.nest_offset,
        )

        # 6) 第一阶段：动态任务选择 → T_t^sel
        # 事件驱动：紧急任务 + 上一周期故障释放的任务强制纳入（绕过 Top-αK 预筛）
        forced_ids = set(replan_ctx.urgent_task_ids) | set(self._prior_fault_released())
        # 记账同步：二阶段②③吞吐上限（sortie_budget / max_participating_uavs）
        # 会把可用机强行压地，其航程不得计入一阶段能力预算；两侧旋钮取交，
        # 保证打开②③时 cap(t) 自动跟着收紧（γ 经济性是二阶段裁量，不计入）
        cap_cfg = replace(
            self.sel_cfg.capability,
            sortie_budget=merge_caps(
                self.sel_cfg.capability.sortie_budget, self.stage_cfg.sortie_budget
            ),
            max_participating_uavs=merge_caps(
                self.sel_cfg.capability.max_participating_uavs,
                self.stage_cfg.max_participating_uavs,
            ),
        )
        sel_config = SelectionConfig(
            priority=self.sel_cfg.priority,
            feasibility=self.sel_cfg.feasibility,
            marginal=self.sel_cfg.marginal,
            capability=cap_cfg,
            # Fix②b：anti_starvation 必须随每周期重建透传——漏传则静默退回
            # 默认的 enabled=False，yaml 里开了防饥饿也不生效（2026-10-15 长程
            # 实验发现：24 周期 n_forced_in 全为 0，老化任务直接饿死到超期）。
            anti_starvation=self.sel_cfg.anti_starvation,
            current_time=self._time,
            forced_task_ids=forced_ids,
        )
        cost_hint = self._cost_hint(tasks, avail, lazy)
        sel = run_selection(
            tasks,
            self.fleet.all(),
            config=sel_config,
            cost_hint=cost_hint,
            pairwise_cost=lazy,
            # 统一距离口径：第一阶段用与代价矩阵/ETA 同一把 DEM 尺子
            distance_fn=self._distance_fn,
        )
        record.selection = dict(sel.diagnostics)
        record.selection["replan"] = replan
        # S 成员无条件落盘（2026-10-22 覆盖门禁）：一阶检验与三分账的 S 基准，
        # 供「进过 S 没装」vs「从未进 S」归因；replan 时 forced_ids 仍只在重规划落。
        record.selection["selected_ids"] = sorted(sel.selected_ids)
        if replan:
            record.selection["forced_task_ids"] = sorted(forced_ids)

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
        # 只对候选子集建 4 个矩阵（pairwise 与一阶段共享同一张惰性表，
        # 一阶段算过的边直接命中、不重复估算）；provider.pairwise 即活视图，
        # 任何范围外查询按需解析，值与全量 eager 建表一致。
        cb_sel = build_pairwise_costs(
            selected,
            self.fleet.all(),
            nests=self.nests,
            dem=self.dem,
            estimator=self.estimator,
            nest_offset=self.nest_offset,
            distance_fn=self._distance_fn,
            pairwise_source=lazy,
        )
        # 事件驱动：不可用机巢（NEST_UNAVAILABLE）从终点候选集剔除
        stage_cfg = replace(
            self.stage_cfg, excluded_nest_ids=set(replan_ctx.unavailable_nest_ids)
        )
        stage: MUASStageResult = run_selective_muas(
            selected,
            self.fleet.all(),
            self.nests,
            cb_sel,
            fleet_uavs=self.fleet.all(),
            problem_config=replace(self.problem_cfg, current_time=self._time),
            stage_config=stage_cfg,
            solver_config=self.solver_cfg,
            nest_offset=self.nest_offset,
            current_time=self._time,
            solver=(self.solver_factory() if self.solver_factory is not None else None),
        )
        solution = stage.solution
        record.muas = dict(stage.diagnostics)
        record.muas["model_type"] = stage.model_type
        record.muas["solve_seconds"] = round(stage.elapsed_seconds, 4)
        record.muas["n_evals"] = stage.n_evals
        record.muas["n_selected"] = len(selected)
        record.muas["replan"] = replan
        if replan:
            record.muas["excluded_nest_ids"] = sorted(replan_ctx.unavailable_nest_ids)
        feas = solution.meta.get("feasibility", {}) or {}
        record.muas["violations"] = list(feas.get("violations", []))
        record.muas["penalty"] = float(feas.get("penalty", 0.0))

        # 分配结构与 DMDE 收敛轨迹（只记有任务的航次）
        record.tours = [tour_snapshot(t) for t in solution.tours if t.task_ids]
        record.cost_history = [float(v) for v in stage.cost_history]

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

    def _converged(self, cycle: int, last_arrival_cycle: int) -> bool:
        """收敛早停判定：到达全部注入后，池内未完成任务数 ``len(pool)`` 连续
        ``stable_patience`` 个周期不变即判稳态、建议停止。

        Args:
            cycle: 刚完成的周期号（1-based）。
            last_arrival_cycle: 最后有任务到达的周期号；此周期及之前为到达阶段，
                不计入稳态判定（到达本身就会改变池大小）。
        """
        if not self._conv_enabled:
            return False
        cur = len(self.pool)  # 未完成任务数 = 已完成/淘汰/不可达之后剩余
        if cycle <= last_arrival_cycle:
            # 仍在到达阶段：重置计数，避免把"到达导致的变化"误判为稳态
            self._conv_last_len = cur
            self._conv_streak = 0
            return False
        if self._conv_last_len is not None and cur == self._conv_last_len:
            self._conv_streak += 1
        else:
            self._conv_streak = 0
        self._conv_last_len = cur
        return self._conv_streak >= self._conv_patience

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
        last_arrival = max(arrivals.keys()) if arrivals else 1
        for step in range(1, n + 1):
            self.run_cycle(new_tasks=arrivals.get(step))
            if self._converged(step, last_arrival):
                print(f"[horizon] 收敛于周期 {step}：池内未完成任务数连续 "
                      f"{self._conv_streak} 周期不变（={len(self.pool)}），提前停止。")
                break
        return self.records

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _rehome_stranded_uavs(self) -> None:
        """把归属在不可用机巢的 UAV 强制改归属到其它可用机巢（改降）。

        覆盖全部挂靠 UAV（在途的由终点机巢剔除强制异巢终止，闲置的由本
        函数改归属）；归属写点唯一（design_nest_state.md §5），此处只调
        ``reassign_nest``。目标选择：最近的可用机巢（优先有空泊位者）。
        物理转场代价在本模型的周期粒度下忽略（与"执行推进瞬时到位"同一近似）。
        """
        dead = {n.id for n in self.nests if not n.available}
        if not dead:
            return
        alive = [n for n in self.nests if n.available]
        if not alive:
            return
        occ = self.fleet.nest_occupancy(self.nests)
        free = free_slots(alive, occ)
        for uav in self.fleet.all():
            if uav.nest_id not in dead:
                continue
            with_slot = [n for n in alive if free.get(n.id, 0) > 0]
            pool = with_slot or alive
            target = min(
                pool,
                key=lambda n: (n.x - uav.x) ** 2 + (n.y - uav.y) ** 2 + (n.z - uav.z) ** 2,
            )
            self.fleet.reassign_nest(uav.id, target.id)
            free[target.id] = free.get(target.id, 0) - 1

    def _inject_faults(self, cycle: int, record: CycleRecord) -> None:
        """执行中注入 UAV 故障，把其未完成任务释放回任务池。

        ``fleet.mark_fault`` 与 ``pool.release`` 两个状态机互不 import，
        由本层显式串联（论文 §6 的 T_t^release）。释放的任务记入
        ``record.released_by_fault``，供下一周期事件驱动重规划时强制纳入选择。
        """
        for uav_id in self.hcfg.faults.get(cycle, []):
            if uav_id not in self.fleet:
                continue
            stranded = self.fleet.mark_fault(uav_id)
            if not stranded:
                continue
            self.pool.release(stranded, reason="uav_fault")
            record.released_ids.extend(int(t) for t in stranded)
            record.released_by_fault.extend(int(t) for t in stranded)

    def _prior_fault_released(self) -> list[int]:
        """上一周期因 UAV 故障释放回任务池的任务 id（本周期强制纳入选择）。"""
        if not self.records:
            return []
        return list(self.records[-1].released_by_fault)

    def _cost_hint(
        self,
        tasks: Sequence[Task],
        avail: Sequence[UAV],
        pairwise: Mapping[tuple[int, int], float],
    ) -> dict[int, float]:
        """第一阶段用的粗粒度执行代价 C_i(t)：最近可用 UAV 到该任务的三维代价。

        ``pairwise`` 为一阶段前的惰性表（:func:`make_lazy_pairwise`）；
        这里发起的 n·K 次查询即它的首批缓存条目（有序对 UAV→任务，
        与全量 eager 建表同方向同值）。
        """
        hint: dict[int, float] = {}
        for t in tasks:
            best = float("inf")
            for u in avail:
                c = pairwise[(-(1 + u.id), t.id)]
                if c < best:
                    best = c
            hint[t.id] = best if np.isfinite(best) else 1e6
        return hint

    # 违约标签 → 受影响任务的映射规则（供完成闸门归因）
    #   任务级：tw_task_{tid} / unknown_task_{tid}
    #   UAV 级：range_uav_{uid} / time_uav_{uid} → 该机航次内全部任务
    #   机巢级：nest_capacity_{nid} / nest_unavailable_{nid} / bad_nest_{nid}
    #           / end_nest_unreachable_{nid} → 终点为该巢的航次内全部任务
    _UAV_LEVEL_TAGS = ("range_uav", "time_uav")
    _NEST_LEVEL_TAGS = (
        "nest_capacity",
        "nest_unavailable",
        "bad_nest",
        "end_nest_unreachable",
    )

    def _violated_task_ids(
        self, solution: AllocationSolution
    ) -> dict[int, list[str]]:
        """把 ``check_feasible`` 的违约标签归因到具体任务。

        Returns:
            ``{task_id: [violation_tag, ...]}``；解可行时返回空字典。

        解析不了的标签（结构类 ``sum_x_neq_y``、时序 ``seq_group_*`` 等）
        不参与归因——它们不指向单个任务的交付失败，交给可行性判定整体把关。
        """
        feas = solution.meta.get("feasibility", {}) or {}
        tags = [str(t) for t in feas.get("violations", [])]
        if not tags:
            return {}

        uav_tasks: dict[int, list[int]] = {}
        nest_tasks: dict[int, list[int]] = {}
        for tour in solution.tours:
            tids = [int(i) for i in tour.task_ids]
            uav_tasks.setdefault(int(tour.uav_id), []).extend(tids)
            if tour.end_nest_id is not None:
                nest_tasks.setdefault(int(tour.end_nest_id), []).extend(tids)

        out: dict[int, list[str]] = {}

        def _mark(tid: int, tag: str) -> None:
            out.setdefault(int(tid), []).append(tag)

        for tag in tags:
            kind, _, tail = tag.rpartition("_")
            try:
                key = int(tail)
            except ValueError:
                continue
            if kind == "tw_task" or kind == "unknown_task":
                _mark(key, tag)
            elif kind in self._UAV_LEVEL_TAGS:
                for tid in uav_tasks.get(key, []):
                    _mark(tid, tag)
            elif kind in self._NEST_LEVEL_TAGS:
                for tid in nest_tasks.get(key, []):
                    _mark(tid, tag)

        # 兜底：航次自身被判不可行（tour.feasible=False）时，其任务一并归因
        for tour in solution.tours:
            if tour.feasible:
                continue
            for tid in tour.task_ids:
                _mark(int(tid), f"tour_infeasible_uav_{int(tour.uav_id)}")

        return out

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

        # 完成闸门：仅**物理不可行**的违约任务不顺延完成（默认开启）。
        # 2026-10-15 协议纠正「迟到不是废飞」：tw_task（迟到）不再是硬
        # 违约——晚到任务正常完成、计入吞吐，有效奖励已在适应度里按
        # 迟到折减（late_reward_decay_seconds），此处只把「飞不到」
        # （航程/续航/终点机巢/结构类）解绑弹回。
        violated = (
            self._violated_task_ids(solution)
            if cfg.completion_feasibility_gate
            else {}
        )
        hard_violated = {
            tid: tags
            for tid, tags in violated.items()
            if any(not str(tag).startswith("tw_task_") for tag in tags)
        }
        lateness_map = (solution.meta.get("feasibility", {}) or {}).get(
            "lateness", {}
        ) or {}

        for tour in solution.tours:
            if not tour.task_ids:
                continue
            bound = self.fleet.bound_tasks(tour.uav_id)
            if not bound:
                continue

            bad = [int(t) for t in bound if int(t) in hard_violated]
            ok = [int(t) for t in bound if int(t) not in hard_violated]

            # ① 物理不可行交付：解绑回任务池，下一周期重新纳入选择
            #    （不计完成、不计奖励；废飞指标只统计这一类）
            if bad:
                self.fleet.unbind_tasks(tour.uav_id, bad)
                self.pool.release(bad, reason="infeasible_delivery")
                for tid in bad:
                    task = self.pool.find(tid)
                    if task is None:
                        continue
                    tags = violated.get(tid, [])
                    task.meta["infeasible_delivery"] = True
                    task.meta["infeasible_tags"] = list(tags)
                    record.infeasible_tags[str(tid)] = list(tags)
                record.infeasible_ids.extend(bad)

            if not ok:
                continue
            # ② 正常交付（含迟到交付）：记录完成时刻与迟到秒数，
            #    供 avg_wait / 按时率 / 平均迟到等指标使用
            for tid in ok:
                task = self.pool.find(int(tid))
                if task is not None:
                    task.meta["completed_at"] = float(self._time)
                    late = float(lateness_map.get(int(tid), 0.0) or 0.0)
                    if late > 1e-9:
                        task.meta["lateness_seconds"] = late
                        record.late_ids.append(int(tid))
                        record.lateness_by_task[str(int(tid))] = late
            self.fleet.complete_tasks(tour.uav_id, ok)
            self.pool.complete(ok)
            record.completed_ids.extend(int(t) for t in ok)

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
            # 往返口径的航程裕度：1.0 = 物理上限（与完成闸门同口径）。
            # 旧单程口径的 0.95 不可直接迁移（会把可达半径腰斩）。
            range_margin=float(feas_d.get("range_margin", 1.0)),
            # 默认 False（2026-10-15 用户原则「任务不被完成只有物理约束被
            # 违反」）：时间窗不是参选资格，迟到由适应度折减定价。仅消融
            # 对照（验证时间预筛的代价）时显式开启。
            check_time_window=bool(feas_d.get("check_time_window", False)),
            budget_factor=float(feas_d.get("budget_factor", 1.0)),
            # 消融口径专用：check_time_window=True 时的迟到容忍 W（秒）。
            late_tolerance_seconds=float(feas_d.get("late_tolerance_seconds", 0.0)),
        )
        marg_d = dict(d.get("marginal", {}) or {})
        marginal = MarginalConfig(
            eps=float(marg_d.get("eps", 1.0)),
            max_selected=marg_d.get("max_selected", None),
            max_ratio=float(marg_d.get("max_ratio", 2.0)),
            min_marginal_ratio=float(marg_d.get("min_marginal_ratio", 0.0)),
            # Fix① 防饥饿软加权：等待时长 → 终刀接受判据的乘性加权。
            # 此前遗漏接线 → yaml 写了 age_gamma 却静默退回 0.0（机制失效）。
            age_gamma=float(marg_d.get("age_gamma", 0.0)),
            wait_scale=float(marg_d.get("wait_scale", 3600.0)),
        )
        cap_d = dict(d.get("capability", {}) or {})
        capability = CapabilityScaleConfig(
            enabled=bool(cap_d.get("enabled", True)),
            budget_scale=float(cap_d.get("budget_scale", 1.0)),
            quantile=float(cap_d.get("quantile", 0.5)),
            min_cap=int(cap_d.get("min_cap", 1)),
            sortie_budget=(
                None if cap_d.get("sortie_budget") is None
                else int(cap_d["sortie_budget"])
            ),
            max_participating_uavs=(
                None if cap_d.get("max_participating_uavs") is None
                else int(cap_d["max_participating_uavs"])
            ),
            mode=str(cap_d.get("mode", "intersect")),
            max_expand_factor=(
                None
                if cap_d.get("max_expand_factor") is None
                else float(cap_d["max_expand_factor"])
            ),
            # Fix③：即便 enabled=False 也按吞吐上界收敛候选规模（默认关）
            fallback_when_disabled=bool(cap_d.get("fallback_when_disabled", False)),
        )
        # Fix② 硬性不停饿兜底：此前遗漏接线 → yaml 写了 enabled=true 却静默
        # 退回 False，防饥饿机制从未真正生效（老化/临期任务拿不到强制优先）。
        asv_d = dict(d.get("anti_starvation", {}) or {})
        anti_starvation = AntiStarvationConfig(
            enabled=bool(asv_d.get("enabled", False)),
            force_after_seconds=float(asv_d.get("force_after_seconds", 1200.0)),
            deadline_slack=float(asv_d.get("deadline_slack", 300.0)),
        )
        return SelectionConfig(
            priority=priority, feasibility=feasibility, marginal=marginal,
            capability=capability, anti_starvation=anti_starvation,
        )
