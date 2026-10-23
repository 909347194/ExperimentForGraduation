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
    max F1 = sum_j R_j y_j
    min F2 = C(x, π, z, y)   （路径代价 + 惩罚）
    min F3 = max_k T_k        （最坏机航迹时间，论文 §5(3) 负载均衡/公平性项）

适应度（DMDE 最小化标量）：
    四层准字典序（方案 A/B 主线；非门禁口径）：
        f(s) = W_hard·n_hard + w_exc·excess_m        # T0 物理违约（硬，真 big-M）
              − big_m·F1_eff                          # T1 交付收益（含迟到折减）
              + w_late·L_tot                          # T2 迟到（软，秒级）
              + λ·path + w_time·F3                    # T3 航程/负载均衡
      T0：W_hard = κ·big_m·R_pool —— 任何一次物理违约压倒任意交付收益
          （幽灵 F1 永不入选：纸面可行、执行弹回的航次不计功）；
      T1：big_m 主导 —— 任一 ≥ 最小正收益的收益优势支配任意次级差异
          （先最大化任务收益，再在收益等价的解中最小化成本）；
      T2：迟到秒数软计价 —— 迟到可接受，只与路径同台竞争；
    legacy（lexicographic=False，消融对比）：f(s) = -F1 + λ·path + w_v·penalty
    ；最老口径（w_violation=None）：惩罚随 F2 一起乘 λ。
其中 F3 = max_k T_k 为各 UAV 航迹时间的最大值（瓶颈机），对应 Ming et al. 2017
Eq.1 中的 a·max(Σ_i Σ_j t(i,j)·x(i,j)) 最坏机飞行时间项。w_time=0 时退化为
原双目标形式，行为与重构前完全一致。

覆盖门禁口径（2026-10-22 起的主口径）：T0 与 T1 之间加
W_skip·n_voluntary_skip 层，且 **搜索用 f 不含 −m·F1，F1 只作事后指标**
——完整公式与固定表述见 :meth:`SelectiveMUASProblem.scalar_fitness`
与 :meth:`SelectiveMUASProblem.gate_mode`。

模块地图（包内分工；各模块符号仍可从本模块导入，既有路径不变）：
    config.py            约束开关与评价权重（ConstraintConfig / MUASProblemConfig）
    cost/provider.py     节点间代价访问（CostProvider / build_cost_provider）
    feasibility.py       可行性校验与惩罚聚合编排（check_solution_feasibility）
    fitness_weights.py   层权重推导与分层统计（FitnessWeightsMixin）
    problem.py（本模块）  问题实例：实体 / 解构造 / 目标分量 / 评价与标量适应度
    constraints/         单条约束判据与违约聚合（ConstraintReport）
    coverage_gate.py     覆盖门禁审计与构造性修复

本模块不实现 DMDE 搜索；求解器只负责搜索，本模块负责问题定义与解评价。
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
from task_allocation.methods.muas.config import ConstraintConfig, MUASProblemConfig
from task_allocation.methods.muas.cost.provider import CostProvider, build_cost_provider
from task_allocation.methods.muas.coverage_gate import audit_coverage
from task_allocation.methods.muas.feasibility import check_solution_feasibility
from task_allocation.methods.muas.fitness_weights import FitnessWeightsMixin

# 拆分后保持既有导入路径不变：config / cost.provider / feasibility /
# fitness_weights 各符号仍可经本模块导入（新家见各模块 docstring）。
__all__ = [
    "ConstraintConfig",
    "MUASProblemConfig",
    "CostProvider",
    "build_cost_provider",
    "SelectiveMUASProblem",
    "FitnessWeightsMixin",
    "check_solution_feasibility",
    "FitnessEvaluator",
    "make_fitness_evaluator",
]


