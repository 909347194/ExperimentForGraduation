# -*- coding: utf-8 -*-
"""dmde_solver.py — 离散映射差分求解器（算法 3.1）

职责：
    实现 DMDE 算法的完整求解流程，串联基因编码、正向映射、
    差分进化、反映射、评估选择、灭绝重启各环节。

对应论文：
    算法 3.1: 基于离散映射差分进化的协同目标分配算法。
    第 3 章 3.4 节完整流程。

算法流程（算法 3.1 伪代码）：
    01-06: 初始化种群（encoder 生成离散三元组基因）
    07-32: DMDE 进化迭代
        08-09:  随机选择差分个体
        10:     计算动态交叉率 CR (公式 3-9)
        11-16:  混合差分策略 (公式 3-10) → 临时代价值向量
        17-26:  反映射 (规则 3.4/3.5/3.6) → 可行子代个体
        27:     评估适应度
        28-30:  贪婪选择（子代优于父代则替换）
    灭绝操作: GMR (公式 3-12) 判断是否触发种群重置
"""

from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ..base.base_optimizer import BaseOptimizer, SolverResult
from ..compare_spec import CandidateFeatures, extract_features
from ..representation.encoder import PopulationEncoder, Individual
from ..representation.inverse_mapper import inverse_phi
from ..operators.crossover import dynamic_crossover_rate
from ..operators.mutation import mutate_population
from ..operators.extinction import should_extinct, apply_extinction


class _EvalResult:
    """最小化评估结果载体（供 Lamarckian 回写取用被评价的解）。

    仅持有 ``fitness`` 与 ``solution`` 两个字段，避免与评估器自身的
    ``EvalResult`` 强耦合。"""

    __slots__ = ("fitness", "solution")

    def __init__(self, fitness: float, solution: Any) -> None:
        self.fitness = fitness
        self.solution = solution


# ---------------------------------------------------------------------------
# Lamarckian 回写回退计数（P0：原 ``except: pass`` 静默回退 → 计数 + 日志）
# ---------------------------------------------------------------------------
# 纯观测：只累加整数 + stderr 警告（沿用「回退永不静默」惯例），
# 不改变回退行为本身。
_LAMARCK_FALLBACKS: dict[str, int] = {"count": 0}


def _geno_id(ind: Any) -> str:
    """候选基因的稳定身份散列（P2-a 四元组 g_raw 项）。

    身份 = **基因三元组序列** ``(uav_id, target_id)``（基因型本身），
    不是 ``cost_vector``——后者只编码代价值、丢弃归属信息，不同
    assignment 可能碰撞出同一向量。纯观测，不影响控制流；
    取不到 genes 时退回 cost_vector，再不行返回 ``"na"``（不抛异常，
    保证台账插桩永不破坏求解）。
    """
    try:
        genes = getattr(ind, "genes", None)
        if genes:
            h = hashlib.sha1()
            for g in genes:
                h.update(f"{int(g.uav_id)}:{int(g.target_id)};".encode())
            return h.hexdigest()[:12]
        cv = np.ascontiguousarray(ind.cost_vector, dtype=np.float64)
        return hashlib.sha1(cv.tobytes()).hexdigest()[:12]
    except Exception:
        return "na"


def lamarckian_fallback_stats() -> dict[str, int]:
    """Lamarckian 回写异常回退计数（诊断用）。"""
    return dict(_LAMARCK_FALLBACKS)


