# -*- coding: utf-8 -*-
"""字典序评价（方案 B 主线）+ 解码端合并修复 聚焦测试。

契约：

1. **收益支配成本**：多完成一个任务（哪怕代价暴涨）必须胜出——big-M 字典序；
2. **收益等价成本裁决**：同完成量取更省解（这是"合并到更少机"的原动力）；
3. **闲置合法**：部分 UAV 零任务不产生任何惩罚/违约；
4. **合并修复**：任务少时整航次并入更少机，不删任务；
5. **e2e**：少任务聚集场景下求解器输出用更少的机完成全部任务；
6. **legacy 对照**：lexicographic=False 退化为加权和（丢任务换成本可以赢）。

背景（问题 2：原 DMDE 单适应度如何处理双目标）：
    Ming 2017 为单标量目标；本方案保持 DMDE 单适应度/选择机制不动，
    用支配性权重 big_m 实现字典序语义：先最大化任务收益 F1=ΣR_j y_j，
    再在收益等价的解中最小化成本（含惩罚/最坏机时间）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
    UAVStatus,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.cost.cost_matrix import (  # noqa: E402
    build_pairwise_costs,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000  # nest_key(id) = 1_000_000 + id（nest_offset 默认）


def _task(tid: int, x: float, reward: float) -> Task:
    return Task(id=tid, x=x, y=0.0, z=0.0, reward=reward,
                earliest=0.0, latest=1e6, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0) -> UAV:
    return UAV(id=uid, x=x, y=0.0, z=0.0, remaining_range=1e7,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


def _pairwise_entries(tasks, uavs, nests):
    """欧氏代价的 pairwise 字典（problem 的 cost() 口径：uav 键 = -(1+id)）。"""
    import math

    pts = {t.id: t.position for t in tasks}
    pts.update({-(1 + u.id): u.position for u in uavs})
    pts.update({NEST_KEY + n.id: n.position for n in nests})
    keys = list(pts)
    out = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            d = math.dist(pts[a], pts[b])
            out[(a, b)] = d
    return out


def _prob(tasks, uavs, nests, pairwise, **kw):
    return SelectiveMUASProblem.from_entities(
        tasks, uavs, nests, pairwise_cost=pairwise,
        config=MUASProblemConfig(w_time=0.0, **kw),
    )


def _sol(tour_specs, cand):
    """tour_specs: [(uav_id, [task_ids], end_nest_id|None), ...]"""
    return AllocationSolution(
        tours=[UAVTour(uav_id=u, task_ids=list(t), end_nest_id=e)
               for u, t, e in tour_specs],
        candidate_task_ids=list(cand),
        executed_task_ids=[tid for _, t, _ in tour_specs for tid in t],
    )


# ---------------------------------------------------------------------------
# 1. 收益支配成本（big-M 字典序核心语义）
# ---------------------------------------------------------------------------


def _dominance_fixture():
    """2 任务；全完成的方案代价暴涨（远巢返航 4e5），只完成 1 个很便宜。"""
    tasks = [_task(0, 100.0, 1.0), _task(1, 101.0, 1.0)]
    uavs = [_uav(0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]
    pairwise = {
        (-1, 0): 100.0, (-1, 1): 500.0,
        (0, 1): 1.0,
        (0, NEST_KEY): 100.0,
        (1, NEST_KEY): 400000.0,  # t1 返航极贵
    }
    return tasks, uavs, nests, pairwise


def test_reward_dominates_cost():
    """完成 2 个任务（F1=2）的方案即使贵 4e5 也必须胜过只完成 1 个（F1=1）。"""
    tasks, uavs, nests, pairwise = _dominance_fixture()
    prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0)

    full = _sol([(0, [0, 1], 0)], cand=[0, 1])     # cost ≈ 100+1+4e5
    partial = _sol([(0, [0], 0)], cand=[0, 1])     # cost = 100+100

    assert prob.scalar_fitness(full) < prob.scalar_fitness(partial), (
        "字典序失效：收益优势未支配成本差异"
    )
    # 收益差必须体现在 f1 上
    assert prob.evaluate(full).f1_reward == 2.0
    assert prob.evaluate(partial).f1_reward == 1.0


def test_legacy_weighted_sum_prefers_cheap_partial():
    """对照：lexicographic=False 的加权和会为省成本丢任务（说明开关有效）。"""
    tasks, uavs, nests, pairwise = _dominance_fixture()
    prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0, lexicographic=False)

    full = _sol([(0, [0, 1], 0)], cand=[0, 1])
    partial = _sol([(0, [0], 0)], cand=[0, 1])
    assert prob.scalar_fitness(partial) < prob.scalar_fitness(full)


def test_explicit_big_m_override():
    """big_m 显式给定时按给定值生效（1.0 ≈ legacy 权重口径）。"""
    tasks, uavs, nests, pairwise = _dominance_fixture()
    prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0, big_m=1.0)
    assert prob.f1_weight() == 1.0
    prob2 = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0)
    assert prob2.f1_weight() > 1.0  # 自动推导必为支配性大权重


# ---------------------------------------------------------------------------
# 2. 收益等价 → 成本裁决（同完成量取更省解）
# ---------------------------------------------------------------------------


def test_same_completions_cheaper_wins():
    """同样的完成集合：合并 1 机串飞 vs 2 机分飞 → 更省者胜。"""
    tasks = [_task(0, 100.0, 5.0), _task(1, 101.0, 5.0), _task(2, 102.0, 5.0)]
    uavs = [_uav(0, 0.0), _uav(1, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]
    pairwise = _pairwise_entries(tasks, uavs, nests)
    prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0)

    merged = _sol([(0, [0, 1, 2], 0)], cand=[0, 1, 2])          # ≈ 204
    spread = _sol([(0, [0], 0), (1, [1, 2], 0)], cand=[0, 1, 2])  # ≈ 404

    fm = prob.scalar_fitness(merged)
    fs = prob.scalar_fitness(spread)
    assert fm < fs, f"同完成量未取更省解：merged={fm} spread={fs}"
    # 且两者收益相同（成本裁决的前提）
    assert prob.evaluate(merged).f1_reward == prob.evaluate(spread).f1_reward == 15.0


# ---------------------------------------------------------------------------
# 3. 闲置合法
# ---------------------------------------------------------------------------


def test_idle_uav_is_free():
    """参与 UAV 零任务：不计违约、不计成本（与不存在等价）。"""
    tasks = [_task(0, 100.0, 5.0)]
    uavs = [_uav(0, 0.0), _uav(1, 0.0)]  # uav1 全程闲置
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]
    pairwise = _pairwise_entries(tasks, uavs, nests)
    prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0)

    with_idle = _sol([(0, [0], 0), (1, [], None)], cand=[0])
    without = _sol([(0, [0], 0)], cand=[0])
    ok, details = prob.check_feasible(with_idle)
    assert ok, f"闲置机不应产生违约：{details['violations']}"
    assert prob.scalar_fitness(with_idle) == prob.scalar_fitness(without)


# ---------------------------------------------------------------------------
# 4-5. 合并修复（解码端）+ e2e
# ---------------------------------------------------------------------------


def _e2e_setup():
    """3 个聚集任务 + 3 架同址 UAV + 1 机巢：合并到 1 机 ≈204，摊派 ≥400。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(i, 0.0) for i in range(3)]
    tasks = [_task(i, 100.0 + i, 5.0) for i in range(3)]
    return tasks, uavs, nests


