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
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from task_allocation.common.types import AllocationSolution, UAVTour
from task_allocation.methods.muas.representation.encoder import Gene, Individual
from task_allocation.methods.muas.representation.inverse_mapper import inverse_phi
from task_allocation.methods.muas.representation.repair_rules.terminal_nest import (
    TOUR_UAV_ID,
    _safe_cost,
)
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

        方案 A 口径：起始基因通道（``uav_id >= 0``）改写为该 UAV 在修复解中的
        **首任务**（M1 已有行为，逐位保留）；巡游基因通道（``uav_id == −1``）
        的**段内访问顺序**在 M2-1 开关开启且 SRP 模型下同步回写（见
        ``task_allocation/docs/m2_gene_writeback_design_2026-10-30.md``）。

        本函数绝不增删任何基因（基因长度严格保持 ``N``，不会改变 DE 代价矩阵
        形状），这是 Lamarckian 回写在本 SRP 基因契约下能安全落地的关键。

        通用纪律（设计稿 §0/§2/§3）：
        - 只写"修复后表型"，不写"搜索中间态"；
        - **Q3.1** 含硬违约的解永不写回；覆盖 ⊇ 回写前表型才允许（集合/顺序
          不匹配即视为不可安全写回，整体回退）；
        - **Q2.1** 写回后以 ``temperature=0`` 重解码，表型必须 == 目标修复解
          （归属集合 + 各段顺序），否则回退（fail-safe，与现回退同纪律）；
        - **Q2.2** 轻量 repair：写回本身只做确定性归一（段内序 + cost 重算），
          不跑 ``reorder_for_deadlines`` / ``repair_range`` / coverage gate——
          它们会重新洗序，把刚写进的序又洗掉（T9 病根之一）；
        - **Q2.3** 不变式断言 I1/I2/I3 逐条校验，任一破 → 回退；
        - **Q3.2** 表型复核：重解码表型的 ``(n_hard, 覆盖, 迟到)`` 三元组逐分量
          不劣于 child 解，否则回退。

        回退纪律全程：任何异常/不一致 → 原 child，stderr 警告一次、不静默
        （沿用「回退永不静默」惯例）。

        Args:
            child:        获胜的子代个体（``inverse_phi`` 译出、已被评价）。
            solution:     本次评价用到的修复后 ``AllocationSolution``。
            cost_matrix:  代价矩阵 ``(K+N, N)``（与编码同布局）。
            n_uavs/n_targets: 维度 K / N。

        Returns:
            回写后的新个体；长度不一致/硬违约/解码漂移/异常时返回 ``None``
            （调用方回退到原 child）。
        """
        if solution is None:
            return None
        # 修复解每机有序航线（局部下标）：{local_uav_idx: [local_task_idx,...]}。
        route_of = self._solution_routes_local(solution)
        start_of = {u: seq[0] for u, seq in route_of.items() if seq}

        # M2-1 主开关（默认关 ⇒ 行为与历史逐位等价；见测试第 5 条）。
        # 组合语义（整理注记）：M2-1 借道 M1 的调用门（dmde_solver 中
        # ``if cfg.lamarckian and ...``），故 **lamarckian=False 时 M2-1 永不触发**——
        # 它不能脱离 M1 单独开启，是 M1 同一回写调用的扩展（同时改写起始+巡游通道）。
        patrol_on = bool(getattr(self.config, "lamarckian_patrol_writeback", False))
        if not patrol_on or child.model_type != "srp":
            # ── 历史行为（M1 起始通道回写，逐位不变）─────────────────
            return self._reencode_start_only(
                child.genes, start_of, cost_matrix, child.model_type
            )

        # ── M2-1：段内巡游序回写 ─────────────────────────────────
        # Q3.1 硬违约过滤：含硬违约的解永不写回（无论 fitness 多好看）。
        # （Q3.1 第二句「覆盖 ⊇ 回写前表型」：段内重排只改顺序、不改任务集合，
        #  覆盖在结构上不变，故该子句被结构性满足；下方 Q3.2 仍显式复核覆盖不劣。）
        n_hard_src, _ = self._hard_stats_safe(solution)
        if n_hard_src > 0:
            return None

        try:
            new_genes = self._reencode_patrol_order(
                child.genes, route_of, cost_matrix, n_uavs, n_targets
            )
        except Exception as exc:  # 任何异常（集合不匹配/索引越界等）
            print(
                "[evaluator][warn] M2-1 段内回写异常，回退起始通道写法："
                f"{exc!r}",
                file=sys.stderr,
            )
            return self._reencode_start_only(
                child.genes, start_of, cost_matrix, child.model_type
            )
        if new_genes is None:
            return self._reencode_start_only(
                child.genes, start_of, cost_matrix, child.model_type
            )

        # I1 长度不变式：必须仍为 N，否则回退，保证 DE 矩阵不错位。
        if len(new_genes) != len(child.genes):
            return None

        re_encoded = Individual(genes=new_genes, model_type=child.model_type)

        # Q2.1 解码一致性 + Q2.3 I1/I2/I3 + Q3.2 表型复核。
        if not self._validate_writeback(
            re_encoded, route_of, solution, cost_matrix, n_uavs, n_targets
        ):
            return None
        return re_encoded

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Lamarckian 回写辅助
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _solution_routes_local(self, solution: Any) -> dict[int, list[int]]:
        """提取修复解每架参与 UAV 的有序航线（局部下标）。

        Returns:
            ``{local_uav_idx: [local_task_idx, ...]}``；空航线不计入。
        """
        local_task_idx = {tid: i for i, tid in enumerate(self._task_ids)}
        local_uav_idx = {u.id: i for i, u in enumerate(self.participating)}
        route_of: dict[int, list[int]] = {}
        for tour in solution.tours:
            u = local_uav_idx.get(tour.uav_id)
            if u is None:
                continue
            seq = [local_task_idx[t] for t in tour.task_ids if t in local_task_idx]
            if seq:
                route_of[u] = seq
        return route_of

    def _reencode_start_only(
        self,
        genes: list["Gene"],
        start_of: dict[int, int],
        cost_matrix: np.ndarray,
        model_type: str,
    ) -> "Individual | None":
        """历史 Lamarckian 回写：只改写起始基因通道（``uav_id >= 0`` → 修复解首任务）。

        与 M2-1 开关关闭时逐位等价；巡游基因（``uav_id == −1``）原样保留。
        长度不一致时返回 ``None``。
        """
        new_genes: list[Gene] = []
        for g in genes:
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
                new_genes.append(g)
        if len(new_genes) != len(genes):
            return None
        return Individual(genes=new_genes, model_type=model_type)

    def _reencode_patrol_order(
        self,
        genes: list["Gene"],
        route_of: dict[int, list[int]],
        cost_matrix: np.ndarray,
        n_uavs: int,
        n_targets: int,
    ) -> list["Gene"]:
        """M2-1：段内巡游序回写 + cost 逐链重算。

        逐段处理基因序列（段 = 一条起始基因 + 其后续巡游基因）：

        - 段内集合匹配（段基因任务集 == 修复解航线集，且长度一致）→ 全量回写：
          起始基因改写为航线首任务；巡游基因按修复解顺序重排，cost 按新链逐段
          重算（``cost_matrix[u, t0]`` / ``cost_matrix[K+prev, t_k]``，与
          ``normalize_srp_segments`` 同口径，保证 ``temperature=0`` 重解码可复现）。
        - 段内集合不匹配（存在剔除 / 改派 / 跨段迁移）→ 该段保守退回起始通道写法，
          巡游基因原样保留。

        返回新基因列表；长度恒不变（I1）——不增删任何基因。

        Raises:
            ValueError: 段集不匹配等不可安全写回的情形（调用方捕获后回退）。
        """
        new_genes: list[Gene] = []
        n = len(genes)
        i = 0
        while i < n:
            g = genes[i]
            if g.uav_id >= 0:
                u = g.uav_id
                seg_start = i
                j = i + 1
                while j < n and genes[j].uav_id == TOUR_UAV_ID:
                    j += 1
                seg_genes = genes[seg_start:j]  # [起始] + [巡游...]
                route = route_of.get(u)
                if route is not None and self._segment_matches(seg_genes, route):
                    # 全量段内回写：起始 + 巡游序（cost 按新链重算）
                    new_genes.append(
                        Gene(
                            uav_id=u,
                            target_id=int(route[0]),
                            cost=_safe_cost(cost_matrix, u, route[0]),
                        )
                    )
                    for k in range(1, len(route)):
                        new_genes.append(
                            Gene(
                                uav_id=TOUR_UAV_ID,
                                target_id=int(route[k]),
                                cost=_safe_cost(
                                    cost_matrix, n_uavs + route[k - 1], route[k]
                                ),
                            )
                        )
                else:
                    # 集合不匹配（剔除/改派）：保守退回起始通道写法，巡游原样保留。
                    if route is not None and route:
                        new_genes.append(
                            Gene(
                                uav_id=u,
                                target_id=int(route[0]),
                                cost=float(cost_matrix[u, route[0]]),
                            )
                        )
                    else:
                        new_genes.append(seg_genes[0])
                    new_genes.extend(seg_genes[1:])
                i = j
            else:
                # 游离巡游基因（理论上不应出现，防御性保留）。
                new_genes.append(g)
                i += 1
        return new_genes

    @staticmethod
    def _segment_matches(seg_genes: list["Gene"], route: list[int]) -> bool:
        """段基因任务集是否 == 修复解航线集（且长度一致）。

        I2（每任务恰出现一次）由 target_id 唯一性保证；此处只比对集合与长度，
        不要求顺序——顺序由后续回写决定。
        """
        if len(seg_genes) != len(route):
            return False
        return {g.target_id for g in seg_genes} == set(route)

    def _validate_writeback(
        self,
        re_encoded: "Individual",
        route_of: dict[int, list[int]],
        solution: Any,
        cost_matrix: np.ndarray,
        n_uavs: int,
        n_targets: int,
    ) -> bool:
        """写回后校验（Q2.1 / Q2.3 / Q3.2）。任一不满足 → 返回 False（回退）。"""
        genes = re_encoded.genes

        # I1 长度不变：基因总数 == N（每任务恰出现一次）。
        if len(genes) != n_targets:
            return False
        # I2 唯一性：target_id 是全排列（每任务恰出现一次）。
        if sorted(g.target_id for g in genes) != list(range(n_targets)):
            return False
        # I3 段结构：K 条起始基因（uav_id 各异，恰为 0..K−1）+ N−K 条巡游基因。
        starts = [g for g in genes if g.uav_id >= 0]
        if len(starts) != n_uavs:
            return False
        if sorted(g.uav_id for g in starts) != list(range(n_uavs)):
            return False
        tours = [g for g in genes if g.uav_id == TOUR_UAV_ID]
        if len(tours) != n_targets - n_uavs:
            return False
        cur: int | None = None
        for g in genes:
            if g.uav_id >= 0:
                cur = g.uav_id
            elif g.uav_id == TOUR_UAV_ID:
                if cur is None:
                    return False
            else:
                return False  # 不允许其它 uav_id（如终止基因 −2）

        # Q2.1 解码一致性：temperature=0 重解码，表型必须 == 目标修复序。
        rng = np.random.default_rng(0)
        try:
            reb = inverse_phi(
                re_encoded.cost_vector,
                cost_matrix,
                n_uavs,
                n_targets,
                "srp",
                rng=rng,
                temperature=0.0,
            )
        except Exception:
            return False
        if reb.srp_routes != route_of:
            return False

        # Q3.2 表型复核：重解码表型的 (n_hard, 覆盖, 迟到) 逐分量不劣于 child 解。
        # （整理注记：Q2.1 解码一致性已保证 reb 路由 == 目标修复序，故 Q3.2 与 Q2.1
        #  在路由层面重叠、几乎必过；此处保留为「解码一致性之上的二次保险」，防止
        #  _assemble / evaluate 任何细微偏差把劣质表型放进来。）
        reb_routes_real = {
            self.participating[u].id: [self._task_ids[t] for t in seq]
            for u, seq in reb.srp_routes.items()
        }
        end_nests = {t.uav_id: t.end_nest_id for t in solution.tours}
        reb_sol = self._assemble(reb_routes_real, end_nests)
        try:
            self.problem.evaluate(reb_sol)
        except Exception:
            return False
        n_hard_reb, _ = self._hard_stats_safe(reb_sol)
        cov_reb = set(reb_sol.executed_task_ids)
        late_reb = self._soft_violation_safe(reb_sol)
        n_hard_src, _ = self._hard_stats_safe(solution)
        cov_src = set(solution.executed_task_ids)
        late_src = self._soft_violation_safe(solution)
        if n_hard_reb > n_hard_src:
            return False
        if not cov_reb >= cov_src:
            return False
        if late_reb > late_src + 1e-6:
            return False
        return True

    def _hard_stats_safe(self, solution: Any) -> tuple[int, float]:
        """``problem.hard_stats`` 的安全包装（硬违约条数, 航程超限米数）。"""
        try:
            return self.problem.hard_stats(solution)
        except Exception:
            return (0, 0.0)

    def _soft_violation_safe(self, solution: Any) -> float:
        """``problem.soft_violation`` 的安全包装（迟到秒数合计）。"""
        try:
            return float(self.problem.soft_violation(solution))
        except Exception:
            return 0.0

