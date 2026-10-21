# -*- coding: utf-8 -*-
"""greedy.py — 收益/代价比贪心插入基线。

经典构造式对照（constructive baseline）：

    - 全局最优插入：每步在所有（任务 × UAV × 插入位）中取
      **插入代价 / 任务收益** 最小者落位（收益代价比贪心）；
    - 构造期硬约束只卡航程（与生产口径「迟到不是废飞」一致——
      时间窗不作参选资格，迟到由奖励折减定价）；
    - 航程不可插入的任务本周期暂缓（y_j = 0）；
    - 终点机巢贪心取「容量余量优先、代价最小」的可行巢。

与 ``vrptw`` 的对照点：本方法纯代价驱动、不看时间窗；
与本文方法（DMDE + 解码修复链 + 方案 A 终点）的对照点：
构造期一次成型、无全局搜索、无容量联合最优。

约定：
    - 类名 ``GreedySolver``，name == "greedy"
    - 继承 BaseOptimizer，返回 SolverResult（``extra["solution"]`` 为权威解）
"""

from __future__ import annotations

import time
from typing import Any

from task_allocation.baselines.construct import ConstructionContext, build_solver_result
from task_allocation.methods.muas.base import BaseOptimizer, SolverResult

__all__ = ["GreedySolver"]

#: 收益下限：零收益任务的插入比值用该地板防除零（等价于按纯代价排序）
REWARD_EPS = 1e-6


class GreedySolver(BaseOptimizer):
    """收益/代价比贪心插入。

    Args:
        reward_floor: 收益下限（防零/负收益任务除零）。
    """

    def __init__(self, reward_floor: float = REWARD_EPS) -> None:
        self._reward_floor = max(float(reward_floor), REWARD_EPS)

    @property
    def name(self) -> str:
        return "greedy"

    def solve(
        self,
        cost_matrix: Any,
        n_uavs: int,
        n_targets: int,
        **kwargs: Any,
    ) -> SolverResult:
        evaluator = kwargs.get("fitness_evaluator")
        if evaluator is None:
            raise ValueError("fitness_evaluator must be provided.")
        t0 = time.time()
        ctx = ConstructionContext(evaluator)

        routes: dict[int, list[int]] = {u.id: [] for u in ctx.uavs}
        pending = {t.id for t in ctx.tasks}

        while pending and ctx.uavs:
            best: tuple[float, int, int, int] | None = None
            for tid in sorted(pending):
                reward = max(float(ctx.task_by_id[tid].reward), self._reward_floor)
                for uav in ctx.uavs:
                    seq = routes[uav.id]
                    for pos in range(len(seq) + 1):
                        delta = ctx.insertion_delta(uav, seq, pos, tid, uav.nest_id)
                        if delta < 0:
                            delta = 0.0
                        trial = seq[:pos] + [tid] + seq[pos:]
                        if not ctx.range_ok(uav, trial):
                            continue
                        score = delta / reward
                        cand = (score, tid, uav.id, pos)
                        if best is None or cand < best:
                            best = cand
            if best is None:
                break  # 剩余任务本周期均不可行插入 → 暂缓
            _, tid, uid, pos = best
            routes[uid].insert(pos, tid)
            pending.discard(tid)

        solution = ctx.finalize(routes)
        return build_solver_result(ctx, solution, self.name, time.time() - t0)
