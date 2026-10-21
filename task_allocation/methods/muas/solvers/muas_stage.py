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
    分给多架 UAV）。故参与规划的 UAV 数 K 恒小于任务数 N（即 srp 巡游模型），
    其余可用 UAV 本周期保持空闲 —— 这正是论文允许的 ``U_t^exec ⊆ U_t^avail``。
"""

from __future__ import annotations

import math
import itertools
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
from task_allocation.methods.muas.solvers.local_search import (
    DEFAULT_MOVES,
    MOVE_POSITION,
    MOVE_RELOCATE,
    MOVE_SWAP,
    polish_assignment,
)

__all__ = [
    "MUASStageConfig",
    "MUASStageResult",
    "EvalResult",
    "MUASAssignmentEvaluator",
    "select_participating_uavs",
    "run_selective_muas",
    "polish_assignment",
    "enumerate_reference_participating",
]

# 参与 UAV 的选择策略
POLICY_CHEAPEST = "cheapest"     # 按「到最近任务的最小代价」升序取前 K 架
POLICY_FIRST = "first"           # 按登记顺序取前 K 架（便于复现）
POLICY_MARGINAL = "marginal"     # 边际出动决策：覆盖代价边际下降 ≤ 出动代价则跳过该机（非停机）
POLICY_DEMAND = "demand"         # 需求感知：贪心最大覆盖，保证每个选中任务都有可达参与机
POLICY_DEMAND_MARGINAL = "demand_marginal"  # 三层选机：设施选址预选 + 字典序边际精修（exp04 B2）


@dataclass
class MUASStageConfig:
    """第二阶段编排参数（全部可由实验 yaml 覆盖）。"""

    # 参与本周期规划的 UAV 数上限；None 表示自动（保证 K < 任务数以走 srp 模型）
    max_participating_uavs: int | None = None
    participating_policy: str = POLICY_CHEAPEST
    # 参与机数 K 与任务数解耦（修复「K 顶格 N-1 → N-K 恒小、巡游顺序维度塌缩」）：
    #   sortie_budget        本周期出动架次上限 S_t（可由滚动层按起降/充电能力推导）；
    #   participation_cost   每架固定出动代价 γ（起降/能耗/架次占用）；
    #   participating_policy="marginal" 时按「覆盖代价边际下降 ≤ γ 即停」内生定 K。
    # 三者都缺省时退化为旧规则（尽量多派，上界 N-1）。
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
    # 零奖励剔除（2026-10-18 四层适应度配套）：对「ETA ≥ latest + W」的任务
    # （迟到折减 eff=R·max(0,1−L/W) 下 F1 贡献≈0）在 repair 链末轮暂缓
    # （y_j=0）。字典序下经济性剔除（prune_unprofitable）整体关闭——丢正收益
    # 任务必降 F1；而零奖励任务不降 F1，剔除它是严格的适应度改进（省 T2
    # 迟到惩罚），还把机会留给下一周期（可能按时交付）。仅当
    # problem.late_reward_decay_seconds>0 且时间窗约束启用时生效。
    #
    # **默认 false（2026-10-18 消融定稿）**：exp01 s42 实测剔除开启后奖励
    # 产出率 94.4%→91.1%（净亏）——剔除在搜索期对每个候选生效，把「硬塞
    # 必然迟发任务」的候选救回可交付形态，搜索偏向「周期 1 多塞一个但迟到
    # 更深」的盆地；而 W=1800 已单独消灭零奖励交付（7→0），靶病不存在。
    # 开关保留供 exp02（|T|≫K）压力场评估。
    prune_zero_reward: bool = False
    # 剔除判据的裕度：仅当 λ·节省 > 收益 × (1 + margin) 时才剔除
    prune_margin: float = 0.0
    # 等待补偿（论文 §3.2 的 W_i(t)）：任务滞留越久越不容易被剔除，
    # 避免偏远任务连续多周期得不到执行而永久滞留
    prune_wait_bonus: float = 1.0
    wait_scale: float = 3600.0
    # 需求感知参与（policy="demand"）：贪心最大覆盖，使每个选中任务都
    # 有一架「可达」的参与机，而非 cheapest 那样把机堆在任务稠密区、饿死
    # 稀疏任务（北/西角离群任务无近参与机，后期 K 收缩时不可达）。
    # 覆盖判据：UAV u 覆盖任务 t 当且仅当 uav_target[row_u, t] <= 覆盖半径；
    #   覆盖半径 = ``demand_coverage_radius``（显式，米）否则
    #   ``demand_range_factor · remaining_range(u)``（默认 0.5，对应单任务
    #   往返航程 ≤ 剩余航程的保守可达性）。覆盖为零的机不纳入（避免塞无用机）。
    demand_coverage_radius: float | None = None
    demand_range_factor: float = 0.5
    # Fix⑤ 服务能力感知 provisioning（policy="demand" 生效）：最小距离覆盖只保证
    # 「每任务有可达参与机」，但一架机 80km 航程常不足以同时服务它覆盖的多个远任务
    # （覆盖≠能服务），于是远任务在有限航程预算下被留成 y_j=0。开启后在第一阶段
    # 最小覆盖基础上做容量感知补机：把「最近参与机往返代价 > 剩余航程×裕度」的过载
    # 任务逐个剥给最近的空闲机（拉入参与），直到无过载或达 K 上限。直接针对
    # 「K 塌缩→远任务未服务」这一 exp01 残余瓶颈。
    demand_provision_serving: bool = True
    demand_serve_margin: float = 0.95  # 服务可行性裕度（与 selection.feasibility.range_margin 对齐）
    # Fix⑥「可达即执行」的救援池口径。方法论：任务只要能力可达、不违反约束就
    # 应最终被执行，成本最小化是此前提下的次级目标（字典序两阶段）。而「可达」
    # 是**存在量化**（∃ 机队中某方案可服务），不是「给定方案内塞得进」。
    # 此前 _reflow_task 只在 participating 内找闲置机改派，但 demand 参与策略
    # 按最小距离覆盖把 K 压到 2~5 架，且这些机实际要服务多个任务（航程被摊薄），
    # 于是物理可达的任务因「参与集内无闲置机可改派」被判 y_j=0——假暂缓。
    # 例：exp01 的 T10，非参与闲置机 UAV1/6/8 单任务航次 67.3/71.0/76.1km
    # 均 ≤ 80km 航程，却因不在参与集而拿不到救援。
    #   "fleet"（默认）：救援池扩到全可用机队，落地存在量化；
    #   "participating"：历史口径，仅在参与集内改派（消融对照）。
    reflow_rescue_scope: str = "fleet"
    # 解码模式（exp04 B1 对照臂）：
    #   "repair_chain"（默认，生产口径）：现状反应式修复链——
    #     _consolidate → _prune → _repair_range → _choose_end_nests（≤6 轮
    #     不动点）→ _complete_coverage → _reorder_for_deadlines →
    #     _prune_zero_reward → _repair_range → _choose_end_nests →
    #     _repair_range。exp01/exp02 既有结果 bit-exact 不变。
    #   "constructive"：构造式解码（_build_constructive）——把「救援」前置为
    #     主解码：逐任务按基因组顺序做可行性内联插入（优先 DMDE 指派机 →
    #     其它参与机 → 全机队闲置机开单 = 第3层安全网），不可行才 y_j=0；
    #     终点机巢/时间窗为耦合子问题，保留 ≤3 轮小迭代（不承诺单趟）。
    #   三条红线（exp04 notes B1）：cost_vector/assignment 结构必须引导插入
    #   （不得退化成 cost 排序键贪心，否则 DMDE 退化为排列编码 GA）；巢/
    #   时间窗留小迭代；默认关、gated A/B。两种模式共用同一批 helper
    #   （_tour_cost/_range_limit/_window_ok/_choose_end_nests/
    #   _reorder_for_deadlines/_assemble），口径一致。
    decode_mode: str = "repair_chain"
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
    # 解码后局部搜索 polish（2026-10-19）：DMDE 收敛后对 best_assignment
    # 做 relocate / swap / 插入位局部搜索，候选一律过 evaluator.evaluate
    # （与 DMDE 个体同一 build_solution 修复链），仅严格改进才接受。
    # 治「曲线第 3 代躺平」的两个根因：① big-M 阶梯适应度下差分算子对
    # 换机/换序级改善不可见（T9 换机省 27.9km 仅折 2.8 单位 vs 单任务
    # 2.8e5）；② Lamarckian 回写只写起始基因通道致归属冻结。局部搜索
    # 直接在解空间操作，绕过编码—回写冻结构悟。
    # 默认开启（生产口径）；消融对照显式传 False。
    polish_enabled: bool = True
    # 启用的移动类型（顺序即扫描优先级；relocate 结构性收益最大排首）
    polish_moves: tuple[str, ...] = DEFAULT_MOVES
    polish_max_rounds: int = 30     # 无改进即停的轮数上限
    polish_max_evals: int = 2000    # 真实评估数预算（缓存命中不计）
    polish_eps: float = 1e-6        # 严格改进阈值（绝对；吸收浮点求和噪声）
    # 搜索期内精英档案（memetic M1，2026-10-19）：开启后每 memetic_K 代在
    # DE 进化循环内对当前精英跑一次 polish_assignment，维护「best polished」
    # 档案（assignment/fitness/solution）。M1 **不改基因型**（无反映射），
    # DE 群体照常探索结构；档案使命中路由在**搜索期内**即被持续压低，交付解
    # 取「收敛后 polish」与「档案最优」的更优者——把 DMDE 早平后留给收敛再做的
    # 路由改进前移进搜索期。默认关（exp01 非 memetic 基线不动）；exp01 memetic
    # 变体显式开。M2（Lamarckian 反射写回，让路由改进回流群体）见报告展望。
    polish_in_loop: bool = False
    memetic_K: int = 20             # 每 K 代触发一次精英 polish（>=1）
    # 搜索期内精英 polish 的**从紧预算**：强化只需 incremental 改进、不必每步
    # 收敛到局部最优（first-improvement 本就早停），故轮数/评估数远小于收敛后
    # 的终打磨。控住单周期耗时（每 K 代一次、每次封顶，避免 10× 全量 polish）。
    memetic_max_rounds: int = 6
    memetic_max_evals: int = 500

    def __post_init__(self) -> None:
        if self.max_participating_uavs is not None and self.max_participating_uavs < 1:
            raise ValueError("max_participating_uavs 必须 >= 1 或为 None")
        if self.marginal_tour_cap is not None and self.marginal_tour_cap < 1:
            raise ValueError("marginal_tour_cap 必须 >= 1 或为 None（None=按航程自动推导）")
        if self.participating_policy not in (
            POLICY_CHEAPEST, POLICY_FIRST, POLICY_MARGINAL, POLICY_DEMAND,
            POLICY_DEMAND_MARGINAL
        ):
            raise ValueError(f"未知的参与 UAV 选择策略：{self.participating_policy}")
        if self.decode_mode not in ("repair_chain", "constructive"):
            raise ValueError(
                'decode_mode 必须是 "repair_chain" 或 "constructive"，'
                f"当前为 {self.decode_mode!r}"
            )
        if str(self.reflow_rescue_scope).lower() not in ("fleet", "participating"):
            raise ValueError(
                'reflow_rescue_scope 必须是 "fleet" 或 "participating"，'
                f"当前为 {self.reflow_rescue_scope!r}"
            )
        bad = [m for m in self.polish_moves
               if m not in (MOVE_RELOCATE, MOVE_SWAP, MOVE_POSITION)]
        if bad:
            raise ValueError(
                "polish_moves 只能含 relocate/swap/position，"
                f"当前含未知项 {bad!r}"
            )
        if self.polish_max_rounds < 1:
            raise ValueError("polish_max_rounds 必须 >= 1")
        if self.polish_max_evals < 1:
            raise ValueError("polish_max_evals 必须 >= 1")
        if self.polish_eps < 0:
            raise ValueError("polish_eps 不能为负")
        if self.memetic_K < 1:
            raise ValueError("memetic_K 必须 >= 1")
        if self.memetic_max_rounds < 1:
            raise ValueError("memetic_max_rounds 必须 >= 1")
        if self.memetic_max_evals < 1:
            raise ValueError("memetic_max_evals 必须 >= 1")


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

    参与机数 K 的确定**与任务数解耦**，按三层裁剪（K ⊆ 可用机数）：

    1. **SRP 模型不变式**：``K ≤ N-1``（保证一任务一机且允许巡游；
       ``N=1`` 时 ``K=1``）。显式配置同样裁剪到该上界——旧实现只裁 auto
       不裁显式值，``K≥N`` 会走进 balanced/overloaded 路径；
    2. **运营预算**：``sortie_budget``（出动架次上限）与
       ``max_participating_uavs``（硬上限），与 N 无关地压低 K；
    3. **出动经济性**（``participating_policy="marginal"``）：按「该机是几个
       任务的最近机」降序逐个纳入，第 j 架带来的「容量受限覆盖代价」边际下降
       ≤ ``participation_cost`` γ 时**跳过该机继续试下一架**（不是停机——
       gain 非单调，零增益机之后仍可能有正增益机）。

    旧 auto 规则在机队充裕时总是顶格 ``K=N-1``，使 ``N-K`` 塌缩为常数、
    巡游顺序搜索维度名存实亡；解耦后 K 可显著小于 N-1，支撑 N≫K 的
    长航次巡游实例（顺序维度 = N-K 个巡游基因）。

    Args:
        uavs:             可用无人机（``U_t^avail``）。
        n_tasks:          候选任务数 N。
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
    elif config.participating_policy == POLICY_DEMAND:
        # 需求感知：贪心最大覆盖（Fix④）。保证每个选中任务都有一架可达
        # 参与机，避免 cheapest 把机堆在稠密区、饿死离群稀疏任务。
        ordered = _greedy_demand_coverage(
            avail, n_tasks, config, uav_target, row_of_uav, k
        )
    elif config.participating_policy == POLICY_DEMAND_MARGINAL:
        # 三层选机（exp04 B2）：第1层设施选址预选（贪心最大覆盖）+
        # 第2层字典序边际精修。第3层安全网在解码端（reflow fleet /
        # 构造式原生覆盖），不在此处——「不漏可达任务」由解码保证，
        # 选机层只做效率预筛。
        # γ=0（未配出动代价）时预选即终选，保持可复现。
        preselected = _greedy_demand_coverage(
            avail, n_tasks, config, uav_target, row_of_uav, k
        )
        if (
            uav_target is not None
            and len(preselected) > 1
            and float(config.participation_cost) > 0.0
        ):
            return _select_by_lexicographic_marginal(
                preselected, config, uav_target, row_of_uav, n_tasks
            )
        return preselected
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

    # 3) 出动经济性：边际收益递减停机（仅 marginal 策略）
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


def _greedy_demand_coverage(
    avail: Sequence[UAV],
    n_tasks: int,
    config: "MUASStageConfig",
    uav_target: np.ndarray,
    row_of_uav: Mapping[int, int] | None,
    k: int,
) -> list[UAV]:
    """需求感知参与：贪心最大覆盖挑选参与机（Fix④）。

    每架可用 UAV 覆盖「代价矩阵中代价 ≤ 覆盖半径」的选中任务；依次挑选覆盖
    最多**尚未覆盖**任务的 UAV，直到所有选中任务被覆盖或达 K 上限 ``k``。
    覆盖为零的机不纳入（避免塞无用机挤占名额）。

    覆盖半径：``config.demand_coverage_radius``（显式，米）优先；否则
    ``demand_range_factor · remaining_range(u)``（默认 0.5，对应单任务往返
    航程 ≤ 剩余航程的保守可达性）。

    Args:
        avail:       可用 UAV（``U_t^avail``）。
        n_tasks:     选中任务数 N。
        config:      编排参数（含 demand 旋钮）。
        uav_target:  ``(n_rows, n_tasks)`` 代价矩阵，行 = 可用 UAV。
        row_of_uav:  ``{uav_id: 矩阵行号}``。
        k:           参与机数上限（SRP 不变式 + 运营预算裁剪后）。

    Returns:
        参与规划的 UAV 列表（长度 ≤ k，覆盖尽量多的选中任务）。
    """
    def _threshold(u: UAV) -> float:
        if config.demand_coverage_radius is not None:
            return float(config.demand_coverage_radius)
        return max(
            0.0, float(getattr(u, "remaining_range", 0.0) or 0.0)
        ) * float(config.demand_range_factor)

    def _cheapest_cost(u: UAV) -> float:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            return float("inf")
        return float(np.min(uav_target[row]))

    # 每架机的覆盖任务集（列下标）
    cover: dict[int, set[int]] = {}
    n_cols = uav_target.shape[1]
    for u in avail:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            cover[u.id] = set()
            continue
        thr = _threshold(u)
        covered = {
            t for t in range(n_tasks)
            if t < n_cols and np.isfinite(uav_target[row, t]) and uav_target[row, t] <= thr
        }
        cover[u.id] = covered

    picked: list[UAV] = []
    picked_ids: set[int] = set()
    covered_tasks: set[int] = set()
    all_tasks = set(range(n_tasks))
    pool = list(avail)

    while len(picked) < k and covered_tasks < all_tasks and pool:
        # 选覆盖「未覆盖任务」最多者；tie-break：更近（cheapest_cost 小）优先，再按 id
        best: UAV | None = None
        best_gain = 0
        for u in pool:
            gain = len(cover[u.id] - covered_tasks)
            if gain <= 0:
                continue
            # 优先：覆盖更多未覆盖任务；其次：更近（代价小）；再次：id 小
            if best is None or (gain, -_cheapest_cost(u), -u.id) > (
                best_gain, -_cheapest_cost(best), -best.id
            ):
                best, best_gain = u, gain
        if best is None:
            break  # 剩余机都无法覆盖任何未覆盖任务
        picked.append(best)
        picked_ids.add(best.id)
        covered_tasks |= cover[best.id]
        pool = [u for u in pool if u.id not in picked_ids]

    # ── Fix⑤：服务能力感知 provisioning ─────────────────────────────
    # 最小覆盖已保证每任务有「可达」参与机，但一架机的 80km 航程常不足以同时服务
    # 它覆盖的多个远任务（覆盖≠服务）。把过载任务（最近参与机往返代价 > 剩余航程×
    # 裕度）逐个剥给最近的空闲机，拉入参与，直到无过载或达 K 上限。
    if config.demand_provision_serving and n_tasks > 0 and picked:
        margin = float(config.demand_serve_margin)

        def _cost(u: UAV, t: int) -> float:
            row = row_of_uav.get(u.id) if row_of_uav else None
            if row is None or row >= uav_target.shape[0] or t >= n_cols:
                return float("inf")
            v = uav_target[row, t]
            return float(v) if np.isfinite(v) else float("inf")

        def _nearest_picked(t: int) -> UAV:
            return min(picked, key=lambda u: _cost(u, t))

        def _loads() -> dict[int, float]:
            ld: dict[int, float] = {u.id: 0.0 for u in picked}
            for t in range(n_tasks):
                u = _nearest_picked(t)
                c = _cost(u, t)
                if np.isfinite(c):
                    ld[u.id] += 2.0 * c  # 往返代价
            return ld

        # 最多迭代 len(avail) 次：每次至多拉入 1 架机，避免死循环
        for _ in range(len(avail)):
            if len(picked) >= k or not pool:
                break
            ld = _loads()
            # 找最过载的参与机
            over = [
                (u, ld[u.id])
                for u in picked
                if ld[u.id] > max(0.0, float(getattr(u, "remaining_range", 0.0) or 0.0)) * margin
            ]
            if not over:
                break  # 无过载：航程预算已够服务全部选中任务
            u, _ = max(over, key=lambda x: x[1])
            # 剥该机最远的归属任务，拉最近的空闲机来服务它
            my_tasks = [
                t for t in range(n_tasks)
                if np.isfinite(_cost(u, t)) and _nearest_picked(t).id == u.id
            ]
            if not my_tasks:
                break
            far = max(my_tasks, key=lambda t: _cost(u, t))
            nu = min(pool, key=lambda x: _cost(x, far))
            # 剩余机都服务不了该任务（代价 inf）时停止补机：拉入也服务不了，
            # 只会白占 K 名额（覆盖≠能服务，补机必须真的能接手才拉）。
            if not np.isfinite(_cost(nu, far)):
                break
            picked.append(nu)
            picked_ids.add(nu.id)
            pool = [x for x in pool if x.id not in picked_ids]

    return picked


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


def _capacity_served_count(sub: np.ndarray, n_tasks: int, cap: int | None) -> int:
    """容量受限下 ``sub`` 的 j 架机最多能服务几个任务（贪心：便宜的先占位）。

    ``cap=None``：无容量口径——有任一有限代价行即算可服务。
    ``cap=k``：每架至多串 k 个——按 (代价, 行, 列) 升序贪心分配，不可达
    （inf）行不占位。与 :func:`_covering_cost` 同一分配语义，只是把
    「总代价」换成「可服务计数」，供字典序边际的第一维使用。
    """
    if sub.shape[0] == 0 or n_tasks <= 0:
        return 0
    if cap is None:
        return int(sum(1 for t in range(n_tasks) if np.isfinite(sub[:, t]).any()))
    j = sub.shape[0]
    pairs = sorted(
        (float(sub[u, t]), u, t) for u in range(j) for t in range(n_tasks)
    )
    load = [0] * j
    done = [False] * n_tasks
    served = 0
    for c, u, t in pairs:
        if done[t] or load[u] >= cap or not np.isfinite(c):
            continue
        done[t] = True
        load[u] += 1
        served += 1
    return served


def _select_by_lexicographic_marginal(
    ordered: Sequence[UAV],
    config: "MUASStageConfig",
    uav_target: np.ndarray,
    row_of_uav: Mapping[int, int] | None,
    n_tasks: int,
) -> list[UAV]:
    """字典序边际出动决策（exp04 B2 第2层精修）。

    边际贡献按**字典序** ``(Δ可服务任务数, −Δ覆盖代价)`` 度量——与四层
    适应度同构：先看多派一架能多服务几个任务（T1 收益层），同数时才看
    覆盖代价下降（T3 成本层）。跳过边际 ``≤ (0, γ)`` 的机（γ =
    ``participation_cost``，折算进成本维；第一架无条件纳入保非空）。
    gain 非单调 ⇒ 跳过该机**继续试下一架**，不停机（同
    ``_select_by_marginal_takeoff`` 的教训）。

    与纯成本边际（``_select_by_marginal_takeoff``）的区别：可服务数放在
    代价之前，避免「省一点航程但不多服务任何任务」的机被误派——这是
    「承诺该紧」在选机层的落点：多派一架必须换来可服务的增量。
    """
    cap = _resolve_tour_cap(ordered, uav_target, n_tasks, config.marginal_tour_cap)
    gamma = float(config.participation_cost)
    picked: list[UAV] = []
    rows: list[int] = []
    for u in ordered:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            continue
        if not picked:
            picked.append(u)
            rows.append(row)
            continue
        prev = uav_target[rows][:, :n_tasks]
        cur = uav_target[rows + [row]][:, :n_tasks]
        d_served = _capacity_served_count(cur, n_tasks, cap) - _capacity_served_count(
            prev, n_tasks, cap
        )
        d_cost = _covering_cost(prev, n_tasks, cap) - _covering_cost(
            cur, n_tasks, cap
        )
        if d_served > 0 or (d_served == 0 and d_cost > gamma):
            picked.append(u)
            rows.append(row)
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

    # ---- 翻译 ----------------------------------------------------------

    def build_solution(
        self,
        assignment: Iterable[tuple[int, int]],
    ) -> AllocationSolution:
        """局部下标的 assignment → AllocationSolution（含 π / y / z）。

        方案 A：终点机巢 z_ub 不在编码内，由 :meth:`_choose_end_nests`
        经内层最小费用流精确求解。
        """
        # exp04 B1：解码模式分流。默认 repair_chain（生产口径，bit-exact）；
        # constructive 为 gated 对照臂（构造式解码，救援前置为主解码）。
        if str(self.config.decode_mode).lower() == "constructive":
            return self._build_constructive(assignment)
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

        # Fix⑥ 覆盖补全：把「可达却被留在解外」的选中任务补进航次。
        # 注意 _reflow_task 是**被动**救援（仅在航程修复移除任务时触发），
        # 救不了压根没被 DMDE 编入航次的任务——DMDE 的搜索空间锁死在参与集
        # 内，无法自己引入参与集外的可用机（exp01 的 T10 正是此情形）。
        routes, end_nests = self._complete_coverage(routes, end_nests)
        # 航次内访问顺序的时间窗修复（不改任务集，只调先后）
        routes = self._reorder_for_deadlines(routes)
        # 零奖励剔除：ETA ≥ latest+W 的任务 eff≈0——带着飞只赔迟到惩罚
        # （T2），暂缓把机会留给下周期（2026-10-18 四层适应度配套）。
        routes, end_nests = self._prune_zero_reward(routes, end_nests)
        # 重排按「迟到数, 迟到量, 航程」字典序挑顺序，末任务换人会拉长返航腿，
        # 可能把航次推过剩余航程（uav_9 周期 3–6 的 [47,59,40,55] 即由可行序
        # [59,40,55,47] 重排而来，全航程 77 686.91 → 80 027.58 m）。先按当前
        # 终点闸一次；被移除任务由 _reflow_task 改派（全航程口径校验），全部
        # 不可行才证明性暂缓。
        routes, end_nests = self._repair_range(routes, end_nests)
        if routes:
            # 新增航次后终点机巢需重新联合分配（容量约束下精确求解）
            end_nests = self._choose_end_nests(routes)
        # 终点机巢在容量竞争下未必是最近巢，改派会再次拉长航程。交付前最后
        # 闸一次：否则完成闸门按「起点→任务→终点巢」全航程判 range_uav_*
        # 弹回——航程/位置已更新却解绑回池不计完成（废飞），uav_9 周期 3–6
        # 的 16 次弹回全部源于此（超限仅 27.58 m = 0.034%）。
        routes, end_nests = self._repair_range(routes, end_nests)

        return self._assemble(routes, end_nests)

    # -- 航次内重排（时间窗可行性）--------------------------------------
    def _window_enabled(self) -> bool:
        cons = getattr(self.problem.config, "constraints", None)
        return bool(getattr(cons, "time_window", True))

    def _reorder_for_deadlines(
        self, routes: Mapping[int, Sequence[int]]
    ) -> dict[int, list[int]]:
        """在不丢任务的前提下重排每个航次的访问顺序，减少时间窗违约。

        为什么必须有这一步：DMDE 搜索的是「UAV 归属 + 访问顺序」，但解码后的
        修复链（``_consolidate`` / ``_repair_range`` / ``_complete_coverage``）
        会往航次里增删任务，顺序却沿用编码原序 —— 于是「本来得及」的任务被
        排到后面而迟到。统一 DEM 距离口径后该缺陷被放大（exp01 完成率
        95% → 70%）：实测 6 个未完成任务**直飞全部来得及**（余量 638~3291 s），
        零个物理不可达，纯粹是被排在了多任务航次的后段。

        访问顺序不改变任务集，是纯可行性问题；在「可达即执行」的方法论下属于
        必须修复项，航程（成本）只是同分时的次级判据。

        候选集是常数个（不是 n! 枚举，避免拖垮每代解码）：
            原序 / EDD(latest 升序) / earliest 升序 / 松弛量升序 / 最近邻
        判据字典序：``(迟到任务数, 迟到总量, 航程)``，并剔除超出剩余航程的候选。
        """
        if not self._window_enabled():
            return {uid: list(tids) for uid, tids in routes.items()}

        out: dict[int, list[int]] = {}
        for uid, tids in routes.items():
            seq = list(tids)
            if len(seq) <= 1:
                out[uid] = seq
                continue
            out[uid] = self._best_order(uid, seq)
        return out

    def _best_order(self, uav_id: int, tids: Sequence[int]) -> list[int]:
        """为单个航次挑最优访问顺序。"""
        by_id = {tid: self._task_by_id.get(tid) for tid in tids}
        if any(t is None for t in by_id.values()):
            return list(tids)

        uav = self._uav_by_id.get(uav_id)
        budget = float(getattr(uav, "remaining_range", float("inf")) or float("inf"))
        start = (
            (float(uav.x), float(uav.y), float(uav.z)) if uav is not None else (0.0, 0.0, 0.0)
        )

        def latest_of(tid: int) -> float:
            lst = getattr(by_id[tid], "latest", None)
            return float(lst) if lst is not None else float("inf")

        def earliest_of(tid: int) -> float:
            est = getattr(by_id[tid], "earliest", None)
            return float(est) if est is not None else 0.0

        candidates: list[list[int]] = [list(tids)]
        candidates.append(sorted(tids, key=lambda t: (latest_of(t), earliest_of(t), t)))
        candidates.append(sorted(tids, key=lambda t: (earliest_of(t), latest_of(t), t)))
        if any(latest_of(t) != float("inf") for t in tids):
            candidates.append(
                sorted(tids, key=lambda t: (latest_of(t) - earliest_of(t), latest_of(t), t))
            )
        # 最近邻（从 UAV 起点贪心）：航程最短的候选，成本维度的对照
        remaining = list(tids)
        nn: list[int] = []
        cur = start
        while remaining:
            nxt = min(
                remaining,
                key=lambda t: (
                    self.problem.costs.travel(cur, by_id[t].position),
                    t,
                ),
            )
            nn.append(nxt)
            cur = by_id[nxt].position
            remaining.remove(nxt)
        candidates.append(nn)

        def _evaluate(seq: Sequence[int]) -> tuple[int, float, float] | None:
            """返回 ``(迟到数, 迟到量, 航程)``；超剩余航程 → None（不可行）。"""
            n_late = 0
            late_total = 0.0
            length = 0.0
            prev = start
            for pos, tid in enumerate(seq):
                task = by_id[tid]
                length += self.problem.costs.travel(prev, task.position)
                prev = task.position
                if length > budget + 1e-6:
                    return None
                arrive = self._arrival_time_of(uav_id, seq, pos)
                lst = getattr(task, "latest", None)
                if lst is not None and arrive > float(lst) + 1e-6:
                    n_late += 1
                    late_total += arrive - float(lst)
            # 返航腿必须计入航程预算。完成闸门（check_range_constraint →
            # problem.tour_path_cost）与 _repair_range/_tour_cost/_reflow_task
            # 用的都是「起点 → 各任务 → 终点巢」全航程；只查外出链会把
            # 「外出 48.7 km + 返航 31.3 km」的越限航次放行——uav_9 周期 3–6
            # 的 [47,59,40,55]（全航程 80 027.58 m，超满电 27.58 m）正是被
            # 这里放行后漏到交付，16 次弹回全部源于此。
            # 终点机巢此刻尚未定稿（_choose_end_nests 在重排之后才跑），取
            # 最近可用巢作为返航下界——实际终点只会更远或相等，保守。
            length += self._return_leg_cost(seq[-1], None)
            if length > budget + 1e-6:
                return None
            return (n_late, round(late_total, 6), round(length, 6))

        # 早退：原序已零迟到 → 不必重排。绝大多数航次走这条路（每代每体都要
        # 解码一次，不早退会把求解时间从 ~72 s 抬到 ~121 s，见实测）。
        base_key = _evaluate(list(tids))
        if base_key is not None and base_key[0] == 0:
            return list(tids)

        best_seq = list(tids)
        best_key = base_key
        for seq in candidates[1:]:
            key = _evaluate(seq)
            if key is None:
                continue
            if best_key is None or key < best_key:
                best_key = key
                best_seq = list(seq)
        return best_seq

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

    # -- 零奖励剔除（2026-10-18 四层适应度配套）--------------------------

    def _task_latest(self, tid: int) -> float | None:
        """任务的 deadline：优先 ``time_window`` 元组，回退 ``.latest``。

        与判废口径（``coop_constraints._task_window`` /
        ``check_time_window_constraint``）一致，避免两处读取不同的字段。
        """
        task = self._task_by_id.get(tid)
        if task is None:
            return None
        tw = getattr(task, "time_window", None)
        if isinstance(tw, (tuple, list)) and len(tw) == 2 and tw[1] is not None:
            return float(tw[1])
        lst = getattr(task, "latest", None)
        return None if lst is None else float(lst)

    def _prune_zero_reward(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """剔除「必然零奖励」的任务（ETA ≥ latest + W ⇒ eff≈0，y_j=0 暂缓）。

        动机（2026-10-18）：迟到折减 ``eff = R·max(0, 1 − L/W)`` 下，到达
        晚于 ``latest + W`` 的任务对 F1 的贡献为 0，却仍占用航次与航程、
        并计迟到惩罚——经济上等价于废飞，却按「完成」计。字典序下
        ``_prune``（经济性剔除）整体关闭（丢正收益任务必降 F1），而零奖励
        任务**不降 F1**：剔除它是严格的适应度改进（省 T2 迟到惩罚），
        还把机会留给下一周期（可能按时交付）。与「尽可能多且**正确地**
        完成任务」的长程目标一致。

        做法：逐航次按完成闸门同口径模拟 ETA，对第一个零奖励任务移除并
        :meth:`_reflow_task`（``require_window`` + ``full_window``：只接受
        「整条航次全部准时」的落点，不制造新违约；不做换机腾挪——剔除
        场景下改派不成立就应当暂缓）。移除只会让后续任务更早，迭代至
        无零奖励任务即终止（每轮至少永久移除一个）。
        """
        decay = getattr(self.problem.config, "late_reward_decay_seconds", None)
        if not decay or float(decay) <= 0.0:
            return {k: list(v) for k, v in routes.items()}, dict(end_nests)
        if not self.config.prune_zero_reward or not self._window_enabled():
            return {k: list(v) for k, v in routes.items()}, dict(end_nests)
        W = float(decay)
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        # 每轮至少永久移除一个任务，轮数上界 = 任务数 + 4
        for _ in range(len(self._task_ids) + 4):
            changed = False
            for uav_id in sorted(work):
                tids = work[uav_id]
                if not tids:
                    continue
                for pos in range(len(tids)):
                    tid = tids[pos]
                    latest = self._task_latest(tid)
                    if latest is None:
                        continue
                    if self._arrival_time_of(uav_id, tids, pos) < latest + W - 1e-9:
                        continue
                    # 零奖励：移除；改派到「整航次准时」处则保住，否则暂缓
                    tids.pop(pos)
                    self._reflow_task(
                        tid, work, ends,
                        require_window=True, full_window=True, allow_evict=False,
                    )
                    changed = True
                    break
                if changed:
                    break
            if not changed:
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
        - 合并带**结构性航程门**：合并后航次超受体航程预算则不合并，
          从源头杜绝"合并制造不可行、修复再删任务"的吞任务链路。
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
                    # 结构性航程门（可行性不依赖事后修复）：合并后的航次必须
                    # 落入受体航程预算，否则不合并——合并修复不得制造超航程航次，
                    # 否则下游 _repair_range 只能靠删任务擦除，吞掉本可执行的任务。
                    if (
                        new_cost < old_cost - 1e-9
                        and new_cost <= self._range_limit(dst) + 1e-6
                    ):
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

        for uav_id in sorted(work):
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
                tid = work[uav_id].pop(best_pos)
                # 只改派不删任务：先回流到其它可行位置（现有航次 / 闲置机
                # 单任务航次），全部不可行才本轮暂缓（y_j=0，证明性暂缓）。
                self._reflow_task(tid, work, ends)

            if not work[uav_id]:
                ends[uav_id] = None

        return work, ends

    # ---- 构造式解码（exp04 B1；decode_mode="constructive"）----------------

    def _feasible_insert(
        self,
        uav_id: int,
        seq: Sequence[int],
        tid: int,
        require_window: bool = False,
    ) -> list[int] | None:
        """``tid`` 插入 ``seq`` 的最优**可行**位；不可行返回 None。

        位置按插入代价最小选（归属由基因组定、位置可重算——与 repair_chain
        的 ``_reorder_for_deadlines`` 同性质，是 B1 对照臂的语义差异之一）；
        返航腿按最近巢保守估（终点巢此时未定，实际只会更远或相等）。
        ``require_window``：True 时同时校验该位时间窗（救援/补全路径用，
        与 ``_complete_coverage`` 的 ``require_window=True`` 同口径；主插入
        路径默认 False，与 repair_chain「先装后重排」保持 parity）。
        """
        best_pos: int | None = None
        best_cost = float("inf")
        limit = self._range_limit(uav_id)
        for pos in range(len(seq) + 1):
            cand = list(seq[:pos]) + [tid] + list(seq[pos:])
            c = self._tour_cost(uav_id, cand, None)
            if c > limit + 1e-6:
                continue
            if require_window and not self._window_ok(uav_id, cand, pos):
                continue
            if c < best_cost - 1e-12:
                best_cost, best_pos = c, pos
        if best_pos is None:
            return None
        return list(seq[:best_pos]) + [tid] + list(seq[best_pos:])

    def _rescue_task(
        self,
        tid: int,
        routes: dict[int, list[int]],
        exclude: set[int],
        require_window: bool = True,
    ) -> bool:
        """构造式解码的出列任务救援（第3层安全网，Fix⑥ 内化）。

        顺序即优先级：① 插入其它**非空**航次的最优可行位（代价最小）；
        ② 闲置机开单任务航次——先参与集内、再（``reflow_rescue_scope="fleet"``
        时）全可用机队。可达即执行（存在量化），但不得制造新违约；
        全部不可行返回 False（调用方记 y_j=0 证明性暂缓，不谎报完成）。
        与 ``_reflow_task`` 的区别：本函数只做「插入 + 闲置开单」两级
        （换机腾位 Fix⑦ 留给 repair_chain 的 _reflow_task，构造式臂不复制
        该补丁——少一层打补丁正是 B1 要检验的假设）。
        """
        # ① 现有非空航次（参与机；exclude 外的）
        best: tuple[float, int, list[int]] | None = None
        for uid in sorted(routes):
            if uid in exclude or not routes.get(uid):
                continue
            new_seq = self._feasible_insert(
                uid, routes[uid], tid, require_window=require_window
            )
            if new_seq is None:
                continue
            c = self._tour_cost(uid, new_seq, None)
            if best is None or c < best[0] - 1e-12:
                best = (c, uid, new_seq)
        if best is not None:
            routes[best[1]] = best[2]
            return True
        # ② 闲置机开单：参与集优先，其次全机队（fleet 口径）
        part_ids = {u.id for u in self.participating}
        pools: list[Sequence[UAV]] = [self.participating]
        if str(self.config.reflow_rescue_scope).lower() == "fleet":
            extra = [u for u in self.available_uavs if u.id not in part_ids]
            if extra:
                pools.append(extra)
        for pool in pools:
            for u in sorted(pool, key=lambda x: x.id):
                uid = u.id
                if uid in exclude or routes.get(uid):
                    continue
                if not getattr(u, "is_available", True):
                    continue
                if (
                    self._tour_cost(uid, [tid], None)
                    <= self._range_limit(uid) + 1e-6
                    and (
                        not require_window
                        or self._window_ok(uid, [tid], 0)
                    )
                ):
                    routes[uid] = [tid]
                    return True
        return False

    def _build_constructive(
        self, assignment: Iterable[tuple[int, int]]
    ) -> AllocationSolution:
        """构造式解码（exp04 B1，``decode_mode="constructive"``）。

        与 repair_chain 的分工：repair_chain 先按基因组装航次、再反应式修复
        （合并→删→救→重排→删→闸 六道 pass）；本函数把「救援」前置为**主
        解码**，逐任务做可行性内联插入：

        1. 初始航次 = assignment 的逐机顺序——**完整保留 DMDE 结构信号**
           （归属与顺序先验；红线1：不把 cost_vector 当排序键）；
        2. 逐任务重建航次：仅当「插入后航程 ≤ 预算」才留下（位置按代价
           最优，与 repair_chain 的 _reorder 同性质），不可行的任务出列；
        3. 出列任务救援：其它参与机最优可行位 → 全机队闲置机开单（第3层
           安全网）→ 仍不可行 y_j=0 证明性暂缓（不谎报完成）；
        4. 终点机巢（内层 MCF）与时间窗是**耦合子问题**：≤3 轮小迭代
           （选巢 → 超航程则剔「节省最多」任务重救 → 重排 → 再闸）；
           红线2：不承诺单趟。

        与 repair_chain 共用同一批 helper，口径一致；产出航次全部满足
        航程约束，时间窗由 _reorder_for_deadlines 同款处理。
        """
        # 1) 基因组 → 逐机航次（保留 assignment 顺序；一任务一机去重）
        routes_local: dict[int, list[int]] = {}
        seen: set[int] = set()
        for uav_idx, task_idx in assignment:
            uav_idx, task_idx = int(uav_idx), int(task_idx)
            if uav_idx < 0 or uav_idx >= len(self.participating):
                continue
            if task_idx < 0 or task_idx >= len(self.tasks):
                continue
            if task_idx in seen:
                continue
            seen.add(task_idx)
            routes_local.setdefault(uav_idx, []).append(task_idx)
        # assignment 未覆盖的选中任务（极端情形）进待救队列，保「候选即候选」
        # （转任务 ID：滚动场景下 t.id 是跨周期全局计数，≠ self.tasks 下标）
        pending = [
            self._task_ids[i] for i in range(len(self.tasks)) if i not in seen
        ]

        routes: dict[int, list[int]] = {}
        for uav_idx, tids in routes_local.items():
            routes[self.participating[uav_idx].id] = [
                self._task_ids[i] for i in tids
            ]

        deferred: list[int] = []

        # 2) 逐任务重建航次（航程可行性内联；不可行出列救援）
        for uid in sorted(routes):
            kept: list[int] = []
            for tid in list(routes[uid]):
                new_kept = self._feasible_insert(uid, kept, tid)
                if new_kept is None:
                    if not self._rescue_task(tid, routes, exclude={uid}):
                        deferred.append(tid)
                else:
                    kept = new_kept
            routes[uid] = kept

        # 3) 待救任务（未被 assignment 覆盖者）
        for tid in pending:
            if not self._rescue_task(tid, routes, exclude=set()):
                deferred.append(tid)

        # 4) 巢 + 时间窗小迭代（耦合子问题，≤3 轮；不动点即收敛）
        ends: dict[int, int | None] = (
            self._choose_end_nests(routes) if routes else {}
        )
        prev_state: tuple | None = None
        for _ in range(3):
            for uid in sorted(routes):
                if not routes.get(uid):
                    continue
                limit = self._range_limit(uid)
                while (
                    routes[uid]
                    and self._tour_cost(uid, routes[uid], ends.get(uid))
                    > limit + 1e-6
                ):
                    best_pos: int | None = None
                    best_saving = float("-inf")
                    for pos in range(len(routes[uid])):
                        saving = self._removal_saving(
                            uid, routes[uid], pos, ends.get(uid)
                        )
                        if saving is not None and saving > best_saving:
                            best_saving, best_pos = saving, pos
                    if best_pos is None:
                        break
                    tid = routes[uid].pop(best_pos)
                    if not self._rescue_task(tid, routes, exclude={uid}):
                        deferred.append(tid)
            routes = self._reorder_for_deadlines(routes)
            ends = self._choose_end_nests(routes) if routes else {}
            state = (
                tuple((k, tuple(v)) for k, v in sorted(routes.items())),
                tuple(sorted((k, str(v)) for k, v in ends.items())),
            )
            if state == prev_state:
                break
            prev_state = state

        # deferred 全程持有**任务 ID**（steps 2-4 均从 routes 取 tid）；
        # 严禁再经 _task_ids 二次映射——滚动场景 t.id 是全局计数，越界。
        self.last_constructive_deferred = list(dict.fromkeys(deferred))
        return self._assemble(routes, ends)

    def greedy_constructive_routes(
        self, participating_ids: Sequence[int]
    ) -> dict[int, list[int]]:
        """无基因组的构造式航线（exp04 B3 枚举参照的固定解码器）。

        与 :meth:`_build_constructive` 同机制但**不读基因组**：每个选中
        任务在给定参与子集上按「最优可行插入代价」全局贪心
        （facility-location 式），不可行走与构造式解码相同的救援链
        （参与机 → 全机队闲置）。用于枚举参照——把「选哪些机」的质量
        从 DMDE 搜索中隔离出来。

        注意（B3 边界）：本方法度量**选机质量**（固定解码器下的代理适应
        度），不是全流程质量；DMDE 的搜索增量由 B1/B2 主指标体现。
        """
        routes: dict[int, list[int]] = {int(uid): [] for uid in participating_ids}
        for tid in self._task_ids:
            best: tuple[float, int, list[int]] | None = None
            for uid in sorted(routes):
                new_seq = self._feasible_insert(uid, routes[uid], tid)
                if new_seq is None:
                    continue
                c = self._tour_cost(uid, new_seq, None)
                if best is None or c < best[0] - 1e-12:
                    best = (c, uid, new_seq)
            if best is not None:
                routes[best[1]] = best[2]
                continue
            # 安全网：参与集内闲置 → 全机队闲置（fleet 口径）
            self._rescue_task(tid, routes, exclude=set())
        return {uid: seq for uid, seq in routes.items() if seq}

    def _arrival_time_of(
        self,
        uav_id: int,
        tids: Sequence[int],
        pos: int,
    ) -> float:
        """航次中第 ``pos`` 个任务的预计到达时刻。

        与 :func:`coop_constraints.time_window_violation` 同口径：
        **DEM 三维航程** / speed（``costs.travel``），早于 ``earliest`` 则等待，
        离开时累加 ``default_service_time``。

        2026-10-13 统一口径：此前本函数用欧氏直线、代价矩阵用 DEM 地形代价，
        于是「补全时判定来得及」用的长度短于「实际飞」的长度 ⇒ 放进来不及的
        任务，白飞一趟还拉低 F1。现在三处（代价矩阵 / 时间窗惩罚 / 本 ETA）
        共用同一个 DEM 距离函数，判定与实际同源。
        """
        uav = self._uav_by_id.get(uav_id)
        speed = max(float(getattr(uav, "speed", 10.0) or 10.0), 1e-6)
        service = float(getattr(self.problem.config, "default_service_time", 0.0))
        t_cursor = float(self.config.current_time)
        if uav is not None:
            prev = (float(uav.x), float(uav.y), float(uav.z))
        else:
            prev = (0.0, 0.0, 0.0)
        for i, tid in enumerate(tids):
            task = self._task_by_id.get(tid)
            if task is None:
                continue
            t_cursor += (
                self.problem.costs.travel(
                    prev, (float(task.x), float(task.y), float(task.z))
                )
                / speed
            )
            # 与 check_time_window_constraint 严格同口径：先「等待至 earliest」，
            # 再用**等待后**的时刻判 latest（完成闸门正是这样判的）。
            # 若用等待前的原始到达时刻判定，会比闸门宽松 —— 补进实际会违约的
            # 任务，白飞一趟还拉低 F1（与只查航程的过度补全同类，只是更隐蔽）。
            est = getattr(task, "earliest", None)
            if est is not None and t_cursor < est:
                t_cursor = est
            if i == pos:
                return t_cursor
            t_cursor += service
            prev = (float(task.x), float(task.y), float(task.z))
        return t_cursor

    def _window_ok(self, uav_id: int, tids: Sequence[int], pos: int) -> bool:
        """第 ``pos`` 个任务能否在其时间窗内到达（True=不违反约束）。"""
        task = self._task_by_id.get(tids[pos]) if pos < len(tids) else None
        if task is None:
            return True
        latest = getattr(task, "latest", None)
        if latest is None:
            return True
        return self._arrival_time_of(uav_id, tids, pos) <= float(latest) + 1e-6

    def _window_ok_all(self, uav_id: int, tids: Sequence[int]) -> bool:
        """**整条航次**所有任务都满足时间窗（True=无一迟到）。

        与 :meth:`_window_ok` 的差别：后者只校验被插入位置上的任务，
        放行「把同航次后续任务推迟到 deadline 之后」的插入——单点可行、
        全航次违约。Fix⑦ 的换机迁移用它做硬门：可达即执行，但不得把
        已守约的任务搞成迟到（用户方法论：违反约束是硬边界，不是可接受的
        代价；实测 exp01 换机版 95%→85% 正是单点校验放行 3 个新违约所致）。
        """
        for pos in range(len(tids)):
            if not self._window_ok(uav_id, tids, pos):
                return False
        return True

    def _complete_coverage(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """Fix⑥ 覆盖补全：**可达即执行**（存在量化口径）。

        DMDE 只在 ``participating`` 内搜索，参与集外的可用机它无从调度；而
        ``_reflow_task`` 是被动救援（仅在航程修复**移除**任务时触发），对
        「压根没被编入航次」的任务不会调用。两者叠加，就出现物理可达却被留在
        解外的任务（exp01 的 T10：参与集外的 UAV1/6/8 单任务航次 67.3/71.0/
        76.1km 均 ≤ 80km，却从未拿到航次）。

        这里主动补：对每个未分配的选中任务，按 :meth:`_reflow_task` 的顺序
        尝试「插入现有航次的最优可行位置 → 拉入闲置可用机（**全机队**）开单
        任务航次」。只要航程可行就执行——可达性是存在量化，不是方案内判定。

        真不可达的任务不会被硬塞：航程不可达（任何机单任务航次都超限，如
        exp01 的 T19，最小单任务航次 82.4km）或**时间窗不可达**（飞得到但赶
        不到 deadline，如 exp01 的 T10，15m/s 下需 4487s 而 deadline 仅
        2914s）时 _reflow_task 返回 False，保持 y_j=0。这正是「可达即执行、
        但不得违反约束」的边界。
        """
        # 口径分流：legacy 加权和（lexicographic=False）下经济性剔除是**开启**
        # 的，「不划算」的任务由 _prune 主动剔除；此时若再做强制补全，等于把
        # 被判不划算的任务又塞回去，架空消融基线。故仅在字典序（覆盖优先，
        # 即「可达即执行」口径）下补全——这也正是本修复的方法论前提。
        economic_prune = self.config.prune_unprofitable and not getattr(
            self.problem.config, "lexicographic", True
        )
        if economic_prune:
            return {k: list(v) for k, v in routes.items()}, dict(end_nests)

        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        assigned = {tid for tids in work.values() for tid in tids}
        for tid in self._task_ids:
            if tid in assigned:
                continue
            # require_window=True：可达＝航程可行 **且** 时间窗可行。只查航程
            # 会补进「飞得到但赶不到 deadline」的任务，白飞一趟、还拉低 F1。
            if self._reflow_task(tid, work, ends, require_window=True):
                assigned.add(tid)

        return work, ends

    def _range_limit(self, uav_id: int) -> float:
        """UAV 航程预算；未知 UAV 返回 +inf（不设限）。"""
        uav = self._uav_by_id.get(uav_id)
        return float(uav.remaining_range) if uav is not None else float("inf")

    def _reflow_task(
        self,
        task_id: int,
        work: dict[int, list[int]],
        ends: dict[int, int | None],
        require_window: bool = False,
        allow_evict: bool = True,
        depth: int = 0,
        full_window: bool = False,
    ) -> bool:
        """把从超限航次移除的任务改派到其它可行位置；全部不可行才暂缓。

        顺序与字典序评价同构——**可行性优先、代价次之**：

        1. 插入现有航次的最优可行位置（插入后航次 ≤ 该机航程预算）；
        2. 给闲置参与 UAV 开单任务航次（闲置合法，U_exec ⊆ U_avail）；
        2b. Fix⑥：救援池扩到全可用机队（参与集外的闲置机同样可用）；
        2c. Fix⑦ 换机：某架机的单任务航次 ``[task_id]`` 可行，但它已被
            占用 → 尝试把它的现有任务整体迁到别处，空出该机飞 ``task_id``。
        （仅 ``require_window`` 下启用，即主动覆盖补全；被动救援不动别人
        的任务，保持修复链最小干预。）
        3. 均不可行 → 返回 False：任务本轮 y_j=0，属**证明性暂缓**，
           而非旧实现的"顺手删除"（任务物理可行却因邻座航次装不下而被吞）。

        为什么需要 2c：exp01 的 T2 三个周期全部 defer——实测全程只有
        UAV2 能飞它（单航次 47.0 km ≤ 80 km、到达余量 1149 s），其余 9 架
        航程超限或时间窗不够；而 UAV2 每周期都被 DMDE 派了别的航次，
        插入即迟到。只做「插入 + 拉闲置」判不出可达，必须允许换机。

        全程确定性（无随机、按 id 升序遍历），评估缓存安全。
        ``depth``：换机链深度上限（``depth < 2`` 才允许 2c）——被腾出的
        任务迁往别处时自身也可能需要换机（exp01 的 T7：插入任何现有航次
        都迟到、能单飞的机都已有航次、闲置机又太远），一层不够；
        但无限递归会震荡且拖慢 evaluate，故限制为两层。
        ``full_window``：为 True 时插入判断用 :meth:`_window_ok_all`
        （整条航次所有任务不得迟到）。2c 的迁移调用置 True——换机是
        事后强制补全，不看 fitness，只认可行性；若放行「被插任务来得及、
        同航次后续任务被推迟到 deadline 之后」的插入，就把已守约的任务
        搞成违约（exp01 换机版实测 95%→85%，3 个新 tw 违约全源于此），
        违反「约束是硬边界」的方法论。可达即执行，但不得制造新违约。
        """
        # 1) 插入现有航次（含原航次——换个位置能装下就不用暂缓）
        best: tuple[float, int, list[int]] | None = None
        for uid in sorted(work):
            if not work[uid]:
                continue
            limit = self._range_limit(uid)
            base = work[uid]
            for pos in range(len(base) + 1):
                cand = base[:pos] + [task_id] + base[pos:]
                c = self._tour_cost(uid, cand, ends.get(uid))
                if (
                    c <= limit + 1e-6
                    # require_window：主动补全时才校验时间窗。可达＝航程与时间窗
                    # 双重可行；只查航程会把「飞得到但赶不到」的任务也塞进来
                    # （exp01 的 T10：航程 67.3km 可达，但 15m/s 下需 4487s，
                    #  而 deadline 仅 2914s → 白飞一趟还压低 F1）。
                    # full_window：换机迁移用全航次校验（不得把同航次后续任务
                    # 推迟到 deadline 之后——单点可行、全航次违约）。
                    and (not require_window or (
                        self._window_ok_all(uid, cand)
                        if full_window
                        else self._window_ok(uid, cand, pos)
                    ))
                    and (best is None or c < best[0] - 1e-9)
                ):
                    best = (c, uid, cand)
        if best is not None:
            work[best[1]] = best[2]
            return True
        # 2) 闲置 UAV 单任务航次：先在参与集内找（历史行为，优先保持原解结构）
        for u in sorted(self.participating, key=lambda x: x.id):
            if work.get(u.id):
                continue
            if (
                self._tour_cost(u.id, [task_id], ends.get(u.id)) <= self._range_limit(u.id) + 1e-6
                and (not require_window or self._window_ok(u.id, [task_id], 0))
            ):
                work[u.id] = [task_id]
                return True
        # 2b) Fix⑥：救援池扩到全可用机队。参与集是按「最小距离覆盖」挑出的子集，
        # 它之外的闲置机物理上同样可用；只在参与集内找，会让可达任务因「参与集
        # 恰好全满」被判 y_j=0（假暂缓）。把它们纳入救援池，才真正落实
        # 「可达即执行」——可达性是存在量化，不是方案内判定。
        # 注：无需把新机并入 self.participating——Lamarckian 回写对参与集外的
        # UAV 本就安全跳过（``local_uav_idx.get(...) is None``），且终点机巢
        # 选择与装配都按 routes 遍历，新机同样被正确处理。
        if str(self.config.reflow_rescue_scope).lower() == "fleet":
            part_ids = {u.id for u in self.participating}
            for u in sorted(self.available_uavs, key=lambda x: x.id):
                if u.id in part_ids or work.get(u.id):
                    continue
                if not getattr(u, "is_available", True):
                    continue
                if (
                    self._tour_cost(u.id, [task_id], ends.get(u.id)) <= self._range_limit(u.id) + 1e-6
                    and (not require_window or self._window_ok(u.id, [task_id], 0))
                ):
                    work[u.id] = [task_id]
                    return True
        # 2c) Fix⑦ 换机：把「能单飞 task_id 但已被占用」的机腾出来。
        # 仅主动补全（require_window）时启用；迁移允许有限深度递归换机。
        if require_window and allow_evict and depth < 2:
            for uid in sorted(work):
                if not work[uid]:
                    continue
                if not (
                    self._tour_cost(uid, [task_id], ends.get(uid))
                    <= self._range_limit(uid) + 1e-6
                    and self._window_ok(uid, [task_id], 0)
                ):
                    continue
                moved = list(work[uid])
                trial = {k: list(v) for k, v in work.items()}
                trial[uid] = []
                ok = True
                for tid in moved:
                    if not self._reflow_task(
                        tid, trial, ends, require_window=True,
                        allow_evict=True, depth=depth + 1, full_window=True,
                    ):
                        ok = False
                        break
                if ok and not trial.get(uid):
                    # 迁移可能把任务回填到 uid 自己（腾空机的唯一去处），
                    # 此时 uid 并未真正腾空；若无条件覆盖 trial[uid]，
                    # 回填的任务会被直接丢弃（exp01 测试实测暴露）。
                    # 回填＝没腾空 → 本架换机失败，继续试下一架。
                    trial[uid] = [task_id]
                    work.clear()
                    work.update(trial)
                    return True
        return False

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


# ---------------------------------------------------------------------------
# 小 N 近精确枚举参照（exp04 B3）
# ---------------------------------------------------------------------------


def enumerate_reference_participating(
    evaluator: MUASAssignmentEvaluator,
    heuristic_ids: Sequence[int],
    *,
    max_avail: int = 12,
    max_tasks: int = 10,
    max_subsets: int = 5000,
) -> dict[str, Any]:
    """小 N 近精确枚举参照：量化启发式选机离最优子集差多远（exp04 B3）。

    仅在 ``len(avail) <= max_avail`` 且 ``N <= max_tasks`` 时执行
    （组合爆炸防护；子集总数超 ``max_subsets`` 时按确定性步长抽样，
    ``sampled=True`` 随结果报告）。枚举可用机的**同架次预算**子集
    （|S| = len(heuristic_ids)——隔离选机质量；架次预算本身的合理性
    归 B2 的 γ 边际准则，不在此混入），对每个子集用
    :meth:`MUASAssignmentEvaluator.greedy_constructive_routes` 固定解码
    + 终点 MCF + 问题层评价取代理适应度（四层字典序，惩罚型小者优），
    最优子集为参照。启发式子集必入列（防抽样错过）。

    **边界声明（必须随结果一起报告）**：参照系固定解码器为贪心构造
    （不含 DMDE 搜索），度量的是**选机质量**，不是全流程质量；DMDE
    的搜索增量由 B1/B2 主指标体现。

    Returns:
        ``{executed, n_subsets, sampled, heuristic, reference, gap}``；
        规模超限时 ``executed=False`` 且仅含 ``reason`` 键。
        ``gap = f_heuristic − f_reference``（适应度惩罚型，小者优，
        故 gap ≥ 0；gap ≈ 0 表示启发式已达同预算最优）。
    """
    avail = [
        u for u in evaluator.available_uavs if getattr(u, "is_available", True)
    ]
    n_tasks = len(evaluator.tasks)
    heur = sorted(int(x) for x in heuristic_ids)
    k = len(heur)
    if k <= 0 or n_tasks <= 0:
        return {"executed": False, "reason": "degenerate"}
    if len(avail) > int(max_avail) or n_tasks > int(max_tasks):
        return {"executed": False, "reason": "scale"}

    def _proxy(subset_ids: Sequence[int]) -> dict[str, Any]:
        routes = evaluator.greedy_constructive_routes(subset_ids)
        ends = evaluator._choose_end_nests(routes) if routes else {}
        sol = evaluator._assemble(routes, ends)
        evaluator.problem.evaluate(sol)
        return {
            "uav_ids": [int(x) for x in subset_ids],
            "fitness": float(evaluator.problem.scalar_fitness(sol)),
            "n_executed": len(sol.executed_task_ids),
        }

    ids = sorted(int(u.id) for u in avail)
    combos = list(itertools.combinations(ids, k))
    sampled = False
    if len(combos) > int(max_subsets):
        step = math.ceil(len(combos) / int(max_subsets))
        combos = combos[::step]
        sampled = True
    if heur not in combos:
        combos.append(heur)

    best: dict[str, Any] | None = None
    for combo in combos:
        cur = _proxy(combo)
        if best is None or cur["fitness"] < best["fitness"] - 1e-12:
            best = cur
    h = _proxy(heur)
    gap = float(h["fitness"] - best["fitness"]) if best is not None else 0.0
    return {
        "executed": True,
        "n_subsets": len(combos),
        "sampled": sampled,
        "heuristic": h,
        "reference": best,
        "gap": gap,
    }


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
        distance_fn=getattr(cost_build, "distance_fn", None),
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
        # Fix⑥：救援池口径为 "fleet" 时， evaluator 需要知道「参与集之外的
        # 闲置可用机」，才能在航程修复改派时把它们拉进来（可达即执行）。
        available_uavs=avail,
    )

    # 把编排层的 lamarckian 开关并入求解器配置（TODO §2.7）
    if solver_config is not None:
        solver_cfg = replace(solver_config, lamarckian=stage_cfg.lamarckian)
    else:
        solver_cfg = DMDEConfig(lamarckian=stage_cfg.lamarckian)

    # memetic M1 精英档案（2026-10-19）：DE 循环内每 memetic_K 代对当代
    # 精英跑一次 polish_assignment，维护 best-polished 档案。轨迹按代记录
    # （长度 == len(cost_history)，gen0 记 +inf 直到首次 polish），供
    # 「搜索期内路由在降」出图。未开启时全零/空、行为逐位等于非 memetic。
    memetic = {
        "enabled": bool(stage_cfg.polish_in_loop and stage_cfg.polish_enabled),
        "fitness": float("inf"),
        "first_fitness": None,
        "assignment": None,
        "solution": None,
        "n_polish": 0,
        "n_accepted": 0,
        "n_failed": 0,           # 精英打磨抛异常次数（进诊断，供审计对账）
        "trajectory": [float("inf")],   # 对齐 cost_history[0]（gen0 初始）
        "last_improve_gen": None,
    }
    if memetic["enabled"]:
        _mK = max(1, int(stage_cfg.memetic_K))
        _m_eps = float(stage_cfg.polish_eps)
        # 搜索期内强化用从紧预算（rounds/evals 封顶），控单周期耗时；fitness
        # 与终打磨同口径可比，档案择优不受预算影响（只影响单次深度）。
        _mem_cfg = replace(
            stage_cfg,
            polish_max_rounds=int(stage_cfg.memetic_max_rounds),
            polish_max_evals=int(stage_cfg.memetic_max_evals),
        )

        def _memetic_hook(gen: int, best_ind: Any) -> None:
            # 审查加固（2026-10-19）：单次精英打磨失败只计数、不得破坏
            # 「每代必落一档」的对齐不变量（trajectory 长度 ==
            # len(cost_history)），也不得中断后续代的档案维护。
            try:
                assign = list(best_ind.assignment)
                if gen % _mK == 0 and assign:
                    pr = polish_assignment(evaluator, assign, _mem_cfg)
                    memetic["n_polish"] += 1
                    memetic["n_accepted"] += int(pr.n_accepted_total)
                    if memetic["first_fitness"] is None:
                        memetic["first_fitness"] = float(pr.best_fitness)
                    if float(pr.best_fitness) < memetic["fitness"] - _m_eps:
                        memetic["fitness"] = float(pr.best_fitness)
                        memetic["assignment"] = list(pr.best_assignment)
                        memetic["solution"] = pr.best_solution
                        memetic["last_improve_gen"] = int(gen)
            except Exception:
                memetic["n_failed"] += 1
            # 每代都记一档（与 cost_history 逐代对齐；未触发代记当前档案值）
            memetic["trajectory"].append(memetic["fitness"])

        solver_cfg.gen_callback = _memetic_hook

    solver = DMDESolver(config=solver_cfg)
    result = solver.solve(
        cost_matrix=cm,
        n_uavs=len(participating),
        n_targets=len(task_list),
        fitness_evaluator=evaluator,
    )

    # 用最优 assignment 重建最终解（终点机巢与选择性剔除在此落地）：
    # 终点由内层最小费用流在容量约束下精确求解（方案 A）
    #
    # 口径说明（2026-10-18，治「记录 best_fitness 与重算目标差 6.5~13.6」的
    # 最后一环）：``result.best_fitness`` 是**搜索期**适应度——Lamarckian
    # 回写只改写起始基因通道、巡游基因保留未修复原值（有意的保守设计，
    # 见 :meth:`lamarckian_reencode`），故回写后基因解码重建的交付解与
    # 当时被评价的解任务集/顺序/ETA 均可不同，两者适应度本不可比。收敛
    # 曲线（``cost_history``）与 ``best_fitness`` 保持搜索期口径自洽
    # （``cost_history[-1] == best_fitness``）；交付解自身的适应度另记
    # ``final_fitness`` 及成分，供审计用快照权重独立重算对账。
    final = evaluator.build_solution(result.best_assignment)
    problem.evaluate(final)
    final_fitness = float(problem.scalar_fitness(final))
    final_n_hard, final_excess_m = problem.hard_stats(final)

    # 解码后局部搜索 polish（2026-10-19）：对 best_assignment 做
    # relocate / swap / 插入位，候选过**同一** build_solution 修复链
    # （evaluator.evaluate），仅严格改进才接受。搜索期口径不受影响
    # （cost_history / best_fitness 仍是 DMDE 曲线）；交付解若被打磨
    # 得更优则采用之——final_fitness 只降不升。
    polish = polish_assignment(
        evaluator, list(result.best_assignment), stage_cfg
    )
    if (
        polish.n_accepted_total > 0
        and polish.best_fitness < final_fitness - stage_cfg.polish_eps
    ):
        final = polish.best_solution
        problem.evaluate(final)
        final_fitness = float(problem.scalar_fitness(final))
        final_n_hard, final_excess_m = problem.hard_stats(final)

    # memetic M1 档案择优：交付解取「收敛后 polish」与「搜索期内精英档案
    # 最优」的更优者。档案是搜索期内多代精英逐一打磨的 best（都过同一修复
    # 链，fitness 可比），常优于仅对末代精英的收敛后 polish。final_fitness
    # 只降不升；未开启或档案为空时跳过（逐位等于非 memetic）。
    if (
        memetic["solution"] is not None
        and float(memetic["fitness"]) < final_fitness - stage_cfg.polish_eps
    ):
        final = memetic["solution"]
        problem.evaluate(final)
        final_fitness = float(problem.scalar_fitness(final))
        final_n_hard, final_excess_m = problem.hard_stats(final)

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
            # 交付解（回写后基因解码重建 + 修复链）自身的适应度与成分。
            # 与搜索期 best_fitness 口径分离：审计 N 项据此验证
            # 「交付解适应度 = 成分 + 快照权重重算」，曲线终点则验证
            # 「cost_history[-1] == best_fitness」的搜索期自洽。
            "final_fitness": final_fitness,
            "final_f1": float(final.f1_reward),
            "final_path_m": float(problem.path_cost(final)),
            "final_late_s": float(problem.soft_violation(final)),
            "final_bottleneck_s": float(final.meta.get("bottleneck_time", 0.0) or 0.0),
            "final_n_hard": int(final_n_hard),
            "final_excess_m": float(final_excess_m),
            "best_fitness": float(result.best_fitness),
            # 适应度权重快照（problem.evaluate 写入 final.meta）：
            # m / W_hard / w_exc / w_late / λ / w_time / W_decay。
            # 落盘后 final_fitness 及其成分可用同一组权重独立重算，
            # 消「记录 best_fitness 与重算目标差 6.5~13.6」（auto big_m
            # 漂移）——审计脚本 N 项据此交叉验证。
            "fitness_weights": dict(final.meta.get("fitness_weights") or {}),
            # 解码后局部搜索 polish 诊断（2026-10-19）。口径三分：
            # best_fitness/cost_history = DMDE 搜索期；final_* = 交付解；
            # polish_* = 「DMDE 原始交付解 → 打磨后交付解」的改进量
            # （before=DMDE 解适应度，after=打磨后，gain=严格改进量；
            # 0 = 邻域已最优或未开启）。final_fitness 只降不升。
            "polish_enabled": bool(stage_cfg.polish_enabled),
            "polish_rounds": int(polish.n_rounds),
            "polish_candidates": int(polish.n_candidates),
            "polish_evals": int(polish.n_evals),
            "polish_accepted": {k: int(v) for k, v in polish.n_accepted.items()},
            "polish_fitness_before": float(polish.base_fitness),
            "polish_fitness_after": float(polish.best_fitness),
            "polish_gain": float(polish.fitness_gain),
            "polish_converged": bool(polish.converged),
            "polish_budget_hit": bool(polish.budget_hit),
            # memetic M1 精英档案诊断（2026-10-19）。enabled=False 时
            # n_polish/n_accepted=0、final_fitness=None、trajectory=[]，
            # 行为逐位等于非 memetic 基线。trajectory 长度 == len(cost_history)，
            # 与搜索期曲线逐代对齐，供「搜索期内路由在降」出图（+inf 记为 None）。
            # gain = 首次精英 polish → 档案最优的严格改进量。
            "memetic_enabled": bool(memetic["enabled"]),
            "memetic_K": int(stage_cfg.memetic_K),
            "memetic_n_polish": int(memetic["n_polish"]),
            "memetic_n_accepted": int(memetic["n_accepted"]),
            "memetic_n_failed": int(memetic["n_failed"]),
            "memetic_first_fitness": (
                float(memetic["first_fitness"])
                if memetic["first_fitness"] is not None else None
            ),
            "memetic_final_fitness": (
                float(memetic["fitness"])
                if memetic["solution"] is not None else None
            ),
            "memetic_gain": (
                float(memetic["first_fitness"] - memetic["fitness"])
                if (memetic["first_fitness"] is not None
                    and memetic["solution"] is not None)
                else None
            ),
            "memetic_last_improve_gen": memetic["last_improve_gen"],
            "memetic_trajectory": (
                [None if not math.isfinite(x) else float(x)
                 for x in memetic["trajectory"]]
                if memetic["enabled"] else []
            ),
        },
    )