def test_solver_consolidates_few_tasks():
    """e2e：字典序 + 合并修复下，全部任务由 1 架机完成（其余闲置）。"""
    tasks, uavs, nests = _e2e_setup()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, dem=None, estimator=None,
                              nest_offset=NEST_KEY)
    stage = run_selective_muas(
        tasks, uavs, nests, cb,
        fleet_uavs=uavs,
        problem_config=MUASProblemConfig(w_time=0.0),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=5, seed=1),
        current_time=0.0,
    )
    sol = stage.solution
    carriers = [t for t in sol.tours if t.task_ids]
    assert len(sol.executed_task_ids) == 3, f"任务未全完成：{sol.executed_task_ids}"
    assert len(carriers) == 1, f"未合并到更少机：{[(t.uav_id, t.task_ids) for t in carriers]}"
    total = sum(t.est_cost for t in sol.tours)
    assert total < 300.0, f"合并后仍不省：{total}"  # 1 机串飞 ≈ 204


def test_consolidation_does_not_drop_tasks():
    """合并修复只改派不删任务（区别于 y_j=0 暂缓）。"""
    tasks, uavs, nests = _e2e_setup()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, dem=None, estimator=None,
                              nest_offset=NEST_KEY)
    stage = run_selective_muas(
        tasks, uavs, nests, cb,
        fleet_uavs=uavs,
        problem_config=MUASProblemConfig(w_time=0.0),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=5, seed=1),
        current_time=0.0,
    )
    executed = set(stage.solution.executed_task_ids)
    assert executed == {0, 1, 2}


