# -*- coding: utf-8 -*-
"""覆盖门禁（coverage gate）常驻回归测试（2026-10-22）。

锁定的契约（用户裁定「合法解 = 物理硬约束 + 覆盖门禁」）：

1. **可救必救**：空闲机可开班/航次可插入的任务，audit 判 voluntary_skip，
   repair 必须救回；
2. **能力不足记 shortfall**：航程或自身窗不可行的任务，两类判据都失败 ⇒
   capacity_shortfall（允许不交付），不得混入 voluntary_skip；
3. **不动点收敛**：repair 幂等（再跑一轮零变化），修复后 audit n_skip 恒 0；
4. **W_skip 层咬合**：lexicographic_coverage 下，能装没装的解显著更差；
5. **默认关逐位不变**：开关关闭时不写 gate_stats、W_skip 不入权重、
   F1 迟到折减行为与历史口径一致；
6. **F1 纯完成计数 + 拆虚构帽**：门禁模式下迟到不折减 F1，
   _secondary_upper_bound 换推导帽；
7. **搜索用 f 不含 −m·F1（F1 只作指标）**：门禁口径（含 stage 层
   coverage_gate 产物落 meta 的路径）下 f 与 F1 无关。
"""

from __future__ import annotations

import math

import pytest

from task_allocation.common.types import AllocationSolution, Nest, Task, UAV, UAVTour
from task_allocation.methods.muas.coverage_gate import (
    audit_coverage,
    repair_coverage,
)
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)

NEST_KEY = 1_000_000


# ── 装配工具（与 test_fitness_tiers 同构）─────────────────────────


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
    kw = dict(w_time=0.0, w_violation=0.01, lambda_cost=1.0e-4)
    kw.update(cfg)
    return SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(**kw),
    )


def _sol(tours, cand=None) -> AllocationSolution:
    executed = [t for tour in tours for t in tour.task_ids]
    if cand is None:
        cand = list(dict.fromkeys(executed))
    return AllocationSolution(
        tours=list(tours),
        candidate_task_ids=list(cand),
        executed_task_ids=list(dict.fromkeys(executed)),
    )


# ── 1. 可救必救：空闲机可救必须救 ─────────────────────────────────


def test_open_tour_rescuable_must_be_skipped_and_rescued():
    """t0 空闲机可开班 ⇒ audit 判 voluntary_skip；repair 必须救回。"""
    tasks = [_task(0, 10_000.0, 0.0), _task(1, 200_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 80_000.0)]
    p = _problem(tasks, uavs, nests)

    stats = audit_coverage(p, {0: []}, {})
    assert stats.n_skip == 1 and stats.skip_ids == [0], (
        f"t0 空闲机开班可行（往返 20km ≤ 80km），必须判 skip：{stats.to_dict()}"
    )
    assert stats.n_shortfall == 1 and stats.shortfall_ids == [1], (
        f"t1 往返 400km 超航程，必须记 shortfall：{stats.to_dict()}"
    )

    work, ends, rstats = repair_coverage(p, {0: []}, {})
    assert work[0] == [0], f"可救必救：t0 必须被救回，实际 {work}"
    assert rstats.n_rescued == 1
    assert rstats.n_skip == 0, f"修复后 n_skip 必须归零：{rstats.to_dict()}"
    assert rstats.shortfall_ids == [1]
    post = audit_coverage(p, work, ends)
    assert post.n_skip == 0, f"末态复检必须干净：{post.to_dict()}"


# ── 2. 窗口/航程不可行必须记 shortfall ────────────────────────────


def test_infeasible_tasks_are_shortfall_not_skip():
    """自身窗不可达（迟到）与航程不可达都归 capacity_shortfall。"""
    tasks = [
        _task(0, 10_000.0, 0.0, latest=100.0),   # ETA=1000s > latest=100 → 窗口死
        _task(1, 200_000.0, 0.0),                 # 往返 400km > 80km → 航程死
    ]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 80_000.0)]
    p = _problem(tasks, uavs, nests)

    stats = audit_coverage(p, {0: []}, {})
    assert stats.n_skip == 0, f"无任何可行插入，不得判 skip：{stats.to_dict()}"
    assert sorted(stats.shortfall_ids) == [0, 1]

    _, _, rstats = repair_coverage(p, {0: []}, {})
    assert rstats.n_rescued == 0
    assert rstats.n_skip == 0
    assert sorted(rstats.shortfall_ids) == [0, 1]