@dataclass
class SelectiveMUASProblem(FitnessWeightsMixin):
    """选择性多机巢 MUAS 问题实例（单滚动周期）。

    层权重推导与门禁判据（``hard_weight`` / ``gate_mode`` / …）由
    :class:`~task_allocation.methods.muas.fitness_weights.FitnessWeightsMixin`
    混入提供；可行性校验委托
    :func:`~task_allocation.methods.muas.feasibility.check_solution_feasibility`。
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

    def f1_reward(self, solution: AllocationSolution) -> float:
        # 有效奖励 = Σ R_j × 折减系数；折减系数按每任务迟到秒数线性衰减
        # （late_reward_decay_seconds=W 时 max(0, 1 − L/W)）。迟到的量取自
        # check_feasible 透出的 lateness 表（时间窗游标的唯一模拟源）。
        total = 0.0
        # 覆盖门禁模式（D5）：F1 回归纯完成计数——迟到折减砍掉，迟到只走
        # w_late·L 单一定价（T2），消「迟到双计」。门禁模式下 F1 只作指标，
        # 故该口径同时是「指标 F1 = 完成计数、不掺迟到折减」的固定定义。
        decay = self.config.late_reward_decay_seconds
        if self.gate_mode(solution):
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

    def evaluate(
        self,
        solution: AllocationSolution,
        penalty: float | None = None,
    ) -> AllocationSolution:
        ok, details = self.check_feasible(solution)
        pen = float(details.get("penalty", 0.0)) if penalty is None else float(penalty)
        solution.feasible = ok
        solution.meta = dict(solution.meta)
        solution.meta["feasibility"] = details
        solution.f1_reward = self.f1_reward(solution)
        solution.f2_cost = self.f2_cost(solution, penalty=pen)
        # 实际计入 F2 的惩罚量（供 w_violation 解耦口径读取，避免重复解析报告）
        solution.meta["penalty"] = pen
        solution.meta["bottleneck_time"] = self.bottleneck_time(solution)
        # 2026-10-18：本次评价实际使用的四层权重快照。记录/重算/绘图共用
        # 同一组权重，消灭 auto big_m 逐周期漂移导致的「记录 best_fitness
        # 与重算目标差 6.5~13.6」。
        gate = self.gate_mode(solution)
        decay = self.config.late_reward_decay_seconds
        if gate:
            decay = None  # 门禁模式 F1 不用迟到折减，快照口径与实际一致
        # 覆盖门禁分账（W_skip 层消费）：解码期已算过（gate_stats 在
        # build_solution 写入 meta）则复用；外源解（测试 /
        # solution_from_sequences）在这里惰性补算。
        if gate and "gate_stats" not in solution.meta:
            routes = {t.uav_id: list(t.task_ids) for t in solution.tours}
            ends = {t.uav_id: t.end_nest_id for t in solution.tours}
            solution.meta["gate_stats"] = audit_coverage(self, routes, ends).to_dict()
        solution.meta["fitness_weights"] = {
            "m": float(self.f1_weight()),
            "w_hard": float(self.hard_weight()),
            "w_excess": float(self.range_excess_weight()),
            "w_skip": (float(self.skip_weight()) if gate else None),
            "w_late": (None if self.config.w_violation is None
                       else float(self.config.w_violation)),
            "lambda": float(self.config.lambda_cost),
            "w_time": float(self.config.w_time),
            "W_decay": (None if not decay else float(decay)),
        }
        for tour in solution.tours:
            tour.est_cost = self.tour_path_cost(tour)
            tour.est_reward = sum(
                self.task_by_id[tid].reward
                for tid in tour.task_ids
                if tid in self.task_by_id
            )
        return solution

    def scalar_fitness(self, solution: AllocationSolution) -> float:
        """DMDE 最小化的标量适应度。

        四层准字典序（2026-10-18，``lexicographic=True`` 且 ``w_violation``
        给定时的生产口径）::

            f = W_hard·n_hard + w_exc·excess_m       # T0 物理违约（硬）
              − m·F1_eff                              # T1 交付收益
              + w_late·L_tot                          # T2 迟到（软，秒级）
              + λ·path + w_time·F3                    # T3 航程/负载均衡

        - **T0**：``W_hard = κ·m·R_pool``（默认自动推导）使任何一次物理违约
          压倒任意交付收益——幽灵 F1（纸面可行、执行弹回）永不入选；
          ``excess_m``（超限米数）仅在不可行解之间做层内排序。
        - **T1**：``F1_eff`` 含迟到折减（``late_reward_decay_seconds``）；
          ``m`` 保证「多交付 ≥1 个任务」支配一切次级差异。
        - **T2**：迟到秒数软计价（``w_violation``，0.01/s）——迟到可接受，
          只与路径同台竞争；迟到与任务数的权衡由 T1 的折减管。
        - **T3**：``λ·path + w_time·F3``（收益等价时的最小化目标）。

        legacy 加权和（``lexicographic=False``）：``f = -F1 + λ·path +
        w_violation·penalty + w_time·F3``（惩罚不再乘 λ 的解耦口径）。
        最老口径（``w_violation=None``）：惩罚随 F2 一起乘 λ。

        **覆盖门禁口径（2026-10-22 起的主口径，见 :meth:`gate_mode`）**::

            f = W_hard·n_hard + w_exc·excess_m       # T0  物理硬约束
              + W_skip·n_voluntary_skip              # T0.5 覆盖门禁
              + w_late·L_tot                         # T2  迟到（秒级软计价）
              + λ·path + w_time·F3                   # T3  航程/负载均衡

        即 **搜索用 f 不含 −m·F1；F1 只作指标**：合法解中 S 内可装载任务
        已全部交付 ⇒ F1 恒为常数、对解无区分度；保留 −m·F1 只会让 m 机制
        继续主导阶梯并把成本项挤成 tie-break（历史上 λ·path 仅占 m·F1 的
        1/5.7e5）。覆盖由门禁承担（构造修复 + W_skip 层），目标层只剩
        成本 + 迟到。``m`` 仍落 fitness_weights 快照，仅供 w_hard/w_skip
        推导与事后分析，不直接进 f。
        """
        if not solution.meta.get("feasibility"):
            self.evaluate(solution)
        bt = solution.meta.get("bottleneck_time", self.bottleneck_time(solution))
        m = self.f1_weight()
        f1 = float(solution.f1_reward)
        wv = self.config.w_violation
        if wv is None:
            # 历史口径：惩罚随 F2 一起乘 λ（量纲未解耦，秒级惩罚被压缩）
            return float(
                -m * f1
                + self.config.lambda_cost * solution.f2_cost
                + self.config.w_time * bt
            )
        if not self.config.lexicographic:
            # legacy 加权和 + 惩罚量纲解耦（消融对比口径，不含 T0 支配层）
            return float(
                -m * f1
                + self.config.lambda_cost * self.path_cost(solution)
                + float(wv) * self.violation_penalty(solution)
                + self.config.w_time * bt
            )
        # 四层准字典序（生产口径）；覆盖门禁口径（见 gate_mode，2026-10-22
        # 起的主口径）在 T0 与 T1 之间加 T0.5 层 W_skip·n_voluntary_skip——
        # voluntary_skip 与物理硬约束等价的非法解，由解码器 repair 至不动点
        # 构造性归零（gate 模式），W_skip 仅在 audit 模式/防御路径咬合。
        n_hard, excess_m = self.hard_stats(solution)
        gate = self.gate_mode(solution)
        skip_pen = 0.0
        if gate:
            n_skip = int((solution.meta.get("gate_stats") or {}).get("n_skip", 0))
            skip_pen = self.skip_weight() * n_skip
        # F1 项：门禁口径置零——**搜索用 f 不含 −m·F1，F1 只作事后指标**；
        # 历史/消融口径（非门禁）保持 −m·F1 逐位不变。
        f1_term = 0.0 if gate else -m * f1
        return float(
            self.hard_weight() * n_hard
            + self.range_excess_weight() * excess_m
            + skip_pen
            + f1_term
            + float(wv) * self.soft_violation(solution)
            + self.config.lambda_cost * self.path_cost(solution)
            + self.config.w_time * bt
        )

    # ── 层权重推导与分层统计（big-M / T0 / W_skip）────────
    # 实现见 fitness_weights.FitnessWeightsMixin（本类混入之）：
    #   gate_mode / hard_weight / skip_weight / range_excess_weight /
    #   hard_stats / soft_violation / f1_weight / _auto_big_m /
    #   _secondary_upper_bound / _max_edge_cost

    def check_feasible(
        self, solution: AllocationSolution
    ) -> tuple[bool, dict[str, Any]]:
        """校验解的可行性并累加约束惩罚（论文 §5(4) 约束处理）。

        编排实现在 :mod:`task_allocation.methods.muas.feasibility`（单条约束判据在
        ``constraints`` 包，目标函数在 ``problem.py``）。透出的
        ``violations`` / ``penalty`` / ``nest_capacity`` 与历史口径完全一致
        （供滚动层 horizon 与指标 metrics 消费）。
        """
        return check_solution_feasibility(self, solution)

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


FitnessEvaluator = Callable[[AllocationSolution], float]


def make_fitness_evaluator(problem: SelectiveMUASProblem) -> FitnessEvaluator:
    def _eval(solution: AllocationSolution) -> float:
        problem.evaluate(solution)
        return problem.scalar_fitness(solution)

    return _eval
