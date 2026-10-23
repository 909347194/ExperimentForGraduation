# -*- coding: utf-8 -*-
"""fitness.py — 适应度标量化（DMDE 最小化目标）与各层权重推导。

职责（回答）：两个解谁更好？

覆盖个数第一键、成本第二键（2026-10-23 起生产口径）::

    f(s) = W_hard·n_hard + w_exc·excess_m        # T0 物理违约（硬，真 big-M）
         + W_skip·n_voluntary_skip               # T0.5 覆盖门禁（gate 模式）
         + W_cov·(N − n_delivered)               # T1 覆盖缺口（个数，第一比较键）
         + w_late·L_tot + λ·path + w_time·F3     # T2/T3 成本（第二比较键）

W_cov 是门槛权重（1 + 次目标上界）：「少交付 1 个」劣于任意航程/迟到差异；
交付个数相同的解之间成本项以全分辨率裁决。F1=ΣR_j 退为指标，不进搜索主阶梯。
消融（coverage_count_first=False）回到收益和 big-M（−m·F1_eff）；
legacy（lexicographic=False）为历史加权和。细则见 problem.py 模块 docstring。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from task_allocation.common.types import AllocationSolution
from task_allocation.methods.muas.coverage_gate import audit_coverage

if TYPE_CHECKING:  # 避免循环导入：problem 组合本 mixin
    from task_allocation.methods.muas.problem import SelectiveMUASProblem


class FitnessMixin:
    """适应度评价与权重推导（evaluate / scalar_fitness / W_* 权重族）。"""

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
        # 实际计入 F2 的惩罚量（供 w_violation 解耦口径读取，避免重复解析报告）
        solution.meta["penalty"] = pen
        solution.meta["bottleneck_time"] = self.bottleneck_time(solution)
        # 2026-10-18：本次评价实际使用的四层权重快照。记录/重算/绘图共用
        # 同一组权重，消灭 auto big_m 逐周期漂移导致的「记录 best_fitness
        # 与重算目标差 6.5~13.6」。
        decay = self.config.late_reward_decay_seconds
        if self.config.lexicographic_coverage:
            decay = None  # 门禁模式 F1 不用迟到折减，快照口径与实际一致
        # 覆盖门禁分账（W_skip 层消费）：解码期已算过（gate_stats 在
        # build_solution 写入 meta）则复用；外源解（测试 /
        # solution_from_sequences）在这里惰性补算。
        if self.config.lexicographic_coverage and "gate_stats" not in solution.meta:
            routes = {t.uav_id: list(t.task_ids) for t in solution.tours}
            ends = {t.uav_id: t.end_nest_id for t in solution.tours}
            solution.meta["gate_stats"] = audit_coverage(self, routes, ends).to_dict()
        solution.meta["fitness_weights"] = {
            "m": float(self.f1_weight()),
            "w_hard": float(self.hard_weight()),
            "w_excess": float(self.range_excess_weight()),
            "w_skip": (float(self.skip_weight())
                       if self.config.lexicographic_coverage else None),
            "w_cov": (float(self.coverage_weight())
                      if self.config.coverage_count_first else None),
            "w_late": (None if self.config.w_violation is None
                       else float(self.config.w_violation)),
            "lambda": float(self.config.lambda_cost),
            "w_time": float(self.config.w_time),
            "W_decay": (None if not decay else float(decay)),
        }
        for tour in solution.tours:
            tour.est_cost = self.tour_path_cost(tour)
            tour.est_reward = sum(
                self.task_by_id[tid].reward
                for tid in tour.task_ids
                if tid in self.task_by_id
            )
        return solution

    def scalar_fitness(self, solution: AllocationSolution) -> float:
        """DMDE 最小化的标量适应度。

        四层准字典序（2026-10-18，``lexicographic=True`` 且 ``w_violation``
        给定时的生产口径）::

            f = W_hard·n_hard + w_exc·excess_m       # T0 物理违约（硬）
              − m·F1_eff                              # T1 交付收益
              + w_late·L_tot                          # T2 迟到（软，秒级）
              + λ·path + w_time·F3                    # T3 航程/负载均衡

        - **T0**：``W_hard = κ·m·R_pool``（默认自动推导）使任何一次物理违约
          压倒任意交付收益——幽灵 F1（纸面可行、执行弹回）永不入选；
          ``excess_m``（超限米数）仅在不可行解之间做层内排序。
        - **T1**：``F1_eff`` 含迟到折减（``late_reward_decay_seconds``）；
          ``m`` 保证「多交付 ≥1 个任务」支配一切次级差异。
        - **T2**：迟到秒数软计价（``w_violation``，0.01/s）——迟到可接受，
          只与路径同台竞争；迟到与任务数的权衡由 T1 的折减管。
        - **T3**：``λ·path + w_time·F3``（收益等价时的最小化目标）。

        2026-10-23 起生产口径为**覆盖个数第一键、成本第二键**
        （``coverage_count_first=True``）：T1 换成覆盖缺口个数
        ``W_cov·(N − n_delivered)``，W_cov 为门槛权重（1 + 次目标上界）；
        交付个数相同则成本全分辨率裁决；F1 退为指标。上述 −m·F1_eff
        形式保留为消融口径（``coverage_count_first=False``）。

        legacy 加权和（``lexicographic=False``）：``f = -F1 + λ·path +
        w_violation·penalty + w_time·F3``（惩罚不再乘 λ 的解耦口径）。
        最老口径（``w_violation=None``）：惩罚随 F2 一起乘 λ。
        """
        if not solution.meta.get("feasibility"):
            self.evaluate(solution)
        bt = solution.meta.get("bottleneck_time", self.bottleneck_time(solution))
        m = self.f1_weight()
        f1 = float(solution.f1_reward)
        wv = self.config.w_violation
        if wv is None:
            # 历史口径：惩罚随 F2 一起乘 λ（量纲未解耦，秒级惩罚被压缩）
            return float(
                -m * f1
                + self.config.lambda_cost * solution.f2_cost
                + self.config.w_time * bt
            )
        if not self.config.lexicographic:
            # legacy 加权和 + 惩罚量纲解耦（消融对比口径，不含 T0 支配层）
            return float(
                -m * f1
                + self.config.lambda_cost * self.path_cost(solution)
                + float(wv) * self.violation_penalty(solution)
                + self.config.w_time * bt
            )
        # 四层准字典序（生产口径）；覆盖门禁模式（lexicographic_coverage，
        # 2026-10-22）在 T0 与 T1 之间加 T0.5 层 W_skip·n_voluntary_skip——
        # voluntary_skip 与物理硬约束等价的非法解，由解码器 repair 至不动点
        # 构造性归零（gate 模式），W_skip 仅在 audit 模式/防御路径咬合。
        n_hard, excess_m = self.hard_stats(solution)
        skip_pen = 0.0
        if self.config.lexicographic_coverage:
            n_skip = int((solution.meta.get("gate_stats") or {}).get("n_skip", 0))
            skip_pen = self.skip_weight() * n_skip
        if self.config.coverage_count_first:
            # 覆盖个数第一键、成本第二键（2026-10-23 生产口径）：T1 从
            # 收益和 big-M 换成覆盖缺口个数；W_cov 门槛权重保证「少交付
            # 1 个」劣于任意成本差；交付个数相同时成本项全分辨率裁决。
            return float(
                self.hard_weight() * n_hard
                + self.range_excess_weight() * excess_m
                + skip_pen
                + self.coverage_weight() * self.coverage_gap(solution)
                + float(wv) * self.soft_violation(solution)
                + self.config.lambda_cost * self.path_cost(solution)
                + self.config.w_time * bt
            )
        return float(
            self.hard_weight() * n_hard
            + self.range_excess_weight() * excess_m
            + skip_pen
            - m * f1
            + float(wv) * self.soft_violation(solution)
            + self.config.lambda_cost * self.path_cost(solution)
            + self.config.w_time * bt
        )

    # ---- T0 硬违约权重与分解（2026-10-18 四层适应度）-------------------

    def hard_weight(self) -> float:
        """T0 硬违约的支配权重 ``W_hard = κ·m·R_pool``。

        保证任一物理违约的代价超过本周期全部候选任务的收益和——可行解
        永远压到不可行解之前，与交付了多少任务无关。legacy 加权和
        （``lexicographic=False``）不设支配层，返回 1.0。
        """
        if not self.config.lexicographic:
            return 1.0
        if self.config.w_hard_violation is not None:
            return max(0.0, float(self.config.w_hard_violation))
        cached = getattr(self, "_hard_weight_cache", None)
        if cached is None:
            m = self.f1_weight()
            r_pool = sum(max(float(t.reward), 0.0) for t in self.tasks)
            cached = float(self.config.hard_violation_kappa) * m * r_pool
            self._hard_weight_cache = cached
        return float(cached)

    def skip_weight(self) -> float:
        """T0.5 覆盖门禁违约的支配权重 W_skip（默认同 W_hard 推导 κ·m·R_pool）。

        voluntary_skip（能装却没装）是与物理硬约束等价的非法解；W_skip
        保证一次 skip 压倒任意交付收益。gate 修复模式下 n_skip 恒 0
        （构造性归零），本层不咬合，仅 audit 模式/防御路径生效。
        """
        if self.config.w_skip_violation is not None:
            return max(0.0, float(self.config.w_skip_violation))
        return self.hard_weight()

    def coverage_weight(self) -> float:
        """覆盖缺口权重 ``W_cov``（个数 → 适应度单位，T1 第一比较键）。

        门槛权重语义（2026-10-23 覆盖个数第一键）：只需大于任意两解间
        可能的最大成本差（次目标上界），保证「少交付 1 个任务」劣于任意
        航程/迟到差异；它不是旧式 ``m·R≈2.7e5`` 的巨额完成奖励——
        交付个数相同的解之间成本项以全分辨率裁决，成本梯度不受挤压。
        """
        if self.config.w_coverage is not None:
            return max(0.0, float(self.config.w_coverage))
        return float(1.0 + self._secondary_upper_bound())

    def coverage_gap(self, solution: AllocationSolution) -> int:
        """本周期覆盖缺口（个数）= 候选任务数 − 交付任务数。

        与比较规则「本周期交付任务数更多者胜」等价（同周期候选集相同，
        缺口小 ⇔ 交付多）；周期内无人可做的任务构成常数偏置，不影响比较。
        """
        executed = {int(t) for t in solution.executed_task_ids}
        delivered = sum(1 for t in self.tasks if int(t.id) in executed)
        return int(len(self.tasks) - delivered)

    def range_excess_weight(self) -> float:
        """T0 层内 tie-break 权重（米 → 奖励当量）；None = 自动 ``1/m``。"""
        if self.config.w_range_excess is not None:
            return max(0.0, float(self.config.w_range_excess))
        return 1.0 / max(self.f1_weight(), 1.0)

    def hard_stats(self, solution: AllocationSolution) -> tuple[int, float]:
        """``(硬违约条数, 航程超限米数)``（T0 层两个计数基）。"""
        det = solution.meta.get("feasibility") or {}
        if "n_hard" not in det:
            _, det = self.check_feasible(solution)
        return int(det.get("n_hard", 0)), float(det.get("range_excess", 0.0))

    def soft_violation(self, solution: AllocationSolution) -> float:
        """软违约量 = 时间窗迟到秒数合计（T2 层；迟到不是不可行）。"""
        det = solution.meta.get("feasibility") or {}
        if "soft_violation" not in det:
            _, det = self.check_feasible(solution)
        return float(det.get("soft_violation", 0.0))

    # ---- 字典序权重（big-M）------------------------------------------

    def f1_weight(self) -> float:
        """F1 的支配性权重 big_m（lexicographic=False 时为 1.0 = 历史加权和）。"""
        if not self.config.lexicographic:
            return 1.0
        if self.config.big_m is not None:
            return max(1.0, float(self.config.big_m))
        cached = getattr(self, "_f1_weight_cache", None)
        if cached is None:
            cached = self._auto_big_m()
            self._f1_weight_cache = cached
        return float(cached)

    def _auto_big_m(self) -> float:
        """按问题数据推导字典序权重：``1 + 次目标上界 / 最小正收益``。

        保证：任一 ≥ 最小正收益 r_min 的收益优势（即多完成 ≥1 个正收益任务）
        支配任意次目标差异；更细粒度的收益差视为同收益、由成本裁决。
        """
        pos = [float(t.reward) for t in self.tasks if float(t.reward) > 0]
        r_min = min(pos) if pos else 1.0
        return 1.0 + self._secondary_upper_bound() / r_min

    def _secondary_upper_bound(self) -> float:
        """任一解次目标值的保守上界（λ·路径 + 软惩罚 + w_time·最坏机时间）。

        2026-10-18 四层适应度：硬违约已升入 T0 支配层（``W_hard = κ·m·R_pool``，
        与 m 的推导解耦），不再进次目标上界；此处只包软违约（时间窗迟到
        秒数）——按每任务 1e6 s 的保守帽估计，保证 m·r_min 覆盖任何可实现的
        迟到总量差异。
        """
        cfg = self.config
        n_t, n_u, n_b = len(self.tasks), len(self.uavs), len(self.nests)
        # 航次腿数上界：每任务一条链边 + 每航次进/出边
        legs = n_t + 2 * n_u + 2
        c_bound = self._max_edge_cost() * legs
        speed_min = min(
            (max(float(getattr(u, "speed", 0.0) or 0.0), 1e-6) for u in self.uavs),
            default=1.0,
        )
        # 软违约（迟到秒数）上界：
        # - 覆盖门禁模式（lexicographic_coverage）：拆「1e6 s/任务」虚构帽
        #   （消 m 病的真正落地）——迟到是 T2 单一定价的成本，不再需要
        #   「一个任务收益压倒任意迟到」的假保证；换**推导**帽
        #   n_t × 单机航迹时间上界（迟到不可能超过航迹时间本身）。
        # - 历史口径：保持 1e6·(n_t + 8) 不变（既有测试与历史格逐位可比）。
        if getattr(cfg, "lexicographic_coverage", False):
            v_bound = n_t * (c_bound / speed_min)
        else:
            v_bound = 1.0e6 * (n_t + 8)
        lam = float(cfg.lambda_cost)
        total = lam * c_bound
        if cfg.w_violation is None:
            total += lam * float(cfg.w_penalty) * v_bound
        else:
            total += float(cfg.w_violation) * v_bound
        total += float(cfg.w_time) * (c_bound / speed_min)
        return float(total)

    def _max_edge_cost(self) -> float:
        """有限边代价上界：pairwise 与实体包围盒对角线（欧氏兜底边）取大。"""
        vals = [
            abs(float(c))
            for c in getattr(self.costs, "pairwise", {}).values()
            if abs(float(c)) < 1e18
        ]
        c_max = max(vals, default=0.0)
        pts = [p.position for p in self.tasks] + [p.position for p in self.uavs]
        pts += [p.position for p in self.nests]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            diag = (
                (max(xs) - min(xs)) ** 2
                + (max(ys) - min(ys)) ** 2
                + (max(zs) - min(zs)) ** 2
            ) ** 0.5
            c_max = max(c_max, diag)
        return max(c_max, 1.0)


FitnessEvaluator = Callable[[AllocationSolution], float]


def make_fitness_evaluator(problem: SelectiveMUASProblem) -> FitnessEvaluator:
    def _eval(solution: AllocationSolution) -> float:
        problem.evaluate(solution)
        return problem.scalar_fitness(solution)

    return _eval
