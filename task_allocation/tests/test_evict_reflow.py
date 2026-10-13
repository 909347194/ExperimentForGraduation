# -*- coding: utf-8 -*-
"""Fix⑦ 换机补全（_reflow_task 步骤 2c）的常驻回归测试。

背景缺陷（exp01 实测暴露）：T2 三个周期全部 defer，但实测全程只有 UAV2
能飞它（单航次 47.0 km ≤ 80 km、到达余量 1149 s），其余 9 架航程超限或
时间窗不够；而 UAV2 每周期都被 DMDE 派了别的航次，插入即迟到。原来的
补全只有「插入现有航次 + 拉闲置机」两手，判不出可达 → 假暂缓。

锁定的契约：

1. **换机执行**：目标任务单飞某架机可行但该机已被占用 → 把该机现有
   任务迁到别处（真腾空），空出该机飞目标任务；
2. **回填不算腾空**：被迁任务唯一的去处是回填腾空机自己时，该架
   换机判失败（覆盖会丢任务；且机并未真正腾空）；
3. **不制造新违约**：迁移用全航次时间窗校验（full_window），不把同
   航次后续任务推迟到 deadline 之后；
4. **硬边界**：物理不可达（单飞任何机都超航程）必须暂缓；
   depth ≥ 2 时禁用 2c（深度门锁死，防无限递归）。
"""

from __future__ import annotations

import math

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.problem import (
    ConstraintConfig,
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000
RANGE = 80_000.0
SPEED = 10.0


def _task(tid: int, x: float, y: float, latest: float = 1e9) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=10.0,
                earliest=0.0, latest=latest, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0, y: float = 0.0,
         remaining_range: float = RANGE) -> UAV:
    return UAV(id=uid, x=x, y=y, z=0.0, remaining_range=remaining_range,
               speed=SPEED, nest_id=0)


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
        config=MUASProblemConfig(
            w_time=0.0,
            constraints=ConstraintConfig(time_window=True),
        ),
    )
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=list(uavs),
        tasks=list(tasks),
        nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(),
    )


