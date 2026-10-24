# -*- coding: utf-8 -*-
"""交付后成本 polish 测试（2026-10-24，组内 2-opt / Or-opt）。

契约（对应 docs/fitness_path_optimization_diagnosis_2026-10-24.md §9 排序 1+2）：

1. **ETA 原语一致**：``_eta_list``（单趟 O(n)）与 ``_arrival_time_of``
   （逐位重算）逐位相等——廉价原语不得悄悄换口径；
2. **压航程**：坏顺序（之字形）经 polish 后航程严格下降，且顺序确为更优；
3. **执行集不变**：2-opt/Or-opt 是航次内置换 ⇒ 每机任务集合与执行集逐位
   不变（「覆盖优先、成本随后」的构造性保证）；
4. **迟到门**：唯一能压航程的候选若把任务推过 deadline ⇒ 拒绝（不制造新
   违约、不加深旧违约）；
5. **航程门**：候选全航程超该机剩余航程 ⇒ 拒绝；
6. **eps 门**：改进量小于阈值 ⇒ 不采纳；
7. **无改进即原样**：已最优时零接受、返回同一解对象（逐位不变）；
8. **配置校验**：非法轮数/eps 抛 ValueError；默认开启。

动机背景：早退缺陷（``_best_order`` 原序零迟到直接返回）使航程从未进入
比较，而解码期「最近邻」修复被 A/B 证伪（访问顺序是 DE 编码的一部分，
贪心覆盖搜索结果净收益为零）。正确解法 = 作用于**交付解**的 2-opt/Or-opt，
只接受严格改进 ⇒ 不可能比原解差。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000


def _task(tid: int, x: float, reward: float = 5.0,
          earliest: float = 0.0, latest: float = 1e6) -> Task:
    return Task(id=tid, x=x, y=0.0, z=0.0, reward=reward,
                earliest=earliest, latest=latest, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0, remaining_range: float = 1e7) -> UAV:
    return UAV(id=uid, x=x, y=0.0, z=0.0, remaining_range=remaining_range,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


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


def _mk(tasks, uavs, nests, *, stage_cfg=None, **prob_kw):
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(w_violation=0.01, w_time=0.0, **prob_kw),
    )
    ev = MUASAssignmentEvaluator(
        prob, participating=list(uavs), tasks=list(tasks), nests=list(nests),
        fleet_uavs=list(uavs), config=stage_cfg or MUASStageConfig(),
    )
    return prob, ev


def _routes_of(sol) -> dict[int, list[int]]:
    return {t.uav_id: list(t.task_ids) for t in sol.tours if t.task_ids}


# ---------------------------------------------------------------------------
# 1. ETA 原语一致性（单趟 O(n) vs 逐位重算 O(n²)）
# ---------------------------------------------------------------------------


def test_eta_list_matches_arrival_time_of():
    """_eta_list 必须与 _arrival_time_of 逐位相等（含 earliest 等待分支）。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0)]
    tasks = [
        _task(0, 100.0, earliest=0.0, latest=1e6),
        _task(1, 500.0, earliest=80.0, latest=1e6),   # 到达 40s < 80 ⇒ 等待
        _task(2, 300.0, earliest=0.0, latest=1e6),
    ]
    _, ev = _mk(tasks, uavs, nests, default_service_time=30.0)

    tids = [0, 1, 2]
    fast = ev._eta_list(0, tids)
    slow = [ev._arrival_time_of(0, tids, p) for p in range(len(tids))]
    assert fast == pytest.approx(slow, abs=1e-9), (
        f"单趟 ETA 与逐位重算不一致：{fast} vs {slow}"
    )
    # 等待分支确实被触发（第 2 个任务的到达被 earliest 抬升）
    assert fast[1] >= 80.0


# ---------------------------------------------------------------------------
# 2. 压航程 + 执行集不变（核心收益场景）
# ---------------------------------------------------------------------------


def test_two_opt_shrinks_path_and_keeps_task_set():
    """之字形坏顺序 → polish 后航程严格下降，任务集合逐位不变。"""
    nests = [Nest(id=0, x=1000.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0)]
    # x: 100 / 900 / 200 / 800（坏顺序 = 编排序）
    tasks = [_task(0, 100.0), _task(1, 900.0), _task(2, 200.0), _task(3, 800.0)]
    prob, ev = _mk(tasks, uavs, nests)

    base = ev.evaluate([(0, 0), (0, 1), (0, 2), (0, 3)])
    sol_before = base.solution
    path_before = prob.path_cost(sol_before)
    routes_before = _routes_of(sol_before)
    exec_before = set(sol_before.executed_task_ids)

    sol_after, stats = ev.post_cost_polish(sol_before)
    assert stats["n_accepted"] > 0, f"未接受任何移动：{stats}"
    assert stats["n_2opt"] + stats["n_oropt"] == stats["n_accepted"]

    path_after = prob.path_cost(sol_after)
    assert path_after < path_before - 1.0, (
        f"航程未严格下降：{path_before} → {path_after}"
    )
    # 覆盖不侵蚀：每机任务**集合**与执行集逐位不变（顺序正是被优化的对象）
    assert {k: sorted(v) for k, v in _routes_of(sol_after).items()} == {
        k: sorted(v) for k, v in routes_before.items()
    }, "机内任务集合被改变"
    assert set(sol_after.executed_task_ids) == exec_before, "执行集被改变"
    # 之字形应被理顺（100→200→800→900 或等价更优序）
    tour = next(t for t in sol_after.tours if t.task_ids)
    assert tour.task_ids == [0, 2, 3, 1], f"未达期望更优序：{tour.task_ids}"
    assert path_after == pytest.approx(1000.0, abs=1e-6)
    # 无时间窗违约场景 ⇒ 不引入迟到
    assert prob.soft_violation(sol_after) <= prob.soft_violation(sol_before) + 1e-9
    assert prob.hard_stats(sol_after)[0] == 0


