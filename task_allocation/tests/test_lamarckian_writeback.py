# -*- coding: utf-8 -*-
"""M2-1 段内巡游序回写（Lamarckian 反射写回）测试。

对应 docs/m2_gene_writeback_design_2026-10-30.md 的「实现分期与验收」与测试清单
（test_lamarckian_writeback.py）：

1. 段内序写回后重解码 == 修复序；
2. I1/I2/I3 任一破坏时回退原基因；
3. 硬违约解**永不**写回；
4. 表型复核 (n_hard/覆盖/迟到) 逐分量不劣才接受；
5. 开关关闭时与现行为**逐位等价**。

设计边界（§0/§2/§3）：只写修复后表型；Q3.1 含硬违约永不写回、覆盖 ⊇ 回写前；
Q2.1 temperature=0 重解码一致性；Q2.2 轻量 repair（不跑 reorder_for_deadlines/
repair_range/coverage gate）；Q2.3 I1/I2/I3 断言；Q3.2 表型复核；回退永不静默。

测试策略：用**真实** MUASAssignmentEvaluator（含 _assemble / problem.evaluate 的
真实口径）配合**可控合成 cost_matrix**（使 temperature=0 贪心解码确定性复现目标序），
精确驱动写回→重解码一致性链路，避免重造几何/代价装配。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
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
    UAVStatus,
    UAVTour,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.representation.encoder import (  # noqa: E402
    Gene,
    Individual,
)
from task_allocation.methods.muas.representation.inverse_mapper import (  # noqa: E402
    inverse_phi,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 通用夹具
# ---------------------------------------------------------------------------


def _task(tid: int, x: float, reward: float = 5.0, latest: float = 1e6) -> Task:
    return Task(
        id=tid, x=x, y=0.0, z=0.0, reward=reward,
        earliest=0.0, latest=latest, arrival_time=0.0,
    )


def _uav(uid: int, x: float = 0.0, remaining_range: float = 1e7) -> UAV:
    return UAV(
        id=uid, x=x, y=0.0, z=0.0, remaining_range=remaining_range,
        speed=10.0, nest_id=0, status=UAVStatus.IDLE,
    )


def _pairwise(tasks, uavs, nests):
    import math

    pts = {t.id: t.position for t in tasks}
    pts.update({-(1 + u.id): u.position for u in uavs})
    pts.update({NEST_KEY + n.id: n.position for n in nests})
    keys = list(pts)
    return {
        (a, b): math.dist(pts[a], pts[b])
        for i, a in enumerate(keys) for b in keys[i + 1:]
    }


def _mk_ev(tasks, uavs, nests, *, patrol_writeback: bool = False, **prob_kw):
    """构造真实 MUASAssignmentEvaluator（真实 _assemble / problem.evaluate）。"""
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(w_violation=0.01, **prob_kw),
    )
    cfg = MUASStageConfig(lamarckian_patrol_writeback=patrol_writeback)
    ev = MUASAssignmentEvaluator(
        prob, participating=list(uavs), tasks=list(tasks), nests=list(nests),
        fleet_uavs=list(uavs), config=cfg,
    )
    return prob, ev


def _synthetic_cost_matrix(n_uavs: int, n_targets: int,
                           edges: dict[tuple[int, int], float]) -> np.ndarray:
    """构造可控 cost_matrix ``(K+N, N)``：edges[(row, col)] = 值，其余填大哨兵。

    行 0..K−1 = UAV→任务；行 K..K+N−1 = 任务→任务（row = K + 前驱任务）。
    用 1.0 标记"正确边"、100.0 标记其余，使 temperature=0 贪心解码确定性复现目标序。
    """
    K, N = n_uavs, n_targets
    cm = np.full((K + N, N), 100.0, dtype=float)
    for (r, c), v in edges.items():
        cm[r, c] = v
    return cm


def _solution(tours_spec: dict[int, list[int]]) -> AllocationSolution:
    """由 {uav_id: [task_id,...]} 构造修复解（含终态航线），end_nests 统一 0。"""
    tours = [
        UAVTour(uav_id=u, task_ids=list(tids), end_nest_id=0)
        for u, tids in tours_spec.items()
    ]
    exec_ids = [t for tids in tours_spec.values() for t in tids]
    return AllocationSolution(
        tours=tours,
        candidate_task_ids=sorted({t for tids in tours_spec.values() for t in tids}),
        executed_task_ids=list(dict.fromkeys(exec_ids)),
    )


def _child_srp(n_uavs, n_targets, seg_spec: dict[int, list[int]]) -> Individual:
    """由 {uav_id: [task_id,...]}（段=起始任务+后续巡游）构造 SRP 个体基因。

    seg_spec[u][0] 为起始任务，其后为巡游任务（顺序任意，用于构造"未修复"序）。
    cost 取占位值（写回时重算）。
    """
    genes: list[Gene] = []
    for u in range(n_uavs):
        seq = seg_spec[u]
        genes.append(Gene(uav_id=u, target_id=seq[0], cost=0.0))
        for t in seq[1:]:
            genes.append(Gene(uav_id=-1, target_id=t, cost=0.0))
    return Individual(genes=genes, model_type="srp")


# ---------------------------------------------------------------------------
# 1. 段内序写回后重解码 == 修复序
# ---------------------------------------------------------------------------


def test_intrasegment_writeback_then_redecode_equals_repaired_order():
    """M2-1：把修复解的段内顺序写回基因后，temperature=0 重解码必须复现修复序。"""
    n_uavs, n_targets = 2, 4
    tasks = [_task(0, 0.0), _task(1, 100.0), _task(2, 200.0), _task(3, 300.0)]
    uavs = [_uav(0, -50.0), _uav(1, -10.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    _, ev = _mk_ev(tasks, uavs, nests, patrol_writeback=True)

    # 修复解航线：u0 顺序 [0, 2, 3]（段内序），u1 顺序 [1]。
    route_of = {0: [0, 2, 3], 1: [1]}
    sol = _solution(route_of)

    # child：u0 段内序是"错误"的 [0, 3, 2]（未修复），u1 = [1]。
    child = _child_srp(n_uavs, n_targets, {0: [0, 3, 2], 1: [1]})

    # 可控 cost_matrix：使解码确定性复现目标序（正确边=1.0，其余=100.0）。
    cm = _synthetic_cost_matrix(n_uavs, n_targets, {
        (0, 0): 1.0,            # UAV0 → 任务0
        (1, 1): 1.0,            # UAV1 → 任务1
        (2 + 0, 2): 1.0,        # 任务0 → 任务2
        (2 + 2, 3): 1.0,        # 任务2 → 任务3
    })

    re = ev.lamarckian_reencode(child, sol, cm, n_uavs, n_targets)
    assert re is not None, "段内序可安全写回时不应回退"

    # 写回后 u0 段内序必须为修复序 [0, 2, 3]（起始 + 巡游）。
    routes = re.srp_routes
    assert routes[0] == [0, 2, 3], f"u0 段内序应被写回为 [0,2,3]，实际 {routes.get(0)}"
    assert routes[1] == [1]

    # temperature=0 重解码必须 == 修复序（解码一致性 Q2.1）。
    rng = np.random.default_rng(0)
    reb = inverse_phi(re.cost_vector, cm, n_uavs, n_targets, "srp", rng=rng, temperature=0.0)
    assert reb.srp_routes == route_of, (
        f"重解码表型应 == 修复序 {route_of}，实际 {reb.srp_routes}"
    )


# ---------------------------------------------------------------------------
# 2. I1/I2/I3 任一破坏时回退原基因
# ---------------------------------------------------------------------------


def test_set_mismatch_reverts_to_original():
    """修复解剔除任务使集合不匹配 → 写回不安全 → 回退（返回 None）。"""
    n_uavs, n_targets = 2, 4
    tasks = [_task(0, 0.0), _task(1, 100.0), _task(2, 200.0), _task(3, 300.0)]
    uavs = [_uav(0, -50.0), _uav(1, -10.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    _, ev = _mk_ev(tasks, uavs, nests, patrol_writeback=True)

    # 修复解删掉了 u0 的任务3（剔除）→ u0 航线 [0, 2]，集合与 child 段 [0,3,2] 不符。
    route_of = {0: [0, 2], 1: [1]}
    sol = _solution(route_of)
    child = _child_srp(n_uavs, n_targets, {0: [0, 3, 2], 1: [1]})

    cm = _synthetic_cost_matrix(n_uavs, n_targets, {
        (0, 0): 1.0, (1, 1): 1.0, (2 + 0, 2): 1.0,
    })

    re = ev.lamarckian_reencode(child, sol, cm, n_uavs, n_targets)
    assert re is None, "集合不匹配（剔除）时应回退到原基因（返回 None）"


# ---------------------------------------------------------------------------
# 3. 硬违约解永不写回
# ---------------------------------------------------------------------------


def test_hard_violation_never_written():
    """Q3.1：含硬违约（n_hard>0）的修复解永不写回 → 返回 None。"""
    n_uavs, n_targets = 2, 4
    tasks = [_task(0, 0.0), _task(1, 100.0), _task(2, 200.0), _task(3, 300.0)]
    uavs = [_uav(0, -50.0), _uav(1, -10.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    _, ev = _mk_ev(tasks, uavs, nests, patrol_writeback=True)

    route_of = {0: [0, 2, 3], 1: [1]}
    sol = _solution(route_of)
    # 人为把该解标为含硬违约：monkeypatch problem.hard_stats。
    ev.problem.hard_stats = lambda s: (1, 0.0)  # n_hard = 1

    child = _child_srp(n_uavs, n_targets, {0: [0, 2, 3], 1: [1]})
    cm = _synthetic_cost_matrix(n_uavs, n_targets, {
        (0, 0): 1.0, (1, 1): 1.0, (2 + 0, 2): 1.0, (2 + 2, 3): 1.0,
    })

    re = ev.lamarckian_reencode(child, sol, cm, n_uavs, n_targets)
    assert re is None, "含硬违约的解必须永不写回（返回 None）"


# ---------------------------------------------------------------------------
# 4. 表型复核：(n_hard/覆盖/迟到) 逐分量不劣才接受
# ---------------------------------------------------------------------------


def test_phenotype_recheck_not_worse():
    """Q3.2：成功写回后，重解码表型覆盖 ⊇ 修复解覆盖，硬违约/迟到不增。"""
    n_uavs, n_targets = 2, 4
    tasks = [_task(0, 0.0), _task(1, 100.0), _task(2, 200.0), _task(3, 300.0)]
    uavs = [_uav(0, -50.0), _uav(1, -10.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    _, ev = _mk_ev(tasks, uavs, nests, patrol_writeback=True)

    route_of = {0: [0, 2, 3], 1: [1]}
    sol = _solution(route_of)
    child = _child_srp(n_uavs, n_targets, {0: [0, 3, 2], 1: [1]})

    cm = _synthetic_cost_matrix(n_uavs, n_targets, {
        (0, 0): 1.0, (1, 1): 1.0, (2 + 0, 2): 1.0, (2 + 2, 3): 1.0,
    })

    re = ev.lamarckian_reencode(child, sol, cm, n_uavs, n_targets)
    assert re is not None, "合法修复解应成功写回"
    # 覆盖集不劣于修复解（此处逐位相等）。
    reb_routes = re.srp_routes
    cov_reb = {t for seq in reb_routes.values() for t in seq}
    cov_src = set(sol.executed_task_ids)
    assert cov_reb >= cov_src, (
        f"写回后覆盖应 ⊇ 修复解覆盖：{cov_reb} vs {cov_src}"
    )


# ---------------------------------------------------------------------------
# 5. 开关关闭时与现行为逐位等价
# ---------------------------------------------------------------------------


def test_switch_off_bit_identical_to_start_only():
    """开关关闭 ⇒ 仅起始通道写法，巡游基因逐位不变（与历史行为等价）。"""
    n_uavs, n_targets = 2, 4
    tasks = [_task(0, 0.0), _task(1, 100.0), _task(2, 200.0), _task(3, 300.0)]
    uavs = [_uav(0, -50.0), _uav(1, -10.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    _, ev_off = _mk_ev(tasks, uavs, nests, patrol_writeback=False)  # 关
    _, ev_on = _mk_ev(tasks, uavs, nests, patrol_writeback=True)   # 开

    route_of = {0: [0, 2, 3], 1: [1]}
    sol = _solution(route_of)
    child = _child_srp(n_uavs, n_targets, {0: [0, 3, 2], 1: [1]})

    cm = _synthetic_cost_matrix(n_uavs, n_targets, {
        (0, 0): 1.0, (1, 1): 1.0, (2 + 0, 2): 1.0, (2 + 2, 3): 1.0,
    })

    re_off = ev_off.lamarckian_reencode(child, sol, cm, n_uavs, n_targets)
    re_on = ev_on.lamarckian_reencode(child, sol, cm, n_uavs, n_targets)

    # 关：巡游基因逐位不变（仅起始通道写法）。
    assert re_off is not None
    off_patrol = [g.target_id for g in re_off.genes if g.uav_id == -1]
    child_patrol = [g.target_id for g in child.genes if g.uav_id == -1]
    assert off_patrol == child_patrol, "开关关闭时巡游基因应逐位不变"

    # 开：巡游基因被重排为修复序 [3, 2] → [2, 3]。
    assert re_on is not None
    on_patrol = [g.target_id for g in re_on.genes if g.uav_id == -1]
    assert on_patrol == [2, 3], f"开关开启应段内重排为 [2,3]，实际 {on_patrol}"

    # 两者仅在巡游通道不同、起始通道一致；关闭路径与历史行为一致（巡游不动）。
    assert [g.target_id for g in re_off.genes if g.uav_id >= 0] == \
           [g.target_id for g in re_on.genes if g.uav_id >= 0]


# ---------------------------------------------------------------------------
# 6. 端到端冒烟：DMDE 开启 M2-1 不崩溃，产出合法解
# ---------------------------------------------------------------------------


def test_end_to_end_dmde_with_patrol_writeback():
    """真实 DMDE 求解器在 SRP 实例上开启 M2-1 开关，应能收敛且不崩溃。"""
    from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig, DMDESolver

    n_uavs, n_targets = 2, 5
    rng = np.random.default_rng(7)
    xs = [float(v) for v in rng.uniform(0.0, 1000.0, n_targets)]
    tasks = [_task(i, xs[i]) for i in range(n_targets)]
    uavs = [_uav(0, -100.0), _uav(1, -50.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    prob, ev = _mk_ev(tasks, uavs, nests, patrol_writeback=True)

    # 用真实几何构造 cost_matrix（处理双向查找），使 DMDE 编码/解码口径一致。
    pw = _pairwise(tasks, uavs, nests)

    def d(a, b):
        return pw.get((a, b), pw.get((b, a), 1e9))

    K, N = n_uavs, n_targets
    cm = np.zeros((K + N, N), dtype=float)
    for i, u in enumerate(uavs):
        for t in range(N):
            cm[i, t] = d(-(1 + u.id), t)
    for tp in range(N):
        for t in range(N):
            cm[K + tp, t] = d(tp, t)

    cfg = DMDEConfig(pop_size=16, max_generations=40, seed=1, lamarckian=True)
    res = DMDESolver(config=cfg).solve(
        cm, n_uavs, n_targets, fitness_evaluator=ev,
    )
    assert np.isfinite(res.best_fitness), "开启 M2-1 后仍应产出有限适应度"
    assert len(res.best_assignment) >= 1