def _fixture_evictable():
    """只有 UAV1 能单飞 T_new；UAV1 的原任务 T_b 可迁入 UAV0 航次。

    几何（x 轴为主，速度 10 m/s，巢在原点）：

    * UAV0 起点 (20k, 0)、UAV1 (0, 0)、UAV2 (-20k, 0)——起点错开，
      使「单飞 T_new」因航程/ETA 分化；
    * T_new (-38k, 0)：UAV0 单飞 20+38+38=96 km 超限；UAV2 单飞
      18+38+38=94 km 超限；**只有 UAV1（76 km）行**；
    * T_new 插入任何现有航次：UAV0 [T_a] 146 km、UAV1 [T_b] 96 km、
      UAV2 [T_c] 110 km —— 全部超限，步骤 1 必败；
    * UAV1 的原任务 T_b (10k, 0) 可插入 UAV0 的 [T_a(25k,0)]：
      [T_a, T_b] = 5+15+10 = 30 km ≤ 80 km → 真腾空可行。
    """
    tasks = [
        _task(0, 25_000.0, 0.0),      # T_a：UAV0 占用
        _task(1, 10_000.0, 0.0),      # T_b：UAV1 占用（待迁走）
        _task(2, 15_000.0, 10_000.0),  # T_c：UAV2 占用
        _task(3, -38_000.0, 0.0),     # T_new：待补全目标
    ]
    uavs = [_uav(0, x=20_000.0), _uav(1), _uav(2, x=-20_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    return tasks, uavs, nests


def _initial_work():
    return {0: [0], 1: [1], 2: [2]}, {0: None, 1: None, 2: None}


# ---------------------------------------------------------------------------
# 1. 换机执行：目标单飞可行但该机被占用 → 迁走原任务，空出该机
# ---------------------------------------------------------------------------


def test_evict_reenables_insertion_infeasible_task():
    tasks, uavs, nests = _fixture_evictable()
    ev = _evaluator(tasks, uavs, nests)
    work, ends = _initial_work()
    ok = ev._reflow_task(3, work, ends, require_window=True)
    assert ok, "换机补全失败：T_new 单飞 UAV1 可行（76 km）却被判不可达"
    assigned = {tid for tids in work.values() for tid in tids}
    assert assigned == {0, 1, 2, 3}, f"任务丢失：{assigned}"
    assert work[1] == [3], f"UAV1 应被腾空飞 T_new：{work}"
    # 换机后所有航次不得超航程
    for uid, tids in work.items():
        if not tids:
            continue
        c = ev._tour_cost(uid, tids, ends.get(uid))
        assert c <= RANGE + 1e-6, f"UAV{uid} 换机后航程超限：{c:.0f} m"


def test_build_solution_evicts_for_unassigned():
    """端到端：DMDE 解码出「T_new 缺席」的解，补全链应把它拉回。"""
    tasks, uavs, nests = _fixture_evictable()
    ev = _evaluator(tasks, uavs, nests)
    sol = ev.build_solution([(0, 0), (1, 1), (2, 2)])
    executed = set(sol.executed_task_ids)
    assert 3 in executed, (
        f"T_new 单飞可行（76 km ≤ 80 km）却未执行：executed={executed}"
    )


# ---------------------------------------------------------------------------
# 2. 回填不算腾空：被迁任务唯一去处是腾空机自己 → 换机失败
# ---------------------------------------------------------------------------


def test_refill_of_evicted_uav_is_not_an_eviction():
    """T_b 插入任何其它航次都迟到（tight deadline），只能回填 UAV1
    （腾空机）→ UAV1 并未真正腾空 → 换机必须失败（否则覆盖丢任务）。"""
    tasks, uavs, nests = _fixture_evictable()
    # ETA 口径为纯飞行时间（无服务时间）：T_b 单飞 UAV1 需 1000 s；
    # 插入 UAV0 的 [T_a] 任一位都把其中一个推迟过 deadline
    # （[T_a,T_b] 2000 > 1900、[T_b,T_a] 2500 > 2400）。
    tasks[1] = _task(1, 10_000.0, 0.0, latest=1_900.0)
    tasks[0] = _task(0, 25_000.0, 0.0, latest=2_400.0)
    # T_c 挪到 (0,-35k)：插入 UAV0 118 km、UAV1 81 km 全超限；
    # 与 T_new(-38k,0) 组合 105 km 也超 → T_new 插入 UAV2 同样失败。
    # 于是两架能单飞 T_new 的机（UAV1 76 km、UAV2 56 km）原任务
    # 都只能回填腾空机自己 → 均未真正腾空 → 换机必须失败。
    tasks[2] = _task(2, 0.0, -35_000.0)
    ev = _evaluator(tasks, uavs, nests)
    work, ends = _initial_work()
    ok = ev._reflow_task(3, work, ends, require_window=True)
    assert ok is False, (
        f"T_b 唯一去处是回填 UAV1（未真正腾空），换机应失败：work={work}"
    )
    # 任务不得丢失
    assigned = {tid for tids in work.values() for tid in tids}
    assert assigned == {0, 1, 2}, f"任务被吞：{assigned}"


# ---------------------------------------------------------------------------
# 3. 硬边界：物理不可达必须暂缓；depth 门必须锁死
# ---------------------------------------------------------------------------


def test_defers_genuinely_unreachable():
    tasks, uavs, nests = _fixture_evictable()
    far = _task(9, 200_000.0, 0.0)  # 距巢 200 km：任何单飞 ≫ 80 km
    ev = _evaluator(tasks + [far], uavs, nests)
    work, ends = _initial_work()
    ok = ev._reflow_task(9, work, ends, require_window=True)
    assert ok is False, f"物理不可达的任务被硬塞：{ok}"


def test_evict_depth_gate_locked():
    """depth ≥ 2 时禁用 2c：同一场景 depth=0 可行、depth=2 必须 False。"""
    tasks, uavs, nests = _fixture_evictable()
    ev = _evaluator(tasks, uavs, nests)
    work, ends = _initial_work()
    assert ev._reflow_task(3, work, ends, require_window=True, depth=0) is True
    work2, ends2 = _initial_work()
    assert ev._reflow_task(3, work2, ends2, require_window=True, depth=2) is False