# ── 3. 不动点收敛：竞争容量 + 幂等 ────────────────────────────────


def test_repair_converges_to_fixed_point_and_is_idempotent():
    """两任务竞争单机航程预算：救急先救（EDD），次者记 shortfall；
    修复结果幂等（再跑零变化），末态 n_skip 恒 0。"""
    tasks = [_task(0, 14_000.0, 0.0), _task(1, -14_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    # 单任务往返各 28km 可行；同 tour 56km 超 30km
    uavs = [_uav(0, 30_000.0)]
    p = _problem(tasks, uavs, nests)

    work, ends, stats = repair_coverage(p, {0: []}, {})
    assert sorted(work[0]) == [0], f"EDD 同期限按 id 先救 t0：{work}"
    assert stats.n_rescued == 1
    assert stats.shortfall_ids == [1]
    assert stats.n_skip == 0

    work2, ends2, stats2 = repair_coverage(p, work, ends)
    assert work2 == work and ends2 == ends, "repair 必须幂等（不动点）"
    assert stats2.n_rescued == 0, "已收敛后再跑不得有新动作"

    post = audit_coverage(p, work, ends)
    assert post.n_skip == 0, f"末态复检必须干净：{post.to_dict()}"
    assert post.shortfall_ids == [1], "t1 与 [t0] 同 tour 超航程、无空闲机 → shortfall"


# ── 4. W_skip 层咬合：能装没装的解必须显著更差 ────────────────────


def test_skip_layer_penalizes_voluntary_skip():
    tasks = [_task(0, 10_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 30_000.0)]
    p = _problem(tasks, uavs, nests, lexicographic_coverage=True)

    sol_done = _sol([UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)], cand=[0])
    sol_empty = _sol([UAVTour(uav_id=0, task_ids=[], end_nest_id=None)], cand=[0])
    p.evaluate(sol_done)
    p.evaluate(sol_empty)

    assert sol_done.meta["gate_stats"]["n_skip"] == 0
    assert sol_empty.meta["gate_stats"]["n_skip"] == 1, (
        f"sol_empty 能装没装必须判 skip：{sol_empty.meta.get('gate_stats')}"
    )
    f_done = p.scalar_fitness(sol_done)
    f_empty = p.scalar_fitness(sol_empty)
    assert f_empty > f_done, (
        f"W_skip 层必须压倒交付收益差：skip={f_empty:.1f} vs done={f_done:.1f}"
    )


# ── 5. 默认关逐位不变 ────────────────────────────────────────────


def test_gate_off_keeps_legacy_behavior():
    tasks = [_task(0, 10_000.0, 0.0, latest=200.0)]  # ETA=1000 > 200 → 迟到
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 30_000.0)]
    p = _problem(tasks, uavs, nests, late_reward_decay_seconds=1000.0)

    sol = _sol([UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)], cand=[0])
    p.evaluate(sol)
    assert "gate_stats" not in sol.meta, "默认关不得写 gate_stats"
    assert sol.meta["fitness_weights"]["w_skip"] is None
    # 历史口径：F1 迟到折减仍生效（迟到 800s / W=1000 → eff = 10×0.2）
    assert abs(sol.f1_reward - 2.0) < 1e-6, f"默认关 F1 折减必须保持：{sol.f1_reward}"


# ── 6. F1 纯完成计数 + 拆虚构帽 ───────────────────────────────────


def test_gate_mode_f1_pure_and_secondary_bound_derived():
    tasks = [_task(0, 10_000.0, 0.0, latest=200.0)]  # 迟到 800s
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 30_000.0)]
    kw = dict(w_time=0.0, w_violation=0.01, lambda_cost=1.0e-4,
              late_reward_decay_seconds=1000.0)
    p_gate = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests, pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(**kw, lexicographic_coverage=True),
    )
    p_legacy = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests, pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(**kw),
    )

    sol = _sol([UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)], cand=[0])
    p_gate.evaluate(sol)
    assert abs(sol.f1_reward - 10.0) < 1e-6, (
        f"门禁模式 F1 必须纯完成计数（不折减）：{sol.f1_reward}"
    )
    assert sol.meta["fitness_weights"]["W_decay"] is None

    assert p_gate._secondary_upper_bound() < p_legacy._secondary_upper_bound(), (
        "门禁模式必须拆掉 1e6 虚构帽（推导帽更小）"
    )


