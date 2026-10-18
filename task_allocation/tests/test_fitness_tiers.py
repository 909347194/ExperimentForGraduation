# -*- coding: utf-8 -*-
"""四层准字典序适应度（2026-10-18）与零奖励剔除的常驻回归测试。

背景：旧口径把物理违约（米/1e3）与迟到（秒）混进同一 penalty 桶同乘
wv=0.01——misc 违约只折 10 单位而单任务收益 m·R≈2.7e5，「纸面可行、
执行弹回」的幽灵 F1 无人拦截（修 uav_9 前 16 次弹回属此类）；且
is_feasible 被迟到污染（实测 22423 次评估 penalty=0 仅 4 次）。

新口径（用户裁定：违反物理航程=严重违规；迟到可接受但最好不迟到）::

    f = W_hard·n_hard + w_exc·excess_m − m·F1_eff + w_late·L_tot + λ·path + w_time·F3

锁定的契约：

1. **T0 支配 T1**：任何一次物理违约压倒任意交付收益（幽灵 F1 永不入选）；
2. **T0 层内 tie-break**：都不可行时，超程米数少的更优；
3. **T1 支配 T2/T3**：多交付一个任务的收益优势压倒任意迟到/航程差异；
4. **T2 软计价**：迟到秒数按 w_violation 线性计价（不判不可行）；
5. **W_hard 自动推导** = κ·m·R_pool；
6. **权重快照**：meta["fitness_weights"] 记录本次评价实际权重，可精确重算；
7. **零奖励剔除**：ETA ≥ latest+W（eff≈0）的任务暂缓；来得及/关闭时不动。
"""

from __future__ import annotations

import math

import pytest

from task_allocation.common.types import AllocationSolution, Nest, Task, UAV, UAVTour
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000


# ── 装配工具 ──────────────────────────────────────────────────────

def _task(tid: int, x: float, y: float, *, reward: float = 10.0,
          latest: float | None = 1e9) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=reward,
                earliest=0.0, latest=latest, arrival_time=0.0)


def _uav(uid: int, remaining_range: float) -> UAV:
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


def _problem(tasks, uavs, nests, **cfg) -> SelectiveMUASProblem:
    # 默认取 exp01 生产口径：λ=1e-4、w_violation=0.01
    kw = dict(w_time=0.0, w_violation=0.01, lambda_cost=1.0e-4)
    kw.update(cfg)
    return SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(**kw),
    )


def _sol(tours) -> AllocationSolution:
    executed = [t for tour in tours for t in tour.task_ids]
    cand = list({t for tour in tours for t in tour.task_ids})
    return AllocationSolution(
        tours=list(tours),
        candidate_task_ids=cand,
        executed_task_ids=list(dict.fromkeys(executed)),
    )


# ── 1. T0 支配 T1：不可行但多任务 < 可行但少任务 ──────────────────

