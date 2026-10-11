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
    f(s) = -F1 + λ·F2 + w_time·F3
其中 F3 = max_k T_k 为各 UAV 航迹时间的最大值（瓶颈机），对应 Ming et al. 2017
Eq.1 中的 a·max(Σ_i Σ_j t(i,j)·x(i,j)) 最坏机飞行时间项。w_time=0 时退化为
原双目标形式，行为与重构前完全一致。

本模块不实现 DMDE 搜索；求解器只负责搜索，本模块负责问题定义与解评价。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
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


@dataclass
class ConstraintConfig:
    """约束开关与惩罚权重（实验可配置层，对齐论文 §5(4) 约束处理）。

    设计要点：每类约束**独立开关**。置 ``False`` 时该约束既不参与可行性
    判定、也不计入惩罚，用于消融对比与论文口径取舍（例如「本模型聚焦
    容量—航程—机巢选择，交付时间窗暂不纳入」这类声明，只需改 yaml 一个
    布尔值，不必动代码）。

    分组：

        结构类      duplicate_task / require_all_tasks / require_no_idle
        单机安全    range（航程）/ endurance（飞行时间，续航）
        协同        time_window（交付时间窗）/ sequence（时序）/ sync（同时到达）
        机巢        nest_capacity（容量）/ nest_availability（可用性）
                    end_nest_reachability（末任务 → 机巢可达性）

    权重与开关一一对应（``w_*``），仅对**已启用**的约束生效。
    """

    # ── 结构类 ──────────────────────────────────────────────────
    duplicate_task: bool = True  # 任务重复分配 / 未知任务 / sum_u x_uj = y_j
    require_all_tasks: bool = True  # allow_unassigned=False 时禁止 y_j=0
    require_no_idle: bool = True  # allow_idle_uav=False 时禁止空闲机

    # ── 单机（飞行安全类）────────────────────────────────────────
    range: bool = True  # 航程
    endurance: bool = True  # 飞行时间 / 续航

    # ── 协同 ────────────────────────────────────────────────────
    time_window: bool = True  # 交付时间窗
    sequence: bool = True  # 时序 / 前置依赖
    sync: bool = True  # 同时到达

    # ── 机巢 ────────────────────────────────────────────────────
    nest_capacity: bool = True  # 容量
    nest_availability: bool = True  # 可用性 / 存在性
    end_nest_reachability: bool = True  # 末任务 → 终点机巢可达性

    # ── 惩罚权重（仅对启用的约束生效）────────────────────────────
    w_misc: float = 1.0
    w_range: float = 1.0
    w_endurance: float = 1.0
    w_window: float = 1.0
    w_seq: float = 1.0
    w_sync: float = 1.0
    w_nest_capacity: float = 1.0
    w_nest_availability: float = 1.0
    w_end_nest_reachability: float = 1.0

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "ConstraintConfig":
        """从 yaml 映射装配；未给出的项保持默认（缺省 = 全部启用、权重 1）。"""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(d or {}).items() if k in known})


@dataclass
class MUASProblemConfig:
    """模型开关与评价权重。"""

    allow_unassigned: bool = True
    allow_idle_uav: bool = True
    # 最坏机航迹时间项权重（论文 §5(3) a·max(Σt)）：最小化瓶颈机任务时间，
    # 实现负载均衡/公平性。默认 0（仅双目标）；>0 时接入 F3=max_k T_k。
    w_time: float = 0.0
    w_penalty: float = 1.0
    # 约束惩罚直接进入适应度的权重（单位：奖励当量 / 惩罚单位）。
    # 历史口径下惩罚与路径代价混进同一个 F2 再乘 λ（λ≈1e-4 是为把「米」级
    # 路径代价折成奖励当量而设），同处 F2 的「秒」级惩罚因此被一并压没，
    # 软约束形同虚设。给定本值时惩罚**不再乘 λ**，量纲得以解耦：
    # 取 0.01~0.02 意味着「迟到 300 s ≈ 3~6 点奖励」，足以抵消单任务收益。
    # None = 沿用历史口径 λ·w_penalty（保持旧行为与既有测试不变）。
    w_violation: float | None = None
    # 约束开关与各类惩罚权重（实验可配置）；判定见 constraints.*
    constraints: ConstraintConfig = field(default_factory=ConstraintConfig)
    # 机巢容量惩罚权重（论文 §4.3）。历史字段，保留作缺省来源：
    # 若未显式给出 constraints.w_nest_capacity，则取其值。
    w_nest_capacity: float = 1.0
    lambda_cost: float = 1.0
    default_service_time: float = 0.0
    # 本周期的规划时刻：时间窗游标从它起算，而不是从 0
    # （滚动时域下任务 latest 是绝对时刻，游标起点必须跟着周期走）
    current_time: float = 0.0