def reset_lamarckian_fallback_stats() -> None:
    _LAMARCK_FALLBACKS["count"] = 0


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class DMDEConfig:
    """DMDE 求解器配置。

    Attributes:
        pop_size:       种群大小。
        max_generations: 最大迭代代数。
        zeta:           动态交叉率曲率指数 ζ（默认 3，论文取值）。
        delta:          可选的灭绝门限 δ（默认 ``None``，即论文公式 3-12 原式
                        GMR = 1 - CR，不加门限）。
        alpha:          时间代价缩放因子 α。
        beta:           约束违背惩罚缩放因子 β。
        seed:           随机种子（可选）。
        verbose:        是否输出迭代日志。
        log_interval:   日志输出间隔（代数）。
    """

    pop_size: int = 50
    max_generations: int = 1000
    zeta: int = 3
    delta: float | None = None
    alpha: float = 2.5
    beta: float = 1.5
    seed: int | None = None
    verbose: bool = False
    log_interval: int = 100
    # Lamarckian 回写（**P2-a 起废弃为哨兵**，定稿 §9.1）：获胜个体在评价中
    # 被修复（_prune / _repair_range）后，把修复结果写回其基因，使后继代际的
    # 差分变异围绕修复后的可行子空间展开，而非未修复空间。依赖评估器提供的
    # ``lamarckian_reencode`` 方法；缺失或失败时回退到原行为（计数 + 警告）。
    # ── 迁移口径（定稿 §9 第 1 项拍板）────────────────────────────
    # 回写语义由 ``repair_replacement``（0.0–1.0 连续比例）承担：
    #   · ``repair_replacement`` 显式设置 → 用它（默认拍板值 0 = 纯 Baldwin）；
    #   · 否则 ``lamarckian=True`` ≡ 1.0、``False`` ≡ 0.0（旧 config 兼容）；
    #   · 两者都未设 → 0.0（P2 方向性默认翻转：master 自 P2 起纯 Baldwin；
    #     复现 method-phase1 数值须 checkout tag，或显式 lamarckian=True /
    #     repair_replacement=1.0——注意 P2-a 的 fitness 重算修复使对照档
    #     与 method-phase1 非逐位一致，属预期内 bug 修复）。
    # 比例取 0/1 不消耗 RNG（逐位可复现）；(0,1) 每次替换事件用 rng 抽签。
    lamarckian: bool | None = None
    repair_replacement: float | None = None
    # 代际回调（memetic M1 精英档案）：每代收敛记录后调用
    # ``gen_callback(gen, best_individual)``，供进化循环内对当代精英做
    # 解码后局部搜索（``run_selective_muas`` 的 M1 档案借此落地）。
    # 默认 ``None`` ⇒ 纯 DMDE 行为逐位不变、零开销。回调一旦抛异常即被
    # 吞并停用（stderr 警告一次，不静默）——memetic 钩子不得破坏纯 DMDE
    # 的数值确定性。
    gen_callback: Callable[[int, Any], None] | None = None
    # 代际种群方差回调（M2-1 A/B 多样性监控，设计稿 §3.4）：每代选择结束后调用
    # ``population_callback(gen, cost_vectors, best_idx)``，``cost_vectors`` 为
    # 整代个体的 gene-space 代价向量矩阵 ``(pop_size, K+N)``（含 Lamarckian
    # 写回后的当代基因），供记录 cost_vector 种群方差、判断写回是否加速同质化。
    # 默认 ``None`` ⇒ 纯 DMDE 行为逐位不变、零开销。回调一旦抛异常即被吞并停用
    # （stderr 警告一次，不静默），且独立标记、绝不连坐停用 ``gen_callback``
    # （memetic 钩子是生产行为，不得被诊断钩子连坐）。
    population_callback: Callable[[int, np.ndarray, int], None] | None = None
    # P1 CompareSpec（定稿 §6 要素 2）：DE 替换的统一比较算子。
    # None / mode="penalty" = 现行硬贪婪（逐位不变）；deb/epsilon/lagaros 为
    # A/B 臂（构造与参数见 compare_spec.CompareSpec，只作用于内部选择标量，
    # 问题目标 f 不动）。DE 变异/交叉/反映射一律不由此开关影响。
    compare_spec: Any = None

    def __post_init__(self) -> None:
        # P2-a：解析有效回写比例（定稿 §9.1 迁移口径）。求解器层无 patrol
        # 字段——patrol 保义在 MUASStageConfig 层解析后经 repair_replacement
        # 透传（stage_runner 转发解析值）。
        if self.repair_replacement is not None:
            r = float(self.repair_replacement)
            if not (0.0 <= r <= 1.0):
                raise ValueError(
                    f"repair_replacement 必须在 [0,1]，当前 {self.repair_replacement!r}"
                )
            self._repair_replacement_eff = r
        elif self.lamarckian is not None:
            self._repair_replacement_eff = 1.0 if self.lamarckian else 0.0
        else:
            self._repair_replacement_eff = 0.0

    @property
    def effective_repair_replacement(self) -> float:
        """有效回写比例（0=纯 Baldwin 默认；1=完整 Lamarck 对照档）。"""
        return float(self._repair_replacement_eff)



