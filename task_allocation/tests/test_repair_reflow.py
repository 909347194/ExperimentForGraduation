# -*- coding: utf-8 -*-
"""合并—删任务链路修复（结构性航程门 + 回流式航程修复）的常驻回归测试。

背景缺陷（exp01 实测暴露）：_consolidate 合并只看代价下降、不查航程，
合并出超航程长航次 → _repair_range 用删任务缩航程、删掉的任务不回流 →
物理可行任务被吞（exp01 周期 1：12 个任务单任务航次 8.4~68.2 km 全部
≤ 80 km 预算，却被延期 8 个）。

锁定的契约：

1. **结构性航程门**：合并后航次超受体航程预算 → 拒绝合并（可行性
   不依赖事后修复，与 MCF 溢出边结构性保底同一设计原则）；
2. **回流式修复**：超限航次移除的任务优先改派（插入现有航次 / 闲置机
   单任务航次），全部不可行才 y_j=0 暂缓；
3. **证明性暂缓**：真不可行（单任务航次即超预算）才延期。

三个用例的几何均为对抗性设计：任务两两分开 120°，单任务航次装得下、
合并航次必超预算、合并比分开省——修复前必失败。
"""

from __future__ import annotations

import math

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000
RANGE = 80_000.0


def _task(tid: int, x: float, y: float) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=10.0,
                earliest=0.0, latest=1e9, arrival_time=0.0)


def _uav(uid: int, remaining_range: float = RANGE) -> UAV:
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, remaining_range=remaining_range,
               speed=10.0, nest_id=0)


def _pairwise(tasks, uavs, nests):
    pts = {t.id: (t.x, t.y, t.z) for t in tasks}
    pts.update({-(1 + u.id): (u.x, u.y, u.z) for u in uavs})
    pts.update({NEST_KEY + n.id: (n.x, n.y, n.z) for n in nests})
    keys = list(pts)
    out = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            out[(a, b)] = math.dist(pts[a], pts[b])
    return out


def _evaluator(tasks, uavs, nests) -> MUASAssignmentEvaluator:
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(w_time=0.0),
    )
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=list(uavs),
        tasks=list(tasks),
        nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(),
    )


def _fixture_2uav_2task():
    """巢在原点；t1/t2 距巢各 30 km、相距约 52 km（120° 夹角）。

    单任务航次 60 km（≤ 80）；合并航次 0→t1→t2→0 ≈ 112 km（> 80）；
    合并 (112) 比分开 (120) 省——正是吞任务链路的触发几何。
    """
    tasks = [_task(0, 30_000.0, 0.0), _task(1, -15_000.0, 26_000.0)]
    uavs = [_uav(0), _uav(1)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    return tasks, uavs, nests


# ---------------------------------------------------------------------------
# 1. 结构性航程门：合并不得制造超航程航次
# ---------------------------------------------------------------------------


def test_consolidate_rejects_range_infeasible_merge():
    tasks, uavs, nests = _fixture_2uav_2task()
    ev = _evaluator(tasks, uavs, nests)
    # 解码：一机一任务（本可全部执行）
    sol = ev.build_solution([(0, 0), (1, 1)])
    executed = set(sol.executed_task_ids)
    carriers = [t for t in sol.tours if t.task_ids]
    assert executed == {0, 1}, f"任务被吞：executed={executed}"
    assert len(carriers) == 2, (
        f"合并制造了超航程航次并被修复吞任务：{[(t.uav_id, t.task_ids) for t in carriers]}"
    )


# ---------------------------------------------------------------------------
# 2. 回流式修复：装不下的任务改派给闲置机，而非删除
# ---------------------------------------------------------------------------


def test_repair_reflows_to_idle_uav():
    tasks, uavs, nests = _fixture_2uav_2task()
    ev = _evaluator(tasks, uavs, nests)
    # 解码：两任务全压在 uav0（超航程），uav1 闲置
    sol = ev.build_solution([(0, 0), (0, 1)])
    executed = set(sol.executed_task_ids)
    assert executed == {0, 1}, f"回流失败，任务被删：executed={executed}"
    carriers = [t for t in sol.tours if t.task_ids]
    assert len(carriers) == 2, (
        f"应改派到闲置机：{[(t.uav_id, t.task_ids) for t in carriers]}"
    )


# ---------------------------------------------------------------------------
# 3. 证明性暂缓：真不可行才 y_j=0
# ---------------------------------------------------------------------------


def test_defers_only_genuinely_infeasible():
    tasks = [_task(0, 30_000.0, 0.0), _task(1, 400_000.0, 0.0)]  # t1 单航次 800km
    uavs = [_uav(0), _uav(1)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator(tasks, uavs, nests)
    sol = ev.build_solution([(0, 0), (0, 1)])
    executed = set(sol.executed_task_ids)
    assert executed == {0}, f"真不可行任务不应被执行或误改派：executed={executed}"
    # 可行的 t0 仍应保留（不因邻座不可行被连坐）
    assert any(t.task_ids == [0] for t in sol.tours)
