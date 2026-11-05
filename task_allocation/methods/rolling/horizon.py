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

import sys
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVStatus,
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
from task_allocation.methods.rolling.sequence_group import (
    expand_to_groups,
    group_members,
    has_groups,
    prune_incomplete_groups,
    prune_unwakeable_groups,
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
    fleet_max_range,
    static_unreachable,
)
from task_allocation.methods.selection.marginal_gain import MarginalConfig
from task_allocation.methods.muas.cost.nest_keys import DEFAULT_NEST_OFFSET
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
    # 缺省满电航程（米）：UAV.max_range 为 None 时的回退值（同构退化）。
    # 异构机队（2026-11-04）：各机自带 max_range / max_time / speed，
    # 换电恢复与可达性判定一律经 UAV.full_charge_range(default) 取值，
    # 禁止直接读本字段判定单机能力。
    uav_max_range: float = 60000.0
    # 故障注入：{周期号(1-based): [uav_id 或 "busiest", ...]}，用于验证 T_t^release 链路。
    # 注入时机是「本周期解已回写、任务尚在执行中」，即 UAV 真正带着任务时，
    # 这样 mark_fault 才有未完成任务可释放（若在周期起点注入，上一周期的任务
    # 已全部完成解绑，无任务可释放，链路观察不到）。
    # "busiest" = 注入时刻绑定任务最多的未故障 UAV（平手取 id 小者）——保证
    # 故障**有实效**（有任务可释放），不靠运气选中空闲机（exp03 E1 稳定性）。
    faults: dict[int, list[int | str]] = field(default_factory=dict)
    # 机巢闭塞注入：{周期号(1-based): [nest_id, ...]}，周期起点（事件检测前）
    # 置 available=False + meta.close_reason="injected"，经 known_unavailable
    # 边沿机制上报 NEST_UNAVAILABLE（exp03 E3 的注入面，对称 faults）。
    nest_closures: dict[int, list[int]] = field(default_factory=dict)
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
    # 收敛早停（2026-11-04 用户裁定口径）：到达全部注入后，**池空**（不存在
    # 「能被完成却没被完成」的任务；静态几何不可达已出池，服务休眠仍在池中）
    # 即自然收敛停机；否则持续推进，让休眠任务等条件恢复唤醒——「没服务，
    # 只要能服务，迟早被服务」。stable_patience 降为兜底：池非空但连续该数
    # 个周期零进展（无完成/唤醒/新休眠/新出池且池大小不变）才提前停，防止
    # 时变能力永不恢复时空转到 n_cycles 硬上限。n_cycles 是硬安全上限，
    # 不是主停机条件（真实周期数由本判据决定）。
    stop_when_stable: bool = False
    stable_patience: int = 4
    # 服务休眠（2026-11-04 异构化口径；2026-10-25 起归因分离）：任务
    # **几何可达**（存在机巢使往返 ≤ 机队最强满电航程，static_unreachable
    # 判否）却连续 service_patience 个周期「无可用 UAV 在往返航程内」时，
    # 转**休眠**（DORMANT：不出池、不进候选），条件恢复即唤醒。
    #
    # 归因背景：预筛原先把「最近可用机往返超限」一律记
    # exceed_round_trip_range（物理不可达），于是这类任务既不进
    # UNREACHABLE（分母照算）、又每周期被拒（分子不计）——exp05 seed43
    # 实证 3 个任务（最近巢往返 74km ≤ 80km，但机队漂移后该巢空巢）被
    # 永久卡死，完成率虚低为 0.950。两把尺子（几何 vs 当前可用机）必须
    # 同源归因。
    #
    # 为什么休眠而非出池（2026-11-04）：服务可达性是时变条件（可用机集合
    # 随换电/故障恢复/机队拓扑演化而变，异构机队尤甚），「连续够不着」≠
    # 「永远够不着」；出池会永久丢失可服务任务，与「暂缓任务回池保留、
    # 持续获得调度机会」矛盾。休眠任务不进候选（同擑 churn 效果）、不入
    # 完成率分母，逐周期复查唤醒（记录 dormant_ids / woken_ids 供归因）。
    service_patience: int = 4


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
        # 【坐标不做 round】2026-10-29 修正：旧实现把起点 round 到 3 位小数，
        # 而运行端 pairwise 用的是**精确**坐标。两者之差只有 0.0002~0.0004 m，
        # 但建筑障碍包络是**离散**查询（采样点是否落在某个建筑多边形内），
        # 微小偏移可让某采样点跨过建筑边界 ⇒ 障碍高度跳变几十米 ⇒ 单腿航程
        # 跳变 1.2 m 量级。审计端拿 round 过的坐标复算，与记账值差 2.5 m
        # （0.076%），J 项因 1 m 绝对容差假 FAIL——且**方向不定**，取决于
        # 采样点落在边界哪侧（gen200 那次 PASS 纯属运气）。
        # 落盘的舍入收益（可读性 / 几字节）远小于「下游无法复算」的代价。
        "start": [
            float(tour.start_x),
            float(tour.start_y),
            float(tour.start_z),
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
    # 超期滞留（无超期淘汰，2026-10-24 口径）：已过 latest 仍在池、可迟到交付；
    # 旧字段 expired_ids（超期出池=淘汰）已废除。
    overdue_ids: list[int] = field(default_factory=list)
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
    # 静态物理不可达（「过于偏远」）：所有机巢往返航程都超过**机队最强**
    # 满电可及范围（异构口径：最强机也够不着才叫永久，2026-11-04），
    # 入池判定一次性出池。用户原则（2026-10-15）下唯一合法的
    # 未完成原因，单列计数，不混入淘汰。
    unreachable_ids: list[int] = field(default_factory=list)
    # 服务休眠（2026-11-04 异构化口径）：几何可达但连续 service_patience
    # 周期无可用机在往返航程内 → 休眠（不出池、不进候选），条件恢复
    # 即唤醒。dormant_ids = 本周期新休眠；woken_ids = 本周期唤醒。
    dormant_ids: list[int] = field(default_factory=list)
    woken_ids: list[int] = field(default_factory=list)
    # 停机原因（2026-11-04）：仅收敛那一周期由 run() 回填。
    # "pool_empty" = 自然收敛（不存在能被完成却没被完成的任务）；
    # "no_progress" = stable_patience 零进展兜底、非自然收敛（须与
    # n_dormant_end 等单列指标并读）；"" = 本周期未发生停机。
    stop_reason: str = ""
    # 顺序组连坐暂缓（2026-11-04 用户裁定 3）：组内某成员物理不可行被弹回
    # 时，为守住「同周期完成或同时暂缓」，同组其余成员一并撤下、release 回
    # 池、下周期整组重试。它们本身没有物理违约，**不计废飞**
    # （infeasible_ids），单列于此供审计；也不与 deferred_ids 混账（它们
    # 本周期已进执行集，只是周期末判定不完成）。
    seq_group_rolled_back_ids: list[int] = field(default_factory=list)
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
            "overdue_ids": self.overdue_ids,
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
            "n_dormant": len(self.dormant_ids),
            "dormant_ids": self.dormant_ids,
            "n_woken": len(self.woken_ids),
            "woken_ids": self.woken_ids,
            "stop_reason": self.stop_reason,
            "seq_group_rolled_back_ids": self.seq_group_rolled_back_ids,
            "n_seq_group_rolled_back": len(self.seq_group_rolled_back_ids),
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
        nest_offset: int = DEFAULT_NEST_OFFSET,
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
        self._warn_stale_forced_task_ids()

        self._nest_by_id = {n.id: n for n in self.nests}
        self._time = float(pool.current_time)
        self._known_unavailable = {n.id for n in self.nests if not n.available}
        # 事件日志边沿去重：同一事件主键只报首次，见 _fresh_events（补丁 F3）
        self._seen_event_keys: set[tuple[str, int]] = set()
        # 故障释放任务的强制纳入保护（补丁 F4）：释放即入集、选择消费后清空；
        # 退化周期（无任务/无可用机提前 return）不消费、顺延到下个选择周期，
        # 「释放任务下周期强制纳入」不因单周期空转而丢失。
        self._pending_forced: set[int] = set()
        self.records: list[CycleRecord] = []
        # 收敛早停状态（2026-11-04 口径）：主判据 = 池空（不存在「能被完成
        # 却没被完成」的任务）；stable_patience 降为零进展兜底。到达阶段
        # （cycle <= 最后到达周期）不计数。
        self._conv_enabled = bool(self.hcfg.stop_when_stable)
        self._conv_patience = int(self.hcfg.stable_patience)
        self._conv_streak = 0
        # 零进展签名 = (池内未完成任务数, 休眠数)；用于识别「条件恢复中」
        # 的推进（唤醒/休眠数变化算有进展，不误判死循环）。
        self._conv_last_sig: tuple[int, int] | None = None
        self._conv_reason: str = ""
        # 服务不可达追踪（2026-10-25 口径对齐）：task_id → 连续「无可用 UAV
        # 在往返航程内」的周期数。达到 hcfg.service_patience 即判机队服务
        # 不可达出池（与静态几何不可达同等对待，单列归因 reason="service"）。
        self._service_stall: dict[int, int] = {}

    # ------------------------------------------------------------------
    # 单周期
    # ------------------------------------------------------------------

    def _warn_stale_forced_task_ids(self) -> None:
        """配置面 ``SelectionConfig.forced_task_ids`` 的一次性告警。

        滚动层每个周期都按当前重规划上下文重建强制集（
        ``forced = urgent_task_ids ∪ 上周期故障释放``）并覆盖该字段，故构造期
        填进来的值是**无效输入**。历史上几次调试都栽在这里（配了强制集、覆盖
        度纹丝不动），而失败是静默的——所以按「回退永不静默」惯例显式说一句。
        默认空集 ⇒ 不打印 ⇒ 对既有基线的输出零影响。
        """
        forced = getattr(self.sel_cfg, "forced_task_ids", None)
        if not forced:
            return
        print(
            "[horizon][warn] selection_config.forced_task_ids 非空，但在滚动层无效："
            "run_cycle 每周期按 urgent_task_ids ∪ 故障释放 重建强制集并覆盖该字段"
            f"（已填 {sorted(forced)[:8]}{'…' if len(forced) > 8 else ''}，共 {len(forced)} 个）。"
            "如需每周期恒强制某任务，请改走 anti_starvation 或直接 fix 事件源。",
            file=sys.stderr,
        )

    def _fleet_max_range(self) -> float:
        """机队最强满电航程（异构 per-UAV max_range 优先，回退全局缺省）。

        永久性不可达判定（static_unreachable）与预筛归因分界必须用最强机：
        「永久不可达」只在最强机也够不着时成立（2026-11-04 设计关键点 1）。
        """
        return float(
            fleet_max_range(self.fleet.all(), self.hcfg.uav_max_range)
            or self.hcfg.uav_max_range
        )

    def _screen_unreachable(self, tasks: Sequence[Task]) -> list[Task]:
        """静态物理不可达筛选：往返航程超满电可及范围的任务出池。

        判据用**机队最强满电航程**（``_fleet_max_range``：异构机队取全队
        ``max_range`` 最大值、None 回退 ``hcfg.uav_max_range``）。margin 取
        **物理上限 1.0**（与完成闸门同口径）——规划保守度（range_margin）
        是 stage 内部的参与/provisioning 决策，用作静态边界会误杀物理
        可达的远站任务。距离走运行级记忆化的 DEM 三维 ``_distance_fn``，
        跨周期命中缓存。
        """
        if not tasks:
            return []
        fleet_max = self._fleet_max_range()
        hit = [
            t
            for t in tasks
            if static_unreachable(
                t,
                self.nests,
                fleet_max,
                distance_fn=self._distance_fn,
            )
        ]
        if hit:
            self.pool.mark_unreachable([t.id for t in hit], reason="geo")
        return hit

    def _serviceable(self, task: Task, uavs: Sequence[UAV]) -> bool:
        """是否存在可用机在往返航程内（逐机判定，异构口径）。

        ∃ 可用机 u：``2·c(task,u) ≤ u.full_charge_range``。同构等航程时与
        旧「2·cost_hint ≤ 全局 uav_max_range」逐位一致；异构下短航程机
        装不下 ≠ 任务不可服务（更远的长航程机可能装得下）。代价走运行级
        记忆化的 DEM 三维 ``_distance_fn``（与预筛/静态判定同源同尺）。
        """
        for u in uavs:
            if not u.is_available:
                continue
            r = u.full_charge_range(self.hcfg.uav_max_range)
            if r is None:
                continue
            c = float(self._distance_fn(u.position, task.position))
            if 2.0 * c <= r:
                return True
        return False

    def _wake_dormant(self) -> list[int]:
        """服务休眠唤醒（2026-11-04）：本周期有可用机可服务即回 PENDING。

        在 ``begin_cycle`` 之前调用，唤醒任务立即进入本周期 T_t；年龄
        （arrival_time）累计不中断，唤醒后继续参与优先级/防饥饿排序。

        顺序组原子性（2026-11-04 用户裁定）：带 ``sequence_group`` 的链**整组
        一起唤醒**——组内只要还有一个成员不可服务，整条链继续休眠，避免
        「组内部分唤醒进 T_t、部分仍休眠」把调度单元拆开。无组任务行为不变。
        """
        uavs = self.fleet.all()
        dormant = self.pool.dormant_tasks()
        wakeable = {t.id for t in dormant if self._serviceable(t, uavs)}
        if has_groups(dormant):
            wakeable = prune_unwakeable_groups(wakeable, group_members(dormant))
        wake_ids = sorted(wakeable)
        if not wake_ids:
            return []
        self.pool.wake(wake_ids)
        for tid in wake_ids:
            self._service_stall.pop(tid, None)
        return wake_ids

    def _update_service_stall(self, tasks: Sequence[Task]) -> list[Task]:
        """机队服务不可达追踪：持续无可用机在往返航程内的任务**转休眠**。

        逐机判据 ``_serviceable``（∃ 可用机：2·c ≤ 该机满电航程，异构
        口径）即「本周期有可用机能服务它」——与预筛 ``no_uav_in_range``
        同源同尺。连续 ``service_patience`` 个周期不满足 → 休眠（
        ``mark_dormant``：不出池、不进候选，2026-11-04 起取代旧
        ``reason="service"`` 出池口径——服务可达性是时变条件，异构机队下
        「连续够不着」≠「永远够不着」）。条件恢复由 ``_wake_dormant``
        唤醒，年龄累计不中断。

        判据**独立于任务是否被选中/完成**（只看机队几何能力），因此不构成
        循环定义；休眠与几何不可达（UNREACHABLE 出池）严格区分。
        """
        patience = int(getattr(self.hcfg, "service_patience", 0) or 0)
        if patience <= 0:
            return []
        uavs = self.fleet.all()
        newly: list[int] = []
        for t in tasks:
            if self._serviceable(t, uavs):
                self._service_stall.pop(t.id, None)
            else:
                n = self._service_stall.get(t.id, 0) + 1
                self._service_stall[t.id] = n
                if n >= patience:
                    newly.append(t.id)
        if not newly:
            return []
        # 顺序组原子性（2026-11-04）：命中组内任一成员 ⇒ **整组一起休眠**，
        # 避免「组内一个 DORMANT、其余仍 PENDING」把调度单元拆成两半。
        # 组映射取全池（含在途/已出池外的活动成员），mark_dormant 会自行
        # 跳过不在池 / 已在执行 / 已休眠的 id，无需额外过滤。
        if has_groups(tasks):
            newly = sorted(expand_to_groups(set(newly), self._seq_groups()))
        marked = self.pool.mark_dormant(newly, reason="service")
        for tid in newly:
            self._service_stall.pop(tid, None)
        return marked

    # ------------------------------------------------------------------
    # 顺序约束组原子性（2026-11-04 用户裁定）：带 sequence_group 的链是
    # 不可拆的调度单元，本周期要么整组一起完成，要么整组一起暂缓。
    # 所有方法在「无组」时立即旁路，既有实验 sequence_chain_ratio=0.0
    # ⇒ 零行为变化。详见 methods/rolling/sequence_group.py 的口径边界。
    # ------------------------------------------------------------------

    def _seq_groups(self) -> dict[int, list[int]]:
        """全池活动任务的顺序组映射（含 DORMANT，不含已出池 DONE/UNREACHABLE）。

        以**全池**而非本周期候选为基准，才能识别「组内某成员休眠/在途」
        导致的不完整；已出池成员不再阻塞其余成员（避免永久暂缓死锁）。
        """
        return group_members(self.pool.active_tasks())

    def _seq_admission_gate(self, tasks: list[Task]) -> tuple[list[Task], list[int]]:
        """准入闸门：成员不齐全的组整组一起暂缓（不进本周期候选）。

        组映射取**全池活动任务**：组内若有成员休眠 / 在途（不在本周期 T_t），
        则把已在 T_t 的成员一并撤下，返回 ``(收口后候选, 一起暂缓的 id)``。
        已出池（DONE / UNREACHABLE）成员不在组映射里，故不会造成永久不完整。
        被撤下的任务保持 PENDING 留池、年龄不中断，下周期整组再一起参与调度。
        这是**批次 defer**（调度原子性），不打 DORMANT 标、不计休眠指标。
        """
        if not has_groups(tasks):
            return tasks, []
        drop = prune_incomplete_groups({int(t.id) for t in tasks}, self._seq_groups())
        if not drop:
            return tasks, []
        return [t for t in tasks if int(t.id) not in drop], sorted(drop)

    def _seq_delivery_gate(self, solution: AllocationSolution) -> list[int]:
        """交付闸门：解里只装载了组的一部分 ⇒ 把已装载成员撤下（整组暂缓）。

        在 ``apply_solution`` / ``record.tours`` 生成之前调用，直接修改
        ``tour.task_ids``；撤下的任务自然落入 ``deferred_ids``。est_cost
        沿用原 tour 估值（偏保守，recharge_at_nest 下会回满），不重算——
        组原子性优先于航程记账精度，如实声明、不影响完成口径。
        """
        groups = self._seq_groups()
        if not groups:
            return []
        executed: set[int] = set()
        for tour in solution.tours:
            executed.update(int(i) for i in tour.task_ids)
        drop = prune_incomplete_groups(executed, groups)
        if not drop:
            return []
        for tour in solution.tours:
            tour.task_ids = [int(i) for i in tour.task_ids if int(i) not in drop]
        solution.executed_task_ids = [
            int(i) for i in solution.executed_task_ids if int(i) not in drop
        ]
        return sorted(drop)

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

        # 0) 服务休眠唤醒（2026-11-04）：条件恢复（出现可服务的可用机）
        #    即唤醒回 PENDING，本周期即重新进入 T_t，年龄累计不中断。
        woken_ids = self._wake_dormant()

        # 1) 周期开始：T_t = remain ∪ new ∪ release
        snap = self.pool.begin_cycle(new_tasks=new_tasks, current_time=self._time)
        tasks = snap.tasks

        record = CycleRecord(
            cycle=snap.cycle,
            current_time=self._time,
            pool_counts=dict(snap.counts),
            overdue_ids=list(snap.overdue_ids),
        )
        record.woken_ids = list(woken_ids)

        # 2) 静态物理不可达筛选（用户原则 2026-10-15：任务不被完成
        # 只有物理约束被违反）。任务/巢位固定 + 每周期满电 ⇒ 判定与周期
        # 状态无关：所有机巢往返航程都超过满电可及范围的任务一次性出池
        # （UNREACHABLE），不留在池里空转——否则它每周期进选择、被规划器
        # 丢弃，把「过于偏远」误导记成能力不足（shortfall）。
        unreachable = self._screen_unreachable(tasks)
        if unreachable:
            record.unreachable_ids = [t.id for t in unreachable]
            dropped = {t.id for t in unreachable}
            tasks = [t for t in tasks if t.id not in dropped]

        # 3) 事件注入（实验时刻表）→ 检测 → 事件驱动重规划决策（论文 §6）
        self._apply_nest_closures(snap.cycle)
        events = detect_events(
            self.fleet.all(),
            self.pool.active_tasks(),
            self.nests,
            self.event_cfg,
            current_time=self._time,
            known_unavailable=self._known_unavailable,
        )
        self._known_unavailable = {n.id for n in self.nests if not n.available}
        # 事件日志走边沿（补丁 F3）：UAV_FAULT 是持续态、URGENT_TASK 是静态属性，
        # 电平上报会让同一事件每周期重复进日志、replan 计数虚高（P1-3）。
        # 决策上下文 replan_ctx 仍取电平全集（强制纳入是持续语义）。
        fresh_events = self._fresh_events(events)
        record.events = [e.as_dict() for e in fresh_events]
        # 机巢不可用 → 其**全部**归属 UAV 强制改降（含闲置机：合并修复可能
        # 让参与机零任务停在原巢）——归属巢退出运行后不得再有 UAV 挂靠。
        if self.hcfg.event_replan:
            self._rehome_stranded_uavs()

        # 把事件解析为本期重规划上下文；replan = 本周期发生**新**事件（边沿）
        # 且机制启用——可直接计「重规划次数」（补丁 F3）。
        replan_ctx = summarize_events(events, self.event_cfg)
        replan = self.hcfg.event_replan and should_replan(fresh_events, self.event_cfg)
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
        # 事件驱动（补丁 F1 闸门）：紧急任务 + 故障释放任务强制纳入（绕过 Top-αK
        # 预筛）。强制纳入是「反应式调整」的组成部分，必须与 _rehome_stranded_uavs
        # 同受 event_replan 开关控制——此前无条件生效，消融臂关停不干净（P0-1）。
        forced_ids = set()
        if self.hcfg.event_replan:
            forced_ids = set(replan_ctx.urgent_task_ids) | set(self._pending_forced)
        self._pending_forced.clear()
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
        # 6.1) 机队服务不可达转休眠（2026-11-04 异构化口径）：几何可达但
        # 连续 service_patience 周期无可用机在往返航程内 → 休眠（不出池、
        # 不进候选，擑 churn），条件恢复即唤醒、年龄累计不中断。必须在
        # 选择前转休眠，否则它每周期进候选又被拒，空转 churn。
        newly_dormant = self._update_service_stall(tasks)
        if newly_dormant:
            dorm_ids = {t.id for t in newly_dormant}
            record.dormant_ids = [t.id for t in newly_dormant]
            tasks = [t for t in tasks if t.id not in dorm_ids]
        # 6.2) 顺序组准入闸门（2026-11-04 用户裁定 3「同周期完成，或同时暂缓」）：
        # 组内成员不齐（部分休眠/在途）时，把已在 T_t 的成员一并撤下，整组
        # 本周期不进选择层。只收窄**送进选择层的副本**，`tasks` 保持 6.1 后的
        # 候选全集不变，使步骤 8 的 deferred_ids 记账天然包含被暂缓的组，
        # 无需额外补账。无组时 has_groups() 为 False ⇒ 原列表原样返回。
        tasks_for_selection, seq_deferred = self._seq_admission_gate(tasks)
        sel = run_selection(
            tasks_for_selection,
            self.fleet.all(),
            config=sel_config,
            cost_hint=cost_hint,
            pairwise_cost=lazy,
            # 统一距离口径：第一阶段用与代价矩阵/ETA 同一把 DEM 尺子
            distance_fn=self._distance_fn,
            # 几何可达性判据（与 static_unreachable 同源）：让预筛把往返
            # 超限分成「真几何不可达」与「当前无可用机」两类归因
            nests=self.nests,
            max_range=self._fleet_max_range(),
        )
        record.selection = dict(sel.diagnostics)
        record.selection["replan"] = replan
        if seq_deferred:
            # 准入门禁整组暂缓的 id：本周期未进选择层，仍留池 PENDING。
            # 单列供审计（它们不在 selected_ids / deferred_ids 的求解口径里）。
            record.selection["seq_group_admission_deferred"] = list(seq_deferred)
        # S 成员无条件落盘（2026-10-22 覆盖门禁）：一阶检验与三分账的 S 基准，
        # 供「进过 S 没装」vs「从未进 S」归因；forced_ids 有即落（与 replan
        # 解耦——边沿化后「有强制」与「有新事件」不再同周期出现）。
        record.selection["selected_ids"] = sorted(sel.selected_ids)
        if forced_ids:
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
        # 事件驱动（补丁 F1 闸门，同上）：不可用机巢（NEST_UNAVAILABLE）从
        # 终点候选集剔除。反应式调整，event_replan 关停时不得生效（P0-1）。
        excluded_nest_ids = (
            set(replan_ctx.unavailable_nest_ids) if self.hcfg.event_replan else set()
        )
        stage_cfg = replace(self.stage_cfg, excluded_nest_ids=excluded_nest_ids)
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
        # 7.1) 顺序组交付闸门（2026-11-04 用户裁定 3）：MUAS 解里只装载了组的
        # 一部分 ⇒ 把已装载成员一并撤下，整组批次 defer（留池 PENDING、下周期
        # 整组重试）。必须在 record.tours / apply_solution / fleet.apply_solution
        # 之前改 tour.task_ids，让任务池回写与机队绑定同步撤下，避免出现
        # 「解里只有半个组、机队却已绑定」的不一致。无组时立即返回 []。
        seq_drop = self._seq_delivery_gate(solution)
        record.muas = dict(stage.diagnostics)
        record.muas["model_type"] = stage.model_type
        record.muas["solve_seconds"] = round(stage.elapsed_seconds, 4)
        record.muas["n_evals"] = stage.n_evals
        record.muas["n_selected"] = len(selected)
        record.muas["replan"] = replan
        if excluded_nest_ids:
            record.muas["excluded_nest_ids"] = sorted(excluded_nest_ids)
        feas = solution.meta.get("feasibility", {}) or {}
        record.muas["violations"] = list(feas.get("violations", []))
        record.muas["penalty"] = float(feas.get("penalty", 0.0))
        # 交付闸门撤下的成员：同步求解期诊断（n_executed/n_deferred 是
        # stage 求解时快照，撤下后会偏大），并单列被撤 id 供审计。
        # 真正的交付记账仍以 apply_res.executing_ids / deferred_ids 为准。
        if seq_drop:
            record.muas["seq_group_deferred_ids"] = list(seq_drop)
            record.muas["n_executed"] = len(solution.executed_task_ids)
            record.muas["n_deferred"] = len(selected) - len(solution.executed_task_ids)

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

        # 9) 执行中故障注入 → 未完成任务释放回任务池（T_t^release）
        # 时机必须在本周期解回写之后、完成反馈之前：此时 UAV 才真正带着任务。
        self._inject_faults(snap.cycle, record)

        # 10) 执行推进：位置 / 航程 / 完成反馈
        self._advance_execution(solution, record)

        self._fill_occupancy(record)
        self.records.append(record)
        return record

    # ------------------------------------------------------------------
    # 多周期
    # ------------------------------------------------------------------

    def _converged(self, cycle: int, last_arrival_cycle: int) -> bool:
        """收敛早停判定（2026-11-04 用户裁定口径）。

        主判据：**不存在「能被完成却没被完成」的任务即停止**。静态几何不可达
        任务已在 ``run_cycle`` 出池，池内留存的未完成任务（含服务休眠）都属
        能力范围内、迟早会被服务，故「池空」= 自然收敛（``pool_empty``）。

        否则持续推进（「一直下去」）——不再因「池大小连续不变」提前停：休眠
        任务留在池里等条件恢复唤醒时，池大小不变恰恰是「正在等」，不是「没
        进展」。旧口径把这种状态误判稳态、把休眠任务锁死，本次废弃。

        ``stable_patience`` 降为**兜底**：仅当池非空、**池内无休眠任务**（不
        在等条件恢复）且连续该数个周期零进展（池大小不变，本周期无完成 /
        唤醒 / 新休眠 / 新不可达出池）才提前停，防止能力范围外的死循环空转到
        ``n_cycles`` 硬安全上限。此时记 ``no_progress``，属**非自然收敛**，
        metrics 侧的 ``n_dormant_end`` 等单列指标如实暴露，不粉饰成干净收敛。

        ``stop_when_stable`` 仍是「是否允许早于 n_cycles 停止」的总开关；关闭
        时按固定周期数跑满（既有固定周期实验 / 全部单周期测试依赖此语义）。

        Args:
            cycle: 刚完成的周期号（1-based）。
            last_arrival_cycle: 最后有任务到达的周期号；此周期及之前为到达阶段，
                不计入收敛判定（到达本身会改变池大小与任务集）。
        """
        if not self._conv_enabled:
            return False
        # 到达阶段：任务尚未全部注入，池空也不代表收敛
        if cycle <= last_arrival_cycle:
            self._conv_last_sig = None
            self._conv_streak = 0
            return False

        cur = len(self.pool)  # 含 DORMANT；UNREACHABLE / DONE 已出池
        n_dormant = len(self.pool.dormant_tasks())

        # 主判据：池空 = 不存在「能被完成却没被完成」的任务 → 自然收敛
        if cur == 0:
            self._conv_reason = "pool_empty"
            return True

        # 池里还有休眠任务 = 正在等条件恢复，忠实「一直下去」，禁用兜底
        if n_dormant > 0:
            self._conv_last_sig = None
            self._conv_streak = 0
            return False

        # 兜底：无休眠但连续 stable_patience 周期零进展 → 防死循环
        rec = self.records[-1] if self.records else None
        progressed = bool(rec) and any(
            (
                getattr(rec, "completed_ids", None),
                getattr(rec, "woken_ids", None),
                getattr(rec, "dormant_ids", None),
                getattr(rec, "unreachable_ids", None),
            )
        )
        sig = (cur, n_dormant)
        if progressed or self._conv_last_sig != sig:
            self._conv_streak = 0
        else:
            self._conv_streak += 1
        self._conv_last_sig = sig
        if self._conv_streak >= self._conv_patience:
            self._conv_reason = "no_progress"
            return True
        return False

    def run(
        self,
        n_cycles: int | None = None,
        arrivals: Mapping[int, Sequence[Task]] | None = None,
        progress_cb=None,
    ) -> list[CycleRecord]:
        """连续推进多个周期。

        Args:
            n_cycles: 硬安全上限（省略取 :class:`HorizonConfig` 的 ``n_cycles``）。
                真实停止由 :meth:`_converged` 决定：池空即自然收敛；否则持续
                推进，仅由 ``stable_patience`` 兜底防死循环。
            arrivals: ``{周期号(1-based): [Task, ...]}``，该周期开始时到达的任务。
            progress_cb: 可选回调，每个周期完成后调用 ``progress_cb(record,
                step, n_total)``（``record`` 为该周期 ``CycleRecord``）。用于向
                终端/日志实时回报进度，避免长跑看起来像卡死；传 ``None`` 则不回调。
        """
        n = int(n_cycles if n_cycles is not None else self.hcfg.n_cycles)
        arrivals = dict(arrivals or {})
        last_arrival = max(arrivals.keys()) if arrivals else 1
        for step in range(1, n + 1):
            self.run_cycle(new_tasks=arrivals.get(step))
            if progress_cb is not None:
                progress_cb(self.records[-1], step, n)
            if self._converged(step, last_arrival):
                # 停机原因落进末周期记录，供 metrics / 审计区分「自然收敛」与
                # 「零进展兜底」（后者属非自然收敛，须与 n_dormant_end 并读）。
                self.records[-1].stop_reason = self._conv_reason
                if self._conv_reason == "pool_empty":
                    why = "不存在能被完成却没被完成的任务（池空，自然收敛）"
                else:
                    why = (f"连续 {self._conv_streak} 周期零进展（池={len(self.pool)}），"
                           "stable_patience 兜底，非自然收敛")
                print(f"[horizon] 收敛于周期 {step}：{why}，停止。")
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
            # 记账 clamp：无空位硬塞时不得记负（只影响选巢偏好，防误读）
            free[target.id] = max(0, free.get(target.id, 0) - 1)

    def _inject_faults(self, cycle: int, record: CycleRecord) -> None:
        """执行中注入 UAV 故障，把其未完成任务释放回任务池。

        ``fleet.mark_fault`` 与 ``pool.release`` 两个状态机互不 import，
        由本层显式串联（论文 §6 的 T_t^release）。释放的任务记入
        ``record.released_by_fault`` 并进 ``_pending_forced``（补丁 F4），
        供后续选择周期强制纳入（不再只看上一周期记录，退化周期不丢）。
        """
        for sel in self.hcfg.faults.get(cycle, []):
            uav_id = self._resolve_fault_target(sel)
            if uav_id is None or uav_id not in self.fleet:
                continue
            stranded = self.fleet.mark_fault(uav_id)
            if not stranded:
                continue
            self.pool.release(stranded, reason="uav_fault")
            record.released_ids.extend(int(t) for t in stranded)
            record.released_by_fault.extend(int(t) for t in stranded)
            self._pending_forced.update(int(t) for t in stranded)

    def _resolve_fault_target(self, sel: int | str) -> int | None:
        """故障注入目标解析（补丁 F6）：字面 uav_id 或 "busiest"。

        "busiest" = 注入时刻绑定任务最多的未故障 UAV（平手取 id 小者），
        保证故障**有实效**（有未完成任务可释放），不靠运气选中空闲机。
        """
        if not isinstance(sel, str):
            return int(sel)
        if sel != "busiest":
            raise ValueError(
                f"未知的故障注入目标 {sel!r}：支持 uav_id（整数）或 'busiest'"
            )
        candidates = [
            u for u in self.fleet.all() if u.status is not UAVStatus.FAULT
        ]
        if not candidates:
            return None
        best = max(
            candidates, key=lambda u: (len(self.fleet.bound_tasks(u.id)), -u.id)
        )
        return int(best.id)

    def _apply_nest_closures(self, cycle: int) -> None:
        """实验时刻表注入机巢闭塞（补丁 F5）：周期起点置不可用（检测之前）。

        与 ``_inject_faults`` 对称的 E3 注入面；置位后经 ``known_unavailable``
        边沿机制在本期检测中上报一次 NEST_UNAVAILABLE。已不可用的巢不重复注入。
        """
        for nid in self.hcfg.nest_closures.get(cycle, []):
            nest = self._nest_by_id.get(int(nid))
            if nest is not None and nest.available:
                nest.available = False
                nest.meta.setdefault("close_reason", "injected")

    @staticmethod
    def _event_key(e) -> int:
        detail = e.detail or {}
        for key in ("task_id", "uav_id", "nest_id"):
            if key in detail:
                return int(detail[key])
        return -1

    def _fresh_events(self, events: Sequence) -> list:
        """事件日志边沿化（补丁 F3）：每类事件按主键只报**首次**发生。

        UAV_FAULT 是持续态、URGENT_TASK 判据是静态/单调属性，电平上报会让
        同一事件每周期重复进日志（同一故障报 N 次）、replan 计数虚高。
        NEST_UNAVAILABLE 已由 known_unavailable 边沿化，主键去重是双保险。
        决策上下文（replan_ctx）仍取电平全集，不影响强制纳入语义。
        """
        fresh = []
        for e in events:
            key = (e.type.value, self._event_key(e))
            if key in self._seen_event_keys:
                continue
            self._seen_event_keys.add(key)
            fresh.append(e)
        return fresh

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
                # 换电恢复到**该机**满电航程（异构 per-UAV，2026-11-04）
                uav.remaining_range = float(
                    uav.full_charge_range(cfg.uav_max_range) or 0.0
                )

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
        hard_ids = {int(t) for t in hard_violated}
        # 顺序组原子性（完成闸门，2026-11-04 用户裁定 3）：组内任一成员被判
        # 物理不可行 ⇒ **整组一起暂缓**，避免「组里一个弹回、其余照常完成」
        # 把调度单元拆开。连坐成员本身没有物理违约，故不计废飞
        # （infeasible_ids），单列 seq_group_rolled_back_ids 供审计。
        # 无组或本周期无硬违约时 seq_deferred 为空 ⇒ 零行为变化。
        groups = self._seq_groups()
        seq_deferred = (
            expand_to_groups(hard_ids, groups) - hard_ids
            if (groups and hard_ids) else set()
        )
        lateness_map = (solution.meta.get("feasibility", {}) or {}).get(
            "lateness", {}
        ) or {}

        for tour in solution.tours:
            if not tour.task_ids:
                continue
            bound = self.fleet.bound_tasks(tour.uav_id)
            if not bound:
                continue

            bad = [int(t) for t in bound if int(t) in hard_ids]
            group_defer = [int(t) for t in bound if int(t) in seq_deferred]
            ok = [
                int(t) for t in bound
                if int(t) not in hard_ids and int(t) not in seq_deferred
            ]

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

            # ①b 顺序组连坐暂缓（本身未违约、不计废飞）：与真违约成员一起
            #     撤下本周期交付、release 回池，下周期整组重试。
            if group_defer:
                self.fleet.unbind_tasks(tour.uav_id, group_defer)
                self.pool.release(group_defer, reason="seq_group_defer")
                record.seq_group_rolled_back_ids.extend(group_defer)

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
