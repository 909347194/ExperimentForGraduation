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
                ──► z_ub 终点机巢（内层精确分配，见 constraints.solve_end_nest_assignment）
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
    solve_end_nest_assignment,
)
from task_allocation.methods.muas.cost.cost_matrix import CostMatrixBuildResult
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.representation.encoder import Gene, Individual
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
POLICY_MARGINAL = "marginal"     # 边际出动决策：覆盖代价边际下降 ≤ 出动代价则跳过该机（非停机）


@dataclass
class MUASStageConfig:
    """第二阶段编排参数（全部可由实验 yaml 覆盖）。"""

    # 参与本周期规划的 UAV 数上限；None 表示自动（保证 K < 任务数以走 srp 模型）
    max_participating_uavs: int | None = None
    participating_policy: str = POLICY_CHEAPEST
    # 参与机数 K 与任务数解耦（修复「K 顶格 M-1 → M-K 恒小、巡游顺序维度塌缩」）：
    #   sortie_budget        本周期出动架次上限 S_t（可由滚动层按起降/充电能力推导）；
    #   participation_cost   每架固定出动代价 γ（起降/能耗/架次占用）；
    #   participating_policy="marginal" 时按「覆盖代价边际下降 ≤ γ 即停」内生定 K。
    # 三者都缺省时退化为旧规则（尽量多派，上界 M-1）。
    sortie_budget: int | None = None
    participation_cost: float = 0.0
    # marginal 估计器的单机服务容量：一架机一次出动最多串几个任务。
    #   None（默认）= 按航程自动推导 cap = max(1, ⌊min_u 剩余航程 / 平均单程代价⌋ − 1)
    #     （串 c 个任务的航程 ≈ (c+1)×平均单程，含返航；推导不出时退化为无容量）；
    #   显式整数 = 固定容量（实验/消融用）。
    # 为什么需要它：旧估计器 est(j)=Σ_t min_{u∈前j架} C(u,t) 把每架机当成能
    # 「同时」覆盖全部任务，完全不含航程/时间窗容量——gain 度量的是纯覆盖代价
    # 下降，与「多派一架的真实服务能力」脱节（γ 因此从不咬合）。加容量后未覆盖
    # 任务记往返惩罚，gain 才反映真实边际贡献。详见 _select_by_marginal_takeoff。
    marginal_tour_cap: int | None = None
    # 选择性：剔除「执行代价超过收益」的任务（y_j = 0，保留到下一周期）。
    # 注意：字典序（lexicographic=True，默认）下经济性剔除与"收益优先"目标
    # 冲突（丢正收益任务必降 F1），自动关闭——任务暂缓仅由可行性修复产生；
    # 本开关仅在 legacy 加权和（lexicographic=False）下生效（消融对比口径）。
    prune_unprofitable: bool = True
    prune_max_rounds: int = 8
    # 剔除判据的裕度：仅当 λ·节省 > 收益 × (1 + margin) 时才剔除
    prune_margin: float = 0.0
    # 等待补偿（论文 §3.2 的 W_i(t)）：任务滞留越久越不容易被剔除，
    # 避免偏远任务连续多周期得不到执行而永久滞留
    prune_wait_bonus: float = 1.0
    wait_scale: float = 3600.0
    # 终点机巢决策的求解方式：master 分支恒为方案 A（分层求解）——
    #   DMDE 只搜任务分配与访问顺序，终点机巢由内层子问题
    #   solve_end_nest_assignment 精确求解（容量约束下最优），返航代价经
    #   完整成本评价间接影响进化搜索。
    #   「终点机巢进编码」的联合变体（方案 B，编码优先、精确分配兜底）已从
    #   master 移除，在 pro 分支实现。
    # Lamarckian 回写（TODO §2.7）：把修复后的解（经 _prune / _repair_range /
    # _choose_end_nests）的「起始任务归属」编码回获胜个体的基因，使后继代际的
    # 差分变异围绕**修复后的可行子空间**展开（默认开启）。
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
        if self.marginal_tour_cap is not None and self.marginal_tour_cap < 1:
            raise ValueError("marginal_tour_cap 必须 >= 1 或为 None（None=按航程自动推导）")
        if self.participating_policy not in (POLICY_CHEAPEST, POLICY_FIRST, POLICY_MARGINAL):
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

    参与机数 K 的确定**与任务数解耦**，按三层裁剪：

    1. **SRP 模型不变式**：``K ≤ M-1``（保证一任务一机且允许巡游；
       ``M=1`` 时 ``K=1``）。显式配置同样裁剪到该上界——旧实现只裁 auto
       不裁显式值，``K≥M`` 会走进 balanced/overloaded 路径；
    2. **运营预算**：``sortie_budget``（出动架次上限）与
       ``max_participating_uavs``（硬上限），与 M 无关地压低 K；
    3. **出动经济性**（``participating_policy="marginal"``）：按「该机是几个
       任务的最近机」降序逐个纳入，第 j 架带来的「容量受限覆盖代价」边际下降
       ≤ ``participation_cost`` γ 时**跳过该机继续试下一架**（不是停机——
       gain 非单调，零增益机之后仍可能有正增益机）。

    旧 auto 规则在机队充裕时总是顶格 ``K=M-1``，使 ``M-K`` 塌缩为常数、
    巡游顺序搜索维度名存实亡；解耦后 K 可显著小于 M-1，支撑 M≫K 的
    长航次巡游实例（顺序维度 = M-K 个巡游基因）。

    Args:
        uavs:             可用无人机（``U_t^avail``）。
        n_tasks:          候选任务数 M。
        config:           编排参数（含 K 决策旋钮）。
        uav_target:       ``(n_rows, n_tasks)`` 代价矩阵，供 cheapest/marginal 策略。
        row_of_uav:       ``{uav_id: 矩阵行号}``。

    Returns:
        参与规划的 UAV 列表（长度 K ≥ 1）。
    """
    avail = [u for u in uavs if u.is_available]
    if not avail or n_tasks <= 0:
        return []

    # 1) 上界裁剪：SRP 不变式 + 运营预算
    k_cap = max(1, n_tasks - 1) if n_tasks > 1 else 1
    if config.max_participating_uavs is not None:
        k_cap = min(k_cap, int(config.max_participating_uavs))
    if config.sortie_budget is not None:
        k_cap = min(k_cap, max(0, int(config.sortie_budget)))
    k = max(1, min(k_cap, len(avail)))

    # 2) 排序（marginal 需要代价序；无代价矩阵时退化为登记序）
    if config.participating_policy == POLICY_FIRST or uav_target is None:
        ordered = avail[:k]
    else:
        def cheapest_cost(u: UAV) -> float:
            row = row_of_uav.get(u.id) if row_of_uav else None
            if row is None or row >= uav_target.shape[0]:
                return float("inf")
            return float(np.min(uav_target[row]))

        if config.participating_policy == POLICY_MARGINAL:
            # marginal 的排序键必须与边际贡献对齐：按「该机是几个任务的
            # 最近机」降序。旧键 cheapest_cost = min_t C(u,t) 度量的是「离
            # 我最近那个任务有多近」——一架机可以只因某个任务恰好在旁就排到
            # 前列，却对任何任务都不是最近机（gain≡0），把真正能贡献的机挤到
            # 后面（实测：0/20 argmin 的机排 #2，6/20 的机排 #5）。
            share = _argmin_share(uav_target, n_tasks)

            def marginal_key(u: UAV) -> tuple[int, float, int]:
                row = row_of_uav.get(u.id) if row_of_uav else None
                if row is None or row >= uav_target.shape[0]:
                    return (0, float("inf"), u.id)  # 无代价行 → 排最后
                return (-int(share[row]), cheapest_cost(u), u.id)

            ordered = sorted(avail, key=marginal_key)[:k]
        else:
            ordered = sorted(avail, key=lambda u: (cheapest_cost(u), u.id))[:k]

    # 3) 出动经济性：边际收益递减停机
    if (
        config.participating_policy == POLICY_MARGINAL
        and uav_target is not None
        and len(ordered) > 1
    ):
        return _select_by_marginal_takeoff(
            ordered,
            float(config.participation_cost),
            uav_target,
            row_of_uav,
            n_tasks,
            config.marginal_tour_cap,
        )
    return ordered


def _argmin_share(uav_target: np.ndarray, n_tasks: int) -> np.ndarray:
    """每行（UAV）是几个任务的「最近机」（代价最小的行），长度 = 行数。

    marginal 排序键用：``gain_j > 0`` 当且仅当新机对某个任务比已选机都近，
    即它必须是某任务的 argmin 行。按 argmin 份额降序可把「真有贡献」的机排到
    前面，避免零贡献机占位（详见 ``select_participating_uavs`` 的排序分支）。
    """
    if uav_target.size == 0 or n_tasks <= 0:
        return np.zeros(uav_target.shape[0], dtype=int)
    arg = np.argmin(uav_target[:, :n_tasks], axis=0)
    return np.bincount(arg, minlength=uav_target.shape[0]).astype(int)


def _resolve_tour_cap(
    ordered: Sequence[UAV],
    uav_target: np.ndarray,
    n_tasks: int,
    tour_cap: int | None,
) -> int | None:
    """解析 marginal 估计器的单机服务容量（None = 无容量 = 旧口径）。

    显式配置优先；否则按航程自动推导：串 c 个任务的航程 ≈ (c+1)×平均单程
    （含返航），故 ``cap = max(1, ⌊min_u 剩余航程 / 平均单程代价⌋ − 1)``。
    取机队内最小剩余航程（最弱成员决定有效容量）；推导不出（无航程信息或
    代价矩阵为空）时返回 None，估计器退化为无容量。
    """
    if tour_cap is not None:
        return max(1, int(tour_cap))
    if uav_target.size == 0 or n_tasks <= 0 or not ordered:
        return None
    ranges = [float(getattr(u, "remaining_range", 0.0) or 0.0) for u in ordered]
    rng = min(ranges) if ranges else 0.0
    mean_leg = float(np.mean(uav_target[:, :n_tasks]))
    if rng <= 0.0 or mean_leg <= 0.0:
        return None
    return max(1, int(rng / mean_leg) - 1)


def _covering_cost(sub: np.ndarray, n_tasks: int, cap: int | None) -> float:
    """j 架机（``sub`` 的行）覆盖 ``n_tasks`` 个任务的最小总代价。

    ``cap=None``：退化为无容量口径 ``Σ_t min_u C(u,t)``（每架机可「同时」
    覆盖全部任务）。

    ``cap=k``：每架机至多串 k 个任务——按代价升序贪心分配（便宜的先占位），
    装不下的任务按「需另派机」记往返惩罚 ``2×min_u C(u,t)``。这样估计器才
    反映航程/时间窗约束下的真实服务能力。
    """
    if cap is None:
        return float(np.min(sub[:, :n_tasks], axis=0).sum())
    j = sub.shape[0]
    if j == 0 or n_tasks <= 0:
        return 0.0
    pairs = sorted(
        (float(sub[u, t]), u, t) for u in range(j) for t in range(n_tasks)
    )
    load = [0] * j
    done = [False] * n_tasks
    total = 0.0
    for c, u, t in pairs:
        if done[t] or load[u] >= cap:
            continue
        done[t] = True
        load[u] += 1
        total += c
    for t in range(n_tasks):
        if not done[t]:
            total += float(np.min(sub[:, t])) * 2.0
    return total


def _select_by_marginal_takeoff(
    ordered: Sequence[UAV],
    gamma: float,
    uav_target: np.ndarray,
    row_of_uav: Mapping[int, int] | None,
    n_tasks: int,
    tour_cap: int | None = None,
) -> list[UAV]:
    """边际出动决策：容量受限覆盖代价的边际下降决定出动架次。

    估计器 ``est(j)`` = 「j 架机、每架至多 ``cap`` 个任务」的最小总覆盖代价
    （见 ``_covering_cost``），第 j 架的边际贡献 ``gain_j = est(j-1) − est(j)``。

    **停机规则**：``gain_j ≤ γ`` 时**跳过该机、继续试下一架**（不是 break）。
    依据：``est(j)`` 单调非增只保证 ``gain_j ≥ 0``，并**不**保证 gain 单调——
    一架零增益机（对任何任务都不比已选机近）之后仍可能有正增益机。旧实现用
    break 并在 docstring 里断言 gain「单调非增」，该断言不成立，导致在第一个
    零增益机处过早停机（实测 K 塌到 1~3、完成率 100%→48.7%）。

    **容量**：``tour_cap`` 为单机服务容量（None=按航程自动推导，见
    ``_resolve_tour_cap``）。无容量的旧口径把每架机当成能同时覆盖全部任务，
    gain 与「多派一架的真实收益」脱节，γ 因此从不咬合。

    第一架无条件纳入（保证非空）。
    """
    cap = _resolve_tour_cap(ordered, uav_target, n_tasks, tour_cap)
    picked: list[UAV] = []
    rows: list[int] = []
    prev = 0.0
    for u in ordered:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            continue
        if not picked:
            picked.append(u)
            rows.append(row)
            prev = _covering_cost(uav_target[rows][:, :n_tasks], n_tasks, cap)
            continue
        cur = _covering_cost(uav_target[rows + [row]][:, :n_tasks], n_tasks, cap)
        if prev - cur <= gamma:
            continue  # 零/低增益机：跳过继续，不是停机
        picked.append(u)
        rows.append(row)
        prev = cur
    return picked


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
    ) -> AllocationSolution:
        """局部下标的 assignment → AllocationSolution（含 π / y / z）。

        方案 A：终点机巢 z_ub 不在编码内，由 :meth:`_choose_end_nests`
        经内层最小费用流精确求解。
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

        # 航线与终点机巢必须**联合收敛**后才能定稿，顺序不能颠倒：
        # 只有真正有航次的 UAV 才会起飞让出原机巢名额。若先选巢再做剪枝 /
        # 航程修复，被清空的航次实际并未起飞，让出的名额是虚的 → 落地期超卖。
        end_nests: dict[int, int | None] = {}
        prev_state = None
        # 字典序（收益优先）下经济性剔除与目标冲突（丢正收益任务必降 F1），
        # 自动关闭；任务暂缓仅由可行性修复产生。legacy 加权和保留选择性剔除
        # （论文 §5(3) y_j=0 的消融对比口径）。
        economic_prune = self.config.prune_unprofitable and not getattr(
            self.problem.config, "lexicographic", True
        )
        for _ in range(6):
            # 合并修复：任务少时向更少机合并（闲置合法，U_exec ⊆ U_avail）
            routes, end_nests = self._consolidate(routes, end_nests)
            if economic_prune:
                routes, end_nests = self._prune(routes, end_nests)
            routes, end_nests = self._repair_range(routes, end_nests)
            end_nests = self._choose_end_nests(routes)
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
    ) -> dict[int, int | None]:
        """为有航次的 UAV 选终点机巢（含飞行中也要占名额）。

        方案 A（master 主线）：终点机巢由内层子问题
        :func:`solve_end_nest_assignment` 在末态容量约束下**精确求解**，
        联合考虑所有 UAV 的终点竞争（不是各抢最近巢、先到先得）；返航代价
        进入 F2 完整评价，间接作用于进化方向。

        「终点进编码」的联合变体（编码终点优先、精确分配兜底）已从 master
        移除，在 pro 分支实现。
        """
        if not self.nests:
            return {uid: None for uid in routes}

        # 事件驱动：被标记不可用的机巢不可选为终点（多机巢异巢终止的容错分支）
        excluded = self.excluded_nest_ids
        cand_nests = [n for n in self.nests if n.id not in excluded]
        # 终点机巢可达性约束开关：关闭时改派不再过滤不可达巢
        # （与 problem.check_feasible 的 end_nest_reachability 共用判据）
        check_reach = bool(
            getattr(self.problem.config, "constraints", None)
            and self.problem.config.constraints.end_nest_reachability
        )

        # 内层精确分配（方案 A）：为有航次的 UAV 联合选终点机巢
        last_cost: dict[int, dict[int, float]] = {}
        for uav_id, tids in routes.items():
            if not tids:
                continue
            last_node = tids[-1]
            costs: dict[int, float] = {}
            for nest in cand_nests:
                if not nest.available or nest.capacity <= 0:
                    continue
                c = float(
                    self.problem.costs.cost(
                        last_node, self.problem.costs.nest_key(nest.id)
                    )
                )
                if check_reach and not np.isfinite(c):
                    # 末任务→机巢不可达（地形遮挡 / 禁飞）：排除该候选，
                    # 不纳入容量感知改派的候选集（可达性约束落地）
                    continue
                costs[nest.id] = c
            if costs:
                last_cost[uav_id] = costs

        if not last_cost:
            return {uid: None for uid in routes}

        chosen = solve_end_nest_assignment(
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

    def _consolidate(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """合并修复：任务少时把航次并入更少的机（闲置合法化，解码端表达）。

        背景：编码层「每架参与 UAV 至少一个任务」是 DE 映射的布局不变量
        （cost_vector 的 K 个起始槽位，见 ``_inverse_phi_srp``）；而"任务少时
        少派更省"要求解能表达闲置。本修复在解码端补上该表达（方案原文：
        "在编码**或解码**中增加'不参与本轮任务'的表达方式"）：

        - 整航次迁移：把供体航次的任务逐个插入受体航次的最优位置，
          仅当「起始腿 + 任务链 + 返航腿（近似）」总代价下降时合并；
        - 合并后供体航次置空 → 该 UAV 本轮闲置（U_exec ⊆ U_avail），
          空航次不计返航义务、不起飞让位（与 _repair_range 同语义）；
        - 不删任务（区别于 _prune 的 y_j=0 暂缓），只改派；
        - 航程可行性交由其后的 _repair_range 按剩余航程硬约束处理。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        improved = True
        while improved:
            improved = False
            carriers = [u for u, t in work.items() if t]
            for src in carriers:
                best: tuple[float, int, list[int]] | None = None
                for dst in carriers:
                    if dst == src or not work[dst]:
                        continue
                    merged = self._cheapest_insertion(
                        dst, work[dst], work[src], ends.get(dst)
                    )
                    new_cost = self._tour_cost(dst, merged, ends.get(dst))
                    old_cost = self._tour_cost(
                        dst, work[dst], ends.get(dst)
                    ) + self._tour_cost(src, work[src], ends.get(src))
                    if new_cost < old_cost - 1e-9:
                        gain = old_cost - new_cost
                        if best is None or gain > best[0]:
                            best = (gain, dst, merged)
                if best is not None:
                    _gain, dst, merged = best
                    work[dst] = merged
                    work[src] = []
                    ends[src] = None
                    improved = True
                    break  # 受体已变，重算下一轮
        return work, ends

    def _cheapest_insertion(
        self,
        uav_id: int,
        base: Sequence[int],
        extra: Sequence[int],
        end_nest_id: int | None,
    ) -> list[int]:
        """把 ``extra`` 的任务逐个按最优位置插入 ``base``（航次代价贪心）。"""
        merged = list(base)
        for tid in extra:
            best_pos, best_cost = 0, float("inf")
            for pos in range(len(merged) + 1):
                cand = merged[:pos] + [tid] + merged[pos:]
                c = self._tour_cost(uav_id, cand, end_nest_id)
                if c < best_cost:
                    best_cost, best_pos = c, pos
            merged.insert(best_pos, tid)
        return merged

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

    # ---- Lamarckian 回写（TODO §2.7）----------------------------------

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
            cost_matrix:  代价矩阵 ``(K+M, M)``（与编码同布局）。
            n_uavs/n_targets: 维度 K / M。

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

    # DMDE 代价矩阵（方案 A）：行 = UAV 起点 + 任务，列 = 任务。
    # 终点机巢不进编码（由解码后内层最小费用流 solve_end_nest_assignment
    # 精确求解，返航代价进 F2），故不挂机巢列；「终点进编码」的联合变体
    # （列扩到 M+B、行末 B 列供终止基因匹配）在 pro 分支实现。
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
    )

    # 用最优 assignment 重建最终解（终点机巢与选择性剔除在此落地）：
    # 终点由内层最小费用流在容量约束下精确求解（方案 A）
    final = evaluator.build_solution(result.best_assignment)
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
            # 方案 A：内层最小费用流实际选定的终点机巢
            # （uav_id → nest_id，仅统计有航次的 UAV；空闲机停在原巢不计）。
            # 供 analyze / nest_choice 图交叉校验「选巢由成本驱动」的归因证据。
            "end_nests": {
                str(t.uav_id): t.end_nest_id
                for t in final.tours
                if t.task_ids and t.end_nest_id is not None
            },
            "n_executed": len(final.executed_task_ids),
            "n_deferred": len(task_list) - len(final.executed_task_ids),
            "f1_reward": final.f1_reward,
            "f2_cost": final.f2_cost,
            "feasible": final.feasible,
            "best_fitness": float(result.best_fitness),
        },
    )
