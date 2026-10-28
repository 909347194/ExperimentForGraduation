# -*- coding: utf-8 -*-
"""构造式解码 + 三层选机 + 小 N 枚举参照的常驻回归测试（exp04 B1/B2/B3）。

锁定的契约：

**B1 构造式解码（``decode_mode="constructive"``）**
1. 默认 ``decode_mode="repair_chain"``——生产口径 bit-exact 不变；
2. 构造式解码产出的航次**全部满足航程约束**（内联可行性）；
3. 第3层安全网：只有参与集**之外**的闲置机可达的任务，构造式臂也必须
   执行（Fix⑥ 内化——"不漏可达任务"由解码端保证）；
4. 证明性暂缓：任何机/巢都不可达的任务才 ``y_j=0``，且记入
   ``last_constructive_deferred``（不谎报完成）；
4b. 任务 ID ≠ ``self.tasks`` 下标（滚动周期 t.id 全局计数）：暂缓/待救
   全程 ID 口径，严禁二次映射。

**B2 三层选机（``POLICY_DEMAND_MARGINAL`` + 字典序边际）**
5. 边际按字典序 ``(Δ可服务数, −Δ覆盖代价)``：可服务数增量优先于代价
   下降（γ 再大也要派能多服务任务的机）；无可服务增量时按 γ 门限决定；
6. 策略返回子集 ≤ SRP 上界且非空。

**B3 近精确枚举参照**
7. 同架次预算下，参照适应度 ≤ 启发式适应度（gap ≥ 0）；
8. 规模超限时 ``executed=False`` 不硬算（组合爆炸防护）。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (
    POLICY_DEMAND_MARGINAL,
    MUASAssignmentEvaluator,
    MUASStageConfig,
    _select_by_lexicographic_marginal,
    enumerate_reference_participating,
    select_participating_uavs,
)

NEST_KEY = 1_000_000
RANGE = 80_000.0


def _task(tid: int, x: float, y: float, latest: float = 1e9) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=10.0,
                earliest=0.0, latest=latest, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0, y: float = 0.0,
         remaining_range: float = RANGE) -> UAV:
    return UAV(id=uid, x=x, y=y, z=0.0, remaining_range=remaining_range,
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


def _evaluator(tasks, uavs, nests, *, participating=None,
               available=None, config=None) -> MUASAssignmentEvaluator:
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(),
    )
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=list(participating if participating is not None else uavs),
        tasks=list(tasks),
        nests=list(nests),
        fleet_uavs=list(uavs),
        config=config or MUASStageConfig(),
        available_uavs=list(available if available is not None else uavs),
    )


def _all_tours_within_range(ev: MUASAssignmentEvaluator, sol) -> bool:
    for tour in sol.tours:
        if not tour.task_ids:
            continue
        ev.problem.evaluate(sol)  # 触发路径代价计算（与完成闸门同口径）
        limit = float(
            getattr(ev._uav_by_id[tour.uav_id], "remaining_range", math.inf)
        )
        cost = float(ev.problem.tour_path_cost(tour))
        if cost > limit + 1e-6:
            return False
    return True


# ---------------------------------------------------------------------------
# B1 构造式解码
# ---------------------------------------------------------------------------


def test_default_decode_mode_is_repair_chain():
    """默认口径必须保持 repair_chain（exp01/exp02 结果 bit-exact 的根基）。"""
    assert MUASStageConfig().decode_mode == "repair_chain"
    with pytest.raises(ValueError):
        MUASStageConfig(decode_mode="bogus")


def test_constructive_decode_feasible_and_full_coverage():
    """两机两任务（单任务航次 60km ≤ 80km）：构造式臂全执行且航程可行。"""
    tasks = [_task(0, 30_000.0, 0.0), _task(1, -15_000.0, 26_000.0)]
    uavs = [_uav(0), _uav(1)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator(
        tasks, uavs, nests,
        config=MUASStageConfig(decode_mode="constructive"),
    )
    sol = ev.build_solution([(0, 0), (1, 1)])
    assert set(sol.executed_task_ids) == {0, 1}
    assert _all_tours_within_range(ev, sol)
    assert ev.last_constructive_deferred == []


def test_constructive_rescues_task_reachable_only_from_fleet_idle_uav():
    """第3层安全网：t0 只有参与集外的闲置机 u1 飞得到，构造式臂必须执行它。

    几何：u0/u1/巢共线；t0 距 u0 110km（单任务航次 120km > 80km 预算），
    距 u1 与巢各 10km（航次 20km ≤ 80km）。participating 只有 u0。
    """
    tasks = [_task(0, 110_000.0, 0.0)]
    uavs = [_uav(0, x=0.0), _uav(1, x=100_000.0)]
    nests = [Nest(id=0, x=100_000.0, y=0.0, z=0.0, capacity=10)]
    # 构造式臂
    ev_c = _evaluator(
        tasks, uavs, nests, participating=[uavs[0]],
        config=MUASStageConfig(decode_mode="constructive"),
    )
    sol_c = ev_c.build_solution([(0, 0)])  # DMDE 把 t0 派给了飞不了的 u0
    assert 0 in sol_c.executed_task_ids
    carriers = {t.uav_id: list(t.task_ids) for t in sol_c.tours if t.task_ids}
    assert carriers == {1: [0]}, "t0 应被救援到参与集外的闲置机 u1"
    assert _all_tours_within_range(ev_c, sol_c)
    # 与 repair_chain 口径对齐（两条路径都必须执行该任务）
    ev_r = _evaluator(tasks, uavs, nests, participating=[uavs[0]])
    sol_r = ev_r.build_solution([(0, 0)])
    assert 0 in sol_r.executed_task_ids


def test_constructive_defers_only_when_unreachable():
    """证明性暂缓：t1 距所有机/巢都超预算才 y_j=0，且记入诊断出口。

    几何（巢在原点）：t0 距巢 30km → u0 单任务航次 60km ≤ 80km（应执行）；
    t1 距巢 300km → 任何机单任务航次 ≥ 500km（u1 在 100km 处：
    200+300）＞ 80km（应暂缓）。
    """
    tasks = [_task(0, 30_000.0, 0.0), _task(1, 300_000.0, 0.0)]
    uavs = [_uav(0, x=0.0), _uav(1, x=100_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator(
        tasks, uavs, nests,
        config=MUASStageConfig(decode_mode="constructive"),
    )
    sol = ev.build_solution([(0, 0), (1, 1)])
    assert set(sol.executed_task_ids) == {0}
    assert ev.last_constructive_deferred == [1]
    assert _all_tours_within_range(ev, sol)


def test_constructive_handles_k_ge_n_regime():
    """任务少、无人机多（K ≥ N）：N 个任务各派一次，多余机闲置。

    3 架机、2 个任务——生产假设 K≤N−1 在此不成立；构造式臂应自然
    表达「用子集、其余闲置」，不要求每架参与机都有任务。
    """
    tasks = [_task(0, 10_000.0, 0.0), _task(1, -10_000.0, 20_000.0)]
    uavs = [_uav(0), _uav(1), _uav(2)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator(
        tasks, uavs, nests,
        config=MUASStageConfig(decode_mode="constructive"),
    )
    sol = ev.build_solution([(0, 0), (1, 1), (2, 0)])
    assert set(sol.executed_task_ids) == {0, 1}
    carriers = [t for t in sol.tours if t.task_ids]
    assert len(carriers) == 2, "应只用 2 架机，第 3 架闲置"
    assert _all_tours_within_range(ev, sol)


def test_constructive_task_ids_are_not_positions():
    """任务 ID ≠ self.tasks 下标（滚动周期 t.id 是全局计数）。

    生产 bug 回归（exp01 cycle2 冒烟抓到的 IndexError）：
    - step2/3/4 的 ``deferred`` 全程持有**任务 ID**，出口不得再经
      ``_task_ids`` 二次映射（ID 30+ 对 2 个任务的下标空间越界）；
    - ``pending`` 待救队列必须以 ID 口径进 ``_rescue_task``。
    """
    # 几何同 defers_only_when_unreachable，但 ID 平移到 30/31
    tasks = [_task(30, 30_000.0, 0.0), _task(31, 300_000.0, 0.0)]
    uavs = [_uav(0, x=0.0), _uav(1, x=100_000.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    cfg = MUASStageConfig(decode_mode="constructive")
    # 场景1：genome 覆盖全部；t31 在 step2 插入失败出列暂缓
    ev = _evaluator(tasks, uavs, nests, config=cfg)
    sol = ev.build_solution([(0, 0), (1, 1)])
    assert set(sol.executed_task_ids) == {30}
    assert ev.last_constructive_deferred == [31]
    assert _all_tours_within_range(ev, sol)
    # 场景2：genome 只覆盖 t30；t31 走 pending 待救队列（同样不可达）
    ev2 = _evaluator(tasks, uavs, nests, config=cfg)
    sol2 = ev2.build_solution([(0, 0)])
    assert set(sol2.executed_task_ids) == {30}
    assert ev2.last_constructive_deferred == [31]
    assert _all_tours_within_range(ev2, sol2)


# ---------------------------------------------------------------------------
# B2 三层选机：字典序边际
# ---------------------------------------------------------------------------


def _lex_cfg(gamma: float, tour_cap) -> MUASStageConfig:
    return MUASStageConfig(
        participating_policy=POLICY_DEMAND_MARGINAL,
        participation_cost=gamma,
        marginal_tour_cap=tour_cap,
    )


def test_lexicographic_marginal_first_uav_unconditional():
    uavs = [_uav(0), _uav(1)]
    cm = np.array([[10.0, 100.0], [100.0, 10.0]])
    rows = {u.id: i for i, u in enumerate(uavs)}
    picked = _select_by_lexicographic_marginal(uavs, _lex_cfg(1e9, 1000), cm, rows, 2)
    assert [u.id for u in picked] == [0]


def test_lexicographic_marginal_cost_dim_gated_by_gamma():
    """无可服务增量时，按 γ 门限决定是否派（成本维阈值）。"""
    uavs = [_uav(0), _uav(1)]
    cm = np.array([[10.0, 100.0], [5.0, 100.0]])  # u1 严格更近，Δcost=5
    rows = {u.id: i for i, u in enumerate(uavs)}
    picked_lo = _select_by_lexicographic_marginal(
        uavs, _lex_cfg(3.0, 1000), cm, rows, 2
    )
    picked_hi = _select_by_lexicographic_marginal(
        uavs, _lex_cfg(10.0, 1000), cm, rows, 2
    )
    assert [u.id for u in picked_lo] == [0, 1]   # Δcost=5 > γ=3 → 派
    assert [u.id for u in picked_hi] == [0]      # Δcost=5 ≤ γ=10 → 不派


def test_lexicographic_marginal_serving_gain_dominates_cost():
    """红线：可服务数增量优先于代价——γ 再大也要派能多服务任务的机。

    cap=1：u0 只能串 1 个任务（服务 t0），u1 服务 t1（u0 服务不了）。
    即便把 γ 设成天文数字，u1 也必须入选（Δserved=1 > 0）。
    """
    uavs = [_uav(0), _uav(1)]
    cm = np.array([[10.0, 20.0], [100.0, 5.0]])
    rows = {u.id: i for i, u in enumerate(uavs)}
    picked = _select_by_lexicographic_marginal(
        uavs, _lex_cfg(1e12, 1), cm, rows, 2
    )
    assert [u.id for u in picked] == [0, 1]


def test_demand_marginal_policy_smoke():
    """策略级 smoke：返回非空、≤ SRP 上界、全是可用机。"""
    uavs = [_uav(i) for i in range(4)]
    cm = np.array([
        [10.0, 100.0, 100.0],
        [100.0, 10.0, 100.0],
        [100.0, 100.0, 10.0],
        [50.0, 50.0, 50.0],
    ])
    rows = {u.id: i for i, u in enumerate(uavs)}
    cfg = MUASStageConfig(
        participating_policy=POLICY_DEMAND_MARGINAL,
        participation_cost=1.0,
        demand_coverage_radius=60.0,
    )
    picked = select_participating_uavs(uavs, 3, cfg, uav_target=cm, row_of_uav=rows)
    assert 1 <= len(picked) <= 2  # SRP 不变式 K ≤ N−1 = 2
    assert all(u in uavs for u in picked)


# ---------------------------------------------------------------------------
# B3 近精确枚举参照
# ---------------------------------------------------------------------------


def test_enumerate_reference_gap_nonneg():
    """同架次预算下：参照适应度 ≤ 启发式适应度（gap ≥ 0）。"""
    tasks = [_task(0, 10_000.0, 0.0), _task(1, -10_000.0, 20_000.0)]
    uavs = [_uav(0), _uav(1), _uav(2)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator(tasks, uavs, nests)
    rep = enumerate_reference_participating(ev, [0])
    assert rep["executed"] is True
    assert rep["gap"] >= -1e-9
    assert rep["reference"]["fitness"] <= rep["heuristic"]["fitness"] + 1e-9
    assert rep["n_subsets"] >= 1


def test_enumerate_reference_scale_guard():
    """规模超限 → executed=False，不硬算（组合爆炸防护）。"""
    tasks = [_task(0, 10_000.0, 0.0)]
    uavs = [_uav(i) for i in range(13)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator(tasks, uavs, nests)
    rep = enumerate_reference_participating(ev, [0], max_avail=12)
    assert rep["executed"] is False
    assert rep["reason"] == "scale"
