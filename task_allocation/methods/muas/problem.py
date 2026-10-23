# -*- coding: utf-8 -*-
"""problem.py — 选择性多机巢 MUAS 模型（论文 3.3）。

职责（回答）：
    任务选择、无人机分配、访问顺序、终点机巢之间是什么关系？目标是什么？

决策变量：
    y_j     任务 j 本周期是否执行
    x_uj    任务 j 是否分配给 UAV u
    π_u     UAV u 的访问顺序
    z_ub    UAV u 的终点机巢

关系：
    sum_u x_uj = y_j
    π_u / z_ub 编码在 UAVTour 中
    允许 y_j=0（选择性）、n_u=0（空闲 UAV）

目标：
    max F1 = sum_j R_j y_j   （收益指标；搜索第一键为交付个数，见下）
    min F2 = C(x, π, z, y)   （路径代价 + 惩罚）
    min F3 = max_k T_k       （最坏机航迹时间，论文 §5(3) 负载均衡/公平性项）

适应度（DMDE 最小化标量）：
    覆盖个数第一键、成本第二键（2026-10-23 起默认）：
        f(s) = W_hard·n_hard + w_exc·excess_m        # T0 物理违约（硬，真 big-M）
             + W_skip·n_voluntary_skip               # T0.5 覆盖门禁（gate 模式）
             + W_cov·(N − n_delivered)               # T1 覆盖缺口（个数，第一比较键）
             + w_late·L_tot + λ·path + w_time·F3     # T2/T3 成本（第二比较键）
      T0：W_hard = κ·m·R_pool —— 任何一次物理违约压倒任意覆盖/成本优势
          （幽灵 F1 永不入选：纸面可行、执行弹回的航次不计功）；
      T1：覆盖缺口按**个数**计 —— 「少交付 1 个」劣于任意航程/迟到差异
          （W_cov 是门槛权重 = 1 + 次目标上界，不是 m·R≈2.7e5 那种巨额
          完成奖励）；交付个数相同的解之间成本项以全分辨率裁决；
          F1=ΣR_j 退为指标，不进搜索主阶梯；
      T2/T3：迟到软计价（w_late·L）与航程/负载均衡同层竞争。
    消融（coverage_count_first=False，2026-10-18 四层准字典序）：
        T1 回到收益和 big-M：− big_m·F1_eff（含迟到折减）。
    legacy（lexicographic=False，消融对比）：f(s) = -F1 + λ·path + w_v·penalty
    ；最老口径（w_violation=None）：惩罚随 F2 一起乘 λ。
其中 F3 = max_k T_k 为各 UAV 航迹时间的最大值（瓶颈机），对应 Ming et al. 2017
Eq.1 中的 a·max(Σ_i Σ_j t(i,j)·x(i,j)) 最坏机飞行时间项。w_time=0 时退化为
原双目标形式，行为与重构前完全一致。

本模块不实现 DMDE 搜索；求解器只负责搜索，本模块负责问题定义与解评价。

模块结构（2026-10-23 拆分，本文件为兼容 facade）：
    problem_config.py    ConstraintConfig / MUASProblemConfig
    cost_provider.py     CostProvider / build_cost_provider
    solution_metrics.py  SolutionMetricsMixin（F1/F2/F3、航次代价）
    feasibility.py       FeasibilityMixin（check_feasible）
    fitness.py           FitnessMixin（evaluate / scalar_fitness / 权重）
以下再导出保持历史 import 路径（from ...muas.problem import X）不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
from task_allocation.methods.muas.problem_config import (
    ConstraintConfig,
    MUASProblemConfig,
)
from task_allocation.methods.muas.cost_provider import (
    CostProvider,
    build_cost_provider,
)
from task_allocation.methods.muas.solution_metrics import SolutionMetricsMixin
from task_allocation.methods.muas.fitness import (
    FitnessEvaluator,
    FitnessMixin,
    make_fitness_evaluator,
)
from task_allocation.methods.muas.feasibility import FeasibilityMixin


@dataclass
class SelectiveMUASProblem(
    SolutionMetricsMixin, FitnessMixin, FeasibilityMixin
):
    """选择性多机巢 MUAS 问题实例（单滚动周期）。

    组合式结构（2026-10-23 拆分）：
        problem_config.py    配置（约束开关 + 权重）
        cost_provider.py     代价与统一距离口径
        solution_metrics.py  指标（F1/F2/F3、航次代价）
        feasibility.py       可行性校验（check_feasible）
        fitness.py           适应度标量化（scalar_fitness + 权重）
    本模块 = 问题实例 + 解构造（relations/empty/solution_from_assignment/
    from_entities）+ 兼容再导出（历史 import 路径不变）。
    """

    tasks: list[Task]
    uavs: list[UAV]
    nests: list[Nest]
    costs: CostProvider
    # 全部无人机（含 BUSY / FAULT），仅用于推导机巢占用；
    # 容量不落第二份状态，一律从 uav.nest_id 现算（design_nest_state.md §0）
    fleet_uavs: list[UAV] | None = None
    config: MUASProblemConfig = field(default_factory=MUASProblemConfig)
    task_by_id: dict[int, Task] = field(init=False, repr=False)
    uav_by_id: dict[int, UAV] = field(init=False, repr=False)
    nest_by_id: dict[int, Nest] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.task_by_id = {t.id: t for t in self.tasks}
        self.uav_by_id = {u.id: u for u in self.uavs}
        self.nest_by_id = {n.id: n for n in self.nests}

    @property
    def n_tasks(self) -> int:
        return len(self.tasks)

    @property
    def n_uavs(self) -> int:
        return len(self.uavs)

    @property
    def n_nests(self) -> int:
        return len(self.nests)

    @property
    def model_mode(self) -> str:
        # 符号口径：K = UAV 数（实例机数），N = 任务数。
        n_uavs, n_tasks = self.n_uavs, self.n_tasks
        if n_tasks == 0:
            return "empty"
        if n_uavs == n_tasks and not self.config.allow_unassigned:
            return "balanced"
        if n_uavs > n_tasks:
            return "overloaded"
        return "selective_srp"

    def relations_from_solution(
        self, solution: AllocationSolution
    ) -> dict[str, Any]:
        """解码 y, x, π, z，并检查 sum_u x_uj == y_j。"""
        y = {tid: 0 for tid in self.task_by_id}
        for tid in solution.executed_task_ids:
            if tid in y:
                y[tid] = 1
        x: dict[tuple[int, int], int] = {}
        pi: dict[int, list[int]] = {}
        z: dict[int, int | None] = {}
        for tour in solution.tours:
            pi[tour.uav_id] = list(tour.task_ids)
            z[tour.uav_id] = tour.end_nest_id
            for tid in tour.task_ids:
                x[(tour.uav_id, tid)] = 1
        relation_ok = True
        for tid, yj in y.items():
            s = sum(x.get((u.id, tid), 0) for u in self.uavs)
            if s != yj:
                relation_ok = False
                break
        return {
            "y": y,
            "x": x,
            "pi": pi,
            "z": z,
            "sum_x_eq_y": relation_ok,
            "unique_tasks": solution.validate_unique_tasks(),
        }

    def empty_solution(self) -> AllocationSolution:
        tours = [
            UAVTour.from_uav(u, task_ids=[], end_nest_id=u.nest_id) for u in self.uavs
        ]
        return AllocationSolution(
            tours=tours,
            candidate_task_ids=[t.id for t in self.tasks],
            executed_task_ids=[],
            f1_reward=0.0,
            f2_cost=0.0,
            feasible=True,
        )

    def solution_from_assignment(
        self,
        assignment: Sequence[tuple[int, int]],
        sequences: Mapping[int, Sequence[int]] | None = None,
        end_nests: Mapping[int, int | None] | None = None,
    ) -> AllocationSolution:
        seq_map: dict[int, list[int]] = {u.id: [] for u in self.uavs}
        if sequences:
            for uid, seq in sequences.items():
                seq_map[int(uid)] = [int(t) for t in seq]
        else:
            for uid, tid in assignment:
                if uid in seq_map and tid in self.task_by_id:
                    seq_map[uid].append(int(tid))
        end_nests = dict(end_nests or {})
        tours: list[UAVTour] = []
        executed: list[int] = []
        for u in self.uavs:
            tids = seq_map.get(u.id, [])
            seen: set[int] = set()
            ordered: list[int] = []
            for tid in tids:
                if tid not in seen and tid in self.task_by_id:
                    seen.add(tid)
                    ordered.append(tid)
            executed.extend(ordered)
            tours.append(
                UAVTour.from_uav(
                    u, task_ids=ordered, end_nest_id=end_nests.get(u.id, u.nest_id)
                )
            )
        sol = AllocationSolution(
            tours=tours,
            candidate_task_ids=[t.id for t in self.tasks],
            executed_task_ids=list(dict.fromkeys(executed)),
        )
        self.evaluate(sol)
        return sol

    @classmethod
    def from_entities(
        cls,
        tasks: Sequence[Task],
        uavs: Sequence[UAV],
        nests: Sequence[Nest] | None = None,
        pairwise_cost: Mapping[tuple[int, int], float] | None = None,
        config: MUASProblemConfig | None = None,
        fleet_uavs: Sequence[UAV] | None = None,
        distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
    ) -> SelectiveMUASProblem:
        """组装问题实例。

        Args:
            fleet_uavs: 全部无人机（含非空闲），用于推导机巢占用；
                        省略时退化为 ``uavs`` 本身。
            distance_fn: 统一距离口径（DEM 三维航程）。由滚动层从代价估算器
                生成并注入；省略 → 评价端 ETA 退回欧氏直线（仅无 DEM 场景）。
        """
        avail = [u for u in uavs if u.is_available]
        nest_list = list(nests or [])
        costs = build_cost_provider(
            tasks, nest_list, avail, pairwise=pairwise_cost, distance_fn=distance_fn
        )
        return cls(
            tasks=list(tasks),
            uavs=avail,
            nests=nest_list,
            costs=costs,
            fleet_uavs=list(fleet_uavs) if fleet_uavs is not None else list(uavs),
            config=config or MUASProblemConfig(),
        )


# ── 兼容再导出（历史 import 路径不变）────────────────────────────
__all__ = [
    "ConstraintConfig",
    "MUASProblemConfig",
    "CostProvider",
    "build_cost_provider",
    "SelectiveMUASProblem",
    "FitnessEvaluator",
    "make_fitness_evaluator",
]