def test_t0_dominates_t1():
    """超程航次即便多交一个任务，也必须输给可行少任务解。"""
    tasks = [_task(0, 30_000.0, 0.0), _task(1, -30_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    # 剩余 80 km：单任务往返 60 km 可行；[t0,t1] 全程 120 km 超 40 km
    uavs = [_uav(0, 80_000.0)]
    p = _problem(tasks, uavs, nests)

    sol_a = _sol([UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)])   # 可行，F1=10
    sol_b = _sol([UAVTour(uav_id=0, task_ids=[0, 1], end_nest_id=0)])  # 超程，F1=20
    p.evaluate(sol_a)
    p.evaluate(sol_b)

    assert sol_a.meta["feasibility"]["n_hard"] == 0
    assert sol_b.meta["feasibility"]["n_hard"] == 1
    assert sol_b.f1_reward > sol_a.f1_reward, "前提：b 确实多交一个任务"
    f_a, f_b = p.scalar_fitness(sol_a), p.scalar_fitness(sol_b)
    assert f_b > f_a, (
        f"T0 必须压倒 T1：不可行多任务 f={f_b:.1f} 却优于可行少任务 f={f_a:.1f}"
    )
    # 支配量级：差距应主要由 W_hard 贡献（≈W_hard − m·ΔF1 > 0）
    assert (f_b - f_a) > 0.5 * p.hard_weight()


# ── 2. T0 层内 tie-break：超程米数少的更优 ────────────────────────

def test_t0_excess_tiebreak():
    """同样超程一次，超得少的解适应度更优（层内梯度）。"""
    tasks = [_task(0, 30_000.0, 0.0), _task(1, -30_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    # 同一航次 [t0,t1]=120 km；剩余 90 km 超 30 km，剩余 70 km 超 50 km
    uavs_a = [_uav(0, 90_000.0)]
    uavs_b = [_uav(0, 70_000.0)]
    p_a = _problem(tasks, uavs_a, nests)
    p_b = _problem(tasks, uavs_b, nests)
    assert p_a.hard_weight() == pytest.approx(p_b.hard_weight()), "同任务集应同权重"

    tour = [UAVTour(uav_id=0, task_ids=[0, 1], end_nest_id=0)]
    sol_a, sol_b = _sol(tour), _sol(tour)
    p_a.evaluate(sol_a)
    p_b.evaluate(sol_b)
    assert sol_a.meta["feasibility"]["range_excess"] == pytest.approx(30_000.0)
    assert sol_b.meta["feasibility"]["range_excess"] == pytest.approx(50_000.0)
    assert p_a.scalar_fitness(sol_a) < p_b.scalar_fitness(sol_b)
    # 差距应恰为 w_exc×Δexcess（其余项完全相同；超限量为浮点减法，留 1e-6 绝对容差）
    w_exc = p_a.range_excess_weight()
    assert p_b.scalar_fitness(sol_b) - p_a.scalar_fitness(sol_a) == pytest.approx(
        w_exc * 20_000.0, abs=1e-6
    )


# ── 3. T1 支配 T2/T3：多一个任务的收益压倒一切次级差异 ────────────

def test_t1_dominates_secondary():
    """都可行时，多交付一个任务永远更优，且优势 ≈ m·R（次级项可忽略）。"""
    tasks = [_task(0, 30_000.0, 0.0), _task(1, -30_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 1e9)]  # 航程不设限：两个解都物理可行
    p = _problem(tasks, uavs, nests)

    sol_1 = _sol([UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)])
    sol_2 = _sol([UAVTour(uav_id=0, task_ids=[0, 1], end_nest_id=0)])
    p.evaluate(sol_1)
    p.evaluate(sol_2)
    assert sol_1.meta["feasibility"]["n_hard"] == 0
    assert sol_2.meta["feasibility"]["n_hard"] == 0

    f_1, f_2 = p.scalar_fitness(sol_1), p.scalar_fitness(sol_2)
    assert f_2 < f_1, "多交一个任务必须更优（最小化器下 f 更小）"
    # 优势 = m·10（F1 差）− λ·Δpath（航程差 60 km）；前者应压倒后者
    m = p.f1_weight()
    d_path = p.path_cost(sol_2) - p.path_cost(sol_1)
    assert f_1 - f_2 == pytest.approx(m * 10.0 - p.config.lambda_cost * d_path, rel=1e-9)
    assert m * 10.0 > 100.0 * p.config.lambda_cost * d_path, "T1 应压倒 T3"


# ── 4. T2 软计价：迟到按秒线性计入，不判不可行 ────────────────────

def test_t2_soft_lateness_priced():
    """同任务同航程，仅迟到量不同：适应度差 = w_violation × 迟到秒数。"""
    # 关闭折减以隔离 T2（折减属 T1 的 F1_eff，另行测试）
    late_cfg = dict(late_reward_decay_seconds=None)
    tasks_on = [_task(0, 50_000.0, 0.0, latest=1e9)]
    tasks_late = [_task(0, 50_000.0, 0.0, latest=1_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 1e9)]
    p_on = _problem(tasks_on, uavs, nests, **late_cfg)
    p_late = _problem(tasks_late, uavs, nests, **late_cfg)

    tour = [UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)]
    sol_on, sol_late = _sol(tour), _sol(tour)
    p_on.evaluate(sol_on)
    p_late.evaluate(sol_late)

    # 50 km @ 10 m/s = 5000 s，latest=1000 → 迟到 4000 s
    assert sol_late.meta["feasibility"]["soft_violation"] == pytest.approx(4_000.0)
    # 迟到是软违约：物理仍可行
    assert sol_late.meta["feasibility"]["n_hard"] == 0
    assert sol_late.feasible is True
    # 适应度差恰为 w_violation × 4000（F1 无折减时相等）
    assert p_late.scalar_fitness(sol_late) - p_on.scalar_fitness(sol_on) == pytest.approx(
        0.01 * 4_000.0, rel=1e-9
    )


# ── 5. W_hard 自动推导 = κ·m·R_pool ───────────────────────────────

def test_hard_weight_auto_formula():
    tasks = [_task(0, 10_000.0, 0.0), _task(1, -10_000.0, 0.0, reward=3.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 1e9)]
    p = _problem(tasks, uavs, nests)
    r_pool = sum(t.reward for t in tasks)
    assert p.hard_weight() == pytest.approx(
        10.0 * p.f1_weight() * r_pool, rel=1e-12
    )
    # 显式给定则照办（消融/测试锁值）
    p2 = _problem(tasks, uavs, nests, w_hard_violation=123.0)
    assert p2.hard_weight() == 123.0
    # 0 = 关闭 T0 层
    p3 = _problem(tasks, uavs, nests, w_hard_violation=0.0)
    assert p3.hard_weight() == 0.0


# ── 6. 权重快照：meta 记录 + 可精确重算（治 auto big_m 漂移）──────

def test_fitness_weights_meta_snapshot():
    tasks = [_task(0, 30_000.0, 0.0), _task(1, -30_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 80_000.0)]
    p = _problem(tasks, uavs, nests)
    sol = _sol([UAVTour(uav_id=0, task_ids=[0, 1], end_nest_id=0)])
    p.evaluate(sol)

    w = sol.meta["fitness_weights"]
    assert w["m"] == pytest.approx(p.f1_weight())
    assert w["w_hard"] == pytest.approx(p.hard_weight())
    assert w["w_late"] == 0.01
    det = sol.meta["feasibility"]
    manual = (
        w["w_hard"] * det["n_hard"]
        + w["w_excess"] * det["range_excess"]
        - w["m"] * sol.f1_reward
        + w["w_late"] * det["soft_violation"]
        + w["lambda"] * p.path_cost(sol)
        + w["w_time"] * sol.meta["bottleneck_time"]
    )
    assert manual == pytest.approx(p.scalar_fitness(sol), rel=1e-12)


# ── 7. 零奖励剔除（evaluator 级，修复链末轮）──────────────────────

def _evaluator(tasks, uavs, nests, **stage_cfg) -> MUASAssignmentEvaluator:
    kw = dict(late_reward_decay_seconds=600.0)
    problem = _problem(tasks, uavs, nests, **kw)
    skw = dict(prune_zero_reward=True)
    skw.update(stage_cfg)
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=list(uavs),
        tasks=list(tasks),
        nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(**skw),
    )


