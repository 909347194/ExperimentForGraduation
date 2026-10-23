# -*- coding: utf-8 -*-
"""fitness_weights.py — 适应度层权重推导与分层统计（mixin）。

从 ``problem.py`` 拆出：集中「四层准字典序 + 覆盖门禁」的各层权重如何从
问题数据推导（big-M 自动推导 / T0 支配权重 / T0.5 门禁权重 / 层内
tie-break），以及分层计数基（硬违约条数、航程超限米、软违约秒数）如何取。

由 :class:`~task_allocation.methods.muas.problem.SelectiveMUASProblem`
混入，``problem.hard_weight()`` 等 API 与拆分前完全一致（muas_stage 与
测试直接消费）。契约（由混入方提供）：``config`` / ``tasks`` / ``uavs`` /
``nests`` / ``costs`` / ``check_feasible(solution)``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_allocation.common.types import AllocationSolution


class FitnessWeightsMixin:
    """适应度层权重推导与分层统计（混入 SelectiveMUASProblem）。"""


    def gate_mode(self, solution: "AllocationSolution | None" = None) -> bool:
        """是否处于覆盖门禁口径（「完成是约束」而非「完成是目标」）。

        两条进入路径，任一成立即按同一口径评价：

        - ``problem.lexicographic_coverage=True``：显式开启门禁适应度；
        - 解已过门禁（``solution.meta`` 带 ``gate_stats``，即 stage 层
          ``coverage_gate=True`` 的终态修复/审计产物）。

        门禁口径下的**固定表述**（2026-10-22，与论文 §4.3 一致）：
        **搜索用 ``f`` 不含 ``−m·F1``；``F1`` 只作事后指标。** 覆盖由门禁
        （构造修复 + W_skip 层）承担，目标层只剩成本 + 迟到。
        """
        if bool(self.config.lexicographic_coverage):
            return True
        return bool(solution is not None and "gate_stats" in solution.meta)

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
