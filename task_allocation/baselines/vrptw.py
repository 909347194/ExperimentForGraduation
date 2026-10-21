# -*- coding: utf-8 -*-
"""vrptw.py — VRPTW 启发式基线（Solomon 插入式构造，硬时间窗）。

经典 VRPTW（带时间窗车辆路径）构造式对照：

    - 按最晚交付时间（EDD）逐任务处理；
    - 对每个任务在**所有航次的所有插入位**做可行性检查：
        · 硬时间窗：每站到达时刻 ≤ latest（最早开始 earliest 由等待满足）；
        · 航程：闭合航次 ≤ 剩余航程（返航按最近巢下界，构造期保守判据）；
    - 可行插入中取 Solomon I1 式评价
      ``c1 = α1·Δ距离 + α2·Δ下游到达时间``（α2 折算成米当量）最小者；
    - 无可行插入的任务本周期暂缓（y_j = 0）；
    - 终点机巢贪心取「容量余量优先、代价最小」的可行巢。

与 ``greedy`` 的对照点：本方法**卡硬时间窗**（经典 VRPTW 假设），
因此「飞得到但赶不到」的任务直接不排——与生产口径「迟到不是废飞、
按 L/W 折减计奖」形成诚实差异，这正是 §3.5.4 要观察的机制差。
与本文方法的对照点：一次成型插入、无全局搜索、无解码修复、
终点逐机贪心（无容量联合最优）。

约定：
    - 类名 ``VRPTWInsertionSolver``，name == "vrptw"
    - 继承 BaseOptimizer，返回 SolverResult（``extra["solution"]`` 为权威解）
"""

from __future__ import annotations

import time
from typing import Any

from task_allocation.baselines.construct import ConstructionContext, build_solver_result
from task_allocation.methods.muas.base import BaseOptimizer, SolverResult

__all__ = ["VRPTWInsertionSolver"]


class VRPTWInsertionSolver(BaseOptimizer):
    """Solomon 插入式 VRPTW 启发（硬时间窗 + 航程）。

    Args:
        alpha_distance: 插入评价的距离权重 α1。
        alpha_time: 插入评价的时间权重 α2（秒）；内部乘 UAV 速度折成
            米当量，与 α1 同量纲后加权。
    """

    def __init__(
        self,
        alpha_distance: float = 1.0,
        alpha_time: float = 1.0,
    ) -> None:
        self._alpha_d = float(alpha_distance)
        self._alpha_t = float(alpha_time)

    @property
    def name(self) -> str:
        return "vrptw"

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
        # EDD：最晚交付时间越早越先安排（无 latest 者排最后，按到达时刻）
        pending = sorted(
            (t for t in ctx.tasks),
            key=lambda t: (
                float(t.latest) if t.latest is not None else float("inf"),
                float(t.arrival_time),
                int(t.id),
            ),
        )

        for task in pending:
            tid = int(task.id)
            # (c1, task_id, uav_id, pos)：四元组结构统一，平局时确定性 tie-break
            best: tuple[float, int, int, int] | None = None
            for uav in ctx.uavs:
                seq = routes[uav.id]
                speed = max(float(getattr(uav, "speed", 0.0) or 0.0), 1e-6)
                for pos in range(len(seq) + 1):
                    trial = seq[:pos] + [tid] + seq[pos:]
                    if not ctx.range_ok(uav, trial):
                        continue
                    if not ctx.hard_tw_ok(uav, trial):
                        continue
                    delta = ctx.insertion_delta(uav, seq, pos, tid, uav.nest_id)
                    # α2·Δ下游到达时间（秒 → 米当量）：插入使后续站点
                    # 到达推迟的部分，经典 I1 的 c2 项
                    delay_s = _downstream_delay(ctx, uav, seq, pos, tid)
                    c1 = self._alpha_d * max(delta, 0.0) + (
                        self._alpha_t * speed * delay_s
                    )
                    cand = (float(c1), tid, int(uav.id), pos)
                    if best is None or cand < best:
                        best = cand
            if best is None:
                continue  # 无可行插入 → 本周期暂缓
            _, _, uid, pos = best
            routes[uid].insert(pos, tid)

        solution = ctx.finalize(routes)
        return build_solver_result(ctx, solution, self.name, time.time() - t0)


def _downstream_delay(
    ctx: ConstructionContext,
    uav: Any,
    seq: list[int],
    pos: int,
    tid: int,
) -> float:
    """插入 ``tid`` 对**后续站点**到达时刻的最大推迟量（秒，≥ 0）。"""
    if pos >= len(seq):
        return 0.0
    before = ctx.arrival_times(uav, seq)
    after = ctx.arrival_times(uav, seq[:pos] + [tid] + seq[pos:])
    # after 中 pos 位是新任务自身，其后是原 seq[pos:] 的到达时刻
    base = before[pos:]
    tail = after[pos + 1 :]
    if not tail:
        return 0.0
    return max(0.0, max(a - b for a, b in zip(tail, base)))