# ---------------------------------------------------------------------------
# 主求解器
# ---------------------------------------------------------------------------

class DMDESolver(BaseOptimizer):
    """离散映射差分求解器。

    对应论文算法 3.1 的完整实现。

    使用方式::

        solver = DMDESolver(config=DMDEConfig(pop_size=50, max_generations=1000))
        result = solver.solve(
            cost_matrix=cm,
            n_uavs=8,
            n_targets=8,
            fitness_evaluator=evaluator,
        )
    """

    def __init__(self, config: DMDEConfig | None = None) -> None:
        self._cfg = config or DMDEConfig()

    @property
    def name(self) -> str:
        return "DMDE"

    def solve(
        self,
        cost_matrix: np.ndarray,
        n_uavs: int,
        n_targets: int,
        **kwargs,
    ) -> SolverResult:
        """执行 DMDE 求解。

        Args:
            cost_matrix: 代价矩阵 ``(K+N, N)``（方案 A：列只有任务）。
            n_uavs:      UAV 数 K（实例机数，≤ 可用机数）。
            n_targets:   任务数 N。
            **kwargs:
                fitness_evaluator: 适应度评估器（必须）。
                uavs: UAV 列表（可选，用于评估器）。
                targets: 目标列表（可选，用于评估器）。

        Returns:
            SolverResult 实例。
        """
        cfg = self._cfg
        fitness_evaluator = kwargs.get("fitness_evaluator")

        if fitness_evaluator is None:
            raise ValueError("fitness_evaluator must be provided.")

        # 随机数生成器
        rng = np.random.default_rng(cfg.seed)

        # P1 CompareSpec：非 penalty 模式才启用特征追踪（penalty 逐位不变）
        compare = cfg.compare_spec
        use_compare = (
            compare is not None
            and str(getattr(compare, "mode", "penalty")) != "penalty"
        )
        pop_features: list[Any] = [None] * cfg.pop_size
        f_best_feasible: float | None = None

        # 决策台账（P0 契约层，纯观测）：选择事件 + 代际号。
        # 评估器自带 ledger；无 ledger（如测试替身）时零开销跳过。
        ledger = getattr(fitness_evaluator, "ledger", None)
        if ledger is not None and getattr(ledger, "enabled", False):
            ledger.set_gen(0)

        # 确定模型类型
        if n_uavs == n_targets:
            model_type = "balanced"
        elif n_uavs > n_targets:
            model_type = "overloaded"
        else:
            model_type = "srp"

        # ---- Step 1: 初始化种群 (算法 3.1 行 01-06) ----
        encoder = PopulationEncoder(cost_matrix, n_uavs, n_targets)
        population = encoder.generate(cfg.pop_size, seed=cfg.seed)

        # 评估初始种群
        best_idx = 0
        for i, ind in enumerate(population):
            if use_compare:
                _er0 = self._evaluate_result(
                    ind, fitness_evaluator, cost_matrix, n_uavs=n_uavs
                )
                ind.fitness = _er0.fitness
                pop_features[i] = extract_features(_er0.fitness, _er0.solution)
                if pop_features[i].n_hard == 0:
                    f_best_feasible = (
                        _er0.fitness
                        if f_best_feasible is None
                        else min(f_best_feasible, _er0.fitness)
                    )
            else:
                ind.fitness = self._evaluate(
                    ind, fitness_evaluator, cost_matrix, n_uavs=n_uavs
                )
            if ind.fitness < population[best_idx].fitness:
                best_idx = i

        best_individual = population[best_idx].copy()
        cost_history = [best_individual.fitness]

        # P2-a 方差护栏基线：初始种群（gen 0）的基因型多样性。
        if ledger is not None and getattr(ledger, "enabled", False):
            _cv0 = np.array([ind.cost_vector for ind in population])
            ledger.set_diversity(0, float(np.mean(np.std(_cv0, axis=0))))

        # P2-e ε 调度（Eq.12）初始化：注入 maxFEs，ε(0)=ϕ_θ
        # （θ=γ·Npop 第 θ 大违反量）。仅 epsilon 模式、use_compare 时驱动。
        if use_compare and str(getattr(compare, "mode", "")) == "epsilon":
            compare.set_epsilon_max_fes(cfg.max_generations * cfg.pop_size)
            _viol = sorted(
                (float(pf.violation) for pf in pop_features if pf is not None),
                reverse=True,
            )
            _theta = max(1, int(round(float(compare.epsilon_gamma) * cfg.pop_size)))
            _phi_theta = _viol[_theta - 1] if len(_viol) >= _theta else (_viol[-1] if _viol else 0.0)
            compare.reset_epsilon(_phi_theta)

        # 代际回调保险：钩子一旦抛异常即吞并停用（见下方），此标记保证
        # 「每次 solve 最多警告一次」且不再重试已失败的回调。
        callback_failed = False
        # ``population_callback`` 独立失败标记：绝不复用 ``callback_failed``，
        # 避免诊断钩子连坐停用生产用的 ``gen_callback``（memetic 钩子）。
        pop_callback_failed = False

        t_start = time.time()

        # ---- Step 2: DMDE 进化迭代 (算法 3.1 行 07-32) ----
        for gen in range(1, cfg.max_generations + 1):
            if ledger is not None and getattr(ledger, "enabled", False):
                ledger.set_gen(gen)
            # P2-e ε 调度推进（Eq.12 状态机）：FEs=gen·Npop，rk=可行比例，
            # ϕ_max=种群最大违反量。仅 epsilon 模式驱动；penalty/deb/lagaros
            # 走各自逻辑、ε 恒 0（与 method-phase1 逐位等价）。
            if use_compare and str(getattr(compare, "mode", "")) == "epsilon":
                _fes = gen * cfg.pop_size
                _rk = sum(
                    1 for pf in pop_features if pf is not None and pf.n_hard == 0
                ) / max(1, cfg.pop_size)
                _phi_max = max(
                    (float(pf.violation) for pf in pop_features if pf is not None),
                    default=0.0,
                )
                compare.tick_epsilon(_fes, _rk, _phi_max)
            # 动态交叉率
            cr = dynamic_crossover_rate(gen, cfg.max_generations, cfg.zeta)

            # 提取代价值矩阵
            cost_vectors = np.array([ind.cost_vector for ind in population])
            # P2-a 种群方差护栏数据源（纯观测，定稿 §9.1 闸门③）：
            # mean per-gene std —— 基因型多样性逐代曲线，护栏判定在分析侧。
            if ledger is not None and getattr(ledger, "enabled", False):
                ledger.set_diversity(
                    gen, float(np.mean(np.std(cost_vectors, axis=0)))
                )

            # 混合变异产生试验向量
            trial_vectors = mutate_population(
                cost_vectors, best_idx, gen, cfg.max_generations, cfg.zeta, rng
            )

            # 温度：线性衰减 1.0 → 0.0
            temperature = 1.0 - gen / cfg.max_generations

            # 对每个个体执行反映射 + 评估 + 贪婪选择
            for i in range(cfg.pop_size):
                # 反映射 (公式 3-7, 规则 3.4/3.5/3.6)
                child = inverse_phi(
                    trial_vectors[i], cost_matrix, n_uavs, n_targets, model_type,
                    rng=rng, temperature=temperature,
                )

                # 评估适应度（保留完整 EvalResult，供 Lamarckian 回写使用）
                eval_result = self._evaluate_result(
                    child, fitness_evaluator, cost_matrix, n_uavs=n_uavs
                )
                child.fitness = eval_result.fitness

                # 贪婪选择 (算法 3.1 行 28-30)
                # P1：CompareSpec 统一比较（penalty 模式 = 原硬贪婪逐位不变）
                if use_compare:
                    cand_feat = extract_features(eval_result.fitness, eval_result.solution)
                    if cand_feat.n_hard == 0:
                        f_best_feasible = (
                            child.fitness
                            if f_best_feasible is None
                            else min(f_best_feasible, child.fitness)
                        )
                    if str(getattr(compare, "mode", "")) == "lagaros":
                        compare.set_best_feasible(f_best_feasible)
                    cur_feat = pop_features[i]
                    if cur_feat is None:
                        cur_feat = CandidateFeatures(fitness=float(population[i].fitness))
                    _replace = bool(
                        compare.should_replace(cand_feat, cur_feat, gen=gen)
                    )
                else:
                    cand_feat = None
                    _replace = child.fitness < population[i].fitness
                if _replace:
                    _parent_f = population[i].fitness
                    _inherited = "g_raw"
                    # P2-a 四元组 (g_raw, S_eval_used, inherited_g, ledger_id)：
                    # g_raw 身份 = 候选基因 cost_vector 的稳定散列。
                    _geno = _geno_id(child)
                    # Lamarckian 回写（P2-a：回写比例化，定稿 §9.1）：
                    # 把修复后的解写回获胜个体基因，使下一代差分变异围绕
                    # 修复后的可行子空间展开。比例 0/1 不消耗 RNG（逐位可
                    # 复现）；(0,1) 每次替换事件用 rng 抽签。
                    _ratio = float(cfg.effective_repair_replacement)
                    _do_wb = (
                        _ratio > 0.0
                        and hasattr(fitness_evaluator, "lamarckian_reencode")
                        and eval_result.solution is not None
                    )
                    if _do_wb and 0.0 < _ratio < 1.0:
                        _do_wb = bool(rng.random() < _ratio)
                    if _do_wb:
                        try:
                            re_encoded = fitness_evaluator.lamarckian_reencode(
                                child,
                                eval_result.solution,
                                cost_matrix,
                                n_uavs,
                                n_targets,
                            )
                            if re_encoded is not None:
                                # P2-a fitness 重算（修双重错配，定稿 §5.1）：
                                # 回写基因必须按**自身解码**重评，不得沿用
                                # S_repair 的适应度直接赋值。
                                _re_eval = self._evaluate_result(
                                    re_encoded, fitness_evaluator, cost_matrix,
                                    n_uavs=n_uavs,
                                )
                                if _re_eval.fitness == child.fitness:
                                    # 表型复核通过（§9.1 闸门①）：重解码
                                    # fitness 与 S_repair 逐位一致 ⇒ 采纳回写。
                                    re_encoded.fitness = _re_eval.fitness
                                    child = re_encoded
                                    _inherited = "g_repaired_reencoded"
                                    if ledger is not None and getattr(ledger, "enabled", False):
                                        ledger.record(
                                            layer="select",
                                            operator="dmde_select",
                                            action="lamarck_writeback",
                                            constraint_id="consistency_contract",
                                            object=f"pop[{i}]",
                                            from_state=f"f_S_repair={_re_eval.fitness!r}",
                                            to_state=f"f_reencoded={_re_eval.fitness!r}",
                                            reason="hard",
                                            delta_f=0.0,
                                            accepted=True,
                                            gate_reason=(
                                                "phenotype_recheck(Q2.1/Q3.2)"
                                                "+fitness_recompute(P2-a)"
                                            ),
                                            selection_object="S_repair",
                                            inherited_object="g_repaired_reencoded",
                                            geno_id=_geno,
                                        )
                                else:
                                    # 表型复核失败：该次回写计失败、保留原基因
                                    # （Baldwin 兜底），差额记台账（不静默）。
                                    _inherited = "g_raw"
                                    if ledger is not None and getattr(ledger, "enabled", False):
                                        ledger.record(
                                            layer="select",
                                            operator="dmde_select",
                                            action="lamarck_writeback",
                                            constraint_id="consistency_contract",
                                            object=f"pop[{i}]",
                                            from_state=f"f_S_repair={child.fitness!r}",
                                            to_state=f"f_reencoded={_re_eval.fitness!r}",
                                            reason="hard",
                                            delta_f=float(child.fitness)
                                            - float(_re_eval.fitness),
                                            accepted=False,
                                            gate_reason="phenotype_recheck_failed(revert_to_g_raw)",
                                            selection_object="S_repair",
                                            inherited_object="g_raw",
                                            geno_id=_geno,
                                        )
                        except Exception as exc:
                            # 任何异常（长度/键缺失等）都回退到原 child，保证 DE 矩阵安全。
                            # P0：原 except: pass 静默回退 → 计数 + 警告（不静默）。
                            _LAMARCK_FALLBACKS["count"] += 1
                            if ledger is not None and getattr(ledger, "enabled", False):
                                ledger.tick("dmde:lamarck_fallback")
                            print(
                                f"[dmde][warn] lamarckian_reencode 异常回退原 child"
                                f"（累计 {_LAMARCK_FALLBACKS['count']} 次）：{exc!r}",
                                file=sys.stderr,
                            )

                    # 台账（P0 纯观测）：选择看见 S_repair（修复后表型的 f），
                    # 遗传传递的是 _inherited（g_raw / g_repaired_reencoded）——
                    # §5 一致性契约的四元组追溯（P2-a 补 geno_id）。
                    if ledger is not None and getattr(ledger, "enabled", False):
                        ledger.record(
                            layer="select",
                            operator="dmde_select",
                            action="replace_parent",
                            constraint_id="objective",
                            object=f"pop[{i}]",
                            from_state=f"f={_parent_f!r}",
                            to_state=f"f={child.fitness!r}",
                            reason="opt",
                            delta_f=float(_parent_f) - float(child.fitness),
                            accepted=True,
                            gate_reason="greedy(child_f<parent_f)",
                            selection_object="S_repair",
                            inherited_object=_inherited,
                            geno_id=_geno,
                        )

                    population[i] = child
                    if use_compare:
                        pop_features[i] = cand_feat
                    if child.fitness < best_individual.fitness:
                        best_individual = child.copy()
                        best_idx = i
                else:
                    if ledger is not None and getattr(ledger, "enabled", False):
                        ledger.record(
                            layer="select",
                            operator="dmde_select",
                            action="keep_parent",
                            constraint_id="objective",
                            object=f"pop[{i}]",
                            from_state=f"f_parent={population[i].fitness!r}",
                            to_state=f"f_child={child.fitness!r}",
                            reason="opt",
                            delta_f=float(population[i].fitness) - float(child.fitness),
                            accepted=False,
                            gate_reason="greedy_child_not_better",
                            selection_object="S_repair",
                            inherited_object="g_parent",
                            geno_id=_geno_id(child),
                        )

            # 代际种群方差回调（M2-1 A/B 多样性监控）：每代选择结束后记录
            # 含 Lamarckian 写回后的当代 gene-space 代价向量矩阵。默认 None
            # ⇒ 零开销、零行为变化；异常即吞并停用、独立标记不连坐 gen_callback。
            if cfg.population_callback is not None and not pop_callback_failed:
                try:
                    _cv = np.array([ind.cost_vector for ind in population])
                    cfg.population_callback(gen, _cv, best_idx)
                except Exception as exc:
                    pop_callback_failed = True
                    print(
                        f"[dmde][warn] population_callback 在第 {gen} 代抛异常，"
                        f"已吞并停用：{exc!r}",
                        file=sys.stderr,
                    )

            # GMR 灭绝判断 (公式 3-12)
            if should_extinct(cr, cfg.delta, rng):
                fitness_arr = np.array([ind.fitness for ind in population])
                new_cost_vectors, survived = apply_extinction(
                    fitness_arr,
                    cost_vectors,
                    best_idx,
                    cost_matrix,
                    n_uavs,
                    n_targets,
                    model_type,
                    rng=rng,
                )
                # 用新代价值重建种群
                for i in range(cfg.pop_size):
                    if i not in survived:
                        population[i] = inverse_phi(
                            new_cost_vectors[i],
                            cost_matrix,
                            n_uavs,
                            n_targets,
                            model_type,
                            rng=rng,
                        )
                        if use_compare:
                            _erx = self._evaluate_result(
                                population[i], fitness_evaluator, cost_matrix,
                                n_uavs=n_uavs,
                            )
                            population[i].fitness = _erx.fitness
                            pop_features[i] = extract_features(
                                _erx.fitness, _erx.solution
                            )
                        else:
                            population[i].fitness = self._evaluate(
                                population[i], fitness_evaluator, cost_matrix, n_uavs=n_uavs
                            )

            # 记录收敛曲线
            cost_history.append(best_individual.fitness)

            # memetic 代际回调（M1 精英档案）：给钩子一次对当代精英下手的
            # 机会。纯 DMDE（gen_callback=None）时零开销、零行为变化；
            # 钩子异常被吞并停用该回调（stderr 警告一次，不静默——
            # 遵循本项目「回退永不静默」惯例），保证 DMDE 数值确定性
            # 不受 memetic 影响。
            if cfg.gen_callback is not None and not callback_failed:
                try:
                    cfg.gen_callback(gen, best_individual)
                except Exception as exc:
                    callback_failed = True
                    print(
                        f"[dmde][warn] gen_callback 在第 {gen} 代抛异常，"
                        f"已吞并停用（纯 DMDE 继续，数值确定性不变）：{exc!r}",
                        file=sys.stderr,
                    )

            # 日志
            if cfg.verbose and gen % cfg.log_interval == 0:
                print(
                    f"  Gen {gen}/{cfg.max_generations}: "
                    f"best_fitness={best_individual.fitness:.2f}, "
                    f"CR={cr:.4f}"
                )

        elapsed = time.time() - t_start

        return SolverResult(
            best_assignment=best_individual.assignment,
            best_fitness=best_individual.fitness,
            cost_history=cost_history,
            total_generations=cfg.max_generations,
            elapsed_seconds=elapsed,
            solver_name=self.name,
            extra={
                "model_type": model_type,
                "pop_size": cfg.pop_size,
                "zeta": cfg.zeta,
                "delta": cfg.delta,
            },
        )

    @staticmethod
    def _evaluate(
        individual: Individual,
        fitness_evaluator: Any,
        cost_matrix: np.ndarray,
        n_uavs: int | None = None,
    ) -> float:
        """评估个体适应度。"""
        assignment = individual.assignment
        if not assignment:
            return 1e12  # 空方案给极大惩罚

        result = fitness_evaluator.evaluate(assignment, cost_matrix, n_uavs=n_uavs)
        return result.fitness

    @staticmethod
    def _evaluate_result(
        individual: Individual,
        fitness_evaluator: Any,
        cost_matrix: np.ndarray,
        n_uavs: int | None = None,
    ) -> "_EvalResult":
        """同 :meth:`_evaluate`，但返回完整结果（含被评价的解），供 Lamarckian 回写。"""
        assignment = individual.assignment
        if not assignment:
            return _EvalResult(fitness=1e12, solution=None)

        result = fitness_evaluator.evaluate(assignment, cost_matrix, n_uavs=n_uavs)
        return _EvalResult(
            fitness=getattr(result, "fitness", 1e12),
            solution=getattr(result, "solution", None),
        )
