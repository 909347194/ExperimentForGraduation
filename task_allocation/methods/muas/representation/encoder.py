# -*- coding: utf-8 -*-
"""encoder.py — 统一三元组基因生成器（规则 3.1 - 3.3）

职责：
    将 UAV-Target 分配关系编码为三元组基因序列，是 DMDE
    离散-连续混合求解流程的起点。

对应论文：
    公式 (3-3): 矩阵关系三元组 g_s = (U_i, T_j, C(i,j))
    公式 (3-4): 巡游关系三元组 g'_s = (T_j, T_{j+1}, Tc(j,j+1))
    规则 3.1 (N=M):  U 和 T 均不重复，一一对应。
    规则 3.2 (N>M):  U 不重复，T 可重复，每个 T 至少出现一次。
    规则 3.3 (N<M):  U 可重复（巡游），T 不重复，每个 U 至少出现一次。

数据结构：
    一个个体（Individual）是一组三元组基因的列表。
    每个基因 (Gene) = (uav_id, target_id, cost_value)。
    对于 N<M 的巡游关系，额外存储 target-to-target 的代价。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Gene:
    """单个基因（三元组）。

    Attributes:
        uav_id:    UAV 编号（-1 表示该基因为巡游关系中的 target-to-target）。
        target_id: 目标编号。
        cost:      航程代价 C(i,j) 或 Tc(j,k)。
    """

    uav_id: int
    target_id: int
    cost: float

    def __repr__(self) -> str:
        return f"Gene(U={self.uav_id}, T={self.target_id}, C={self.cost:.1f})"


@dataclass
class Individual:
    """一个进化个体（基因序列）。

    Attributes:
        genes:      基因列表。
        fitness:    适应度值（由评估器计算后填入）。
        model_type: 分配模型类型 ('balanced' / 'overloaded' / 'srp')。
    """

    genes: list[Gene]
    fitness: float = float("inf")
    model_type: str = "balanced"

    @property
    def assignment(self) -> list[tuple[int, int]]:
        """提取分配方案 [(uav_id, target_id), ...]。

        对于 SRP 模型，巡游基因（uav_id=-1）会被归属到
        前一个 UAV→Target 基因对应的 UAV。
        """
        result = []
        current_uav = -1
        for g in self.genes:
            if g.uav_id >= 0:
                current_uav = g.uav_id
                result.append((g.uav_id, g.target_id))
            else:
                # 巡游基因：归属到当前 UAV
                result.append((current_uav, g.target_id))
        return result

    @property
    def srp_routes(self) -> dict[int, list[int]]:
        """SRP 专用：返回 UAV 巡游路线 {uav_id: [target_ids]}。"""
        routes: dict[int, list[int]] = {}
        current_uav = -1
        for g in self.genes:
            if g.uav_id >= 0:
                current_uav = g.uav_id
                routes.setdefault(current_uav, []).append(g.target_id)
            else:
                routes[current_uav].append(g.target_id)
        return routes

    @property
    def cost_vector(self) -> np.ndarray:
        """提取代价值向量（用于正向映射）。"""
        return np.array([g.cost for g in self.genes])

    def copy(self) -> "Individual":
        """深拷贝。"""
        return Individual(
            genes=list(self.genes),
            fitness=self.fitness,
            model_type=self.model_type,
        )

    def __len__(self) -> int:
        return len(self.genes)


# ---------------------------------------------------------------------------
# 种群初始化
# ---------------------------------------------------------------------------

class PopulationEncoder:
    """种群初始化编码器。

    对应论文规则 3.1 ~ 3.3，根据代价矩阵生成初始种群。

    使用方式::

        encoder = PopulationEncoder(cost_matrix, n_uavs, n_targets)
        population = encoder.generate(pop_size=50)
    """

    def __init__(
        self,
        cost_matrix: np.ndarray,
        n_uavs: int,
        n_targets: int,
    ) -> None:
        self._cm = cost_matrix
        self._n = n_uavs
        self._m = n_targets

    @property
    def model_type(self) -> str:
        if self._n == self._m:
            return "balanced"
        elif self._n > self._m:
            return "overloaded"
        else:
            return "srp"

    def generate(self, pop_size: int, seed: int | None = None) -> list[Individual]:
        """生成初始种群。

        Args:
            pop_size: 种群大小。
            seed:     随机种子（可选）。

        Returns:
            个体列表。
        """
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        method = {
            "balanced": self._generate_balanced,
            "overloaded": self._generate_overloaded,
            "srp": self._generate_srp,
        }[self.model_type]

        return [method() for _ in range(pop_size)]

    # ---- N=M: 规则 3.1 -----------------------------------------------------

    def _generate_balanced(self) -> Individual:
        """规则 3.1: U 和 T 均不重复，一一对应。"""
        n = self._n
        targets = list(range(n))
        random.shuffle(targets)

        genes = []
        for i, j in enumerate(targets):
            cost = float(self._cm[i, j])
            genes.append(Gene(uav_id=i, target_id=j, cost=cost))

        return Individual(genes=genes, model_type="balanced")

    # ---- N>M: 规则 3.2 -----------------------------------------------------

    def _generate_overloaded(self) -> Individual:
        """规则 3.2: U 不重复，T 可重复，每个 T 至少出现一次。"""
        n, m = self._n, self._m

        # 首先保证每个目标至少分配一个 UAV
        uav_indices = list(range(n))
        random.shuffle(uav_indices)

        genes = []
        # 前 m 个 UAV 各分配一个不同目标
        for idx in range(m):
            uav_id = uav_indices[idx]
            target_id = idx
            cost = float(self._cm[uav_id, target_id])
            genes.append(Gene(uav_id=uav_id, target_id=target_id, cost=cost))

        # 剩余 UAV 随机分配到任意目标
        for idx in range(m, n):
            uav_id = uav_indices[idx]
            target_id = random.randint(0, m - 1)
            cost = float(self._cm[uav_id, target_id])
            genes.append(Gene(uav_id=uav_id, target_id=target_id, cost=cost))

        return Individual(genes=genes, model_type="overloaded")

    # ---- N<M: 规则 3.3 -----------------------------------------------------

    def _generate_srp(self) -> Individual:
        """规则 3.3: U 可重复（巡游），T 不重复，每个 U 至少出现一次。"""
        n, m = self._n, self._m

        # 将目标随机分配给 UAV（每个 UAV 至少一个目标）
        targets = list(range(m))
        random.shuffle(targets)

        # 分组：前 n 个目标各分配给一个 UAV，剩余目标随机分配
        uav_groups: dict[int, list[int]] = {}
        for i in range(n):
            uav_groups[i] = [targets[i]]
        for i in range(n, m):
            uav_id = random.randint(0, n - 1)
            uav_groups[uav_id].append(targets[i])

        # 对每组内目标按最近邻排序（贪心 TSP）
        genes = []
        for uav_id, tgt_list in uav_groups.items():
            ordered = self._order_targets_greedy(uav_id, tgt_list)
            for seq, tgt_id in enumerate(ordered):
                if seq == 0:
                    # 第一个目标：UAV -> Target
                    cost = float(self._cm[uav_id, tgt_id])
                    genes.append(Gene(uav_id=uav_id, target_id=tgt_id, cost=cost))
                else:
                    # 后续目标：Target -> Target（巡游代价）
                    prev_tgt = ordered[seq - 1]
                    cost = float(self._cm[self._n + prev_tgt, tgt_id])
                    genes.append(Gene(uav_id=-1, target_id=tgt_id, cost=cost))

        return Individual(genes=genes, model_type="srp")

    def _order_targets_greedy(self, uav_id: int, targets: list[int]) -> list[int]:
        """最近邻贪心排序（用于 SRP 巡游顺序）。"""
        if len(targets) <= 1:
            return targets

        remaining = set(targets)
        # 从距离 UAV 最近的目标开始
        first = min(remaining, key=lambda t: self._cm[uav_id, t])
        ordered = [first]
        remaining.remove(first)

        while remaining:
            last = ordered[-1]
            next_tgt = min(remaining, key=lambda t: self._cm[self._n + last, t])
            ordered.append(next_tgt)
            remaining.remove(next_tgt)

        return ordered
