# -*- coding: utf-8 -*-
"""覆盖门禁口径（唯一主口径）+ 解码端合并修复 聚焦测试。

契约（2026-10-24：非门禁 big-M 路径已从代码移除，见
``docs/objective_caliber_two_approaches_archive_2026-10-24.md``）：

1. **可装必须装**：一阶意义下可装载的任务必须交付；能装没装
   （voluntary_skip）触发 W_skip 层、代价压倒任意成本差；
2. **完成等价成本裁决**：同完成集合取更省解（"合并到更少机"的原动力）；
3. **闲置合法**：部分 UAV 零任务不产生任何惩罚/违约；
4. **合并修复**：任务少时整航次并入更少机，不删任务；
5. **e2e**：少任务聚集场景下求解器输出用更少的机完成全部任务；
6. **完成是约束**：可达任务不得经济性剔除，暂缓只能源于可行性修复。

背景：DMDE 单适应度框架不动，适应度仅含 硬违约 + W_skip·n_skip +
迟到 + λ·path；覆盖由门禁（构造修复 + W_skip 层）承担，
成本项（含惩罚/最坏机时间）只在完成等价的解间裁决。
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
        config=MUASProblemConfig(**kw),
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
    """完成 2 个任务（F1=2）的方案即使贵 4e5 也必须胜过只完成 1 个（F1=1）。

    门禁口径下该优势由 W_skip 层提供：partial 把一阶可装载的任务 1 留在
    S 内未交付 ⇒ 判 voluntary_skip ⇒ W_skip·1 压倒一切成本差（而非旧口径的
    −m·F1 支配项）。F1 只作指标、不进 f，但"可装必须装"仍强制全交。
    """
    tasks, uavs, nests, pairwise = _dominance_fixture()
    prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0)

    full = _sol([(0, [0, 1], 0)], cand=[0, 1])     # cost ≈ 100+1+4e5
    partial = _sol([(0, [0], 0)], cand=[0, 1])     # cost = 100+100

    assert prob.scalar_fitness(full) < prob.scalar_fitness(partial), (
        "可装必须装失效：partial 留一阶可装载任务未交付应触发 W_skip 而更差"
    )
    # 完成量差异体现在 f1 指标上（不进 f，但指标须正确）
    assert prob.evaluate(full).f1_reward == 2.0
    assert prob.evaluate(partial).f1_reward == 1.0


def test_gate_caliber_forces_loadable_tasks_delivered():
    """门禁口径（完成是约束）：可装却没装（voluntary_skip）必被 W_skip 压倒，
    即便省下巨额航程也不得赢。"""
    tasks, uavs, nests, pairwise = _dominance_fixture()
    prob = _prob(tasks, uavs, nests, pairwise, lambda_cost=1.0)

    full = _sol([(0, [0, 1], 0)], cand=[0, 1])
    partial = _sol([(0, [0], 0)], cand=[0, 1])
    f_full = prob.scalar_fitness(full)
    f_partial = prob.scalar_fitness(partial)
    assert f_full < f_partial, (
        "可装任务必须交付：partial 留一阶可装载任务 1 必触发 W_skip 罚层而更差"
        f"（f_full={f_full}, f_partial={f_partial}）"
    )
    # partial 确属 voluntary_skip（任务 1 一阶可插入既有航次）
    gs = prob.evaluate(partial).meta["gate_stats"]
    assert gs["n_skip"] >= 1, f"partial 必须被记 voluntary_skip：{gs}"


def test_explicit_big_m_override():
    """big_m 显式给定时按给定值生效（用于 W_hard/W_skip 的推导基数）。

    注：big_m 在门禁口径下**只**作为硬违约/门禁权重的推导基数
    （W_hard = κ·m·R_pool），不再进入 f 的 −m·F1 项。
    """
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
        problem_config=MUASProblemConfig(),
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
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=5, seed=1),
        current_time=0.0,
    )
    executed = set(stage.solution.executed_task_ids)
    assert executed == {0, 1, 2}


# ---------------------------------------------------------------------------
# 6. 门禁口径下经济性剔除自动关闭（完成是约束）
# ---------------------------------------------------------------------------


def test_gate_caliber_never_prunes_completable_task():
    """门禁口径（完成是约束）：即便任务代价 ≫ 收益，也不经济性剔除——
    可达任务必须交付，暂缓只能源于可行性修复。

    构造：单任务、极高代价（返航 1e5 ≫ 收益 1），求解器仍须交付它。
    """
    tasks = [_task(0, 100.0, 1.0)]
    uavs = [_uav(0, 0.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]
    pairwise = {(-1, 0): 1e5, (0, NEST_KEY): 1e5}  # 返航 1e5 ≫ 收益 1

    cb = build_pairwise_costs(tasks, uavs, nests=nests, dem=None,
                              estimator=None, nest_offset=NEST_KEY)
    stage = run_selective_muas(
        tasks, uavs, nests, cb,
        fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=6, max_generations=3, seed=1),
        current_time=0.0,
    )
    assert bool(stage.solution.executed_task_ids), (
        "门禁口径下可达任务不得被经济性剔除（完成是约束）"
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
        problem_config=MUASProblemConfig(w_violation=0.01),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=5, seed=1),
        current_time=0.0,
    )
    d = stage.diagnostics
    for key in ("final_fitness", "final_f1", "final_path_m", "final_late_s",
                "final_n_hard", "final_excess_m", "fitness_weights"):
        assert key in d, f"diagnostics 缺字段 {key}"
    w = d["fitness_weights"]
    gs = d.get("gate_stats") or {}
    rec = (
        w["w_hard"] * d["final_n_hard"]
        + w["w_excess"] * d["final_excess_m"]
        + (w["w_skip"] or 0.0) * int((gs or {}).get("n_skip", 0))
        + (w["w_late"] or 0.0) * d["final_late_s"]
        + w["lambda"] * d["final_path_m"]
    )
    assert rec == pytest.approx(d["final_fitness"], rel=1e-9), (
        f"重算 {rec} != 记录 {d['final_fitness']}"
    )
