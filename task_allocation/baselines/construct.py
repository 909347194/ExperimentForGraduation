# -*- coding: utf-8 -*-
"""construct.py — 构造式基线（random / greedy / vrptw）的公共构件。

只放被多个构造式求解器**共享**的口径与工具，不放求解策略本身：

    - 航段代价：``problem.costs.cost(键A, 键B)``（UAV 起点键 ``-(1+uid)``、
      任务键 ``task_id``、机巢键 ``nest_offset + nest_id``，与 methods/ 一致）；
    - 物理航程 / 时间：``problem.costs.travel(...)``（统一 DEM 三维距离口径，
      无 DEM 时退化三维直线）；
    - 时间窗游标：与 ``problem.tour_time`` 相同的「到达 → 等待 → 服务」语义；
    - 航程可行性：航次闭合代价 ≤ ``uav.remaining_range``（与解码修复链
      ``_repair_range`` 同口径）；
    - 终点机巢贪心选择：航程可行的终点中优先有容量余量者、再取最小代价
      （构造式对照的"最近可行巢"策略；本文方法的方案 A 是容量联合最优
      最小费用流，见 ``methods/muas/constraints/nest_capacity.py``）。

设计原则（与 ``baselines/README.md`` 一致）：

    - 评价端唯一事实来源仍是 ``SelectiveMUASProblem.check_feasible`` ——
      本模块只负责**构造期**的可行性预判，保证构造出的航次天然满足
      航程（与硬时间窗）约束，不做解码端修复；
    - 构造结果以 ``AllocationSolution``（``extra["solution"]``）交付，
      由 ``run_selective_muas`` 统一 ``problem.evaluate`` 记账。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from task_allocation.common.types import AllocationSolution, Nest, Task, UAV, UAVTour
from task_allocation.methods.muas.base import SolverResult

__all__ = ["ConstructionContext", "build_solver_result"]

#: 数值容差（与 _repair_range 的 1e-6 同量级）
EPS = 1e-6


class ConstructionContext:
    """构造式基线的公共几何 / 时间 / 容量口径。

    从 ``MUASAssignmentEvaluator`` 提取一次求解所需的实体与代价访问器。
    求解器拿到本对象后只关心「把哪些任务按什么顺序串给哪架机」。

    Args:
        evaluator: ``run_selective_muas`` 传给 ``solve()`` 的评价器。
            其 ``problem`` 提供统一代价口径，``available_uavs`` 提供
            **全可用机队**（构造式基线不受参与集 K 裁剪限制，
            与本文方法的两阶段裁剪形成对照，见 exp05 notes.md）。
    """

    def __init__(self, evaluator: Any) -> None:
        self.evaluator = evaluator
        self.problem = evaluator.problem
        self.costs = self.problem.costs
        self.tasks: list[Task] = list(evaluator.tasks)
        self.nests: list[Nest] = list(evaluator.nests)
        self.nest_by_id: dict[int, Nest] = {n.id: n for n in self.nests}
        self.excluded_nest_ids: set[int] = set(getattr(evaluator, "excluded_nest_ids", ()))
        # 可路由机队：全可用机队优先（Fix⑥ 口径），退化为参与集
        uavs = list(getattr(evaluator, "available_uavs", None) or evaluator.participating)
        self.uavs: list[UAV] = [u for u in uavs if u.is_available]
        self.uav_by_id: dict[int, UAV] = {u.id: u for u in self.uavs}
        self.fleet_uavs: list[UAV] = list(
            getattr(evaluator, "fleet_uavs", None) or self.uavs
        )
        cfg = self.problem.config
        self.current_time: float = float(cfg.current_time)
        self.service_time: float = float(cfg.default_service_time)
        self.task_by_id: dict[int, Task] = {t.id: t for t in self.tasks}

    # ------------------------------------------------------------------
    # 键与航段代价（与 problem.CostProvider 同一口径）
    # ------------------------------------------------------------------

    @staticmethod
    def uav_key(uav_id: int) -> int:
        return -(1 + int(uav_id))

    def nest_key(self, nest_id: int) -> int:
        return self.costs.nest_key(nest_id)

    def _edge(self, key_a: int, key_b: int) -> float:
        return float(self.costs.cost(key_a, key_b))

    def route_cost(
        self,
        uav: UAV,
        seq: Sequence[int],
        end_nest_id: int | None,
    ) -> float:
        """航次闭合代价：start → tasks → 终点机巢（终点缺席时只算开放段）。"""
        total = 0.0
        prev = self.uav_key(uav.id)
        for tid in seq:
            total += self._edge(prev, int(tid))
            prev = int(tid)
        if end_nest_id is not None:
            total += self._edge(prev, self.nest_key(end_nest_id))
        return float(total)

    def insertion_delta(
        self,
        uav: UAV,
        seq: Sequence[int],
        pos: int,
        tid: int,
        end_nest_id: int | None,
    ) -> float:
        """把任务 ``tid`` 插入 ``seq`` 的 ``pos`` 位带来的闭合航次代价增量。

        插入位置语义与 :meth:`build_solution` 的路由列表一致：
        ``pos == len(seq)`` 表示追加到末尾。
        """
        seq = list(seq)
        prev_key = self.uav_key(uav.id) if pos == 0 else int(seq[pos - 1])
        next_key = (
            (self.nest_key(end_nest_id) if end_nest_id is not None else None)
            if pos == len(seq)
            else int(seq[pos])
        )
        added = self._edge(prev_key, int(tid))
        if next_key is not None:
            added += self._edge(int(tid), next_key)
            removed = self._edge(prev_key, next_key)
        else:
            removed = 0.0
        return float(added - removed)

    # ------------------------------------------------------------------
    # 时间窗游标（与 problem.tour_time 同语义：到达 → 等待 → 服务）
    # ------------------------------------------------------------------

    def arrival_times(self, uav: UAV, seq: Sequence[int]) -> list[float]:
        """各任务的**绝对到达时刻**（含 earliest 等待与服务时长游标）。"""
        speed = max(float(getattr(uav, "speed", 0.0) or 0.0), 1e-6)
        cursor = self.current_time
        prev = uav.position
        out: list[float] = []
        for tid in seq:
            task = self.task_by_id.get(int(tid))
            if task is None:
                continue
            cursor += float(self.costs.travel(prev, task.position)) / speed
            if task.earliest is not None and cursor < float(task.earliest):
                cursor = float(task.earliest)
            out.append(cursor)
            cursor += self.service_time
            prev = task.position
        return out

    @staticmethod
    def tw_ok(tasks: Sequence[Task], arrivals: Sequence[float], *, hard: bool) -> bool:
        """时间窗判据。``hard=True``：到达必须 ≤ latest（经典 VRPTW 口径）。

        本文问题的生产口径是「迟到不是废飞」（迟到计完成、奖励按 L/W 折减），
        因此 greedy/random 构造不卡时间窗（``hard=False`` 恒真），
        只有 VRPTW 启发式按经典硬窗拒绝插入——这正是对照的差异点之一。
        """
        if not hard:
            return True
        for task, arrive in zip(tasks, arrivals):
            if task.latest is not None and arrive > float(task.latest) + EPS:
                return False
        return True

    def hard_tw_ok(self, uav: UAV, seq: Sequence[int]) -> bool:
        """整条航次的硬时间窗判定（仅 VRPTW 使用）。"""
        sub = [self.task_by_id[int(t)] for t in seq if int(t) in self.task_by_id]
        return self.tw_ok(sub, self.arrival_times(uav, seq), hard=True)

    # ------------------------------------------------------------------
    # 航程可行性与终点机巢贪心选择
    # ------------------------------------------------------------------

    def _candidate_nests(self) -> list[Nest]:
        return [
            n
            for n in self.nests
            if n.available and n.id not in self.excluded_nest_ids
        ]

    def closing_cost_lb(self, uav: UAV, seq: Sequence[int]) -> float:
        """返航段代价下界：末节点 → 最近机巢（构造期快速可行性判据）。"""
        if not seq:
            return 0.0
        last = int(seq[-1])
        nests = self._candidate_nests() or self.nests
        return min(self._edge(last, self.nest_key(n.id)) for n in nests)

    def range_ok(self, uav: UAV, seq: Sequence[int]) -> bool:
        """构造期航程可行性（返航按最近机巢下界计，保守判据）。

        终点机巢最终取「航程可行的最小代价巢」（:meth:`choose_end_nest`），
        其闭合代价恰等于本下界；因此构造期通过 ⇒ 定稿解仍航程可行。
        """
        limit = float(uav.remaining_range)
        open_cost = self.route_cost(uav, seq, None)
        return open_cost + self.closing_cost_lb(uav, seq) <= limit + EPS

    def choose_end_nest(
        self,
        uav: UAV,
        seq: Sequence[int],
        occupancy: Counter | None = None,
    ) -> int | None:
        """贪心终点机巢：航程可行者中「有容量余量优先、代价最小」。

        与本文方法方案 A（容量约束下最小费用流**联合**最优）的对照点：
        这里逐机贪心、无跨机协调，容量只作优先级、不作硬保证；
        若所有机巢均不可行则取代价最小者（代价不可行由评价端如实记账）。
        """
        if not seq:
            return uav.nest_id
        nests = self._candidate_nests() or self.nests
        open_cost = self.route_cost(uav, seq, None)
        limit = float(uav.remaining_range)

        scored: list[tuple[int, float, int]] = []
        for n in nests:
            total = open_cost + self._edge(int(seq[-1]), self.nest_key(n.id))
            slack = 0
            if occupancy is not None:
                slack = 1 if occupancy[n.id] < n.capacity else 0
            scored.append((slack, total, n.id))

        feasible = [s for s in scored if s[1] <= limit + EPS]
        pool = feasible or scored
        # 有余量优先 → 代价最小 → id 最小（确定性 tie-break）
        pool.sort(key=lambda s: (-s[0], s[1], s[2]))
        return int(pool[0][2]) if pool else uav.nest_id

    def occupancy(self) -> Counter:
        """机巢泊位占用计数（机队登记巢 + 本周期航次终点增量现算）。"""
        return Counter(
            u.nest_id for u in self.fleet_uavs if u.nest_id is not None
        )

    # ------------------------------------------------------------------
    # 解组装
    # ------------------------------------------------------------------

    def make_solution(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> AllocationSolution:
        """路由表 → :class:`AllocationSolution`（不评价，评价由调用方统一做）。"""
        tours: list[UAVTour] = []
        executed: list[int] = []
        for uav in self.uavs:
            seq = [int(t) for t in routes.get(uav.id, ())]
            if not seq:
                continue
            executed.extend(seq)
            tours.append(
                UAVTour.from_uav(
                    uav,
                    task_ids=seq,
                    end_nest_id=end_nests.get(uav.id),
                )
            )
        return AllocationSolution(
            tours=tours,
            candidate_task_ids=[t.id for t in self.tasks],
            executed_task_ids=list(dict.fromkeys(executed)),
        )

    def finalize(
        self,
        routes: Mapping[int, Sequence[int]],
        *,
        occupancy: Counter | None = None,
    ) -> AllocationSolution:
        """路由表 → 定稿解（贪心选终点机巢 + 组装）。"""
        occ = self.occupancy() if occupancy is None else occupancy
        end_nests: dict[int, int | None] = {}
        for uav in self.uavs:
            seq = [int(t) for t in routes.get(uav.id, ())]
            if not seq:
                continue
            end = self.choose_end_nest(uav, seq, occ)
            end_nests[uav.id] = end
            if uav.nest_id is not None:
                occ[uav.nest_id] -= 1
            if end is not None:
                occ[end] += 1
        return self.make_solution(routes, end_nests)


def build_solver_result(
    ctx: ConstructionContext,
    solution: AllocationSolution,
    name: str,
    elapsed: float,
) -> SolverResult:
    """构造式求解器共用的 :class:`SolverResult` 组装。

    评价口径统一走 ``problem.evaluate``（与主算法同一套约束评价）；
    ``extra["solution"]`` 为权威交付解（``run_selective_muas`` 的
    预构建解通道直接消费），``best_assignment`` 用**真实 id 对**记录
    路由概览——构造式解自含路由与终点，不进 assignment 解码器。
    无迭代：``cost_history=[]``、``total_generations=0``。
    """
    ctx.problem.evaluate(solution)
    fitness = float(ctx.problem.scalar_fitness(solution))
    return SolverResult(
        best_assignment=[
            (t.uav_id, tid) for t in solution.tours for tid in t.task_ids
        ],
        best_fitness=fitness,
        cost_history=[],
        total_generations=0,
        elapsed_seconds=float(elapsed),
        solver_name=name,
        extra={"solution": solution, "n_evals": 1},
    )