def test_cost_polish_noop_when_already_optimal():
    """已最优（单调递增 + 巢在远端）：零接受、返回同一解对象。"""
    nests = [Nest(id=0, x=1000.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0)]
    tasks = [_task(0, 100.0), _task(1, 200.0), _task(2, 300.0)]
    _, ev = _mk(tasks, uavs, nests)

    sol = ev.evaluate([(0, 0), (0, 1), (0, 2)]).solution
    out, stats = ev.post_cost_polish(sol)
    assert stats.get("n_accepted", 0) == 0
    assert out is sol, "无改进时必须原样返回（逐位不变）"


# ---------------------------------------------------------------------------
# 3. 迟到门：更低航程但制造迟到的候选必须被拒绝
# ---------------------------------------------------------------------------


def test_late_gate_rejects_cheaper_but_late_order():
    """唯一压航程的候选把任务推过 deadline ⇒ 拒绝（不制造新违约）。

    构造：service=30s。顺序 [T1(500), T0(100)] 零迟到但航程 1800；
    顺序 [T0, T1] 航程 1000 但 T1 到达 80s > deadline 60s ⇒ 必须拒绝。
    """
    nests = [Nest(id=0, x=1000.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0)]
    tasks = [
        _task(0, 100.0, latest=1000.0),   # 宽松
        _task(1, 500.0, latest=60.0),     # 紧：必须先访
    ]
    prob, ev = _mk(tasks, uavs, nests, default_service_time=30.0)

    # 基线 = 零迟到序（T1 先访）：到达 50s ≤ 60；航程 500+400+900=1800
    base = ev.evaluate([(0, 1), (0, 0)])
    sol = base.solution
    assert prob.soft_violation(sol) == 0.0, "基线应零迟到"
    path_before = prob.path_cost(sol)

    out, stats = ev.post_cost_polish(sol)
    assert stats.get("n_accepted", 0) == 0, (
        f"接受了会制造迟到的候选：{stats}"
    )
    assert out is sol
    assert prob.path_cost(out) == path_before


# ---------------------------------------------------------------------------
# 4. 航程门：候选超剩余航程 ⇒ 拒绝
# ---------------------------------------------------------------------------


def test_range_gate_rejects_over_budget_candidates():
    """剩余航程小于任何可行全航程时，一切候选（含更优序）都被拒绝。"""
    nests = [Nest(id=0, x=1000.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0, remaining_range=900.0)]   # 任何全航程 ≥ 1000
    tasks = [_task(0, 100.0), _task(1, 900.0)]
    _, ev = _mk(tasks, uavs, nests)

    routes, ends, stats = ev._cost_polish_routes({0: [1, 0]}, {0: 0})
    assert stats["n_2opt"] + stats["n_oropt"] == 0, "超航程候选不得被接受"
    assert routes == {0: [1, 0]}, "路由不得被改动"


# ---------------------------------------------------------------------------
# 5. eps 门与配置校验
# ---------------------------------------------------------------------------


def test_eps_gate_blocks_tiny_improvements():
    """阈值 huge ⇒ 任何改进都小于 eps ⇒ 不采纳。"""
    nests = [Nest(id=0, x=1000.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0)]
    tasks = [_task(0, 100.0), _task(1, 900.0), _task(2, 200.0)]
    _, ev = _mk(
        tasks, uavs, nests,
        stage_cfg=MUASStageConfig(cost_polish_eps=1.0e12),
    )
    sol = ev.evaluate([(0, 0), (0, 1), (0, 2)]).solution
    out, stats = ev.post_cost_polish(sol)
    assert stats.get("n_accepted", 0) == 0
    assert out is sol


def test_stage_config_validates_cost_polish_fields():
    with pytest.raises(ValueError, match="cost_polish_max_rounds"):
        MUASStageConfig(cost_polish_max_rounds=0)
    with pytest.raises(ValueError, match="cost_polish_eps"):
        MUASStageConfig(cost_polish_eps=-1.0)
    cfg = MUASStageConfig()
    assert cfg.cost_polish_enabled is True, "修复后默认必须开启（生产口径）"
    assert cfg.cost_polish_max_rounds == 30
    assert cfg.cost_polish_eps == 1.0