def test_prune_zero_reward_defers_unrecoverable_task():
    """ETA ≥ latest+W 且无处可按时飞 → 暂缓（不带着零奖励上天）。"""
    # 50 km @ 10 m/s = 5000 s；latest=1000、W=600 ⇒ 阈值 1600 s，必零奖励
    tasks = [_task(0, 50_000.0, 0.0, latest=1_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 1e9)]
    ev = _evaluator(tasks, uavs, nests)
    sol = ev.build_solution([(0, 0)])
    assert sol.tours[0].task_ids == [], "零奖励任务应被剔除暂缓"
    assert 0 not in sol.executed_task_ids


def test_prune_zero_reward_keeps_ontime_task():
    """来得及的任务不受影响（阈值 latest+W 内到达）。"""
    tasks = [_task(0, 10_000.0, 0.0, latest=1e9)]  # 10 km → 1000 s，余量充足
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 1e9)]
    ev = _evaluator(tasks, uavs, nests)
    sol = ev.build_solution([(0, 0)])
    assert sol.tours[0].task_ids == [0]


def test_prune_zero_reward_disabled_keeps_task():
    """关闭开关 → 保持旧行为（零奖励也交付并计完成）。"""
    tasks = [_task(0, 50_000.0, 0.0, latest=1_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 1e9)]
    ev = _evaluator(tasks, uavs, nests, prune_zero_reward=False)
    sol = ev.build_solution([(0, 0)])
    assert sol.tours[0].task_ids == [0], "关闭后不得剔除"


def test_prune_zero_reward_off_without_decay():
    """late_reward_decay_seconds=None（关闭折减）→ 本特性不生效。"""
    tasks = [_task(0, 50_000.0, 0.0, latest=1_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 1e9)]
    problem = _problem(tasks, uavs, nests, late_reward_decay_seconds=None)
    ev = MUASAssignmentEvaluator(
        problem=problem, participating=list(uavs), tasks=list(tasks),
        nests=list(nests), fleet_uavs=list(uavs),
        config=MUASStageConfig(prune_zero_reward=True),
    )
    sol = ev.build_solution([(0, 0)])
    assert sol.tours[0].task_ids == [0], "无折减口径时零奖励无定义，不剔除"
