# -*- coding: utf-8 -*-
"""muas_stage.py — 第二阶段编排：把 DMDE 的离散映射接到选择性多机巢 MUAS 模型。

职责边界：

    DMDESolver            只负责**搜索**：在连续空间做差分进化，产出
                          ``best_assignment``（局部下标的 (uav_idx, task_idx) 序列）
    SelectiveMUASProblem  只负责**问题定义与评价**：y / x / π / z、F1 / F2、约束惩罚
    本模块               负责**两者之间的翻译**与选择性决策

翻译内容（对应论文 §4.2 决策变量）：

    assignment ──► π_u  任务访问顺序（保留 DMDE 给出的基因顺序）
                ──► y_j  任务是否执行（默认执行，可因代价过高置 0）
                ──► z_ub 终点机巢（容量感知挑选，见 constraints.choose_end_nests）
                ──► x_uj 由 π_u 与 y_j 导出（一任务一机，自动去重）

关于模型类型（N/M 关系）：

    DMDE 按 ``n_uavs`` 与 ``n_targets`` 的大小自动选模型：
      N == M → balanced（一一对应，无巡游）
      N  > M → overloaded（UAV 不重复、**任务可重复**）
      N  < M → srp（UAV 巡游，任务不重复）

    本问题要求「一任务一机」，因此 **overloaded 不可用**（它会把同一任务
    分给多架 UAV）。故参与规划的 UAV 数 K 恒取 < 任务数 M（即 srp 巡游模型），
    其余可用 UAV 本周期保持空闲 —— 这正是论文允许的 ``U_t^exec ⊆ U_t^avail``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
from task_allocation.methods.muas.constraints.nest_capacity import (
    check_nest_capacity,
    choose_end_nests,
)
from task_allocation.methods.muas.cost.cost_matrix import CostMatrixBuildResult
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.representation.encoder import Gene, Individual
from task_allocation.methods.muas.representation.repair_rules.terminal_nest import (
    TERMINAL_UAV_ID,
    TOUR_UAV_ID,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig, DMDESolver

__all__ = [
    "MUASStageConfig",
    "MUASStageResult",
    "EvalResult",
    "MUASAssignmentEvaluator",
    "select_participating_uavs",
    "run_selective_muas",
]

# 参与 UAV 的选择策略
POLICY_CHEAPEST = "cheapest"     # 按「到最近任务的最小代价」升序取前 K 架
POLICY_FIRST = "first"           # 按登记顺序取前 K 架（便于复现）


@dataclass
class MUASStageConfig:
    """第二阶段编排参数（全部可由实验 yaml 覆盖）。"""

    # 参与本周期规划的 UAV 数上限；None 表示自动（保证 K < 任务数以走 srp 模型）
    max_participating_uavs: int | None = None
    participating_policy: str = POLICY_CHEAPEST
    # 选择性：剔除「执行代价超过收益」的任务（y_j = 0，保留到下一周期）
    prune_unprofitable: bool = True
    prune_max_rounds: int = 8
    # 剔除判据的裕度：仅当 λ·节省 > 收益 × (1 + margin) 时才剔除
    prune_margin: float = 0.0
    # 等待补偿（论文 §3.2 的 W_i(t)）：任务滞留越久越不容易被剔除，
    # 避免偏远任务连续多周期得不到执行而永久滞留
    prune_wait_bonus: float = 1.0
    wait_scale: float = 3600.0
    # 终点机巢是否**进入编码**由差分进化搜索（论文 §5(1)(2)）。
    # 关闭后退化为「解码后再用容量感知策略挑终点」，供 exp04 消融对比。
    end_nest_in_encoding: bool = True
    # Lamarckian 回写（TODO §2.7）：把修复后的解（经 _prune / _repair_range /
    # _choose_end_nests）的「起始任务归属」与「终点机巢」编码回获胜个体的基因，
    # 使后继代际的差分变异围绕**修复后的可行子空间**展开（默认开启）。
    # 关闭后退化为当前行为：进化搜索的是未修复空间。
    lamarckian: bool = True
    # 事件驱动重规划：被标记不可用的机巢（论文 §6 的 NEST_UNAVAILABLE 事件）
    # 一律不可选为终点机巢 z_ub，其归属 UAV 强制改降其它巢（多机巢异巢终止的
    # 容错分支）。由滚动层每周期从事件上下文注入。
    excluded_nest_ids: set[int] = field(default_factory=set)
    # 当前时刻，用于算等待补偿；由滚动层每周期传入
    current_time: float = 0.0
    # 适应度缓存（DMDE 会重复评估同一个体）
    use_cache: bool = True

    def __post_init__(self) -> None:
        if self.max_participating_uavs is not None and self.max_participating_uavs < 1:
            raise ValueError("max_participating_uavs 必须 >= 1 或为 None")
        if self.participating_policy not in (POLICY_CHEAPEST, POLICY_FIRST):
            raise ValueError(f"未知的参与 UAV 选择策略：{self.participating_policy}")


@dataclass(frozen=True)
class EvalResult:
    """DMDE 评估器返回值（求解器只读 ``fitness``）。"""

    fitness: float
    solution: AllocationSolution


@dataclass
class MUASStageResult:
    """第二阶段输出。"""

    solution: AllocationSolution
    participating_uav_ids: list[int]
    model_type: str
    n_evals: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)
    solver_extra: dict[str, Any] = field(default_factory=dict)
    cost_history: list[float] = field(default_factory=list)
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# 参与 UAV 选择
# ---------------------------------------------------------------------------


def select_participating_uavs(
    uavs: Sequence[UAV],
    n_tasks: int,
    config: MUASStageConfig,
    uav_target: np.ndarray | None = None,
    row_of_uav: Mapping[int, int] | None = None,
) -> list[UAV]:
    """挑选本周期参与规划的 UAV（``U_t^exec`` 的候选）。

    上限 K 满足 ``K < n_tasks``（走 srp 巡游模型，保证一任务一机且允许巡游）；
    任务数为 1 时退化为 K = 1（balanced 亦可，一一对应）。

    Args:
        uavs:        可用无人机（``U_t^avail``）。
        n_tasks:     候选任务数 M。
        config:      编排参数。
        uav_target:  ``(n_uavs, n_tasks)`` 代价矩阵，用于 cheapest 策略。
        row_of_uav:  ``{uav_id: 矩阵行号}``，配合 ``uav_target`` 使用。

    Returns:
        参与规划的 UAV 列表（长度 K）。
    """
    avail = [u for u in uavs if u.is_available]
    if not avail or n_tasks <= 0:
        return []

    # K 必须 < M 才能走 srp；M == 1 时只能 1:1
    auto_k = min(len(avail), max(1, n_tasks - 1)) if n_tasks > 1 else min(len(avail), 1)
    k = auto_k if config.max_participating_uavs is None else min(
        config.max_participating_uavs, len(avail)
    )
    k = max(1, min(k, len(avail)))

    if config.participating_policy == POLICY_FIRST or uav_target is None:
        return avail[:k]

    def cheapest_cost(u: UAV) -> float:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            return float("inf")
        return float(np.min(uav_target[row]))

    return sorted(avail, key=lambda u: (cheapest_cost(u), u.id))[:k]


# ---------------------------------------------------------------------------
# 评估器：assignment → AllocationSolution → fitness
# ---------------------------------------------------------------------------


class MUASAssignmentEvaluator:
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
    ) -> None:
        self.problem = problem
        self.participating = list(participating)
        self.tasks = list(tasks)
        self.nests = list(nests)
        self.fleet_uavs = list(fleet_uavs)
        self.config = config or MUASStageConfig()
        self.excluded_nest_ids = set(self.config.excluded_nest_ids)

        self._task_ids = [t.id for t in self.tasks]
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

    # ---- 翻译 ----------------------------------------------------------

    def build_solution(
        self,
        assignment: Iterable[tuple[int, int]],
        coded_end_nests: Mapping[int, int] | None = None,
    ) -> AllocationSolution:
        """局部下标的 assignment → AllocationSolution（含 π / y / z）。

        Args:
            assignment: DMDE 输出的任务分配（局部下标）。
            coded_end_nests: **编码给出的**终点机巢 ``{uav_idx(局部): nest_idx}``
                （论文 §5(2)）。省略时终点完全由容量感知策略决定。
        """
        routes_local: dict[int, list[int]] = {}
        seen: set[int] = set()
        for uav_idx, task_idx in assignment:
            uav_idx = int(uav_idx)
            task_idx = int(task_idx)
            if uav_idx < 0 or uav_idx >= len(self.participating):
                continue
            if task_idx < 0 or task_idx >= len(self.tasks):
                continue
            if task_idx in seen:
                continue  # 一任务一机：重复出现只保留首次
            seen.add(task_idx)
            routes_local.setdefault(uav_idx, []).append(task_idx)

        # 局部下标 → 真实 id
        routes: dict[int, list[int]] = {}
        for uav_idx, tids in routes_local.items():
            routes[self.participating[uav_idx].id] = [self._task_ids[i] for i in tids]

        # 编码终点：局部 uav_idx / nest_idx → 真实 id
        # nest 用 self.nests 的下标换算成 Nest.id
        coded_ends: dict[int, int] | None = None
        if coded_end_nests:
            coded_ends = {}
            for uav_idx, nest_idx in coded_end_nests.items():
                if nest_idx is None:
                    continue  # 该 UAV 段未生成终止基因，交由容量感知兜底
                uav_idx = int(uav_idx)
                nest_idx = int(nest_idx)
                if not (0 <= uav_idx < len(self.participating)):
                    continue
                if not (0 <= nest_idx < len(self.nests)):
                    continue
                coded_ends[self.participating[uav_idx].id] = self.nests[nest_idx].id

        # 航线与终点机巢必须**联合收敛**后才能定稿，顺序不能颠倒：
        # 只有真正有航次的 UAV 才会起飞让出原机巢名额。若先选巢再做剪枝 /
        # 航程修复，被清空的航次实际并未起飞，让出的名额是虚的 → 落地期超卖。
        end_nests: dict[int, int | None] = {}
        prev_state = None
        for _ in range(6):
            if self.config.prune_unprofitable:
                routes, end_nests = self._prune(routes, end_nests)
            routes, end_nests = self._repair_range(routes, end_nests)
            end_nests = self._choose_end_nests(routes, coded_ends=coded_ends)
            state = (
                tuple((k, tuple(v)) for k, v in sorted(routes.items())),
                tuple(sorted(end_nests.items(), key=lambda kv: str(kv[0]))),
            )
            if state == prev_state:
                break
            prev_state = state

        return self._assemble(routes, end_nests)

    def _choose_end_nests(
        self,
        routes: Mapping[int, Sequence[int]],
        coded_ends: Mapping[int, int] | None = None,
    ) -> dict[int, int | None]:
        """为有航次的 UAV 选终点机巢（含飞行中也要占名额）。

        论文 §5(2)：终点机巢由**编码**（差分进化搜索）给出。此处
        **以编码结果为主**，仅当编码终点违反机巢容量时才回退到容量感知
        改派，保证落地期可行 —— 编码为主、启发式为兜底。
        """
        if not self.nests:
            return {uid: None for uid in routes}

        # 事件驱动：被标记不可用的机巢不可选为终点（多机巢异巢终止的容错分支）
        excluded = self.excluded_nest_ids
        cand_nests = [n for n in self.nests if n.id not in excluded]

        # ① 优先采用编码给出的终点（仅当容量校验通过且非禁用机巢）
        if coded_ends:
            flying = [uid for uid, tids in routes.items() if tids]
            cand = {uid: coded_ends[uid] for uid in flying if uid in coded_ends}
            if flying and len(cand) == len(flying):
                # 编码终点命中禁用机巢 → 视为冲突，走容量感知兜底改派
                if any(eid in excluded for eid in cand.values()):
                    pass
                else:
                    rep = check_nest_capacity(
                        self.fleet_uavs, self.nests, cand
                    )
                    if not rep.violations:
                        return {**{uid: None for uid in routes}, **cand}

        # ② 编码终点缺失 / 超容 / 命中禁用机巢 → 容量感知改派（兜底）
        last_cost: dict[int, dict[int, float]] = {}
        for uav_id, tids in routes.items():
            if not tids:
                continue
            last_node = tids[-1]
            costs: dict[int, float] = {}
            for nest in cand_nests:
                if not nest.available or nest.capacity <= 0:
                    continue
                costs[nest.id] = float(
                    self.problem.costs.cost(
                        last_node, self.problem.costs.nest_key(nest.id)
                    )
                )
            if costs:
                last_cost[uav_id] = costs

        if not last_cost:
            return {uid: None for uid in routes}

        chosen = choose_end_nests(
            self.fleet_uavs,
            cand_nests,
            last_cost,
            planning_uav_ids=list(last_cost),
            excluded_nest_ids=excluded,
        )
        return chosen

    def _prune(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """剔除执行代价高于收益的任务（论文 §4.2 的 ``y_j = 0``）。

        判据：移除任务 j 节省的路径代价 ``saving`` 满足

            λ · saving > R_j^eff · (1 + margin)

        则本周期不执行 j（保留至下一规划周期）。其中 ``R_j^eff`` 是含等待
        补偿的有效收益（论文 §3.2 的 ``W_i(t)``）：任务滞留越久，补偿越大，
        越不容易被剔除，从而避免偏远任务永久滞留。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)
        lam = self._lambda
        margin = float(self.config.prune_margin)

        for _ in range(max(1, self.config.prune_max_rounds)):
            removed = False
            for uav_id in list(work):
                tids = work[uav_id]
                if not tids:
                    continue
                for pos in range(len(tids)):
                    tid = tids[pos]
                    saving = self._removal_saving(uav_id, tids, pos, ends.get(uav_id))
                    reward = self._eff_reward.get(tid, 0.0)
                    if saving is None:
                        continue
                    if lam * saving > reward * (1.0 + margin) + 1e-9:
                        tids.pop(pos)
                        removed = True
                        break
                if removed:
                    break
            if not removed:
                break

        # 航次被清空的 UAV 转为空闲，回到原归属机巢（释放名额）
        for uav_id, tids in work.items():
            if not tids:
                ends[uav_id] = None

        return work, ends

    def _nearest_nest_cost(self, node_id: int) -> float:
        """到最近可用机巢的代价；终点尚未定稿时用于保守估计返航段。"""
        best = float("inf")
        for nest in self.nests:
            if not nest.available or nest.capacity <= 0:
                continue
            c = float(
                self.problem.costs.cost(node_id, self.problem.costs.nest_key(nest.id))
            )
            if c < best:
                best = c
        return best if np.isfinite(best) else 0.0

    def _return_leg_cost(
        self,
        node_id: int,
        end_nest_id: int | None,
    ) -> float:
        """末节点 → 终点机巢的代价；终点未定时退化为最近机巢（保守）。"""
        if end_nest_id is not None:
            return float(
                self.problem.costs.cost(
                    node_id, self.problem.costs.nest_key(end_nest_id)
                )
            )
        return self._nearest_nest_cost(node_id)

    def _tour_cost(
        self,
        uav_id: int,
        tids: Sequence[int],
        end_nest_id: int | None,
    ) -> float:
        """航次总代价：起点 → 各任务 → 终点机巢。"""
        prev: int = -(1 + uav_id)
        total = 0.0
        for tid in tids:
            total += float(self.problem.costs.cost(prev, tid))
            prev = tid
        if tids:
            total += self._return_leg_cost(tids[-1], end_nest_id)
        return float(total)

    def _repair_range(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """航程约束修复（论文 §5(4) 约束处理）。

        与机巢容量同理，只靠 penalty 兜底不够——DMDE 会在不可行个体上浪费
        代数。这里对超航程的航次逐个移除「移除后节省最多」的任务，
        直到航次代价落入剩余航程内；被移除的任务本周期不执行（y_j = 0）。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        for uav_id in list(work):
            uav = self._uav_by_id.get(uav_id)
            if uav is None:
                continue
            limit = float(uav.remaining_range)
            while work[uav_id]:
                if self._tour_cost(uav_id, work[uav_id], ends.get(uav_id)) <= limit + 1e-6:
                    break
                best_pos: int | None = None
                best_saving = float("-inf")
                for pos in range(len(work[uav_id])):
                    saving = self._removal_saving(
                        uav_id, work[uav_id], pos, ends.get(uav_id)
                    )
                    if saving is not None and saving > best_saving:
                        best_saving = saving
                        best_pos = pos
                if best_pos is None:
                    break
                work[uav_id].pop(best_pos)

            if not work[uav_id]:
                ends[uav_id] = None

        return work, ends

    def _removal_saving(
        self,
        uav_id: int,
        tids: Sequence[int],
        pos: int,
        end_nest_id: int | None,
    ) -> float | None:
        """移除 ``tids[pos]`` 节省的路径代价（>0 表示值得移除）。"""
        if pos < 0 or pos >= len(tids):
            return None
        tid = tids[pos]
        prev_id = -(1 + uav_id) if pos == 0 else tids[pos - 1]
        is_last = pos == len(tids) - 1
        next_key = self._return_leg_cost(tid, end_nest_id) if is_last else None

        c_prev_t = float(self.problem.costs.cost(prev_id, tid))
        if is_last:
            # 末位任务：移除后由前驱直接返航
            c_t_next = float(next_key)  # type: ignore[arg-type]
            c_prev_next = self._return_leg_cost(prev_id, end_nest_id)
        else:
            next_id = tids[pos + 1]
            c_t_next = float(self.problem.costs.cost(tid, next_id))
            c_prev_next = float(self.problem.costs.cost(prev_id, next_id))
        return float(c_prev_t + c_t_next - c_prev_next)

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

    # ---- 评价 ----------------------------------------------------------

    def evaluate(
        self,
        assignment: Iterable[tuple[int, int]],
        cost_matrix: np.ndarray | None = None,
        n_uavs: int | None = None,
        individual: Any | None = None,
    ) -> EvalResult:
        """DMDE 求解器要求的评估接口。

        Args:
            individual: 进化个体（可选）。给出时从中读取**编码的终点机巢**
                （``Individual.end_nests``，论文 §5(2)），使终点真正参与
                适应度评价；省略时终点退化为容量感知启发式。
        """
        assign_key = tuple((int(a), int(b)) for a, b in assignment)
        coded = dict(individual.end_nests) if individual is not None else None

        # 缓存键必须包含终点，否则「同任务分配、不同终点」会命中错误缓存
        key: tuple = assign_key
        if coded:
            key = assign_key + tuple(
                sorted((int(k), int(v)) for k, v in coded.items() if v is not None)
            )

        if self.config.use_cache and key in self._cache:
            return self._cache[key]

        self.n_evals += 1
        solution = self.build_solution(assign_key, coded_end_nests=coded)
        self.problem.evaluate(solution)
        fitness = float(self.problem.scalar_fitness(solution))
        result = EvalResult(fitness=fitness, solution=solution)
        if self.config.use_cache:
            self._cache[key] = result
        return result

    # ---- Lamarckian 回写（TODO §2.7）----------------------------------

    def lamarckian_reencode(
        self,
        child: "Individual",
        solution: Any,
        cost_matrix: np.ndarray,
        n_uavs: int,
        n_targets: int,
        n_nests: int,
    ) -> "Individual | None":
        """把修复后的解写回获胜个体基因（Lamarckian 回写）。

        论文 §5(4) 的约束处理在 ``build_solution`` 内对 assignment 做
        ``_prune`` / ``_repair_range`` / ``_choose_end_nests``，得到的
        ``solution`` 才是真正被评价的可行解。但个体的基因（``cost_vector``）
        仍是 ``inverse_phi`` 由试验向量译出的**未修复**版本，于是下一世代的
        差分变异围绕未修复空间展开（进化搜的是未修复解）。

        本函数把 ``solution`` 中可解码的两条通道回写到获胜个体的基因：

          * 起始基因（位置 ``[0,K)``）：改写为该 UAV 在修复解中的**首任务**，
            使下一轮 ``inverse_phi`` 的贪心起点精确复现修复解的归属；
          * 终止基因（位置 ``[K+M, K+M+K)``）：改写为修复解的**终点机巢**
            （论文 §5(2) 的创新点），使终点机巢真正进入后继的进化搜索压力。

        巡游基因（任务→任务）**保留原样**：本函数只原地改写 start / terminal
        基因的 ``target_id`` 与 ``cost`` 字段，绝不增删任何基因，因此基因长度
        严格保持 ``M+K``，不会改变 DE 的代价矩阵形状——这是 Lamarckian 回写
        在本 SRP 基因契约下能安全落地的关键。

        注意：巡游基因虽不被本函数改写，但 ``assignment`` / ``split_segments``
        在计算修复解与提取航线时仍会读取其 ``target_id``；修复阶段经 ``_prune`` /
        ``_repair_range`` 剔除的任务，其巡游基因会随下一轮 ``inverse_phi`` 解码
        重新出现并被再次剔除，故修复结果中"被剔除任务"的这一部分不会永久写回基因
        （属于有意的保守设计：避免在基因层做脆弱的增删、破坏"每任务恰出现一次"不变式）。

        Args:
            child:        获胜的子代个体（``inverse_phi`` 译出、已被评价）。
            solution:     本次评价用到的修复后 ``AllocationSolution``。
            cost_matrix:  扩展代价矩阵 ``(K+M, M+B)``（与编码同布局）。
            n_uavs/n_targets/n_nests: 维度 K / M / B。

        Returns:
            回写后的新个体；长度不一致或异常时返回 ``None``（调用方回退到原 child）。
        """
        if solution is None:
            return None
        local_task_idx = {tid: i for i, tid in enumerate(self._task_ids)}
        local_uav_idx = {u.id: i for i, u in enumerate(self.participating)}
        local_nest_idx = {n.id: i for i, n in enumerate(self.nests)}

        # 修复解里每架参与 UAV 的首任务 / 末任务 / 终点机巢（局部下标）
        start_of: dict[int, int] = {}
        end_of: dict[int, int] = {}
        last_of: dict[int, int] = {}
        for tour in solution.tours:
            u = local_uav_idx.get(tour.uav_id)
            if u is None:
                continue
            tlocal = [local_task_idx[t] for t in tour.task_ids if t in local_task_idx]
            if tlocal:
                start_of[u] = tlocal[0]
                last_of[u] = tlocal[-1]
            if tour.end_nest_id is not None:
                b = local_nest_idx.get(tour.end_nest_id)
                if b is not None:
                    end_of[u] = b

        K, M, B = n_uavs, n_targets, n_nests
        new_genes: list[Gene] = []
        current: int | None = None
        for g in child.genes:
            if g.uav_id >= 0:
                current = g.uav_id
                u = g.uav_id
                if u in start_of:
                    t0 = start_of[u]
                    new_genes.append(
                        Gene(uav_id=u, target_id=t0, cost=float(cost_matrix[u, t0]))
                    )
                else:
                    new_genes.append(g)
            elif g.uav_id == TOUR_UAV_ID:
                new_genes.append(g)
            elif g.uav_id == TERMINAL_UAV_ID:
                u = current
                if u is not None and u in end_of and B > 0:
                    b = end_of[u]
                    last = last_of.get(u)
                    col = M + b
                    if last is not None and col < cost_matrix.shape[1]:
                        new_genes.append(
                            Gene(
                                uav_id=TERMINAL_UAV_ID,
                                target_id=b,
                                cost=float(cost_matrix[K + last, col]),
                            )
                        )
                        continue
                new_genes.append(g)
            else:
                new_genes.append(g)

        # 长度不变式校验：必须仍为 M+K，否则回退到原 child，保证 DE 矩阵不错位
        if len(new_genes) != len(child.genes):
            return None
        return Individual(genes=new_genes, model_type=child.model_type)


# ---------------------------------------------------------------------------
# 第二阶段主入口
# ---------------------------------------------------------------------------


def run_selective_muas(
    tasks: Sequence[Task],
    uavs: Sequence[UAV],
    nests: Sequence[Nest],
    cost_build: CostMatrixBuildResult,
    *,
    fleet_uavs: Sequence[UAV] | None = None,
    problem_config: MUASProblemConfig | None = None,
    stage_config: MUASStageConfig | None = None,
    solver_config: DMDEConfig | None = None,
    nest_offset: int = 1_000_000,
    current_time: float | None = None,
) -> MUASStageResult:
    """执行第二阶段：选择性多机巢 MUAS 求解。

    Args:
        tasks:       候选任务集 ``T_t^sel``（第一阶段输出）。
        uavs:        全部无人机（内部会过滤出 ``U_t^avail``）。
        nests:       机巢列表。
        cost_build:  :func:`build_pairwise_costs` 的结果（三维代价 + provider）。
        fleet_uavs:  全部无人机（含非空闲），用于容量占用推导；省略时取 ``uavs``。
        problem_config: MUAS 模型权重。
        stage_config:   编排参数。
        solver_config:  DMDE 超参。
        current_time:   当前时刻；给出则覆盖 ``stage_config.current_time``
                        （等待补偿需要它，滚动层每周期都会变）。

    Returns:
        :class:`MUASStageResult`
    """
    stage_cfg = stage_config or MUASStageConfig()
    if current_time is not None:
        stage_cfg = replace(stage_cfg, current_time=float(current_time))
    problem_cfg = problem_config or MUASProblemConfig()
    fleet = list(fleet_uavs) if fleet_uavs is not None else list(uavs)

    avail = [u for u in uavs if u.is_available]
    task_list = list(tasks)

    problem = SelectiveMUASProblem.from_entities(
        task_list,
        uavs,
        nests=nests,
        pairwise_cost=cost_build.provider.pairwise,
        config=problem_cfg,
        fleet_uavs=fleet,
    )

    # 退化情形：无任务或无可用 UAV → 空解（全部任务保留至下一周期）
    if not task_list or not avail:
        return MUASStageResult(
            solution=problem.empty_solution(),
            participating_uav_ids=[],
            model_type="empty",
            diagnostics={
                "n_tasks": len(task_list),
                "n_uav_avail": len(avail),
                "reason": "no_tasks" if not task_list else "no_available_uav",
            },
        )

    # 参与规划的 UAV（K < M 走 srp 巡游模型）
    row_of_uav = {u.id: i for i, u in enumerate(
        [x for x in uavs if x.is_available]
    )}
    participating = select_participating_uavs(
        avail, len(task_list), stage_cfg,
        uav_target=cost_build.uav_target, row_of_uav=row_of_uav,
    )
    part_rows = [row_of_uav[u.id] for u in participating]

    # DMDE 代价矩阵（论文 §5(2)）：列从 M 扩到 M+B，末 B 列供终止基因
    # 在「末任务 → 机巢」代价行上做匹配，使终点机巢进入进化搜索。
    #   行 0..K-1     UAV → 任务 | UAV → 机巢
    #   行 K..K+M-1   任务 → 任务 | 任务 → 机巢
    n_nests = len(nests) if stage_cfg.end_nest_in_encoding else 0
    uav_nest = getattr(cost_build, "uav_nest", None)
    task_nest = getattr(cost_build, "task_nest", None)
    has_nest_block = (
        n_nests > 0
        and uav_nest is not None
        and task_nest is not None
        and uav_nest.shape[1] == n_nests
        and task_nest.shape[1] == n_nests
    )
    if has_nest_block:
        upper = np.hstack([
            cost_build.uav_target[part_rows, :],
            np.asarray(uav_nest)[part_rows, :],
        ])
        lower = np.hstack([cost_build.task_task, np.asarray(task_nest)])
        cm = np.vstack([upper, lower]).astype(float)
    else:
        # 无机巢或缺少机巢代价块：退化为原布局
        n_nests = 0
        cm = np.vstack([
            cost_build.uav_target[part_rows, :],
            cost_build.task_task,
        ]).astype(float)

    evaluator = MUASAssignmentEvaluator(
        problem=problem,
        participating=participating,
        tasks=task_list,
        nests=nests,
        fleet_uavs=fleet,
        config=stage_cfg,
    )

    # 把编排层的 lamarckian 开关并入求解器配置（TODO §2.7）
    if solver_config is not None:
        solver_cfg = replace(solver_config, lamarckian=stage_cfg.lamarckian)
    else:
        solver_cfg = DMDEConfig(lamarckian=stage_cfg.lamarckian)
    solver = DMDESolver(config=solver_cfg)
    result = solver.solve(
        cost_matrix=cm,
        n_uavs=len(participating),
        n_targets=len(task_list),
        fitness_evaluator=evaluator,
        n_nests=n_nests,
    )

    # 用最优 assignment 重建最终解（端点机巢与选择性剔除在此落地）
    # 终点取**编码给出的结果**（论文 §5(2)），容量超限时由 _choose_end_nests 兜底改派
    best_ends = (result.extra or {}).get("best_end_nests") or None
    final = evaluator.build_solution(
        result.best_assignment, coded_end_nests=best_ends
    )
    problem.evaluate(final)

    model_type = (
        "balanced" if len(participating) == len(task_list)
        else "srp" if len(participating) < len(task_list)
        else "overloaded"
    )

    return MUASStageResult(
        solution=final,
        participating_uav_ids=[u.id for u in participating],
        model_type=model_type,
        n_evals=evaluator.n_evals,
        cost_history=list(result.cost_history),
        elapsed_seconds=float(result.elapsed_seconds),
        solver_extra=dict(result.extra or {}),
        diagnostics={
            "n_tasks": len(task_list),
            "n_uav_avail": len(avail),
            "n_participating": len(participating),
            "n_nests": n_nests,
            # 终点机巢中有多少来自编码（论文 §5(2) 的可观测证据）
            "n_end_nests_coded": len(best_ends or {}),
            "coded_end_nests": {str(k): v for k, v in (best_ends or {}).items()},
            "n_executed": len(final.executed_task_ids),
            "n_deferred": len(task_list) - len(final.executed_task_ids),
            "f1_reward": final.f1_reward,
            "f2_cost": final.f2_cost,
            "feasible": final.feasible,
            "best_fitness": float(result.best_fitness),
        },
    )