def _euclid(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    return float(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
    )


@dataclass
class CostProvider:
    """节点间代价：pairwise 优先，否则欧氏；机巢键 = nest_offset + nest_id。"""

    pairwise: dict[tuple[int, int], float] = field(default_factory=dict)
    positions: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    nest_offset: int = 1_000_000

    def nest_key(self, nest_id: int) -> int:
        return self.nest_offset + nest_id

    def cost(self, id_a: int, id_b: int) -> float:
        if (id_a, id_b) in self.pairwise:
            return float(self.pairwise[(id_a, id_b)])
        if (id_b, id_a) in self.pairwise:
            return float(self.pairwise[(id_b, id_a)])
        pa = self.positions.get(id_a)
        pb = self.positions.get(id_b)
        if pa is not None and pb is not None:
            return _euclid(pa, pb)
        return 1e6


def build_cost_provider(
    tasks: Sequence[Task],
    nests: Sequence[Nest],
    uavs: Sequence[UAV],
    pairwise: Mapping[tuple[int, int], float] | None = None,
    nest_offset: int = 1_000_000,
) -> CostProvider:
    positions: dict[int, tuple[float, float, float]] = {}
    for t in tasks:
        positions[t.id] = t.position
    for n in nests:
        positions[nest_offset + n.id] = n.position
    for u in uavs:
        positions[-(1 + u.id)] = u.position
    return CostProvider(
        pairwise=dict(pairwise or {}),
        positions=positions,
        nest_offset=nest_offset,
    )


@dataclass
class SelectiveMUASProblem:
    """选择性多机巢 MUAS 问题实例（单滚动周期）。"""

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
        n, m = self.n_uavs, self.n_tasks
        if m == 0:
            return "empty"
        if n == m and not self.config.allow_unassigned:
            return "balanced"
        if n > m:
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
        total = 0.0
        for tid in solution.executed_task_ids:
            t = self.task_by_id.get(tid)
            if t is not None:
                total += max(t.reward, 0.0)
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
        """解当前的约束惩罚总量（单位取决于各约束：s / 单位）。

        优先取 :meth:`evaluate` 实际计入 ``f2_cost`` 的惩罚（``meta["penalty"]``），
        否则回落到可行性报告里的惩罚值。
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
            dist = _euclid(prev, task.position)
            t_cursor += dist / speed
            if task.earliest is not None and t_cursor < task.earliest:
                t_cursor = task.earliest
            t_cursor += float(self.config.default_service_time)
            prev = task.position
        if tour.end_nest_id is not None:
            nest = self.nest_by_id.get(tour.end_nest_id)
            if nest is not None:
                dist = _euclid(prev, nest.position)
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
        for tour in solution.tours:
            tour.est_cost = self.tour_path_cost(tour)
            tour.est_reward = sum(
                self.task_by_id[tid].reward
                for tid in tour.task_ids
                if tid in self.task_by_id
            )
        return solution

    def scalar_fitness(self, solution: AllocationSolution) -> float:
        """DMDE 最小化的标量适应度：f = -F1 + λ·F2 + w_time·F3。

        - F1 任务奖励（最大化 → 取负）
        - F2 路径代价 + 约束惩罚（最小化）
        - F3=max_k T_k 最坏机航迹时间（最小化，负载均衡；w_time=0 时关闭）
        """
        if not solution.meta.get("feasibility"):
            self.evaluate(solution)
        bt = solution.meta.get("bottleneck_time", self.bottleneck_time(solution))
        wv = self.config.w_violation
        if wv is None:
            # 历史口径：惩罚随 F2 一起乘 λ（量纲未解耦，秒级惩罚被压缩）
            return float(
                -solution.f1_reward
                + self.config.lambda_cost * solution.f2_cost
                + self.config.w_time * bt
            )
        # 量纲解耦：路径代价仍乘 λ（米 → 奖励当量），惩罚按 w_violation
        # 直接以「奖励当量 / 惩罚单位」计入，不再被 λ 压没。
        return float(
            -solution.f1_reward
            + self.config.lambda_cost * self.path_cost(solution)
            + float(wv) * self.violation_penalty(solution)
            + self.config.w_time * bt
        )

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
                    report.add_range(
                        f"range_uav_{tour.uav_id}", cons.w_range * rv
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
            w_pen, w_tags = check_time_window_constraint(
                solution.tours,
                self.task_by_id,
                self.uav_by_id,
                current_time=float(self.config.current_time),
                service_time=float(self.config.default_service_time),
            )
            if w_pen > 0:
                report.add_window(w_tags, cons.w_window * w_pen)
                tw_ids = {int(t.split("_")[-1]) for t in w_tags}
                for tour in solution.tours:
                    if any(tid in tw_ids for tid in tour.task_ids):
                        tour.feasible = False
        if cons.sequence:
            s_pen, s_tags = check_sequence_constraint(solution.tours, self.task_by_id)
            if s_pen > 0:
                report.add_seq(s_tags, cons.w_seq * s_pen)
        if cons.sync:
            sy_pen, sy_tags = check_sync_constraint(solution.tours, self.task_by_id)
            if sy_pen > 0:
                report.add_sync(sy_tags, cons.w_sync * sy_pen)

        return report.is_feasible, report.as_details()

    @classmethod
    def from_entities(
        cls,
        tasks: Sequence[Task],
        uavs: Sequence[UAV],
        nests: Sequence[Nest] | None = None,
        pairwise_cost: Mapping[tuple[int, int], float] | None = None,
        config: MUASProblemConfig | None = None,
        fleet_uavs: Sequence[UAV] | None = None,
    ) -> SelectiveMUASProblem:
        """组装问题实例。

        Args:
            fleet_uavs: 全部无人机（含非空闲），用于推导机巢占用；
                        省略时退化为 ``uavs`` 本身。
        """
        avail = [u for u in uavs if u.is_available]
        nest_list = list(nests or [])
        costs = build_cost_provider(tasks, nest_list, avail, pairwise=pairwise_cost)
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