# ── 7. stage 挂点集成：build_solution 末态修复 + gate_stats 落 meta ──

def test_stage_hook_gate_stats_and_default_off():
    """coverage_gate=True：终态账面干净（skip=0、能力不足记 shortfall）；
    默认关：不写 gate_stats（行为逐位不变）。"""
    from task_allocation.methods.muas.solvers.muas_stage import (
        MUASAssignmentEvaluator,
        MUASStageConfig,
    )

    tasks = [_task(0, 10_000.0, 0.0), _task(1, 200_000.0, 0.0)]
    uavs = [_uav(0, 80_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]

    def _ev(stage_cfg, gate_fit: bool) -> MUASAssignmentEvaluator:
        problem = SelectiveMUASProblem.from_entities(
            tasks, uavs, nests, pairwise_cost=_pairwise(tasks, uavs, nests),
            config=MUASProblemConfig(
                w_time=0.0, w_violation=0.01, lambda_cost=1.0e-4,
                lexicographic_coverage=gate_fit,
            ),
        )
        return MUASAssignmentEvaluator(
            problem=problem, participating=list(uavs), tasks=list(tasks),
            nests=list(nests), fleet_uavs=list(uavs), config=stage_cfg,
            available_uavs=list(uavs),
        )

    ev = _ev(MUASStageConfig(coverage_gate=True), gate_fit=True)
    sol = ev.build_solution([(0, 0)])  # 基因只编入 t0
    gs = sol.meta.get("gate_stats")
    assert gs is not None, "coverage_gate=True 必须写 gate_stats"
    assert gs["n_skip"] == 0, f"终态不得留 voluntary_skip：{gs}"
    assert gs["shortfall_ids"] == [1], f"t1 一阶检验不可行应记 shortfall：{gs}"
    assert set(sol.executed_task_ids) == {0}
    assert gs["window_mode"] == "self"

    ev_off = _ev(MUASStageConfig(), gate_fit=False)
    sol_off = ev_off.build_solution([(0, 0)])
    assert "gate_stats" not in sol_off.meta, "默认关不得改写解的 meta 结构"


# ── 7. 搜索用 f 不含 −m·F1（F1 只作指标）─────────────────────────


def test_search_fitness_excludes_f1_under_gate():
    """门禁口径固定表述：**搜索用 f 不含 −m·F1；F1 只作事后指标**。

    两条进入路径都必须生效：① problem.lexicographic_coverage=True；
    ② stage 层 coverage_gate=True（gate_stats 落 solution.meta）。
    判据：同一解在「带 gate_stats」与「不带」两态下的 f 差值恰为 m·F1。
    """
    tasks = [_task(0, 10_000.0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 80_000.0)]
    p = _problem(tasks, uavs, nests)  # lexicographic_coverage 默认 False

    sol_legacy = _sol([UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)], cand=[0])
    f_legacy = p.scalar_fitness(sol_legacy)
    m, f1 = p.f1_weight(), float(sol_legacy.f1_reward)
    assert f1 > 0.0 and m > 0.0, "前提：正收益任务已完成交付"
    assert p.gate_mode(sol_legacy) is False, "无 gate_stats 且开关关 ⇒ 非门禁口径"

    sol_gate = _sol([UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)], cand=[0])
    sol_gate.meta["gate_stats"] = {
        "n_skip": 0, "skip_ids": [], "n_shortfall": 0,
        "shortfall_ids": [], "n_gate_rescued": 0,
    }
    assert p.gate_mode(sol_gate) is True, "meta 带 gate_stats ⇒ 门禁口径"
    f_gate = p.scalar_fitness(sol_gate)

    assert f_gate == pytest.approx(f_legacy + m * f1, rel=1e-9), (
        "门禁口径的 f 应不含 −m·F1：与历史口径之差恰为 m·F1（"
        f"f_gate={f_gate}, f_legacy={f_legacy}, m·F1={m * f1}）"
    )