# ---------------------------------------------------------------------------
# 6. 字典序下经济性剔除自动关闭
# ---------------------------------------------------------------------------


def test_economic_prune_disabled_under_lexicographic():
    """字典序（收益优先）下，丢正收益任务的经济性剔除不得生效。

    构造：单任务、极高代价（λ·saving ≫ R），legacy 会剔除（y_j=0），
    字典序必须保留任务（暂缓只能来自可行性修复）。
    """
    tasks = [_task(0, 100.0, 1.0)]
    uavs = [_uav(0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]
    pairwise = {(-1, 0): 1e5, (0, NEST_KEY): 1e5}  # 返航 1e5 ≫ 收益 1

    for lexi, expect_done in ((True, True), (False, False)):
        prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0,
                     lexicographic=lexi)
        cb = build_pairwise_costs(tasks, uavs, nests=nests, dem=None,
                                  estimator=None, nest_offset=NEST_KEY)
        stage = run_selective_muas(
            tasks, uavs, nests, cb,
            fleet_uavs=uavs,
            problem_config=MUASProblemConfig(w_time=0.0, lexicographic=lexi),
            stage_config=MUASStageConfig(),
            solver_config=DMDEConfig(pop_size=6, max_generations=3, seed=1),
            current_time=0.0,
        )
        done = bool(stage.solution.executed_task_ids)
        assert done is expect_done, (
            f"lexicographic={lexi}: 执行={done}，期望={expect_done}"
        )


# ── 7. 交付解适应度对账（审计 N 项 N2 口径，2026-10-18）──────────────
#
# muas_stage 落盘的 final_fitness/final_f1/final_path_m/final_late_s/
# final_n_hard/final_excess_m + fitness_weights 快照，必须能独立重算出
# 交付解适应度（审计 audit_correctness.py N2 的算术）。同时锁死：
# final_fitness == scalar_fitness(stage.solution)（交付解自身口径），
# 且与搜索期 best_fitness 口径分离（Lamarckian 只回写起始基因通道，
# 回写后基因解码重建的解与搜索期被评价解可不同，两者不要求相等）。

def test_delivered_fitness_recompute_from_components():
    tasks, uavs, nests = _e2e_setup()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, dem=None, estimator=None,
                              nest_offset=NEST_KEY)
    stage = run_selective_muas(
        tasks, uavs, nests, cb,
        fleet_uavs=uavs,
        problem_config=MUASProblemConfig(w_time=0.0, w_violation=0.01),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=5, seed=1),
        current_time=0.0,
    )
    d = stage.diagnostics
    for key in ("final_fitness", "final_f1", "final_path_m", "final_late_s",
                "final_n_hard", "final_excess_m", "fitness_weights"):
        assert key in d, f"diagnostics 缺字段 {key}"
    w = d["fitness_weights"]
    # 2026-10-23 覆盖个数第一键：T1 = W_cov·覆盖缺口（n_tasks − n_executed）
    gap = d["n_tasks"] - d["n_executed"]
    rec = (
        w["w_hard"] * d["final_n_hard"]
        + w["w_excess"] * d["final_excess_m"]
        + w["w_cov"] * gap
        + (w["w_late"] or 0.0) * d["final_late_s"]
        + w["lambda"] * d["final_path_m"]
        + w["w_time"] * 0.0
    )
    assert rec == pytest.approx(d["final_fitness"], rel=1e-9), (
        f"重算 {rec} != 记录 {d['final_fitness']}"
    )
