# -*- coding: utf-8 -*-
"""evaluator.py — ``MUASAssignmentEvaluator``：assignment → 解 → 标量适应度。

2026-10-27 自 ``muas_stage.py`` 拆出。本模块是评价器的**状态与装配核心**：

- :meth:`__init__`              实体索引 / 有效收益 / 评估缓存
- :meth:`_assemble`             routes + end_nests → ``AllocationSolution``
- :meth:`evaluate`              DMDE 评估接口（缓存 → build_solution → problem）
- :meth:`lamarckian_reencode`   修复解回写基因（只改起始基因通道，长度不变式）

解码 / 修复 / 成本 polish 算子组以 Mixin 拆在同包兄弟模块，共享
``tour_cost`` 原语：

- :mod:`.tour_cost`            航次代价 / ETA / 时间窗原语
- :mod:`.decode_repair`        repair_chain 解码与修复算子组
- :mod:`.decode_constructive`  构造式解码（exp04 B1 对照臂）
- :mod:`.route_polish`         交付解成本 polish 算子组
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from task_allocation.common.types import AllocationSolution, UAVTour
from task_allocation.methods.muas.representation.encoder import Gene, Individual
from task_allocation.methods.muas.solvers.decode_constructive import (
    ConstructiveDecodeMixin,
)
from task_allocation.methods.muas.solvers.decode_repair import DecodeRepairMixin
from task_allocation.methods.muas.solvers.route_polish import RoutePolishMixin
from task_allocation.methods.muas.solvers.tour_cost import TourCostMixin


@dataclass(frozen=True)


class EvalResult:
    """DMDE 评估器返回值（求解器只读 ``fitness``）。"""

    fitness: float
    solution: AllocationSolution

# 评估器：assignment → AllocationSolution → fitness


class MUASAssignmentEvaluator(
    TourCostMixin,
    DecodeRepairMixin,
    ConstructiveDecodeMixin,
    RoutePolishMixin,
):
    """把 DMDE 的 ``assignment`` 翻译成 :class:`AllocationSolution` 并评价。

    DMDE 侧的下标是**局部**的：``uav_idx`` 是 ``participating`` 列表的下标，
    ``task_idx`` 是 ``tasks`` 列表的下标；本类负责映射回真实 id。
    """

    def __init__(
        self,
        problem: SelectiveMUASProblem,
        participating: Sequence[UAV],
        tasks: Sequence[Task],
        nests: Sequence[Nest],
        fleet_uavs: Sequence[UAV],
        config: MUASStageConfig | None = None,
        available_uavs: Sequence[UAV] | None = None,
    ) -> None:
        self.problem = problem
        self.participating = list(participating)
        # Fix⑥ 救援池的「全可用机队」口径：默认退化为参与集（历史行为）
        self.available_uavs = (
            list(available_uavs) if available_uavs is not None else list(participating)
        )
        self.tasks = list(tasks)
        self.nests = list(nests)
        self.fleet_uavs = list(fleet_uavs)
        self.config = config or MUASStageConfig()
        self.excluded_nest_ids = set(self.config.excluded_nest_ids)

        self._task_ids = [t.id for t in self.tasks]
        self._task_by_id = {t.id: t for t in self.tasks}
        self._uav_by_id = {u.id: u for u in self.problem.uavs}
        self._reward = {t.id: max(float(t.reward), 0.0) for t in self.tasks}
        # 剔除判据用的是「含等待补偿的有效收益」：滞留越久越难被剔除
        scale = max(float(self.config.wait_scale), 1e-6)
        self._eff_reward = {
            t.id: self._reward[t.id]
            * (
                1.0
                + float(self.config.prune_wait_bonus)
                * (1.0 - math.exp(-max(0.0, self.config.current_time - t.arrival_time) / scale))
            )
            for t in self.tasks
        }
        self._lambda = float(problem.config.lambda_cost)
        self._cache: dict[tuple, EvalResult] = {}
        self.n_evals = 0
        # 构造式解码（decode_mode="constructive"）的诊断出口：上一轮
        # build_solution 中 y_j=0 证明性暂缓的任务 id（供上层对账；
        # repair_chain 模式下恒为空列表）。
        self.last_constructive_deferred: list[int] = []

    def _assemble(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> AllocationSolution:
        """组装 AllocationSolution（tours 覆盖全部可用 UAV，空闲航次也要有）。"""
        tours: list[UAVTour] = []
        executed: list[int] = []
        for uav in self.problem.uavs:
            tids = [t for t in routes.get(uav.id, [])]
            end = end_nests.get(uav.id, None)
            if not tids:
                end = uav.nest_id  # 空闲：停在原归属机巢
            executed.extend(tids)
            tours.append(UAVTour.from_uav(uav, task_ids=tids, end_nest_id=end))

        return AllocationSolution(
            tours=tours,
            candidate_task_ids=[t.id for t in self.tasks],
            executed_task_ids=list(dict.fromkeys(executed)),
        )

    def evaluate(
        self,
        assignment: Iterable[tuple[int, int]],
        cost_matrix: np.ndarray | None = None,
        n_uavs: int | None = None,
    ) -> EvalResult:
        """DMDE 求解器要求的评估接口。

        方案 A：终点机巢在 :meth:`build_solution` 内由内层最小费用流求解，
        不经过编码，因此缓存键只需 assignment。
        """
        assign_key = tuple((int(a), int(b)) for a, b in assignment)

        if self.config.use_cache and assign_key in self._cache:
            return self._cache[assign_key]

        self.n_evals += 1
        solution = self.build_solution(assign_key)
        self.problem.evaluate(solution)
        fitness = float(self.problem.scalar_fitness(solution))
        result = EvalResult(fitness=fitness, solution=solution)
        if self.config.use_cache:
            self._cache[assign_key] = result
        return result

    def lamarckian_reencode(
        self,
        child: "Individual",
        solution: Any,
        cost_matrix: np.ndarray,
        n_uavs: int,
        n_targets: int,
    ) -> "Individual | None":
        """把修复后的解写回获胜个体基因（Lamarckian 回写）。

        论文 §5(4) 的约束处理在 ``build_solution`` 内对 assignment 做
        ``_prune`` / ``_repair_range`` / ``_choose_end_nests``，得到的
        ``solution`` 才是真正被评价的可行解。但个体的基因（``cost_vector``）
        仍是 ``inverse_phi`` 由试验向量译出的**未修复**版本，于是下一世代的
        差分变异围绕未修复空间展开（进化搜的是未修复解）。

        方案 A 口径：只回写**起始基因**一条通道 —— 基因序列中 ``uav_id >= 0``
        者改写为该 UAV 在修复解中的**首任务**，使下一轮 ``inverse_phi`` 的
        贪心起点精确复现修复解的归属。终点机巢不进编码（「终点进编码」的
        联合变体在 pro 分支实现，届时才回写终止基因通道），巡游基因保留原样。

        本函数只原地改写 start 基因的 ``target_id`` 与 ``cost`` 字段，绝不
        增删任何基因，因此基因长度严格保持 ``M``，不会改变 DE 的代价矩阵
        形状——这是 Lamarckian 回写在本 SRP 基因契约下能安全落地的关键。

        注意：巡游基因虽不被本函数改写，但 ``assignment`` 在计算修复解与
        提取航线时仍会读取其 ``target_id``；修复阶段经 ``_prune`` /
        ``_repair_range`` 剔除的任务，其巡游基因会随下一轮 ``inverse_phi``
        解码重新出现并被再次剔除，故修复结果中"被剔除任务"的这一部分不会
        永久写回基因（属于有意的保守设计：避免在基因层做脆弱的增删、破坏
        "每任务恰出现一次"不变式）。

        Args:
            child:        获胜的子代个体（``inverse_phi`` 译出、已被评价）。
            solution:     本次评价用到的修复后 ``AllocationSolution``。
            cost_matrix:  代价矩阵 ``(K+N, N)``（与编码同布局）。
            n_uavs/n_targets: 维度 K / N。

        Returns:
            回写后的新个体；长度不一致或异常时返回 ``None``（调用方回退到原 child）。
        """
        if solution is None:
            return None
        local_task_idx = {tid: i for i, tid in enumerate(self._task_ids)}
        local_uav_idx = {u.id: i for i, u in enumerate(self.participating)}

        # 修复解里每架参与 UAV 的首任务（局部下标）
        start_of: dict[int, int] = {}
        for tour in solution.tours:
            u = local_uav_idx.get(tour.uav_id)
            if u is None:
                continue
            tlocal = [local_task_idx[t] for t in tour.task_ids if t in local_task_idx]
            if tlocal:
                start_of[u] = tlocal[0]

        new_genes: list[Gene] = []
        for g in child.genes:
            if g.uav_id >= 0:
                u = g.uav_id
                if u in start_of:
                    t0 = start_of[u]
                    new_genes.append(
                        Gene(uav_id=u, target_id=t0, cost=float(cost_matrix[u, t0]))
                    )
                else:
                    new_genes.append(g)
            else:
                # 巡游基因（uav_id == -1）保留原样
                new_genes.append(g)

        # 长度不变式校验：必须仍为 M，否则回退到原 child，保证 DE 矩阵不错位
        if len(new_genes) != len(child.genes):
            return None
        return Individual(genes=new_genes, model_type=child.model_type)

