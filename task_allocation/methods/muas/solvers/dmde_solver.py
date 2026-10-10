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

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..base.base_optimizer import BaseOptimizer, SolverResult
from ..representation.encoder import PopulationEncoder, Individual, Gene
from ..representation.inverse_mapper import inverse_phi
from ..operators.crossover import dynamic_crossover_rate
from ..operators.scale_factor import dynamic_scale_factor
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
# 配置
# ---------------------------------------------------------------------------

@dataclass
class DMDEConfig:
    """DMDE 求解器配置。

    Attributes:
        pop_size:       种群大小。
        max_generations: 最大迭代代数。
        zeta:           动态交叉率曲率指数 ζ（默认 3）。
        delta:          灭绝临界值 δ（默认 0.3）。
        alpha:          时间代价缩放因子 α。
        beta:           约束违背惩罚缩放因子 β。
        seed:           随机种子（可选）。
        verbose:        是否输出迭代日志。
        log_interval:   日志输出间隔（代数）。
    """

    pop_size: int = 50
    max_generations: int = 1000
    zeta: int = 3
    delta: float = 0.3
    alpha: float = 2.5
    beta: float = 1.5
    seed: int | None = None
    verbose: bool = False
    log_interval: int = 100
    # Lamarckian 回写（TODO §2.7）：获胜个体在评价中被修复（_prune /
    # _repair_range / 终点机巢）后，把修复结果写回其基因，使后继代际的差分变异
    # 围绕修复后的可行子空间展开，而非未修复空间。依赖评估器提供的
    # ``lamarckian_reencode`` 方法；缺失或失败时静默回退到原行为。
    lamarckian: bool = True


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
            cost_matrix: 代价矩阵。
            n_uavs:      UAV 数量。
            n_targets:   目标数量。
            **kwargs:
                fitness_evaluator: 适应度评估器（必须）。
                uavs: UAV 列表（可选，用于评估器）。
                targets: 目标列表（可选，用于评估器）。
                n_nests: 机巢数量 B（可选）。``>0`` 时终点机巢作为航次终止节点
                    进入编码，与任务一同参与差分进化（论文 §5(1)(2)）。

        Returns:
            SolverResult 实例。
        """
        cfg = self._cfg
        fitness_evaluator = kwargs.get("fitness_evaluator")
        # 结构扩展参数：仅透传给编码 / 反映射，不改动任何进化算子
        n_nests = int(kwargs.get("n_nests", 0) or 0)

        if fitness_evaluator is None:
            raise ValueError("fitness_evaluator must be provided.")

        # 随机数生成器
        rng = np.random.default_rng(cfg.seed)

        # 确定模型类型
        if n_uavs == n_targets:
            model_type = "balanced"
        elif n_uavs > n_targets:
            model_type = "overloaded"
        else:
            model_type = "srp"

        # ---- Step 1: 初始化种群 (算法 3.1 行 01-06) ----
        encoder = PopulationEncoder(cost_matrix, n_uavs, n_targets, n_nests=n_nests)
        population = encoder.generate(cfg.pop_size, seed=cfg.seed)

        # 评估初始种群
        best_idx = 0
        for i, ind in enumerate(population):
            ind.fitness = self._evaluate(ind, fitness_evaluator, cost_matrix, n_uavs=n_uavs)
            if ind.fitness < population[best_idx].fitness:
                best_idx = i

        best_individual = population[best_idx].copy()
        cost_history = [best_individual.fitness]

        t_start = time.time()

        # ---- Step 2: DMDE 进化迭代 (算法 3.1 行 07-32) ----
        for gen in range(1, cfg.max_generations + 1):
            # 动态交叉率
            cr = dynamic_crossover_rate(gen, cfg.max_generations, cfg.zeta)

            # 提取代价值矩阵
            cost_vectors = np.array([ind.cost_vector for ind in population])

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
                    rng=rng, temperature=temperature, n_nests=n_nests,
                )

                # 评估适应度（保留完整 EvalResult，供 Lamarckian 回写使用）
                eval_result = self._evaluate_result(
                    child, fitness_evaluator, cost_matrix, n_uavs=n_uavs
                )
                child.fitness = eval_result.fitness

                # 贪婪选择 (算法 3.1 行 28-30)
                if child.fitness < population[i].fitness:
                    # Lamarckian 回写（TODO §2.7）：把修复后的解写回获胜个体基因，
                    # 使下一代差分变异围绕修复后的可行子空间展开。
                    if (
                        cfg.lamarckian
                        and hasattr(fitness_evaluator, "lamarckian_reencode")
                        and eval_result.solution is not None
                    ):
                        try:
                            re_encoded = fitness_evaluator.lamarckian_reencode(
                                child,
                                eval_result.solution,
                                cost_matrix,
                                n_uavs,
                                n_targets,
                                n_nests,
                            )
                            if re_encoded is not None:
                                re_encoded.fitness = child.fitness
                                child = re_encoded
                        except Exception:
                            # 任何异常（长度/键缺失等）都回退到原 child，保证 DE 矩阵安全
                            pass

                    population[i] = child
                    if child.fitness < best_individual.fitness:
                        best_individual = child.copy()
                        best_idx = i

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
                    n_nests=n_nests,
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
                            n_nests=n_nests,
                        )
                        population[i].fitness = self._evaluate(
                            population[i], fitness_evaluator, cost_matrix, n_uavs=n_uavs
                        )

            # 记录收敛曲线
            cost_history.append(best_individual.fitness)

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
                "n_nests": n_nests,
                # 编码给出的终点机巢（论文 §5(2)），供下游从最优个体还原 z_ub
                "best_end_nests": dict(best_individual.end_nests),
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

        # 把个体一并交给评估器，使其能读取编码中的终点机巢（论文 §5(2)）。
        # 兼容不接受 individual 的旧评估器。
        try:
            result = fitness_evaluator.evaluate(
                assignment, cost_matrix, n_uavs=n_uavs, individual=individual
            )
        except TypeError:
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

        try:
            result = fitness_evaluator.evaluate(
                assignment, cost_matrix, n_uavs=n_uavs, individual=individual
            )
        except TypeError:
            result = fitness_evaluator.evaluate(assignment, cost_matrix, n_uavs=n_uavs)
        return _EvalResult(
            fitness=getattr(result, "fitness", 1e12),
            solution=getattr(result, "solution", None),
        )
