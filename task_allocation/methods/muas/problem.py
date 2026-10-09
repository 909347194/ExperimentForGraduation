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


@dataclass
class MUASProblemConfig:
    """模型开关与评价权重。"""

    allow_unassigned: bool = True
    allow_idle_uav: bool = True
    w_time: float = 0.0
    w_penalty: float = 1.0
    lambda_cost: float = 1.0
    default_service_time: float = 0.0


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

    def f2_cost(self, solution: AllocationSolution, penalty: float = 0.0) -> float:
        path = sum(self.tour_path_cost(t) for t in solution.tours)
        return float(path + self.config.w_penalty * penalty)

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
        for tour in solution.tours:
            tour.est_cost = self.tour_path_cost(tour)
            tour.est_reward = sum(
                self.task_by_id[tid].reward
                for tid in tour.task_ids
                if tid in self.task_by_id
            )
        return solution

    def scalar_fitness(self, solution: AllocationSolution) -> float:
        if not solution.meta.get("feasibility"):
            self.evaluate(solution)
        return float(-solution.f1_reward + self.config.lambda_cost * solution.f2_cost)

    def check_feasible(
        self, solution: AllocationSolution
    ) -> tuple[bool, dict[str, Any]]:
        details: dict[str, Any] = {"violations": [], "penalty": 0.0}
        penalty = 0.0

        if not solution.validate_unique_tasks():
            details["violations"].append("duplicate_task_assignment")
            penalty += 1e3

        rel = self.relations_from_solution(solution)
        if not rel["sum_x_eq_y"]:
            details["violations"].append("sum_x_neq_y")
            penalty += 1e3

        cand = set(solution.candidate_task_ids) or set(self.task_by_id)
        for tid in solution.executed_task_ids:
            if tid not in cand and tid not in self.task_by_id:
                details["violations"].append(f"unknown_task_{tid}")
                penalty += 1e3

        if not self.config.allow_unassigned:
            missing = [
                t.id for t in self.tasks if t.id not in set(solution.executed_task_ids)
            ]
            if missing:
                details["violations"].append("unassigned_not_allowed")
                penalty += 100.0 * len(missing)

        if not self.config.allow_idle_uav:
            for tour in solution.tours:
                if tour.is_idle:
                    details["violations"].append(f"idle_uav_{tour.uav_id}")
                    penalty += 100.0

        for tour in solution.tours:
            u = self.uav_by_id.get(tour.uav_id)
            if u is None:
                details["violations"].append(f"unknown_uav_{tour.uav_id}")
                penalty += 1e3
                continue
            pc = self.tour_path_cost(tour)
            if pc > u.remaining_range + 1e-6:
                details["violations"].append(f"range_uav_{tour.uav_id}")
                penalty += pc - u.remaining_range
                tour.feasible = False
            else:
                tour.feasible = True

        for tour in solution.tours:
            if tour.end_nest_id is None:
                continue
            nest = self.nest_by_id.get(tour.end_nest_id)
            if nest is None:
                details["violations"].append(f"bad_nest_{tour.end_nest_id}")
                penalty += 1e3
            elif not nest.available:
                details["violations"].append(f"nest_unavailable_{tour.end_nest_id}")
                penalty += 100.0

        for tour in solution.tours:
            u = self.uav_by_id.get(tour.uav_id)
            if u is None or not tour.task_ids:
                continue
            t_cursor = 0.0
            prev_pos = tour.start_position
            speed = max(u.speed, 1e-6)
            for tid in tour.task_ids:
                task = self.task_by_id.get(tid)
                if task is None:
                    continue
                dist = _euclid(prev_pos, task.position)
                t_cursor += dist / speed
                if task.earliest is not None and t_cursor < task.earliest:
                    t_cursor = task.earliest
                if task.latest is not None and t_cursor > task.latest + 1e-6:
                    details["violations"].append(f"tw_task_{tid}")
                    penalty += t_cursor - task.latest
                    tour.feasible = False
                t_cursor += self.config.default_service_time
                prev_pos = task.position

        details["penalty"] = float(penalty)
        return len(details["violations"]) == 0, details

    @classmethod
    def from_entities(
        cls,
        tasks: Sequence[Task],
        uavs: Sequence[UAV],
        nests: Sequence[Nest] | None = None,
        pairwise_cost: Mapping[tuple[int, int], float] | None = None,
        config: MUASProblemConfig | None = None,
    ) -> SelectiveMUASProblem:
        avail = [u for u in uavs if u.is_available]
        nest_list = list(nests or [])
        costs = build_cost_provider(tasks, nest_list, avail, pairwise=pairwise_cost)
        return cls(
            tasks=list(tasks),
            uavs=avail,
            nests=nest_list,
            costs=costs,
            config=config or MUASProblemConfig(),
        )


FitnessEvaluator = Callable[[AllocationSolution], float]


def make_fitness_evaluator(problem: SelectiveMUASProblem) -> FitnessEvaluator:
    def _eval(solution: AllocationSolution) -> float:
        problem.evaluate(solution)
        return problem.scalar_fitness(solution)

    return _eval
