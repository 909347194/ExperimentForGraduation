# -*- coding: utf-8 -*-
"""stage_runner.py — 第二阶段主入口与枚举参照。

2026-10-27 自 ``muas_stage.py`` 拆出：

- :class:`MUASStageResult`                     第二阶段输出（含诊断量）
- :func:`enumerate_reference_participating`    小 N 近精确枚举参照（exp04 B3）
- :func:`run_selective_muas`                   主入口：选机 → DMDE → 交付后择优
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

import numpy as np

from task_allocation.common.types import AllocationSolution, Nest, Task, UAV
from task_allocation.methods.muas.base import BaseOptimizer
from task_allocation.methods.muas.cost.cost_matrix import CostMatrixBuildResult
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig, DMDESolver
from task_allocation.methods.muas.solvers.evaluator import MUASAssignmentEvaluator
from task_allocation.methods.muas.solvers.local_search import (
    PolishResult,
    polish_assignment,
)
from task_allocation.methods.muas.solvers.participating import (
    select_participating_uavs,
)
from task_allocation.methods.muas.solvers.stage_config import MUASStageConfig


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

# 小 N 近精确枚举参照（exp04 B3）


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

# 第二阶段主入口


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
    solver: BaseOptimizer | None = None,
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
        solver:      对比求解器注入（``task_allocation/baselines/``，
                        exp05_baselines 用）。None = 默认 DMDE（主算法，
                        行为逐位不变）。注入求解器若在 ``extra["solution"]``
                        返回 :class:`AllocationSolution`（预构建解通道，
                        构造式启发式的交付形态），则以之为交付解、不再走
                        assignment 解码修复链与 polish——基线的构造策略
                        即其方法本身；否则退回 assignment 解码通道。
                        两种通道共用同一套 ``problem.evaluate`` 约束评价。

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

    # 把编排层的 lamarckian 开关并入求解器配置
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

    # 求解器装配：solver=None（默认）= DMDE 主算法；注入对比求解器时
    # 直接用之（上方 lamarckian 并入 / memetic 钩子是 DMDE 搜索机制的
    # 一部分，构造式基线不消费——DMDEConfig 仅供 DMDESolver 读取）。
    if solver is not None:
        solver_obj: BaseOptimizer = solver
    else:
        solver_obj = DMDESolver(config=solver_cfg)
    result = solver_obj.solve(
        cost_matrix=cm,
        n_uavs=len(participating),
        n_targets=len(task_list),
        fitness_evaluator=evaluator,
    )

    # 用最优 assignment 重建最终解（终点机巢与选择性剔除在此落地）：
    # 终点由内层最小费用流在容量约束下精确求解（方案 A）
    #
    # 口径说明：``result.best_fitness`` 是**搜索期**适应度——Lamarckian
    # 回写只改写起始基因通道、巡游基因保留未修复原值（有意的保守设计，
    # 见 :meth:`lamarckian_reencode`），故回写后基因解码重建的交付解与
    # 当时被评价的解任务集/顺序/ETA 均可不同，两者适应度本不可比。收敛
    # 曲线（``cost_history``）与 ``best_fitness`` 保持搜索期口径自洽
    # （``cost_history[-1] == best_fitness``）；交付解自身的适应度另记
    # ``final_fitness`` 及成分，供审计用快照权重独立重算对账。
    prebuilt = (result.extra or {}).get("solution")
    # 覆盖优先两段式的终态后处理诊断（2026-10-23）；预构建解通道不走
    # 后处理（基线的构造策略即其方法本身），保持空字典。
    mono_stats: dict[str, int] = {}
    # 交付后成本 polish 诊断（2026-10-24）；预构建解通道不走（基线的
    # 构造策略即其方法本身），保持空字典。
    cost_polish_stats: dict[str, int] = {}
    if isinstance(prebuilt, AllocationSolution):
        # 预构建解通道（构造式基线，exp05_baselines）：extra["solution"] 即
        # 交付解——不走 assignment 解码修复链 / polish / memetic 档案，
        # 基线的构造策略就是其方法本身；约束评价仍统一走 problem.evaluate。
        final = prebuilt
        problem.evaluate(final)
        final_fitness = float(problem.scalar_fitness(final))
        final_n_hard, final_excess_m = problem.hard_stats(final)
        polish = PolishResult(
            best_assignment=[],
            base_fitness=final_fitness,
            best_fitness=final_fitness,
            best_solution=final,
            n_accepted={},
        )
    else:
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

        # ── 覆盖优先两段式的终态后处理（2026-10-23 用户裁定）────────
        # 对交付解（DMDE 原始 / assignment polish / memetic 档案， whichever
        # 胜出）做：单调成本 polish（relocate/调序，四道门）→ 覆盖优先
        # 装载（补回新可行任务）→ 重排/航程闸/换巢 → 覆盖门禁 → 重装配。
        # 采纳判据（覆盖优先字典序）：覆盖不减少（executed ⊇ 旧解）且
        # 硬违约不增；同覆盖时要求适应度严格更优（覆盖增长则优先采纳，
        # 成本让位——「完成是约束、成本是目标」）。仅在 coverage_first
        # 开启时生效（默认路径逐位不变）；预构建解通道不适用（基线的
        # 构造策略即其方法本身）。
        if stage_cfg.coverage_first:
            cand, mono_stats = evaluator.post_polish(final)
            problem.evaluate(cand)
            cand_fitness = float(problem.scalar_fitness(cand))
            cand_n_hard, cand_excess = problem.hard_stats(cand)
            old_exec = set(final.executed_task_ids)
            new_exec = set(cand.executed_task_ids)
            if new_exec >= old_exec and cand_n_hard <= final_n_hard and (
                len(new_exec) > len(old_exec)
                or cand_fitness < final_fitness - stage_cfg.monotone_polish_eps
            ):
                final = cand
                final_fitness = cand_fitness
                final_n_hard, final_excess_m = cand_n_hard, cand_excess
            else:
                mono_stats = dict(mono_stats)
                mono_stats["rejected"] = 1

        # ── 交付后成本 polish（2026-10-24 用户裁定，诊断报告 §9 排序 1+2）
        # 对最终交付解（DMDE 原始 / assignment polish / memetic 档案 /
        # coverage_first 后处理，whichever 胜出）做「跨机改派（归属）+
        # 组内 2-opt/Or-opt（顺序）」纯成本局部搜索。与解码期
        # reorder_range_optimize（最近邻，A/B 证伪）的关键区别：作用于
        # 交付解不干扰 DE 搜索；只接受严格改进故不可能更差；走廉价原语
        # 不过完整修复链。采纳判据与 coverage_first 终态后处理同构：
        # 执行集不减少、硬违约不增、交付航程严格下降。
        # 预构建解通道（构造式基线）不适用——基线的构造策略即其方法本身。
        if stage_cfg.cost_polish_enabled:
            path_before_cp = float(problem.path_cost(final))
            cand, cost_polish_stats = evaluator.post_cost_polish(final)
            cost_polish_stats["path_before_m"] = round(path_before_cp, 3)
            # 诊断口径：零接受/被拒时 path_after 恒等于 path_before（旧代码
            # 漏初始化，落盘 0.0 会被误读成「航程被清零」——2026-10-24 修复）。
            cost_polish_stats.setdefault("path_after_m", round(path_before_cp, 3))
            cost_polish_stats.setdefault("path_gain_m", 0.0)
            if int(cost_polish_stats.get("n_accepted", 0)) > 0:
                problem.evaluate(cand)
                cand_fitness = float(problem.scalar_fitness(cand))
                cand_n_hard, cand_excess = problem.hard_stats(cand)
                cand_path = float(problem.path_cost(cand))
                old_exec = set(final.executed_task_ids)
                new_exec = set(cand.executed_task_ids)
                if (
                    new_exec >= old_exec
                    and cand_n_hard <= final_n_hard
                    and cand_excess <= final_excess_m + 1e-9
                    and cand_path < path_before_cp - stage_cfg.cost_polish_eps
                ):
                    final = cand
                    final_fitness = cand_fitness
                    final_n_hard, final_excess_m = cand_n_hard, cand_excess
                    cost_polish_stats["path_after_m"] = round(cand_path, 3)
                    cost_polish_stats["path_gain_m"] = round(
                        path_before_cp - cand_path, 3
                    )
                else:
                    cost_polish_stats = dict(cost_polish_stats)
                    cost_polish_stats["rejected"] = 1

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
            # 覆盖门禁分账（2026-10-22）：gate_stats 与 S 内漏装名单。
            #   n_skip = voluntary_skip（能装没装；gate 修复下恒 0）
            #   n_shortfall = 一阶检验能力不足（允许不交付）
            "gate_stats": dict(final.meta.get("gate_stats") or {}),
            "deferred_ids": sorted(
                t.id for t in task_list
                if t.id not in set(final.executed_task_ids)
            ),
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
            # 覆盖优先两段式诊断（2026-10-23）：coverage_first 开启时
            # 记录单调成本 polish 的移动数与覆盖补装数；rejected=1 表示
            # 后处理候选未达采纳判据（覆盖减少/硬违约增/适应度未改进），
            # 交付解保持原样。关闭时全 0（逐位等于旧口径）。
            "coverage_first": bool(stage_cfg.coverage_first),
            "monotone_polish_relocate": int(mono_stats.get("n_relocate", 0)),
            "monotone_polish_position": int(mono_stats.get("n_position", 0)),
            "monotone_polish_rounds": int(mono_stats.get("n_rounds", 0)),
            "monotone_polish_converged": bool(mono_stats.get("converged", 0)),
            "monotone_polish_reloaded": int(mono_stats.get("n_reloaded", 0)),
            "monotone_polish_rejected": int(mono_stats.get("rejected", 0)),
            # 交付后成本 polish 诊断（2026-10-24，组内 2-opt/Or-opt）。
            # enabled=False 或预构建解通道 ⇒ 全 0（逐位等于旧口径）；
            # rejected=1 表示候选未达采纳判据（执行集减少/硬违约增/
            # 航程未严格下降），交付解保持原样。path_gain_m = 采纳时
            # 交付航程的绝对下降量（米）。
            "cost_polish_enabled": bool(stage_cfg.cost_polish_enabled),
            "cost_polish_rounds": int(cost_polish_stats.get("n_rounds", 0)),
            "cost_polish_n_2opt": int(cost_polish_stats.get("n_2opt", 0)),
            "cost_polish_n_oropt": int(cost_polish_stats.get("n_oropt", 0)),
            # 跨机改派诊断（2026-10-24，归属维度）：n_relocate = 采纳的
            # 单任务跨机迁移数；blocked_range / blocked_late = 几何上更
            # 便宜但分别被航程门 / 迟到门拦死的改派数——两者均 >0 而
            # n_relocate=0 表示「有余量、被约束堵住」（搜索期/约束口径
            # 问题），全 0 表示「归属维度亦已局部最优」。
            "cost_polish_n_relocate": int(cost_polish_stats.get("n_relocate", 0)),
            "cost_polish_relocate_blocked_range": int(
                cost_polish_stats.get("blocked_range", 0)
            ),
            "cost_polish_relocate_blocked_late": int(
                cost_polish_stats.get("blocked_late", 0)
            ),
            "cost_polish_converged": bool(cost_polish_stats.get("converged", 0)),
            "cost_polish_rejected": int(cost_polish_stats.get("rejected", 0)),
            "cost_polish_path_before_m": float(
                cost_polish_stats.get("path_before_m", 0.0)
            ),
            "cost_polish_path_after_m": float(
                cost_polish_stats.get("path_after_m", 0.0)
            ),
            "cost_polish_path_gain_m": float(
                cost_polish_stats.get("path_gain_m", 0.0)
            ),
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

