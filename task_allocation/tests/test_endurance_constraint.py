# -*- coding: utf-8 -*-
"""最大飞行时间约束（endurance）全链路测试。

锁定契约（与「最大航程约束」严格对称）：

1. 数据层：``UAV.max_time`` 由 build_uavs 从 ``uav_max_time`` 派生/显式灌入；
   为 ``None`` 时 ``check_time_constraint`` 退化为 0（约束不绑定，向后兼容）。
2. 可行性层：``endurance=True`` 且航次飞行时间超 ``max_time`` 时，记
   ``time_uav_<id>`` 硬违约、``is_feasible=False``，且**置 tour.feasible=False**
   （触发 horizon 返工/剔除，与 range 同款）——旧实现只记惩罚不置 feasible。
3. 覆盖闸门：超时任务无法插入/开班 ⇒ 判为 capacity_shortfall（物理装不下），
   而非 voluntary_skip（能装不装），与航程对称。
4. 派生口径：``uav_max_time=None`` 时派生 = ``uav_max_range / uav_speed``，
   因此满足航程的航次必满足时间（派生下两约束等价重述，不新增淘汰）。
"""

from __future__ import annotations

import math

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
from task_allocation.methods.muas.coverage_gate import audit_coverage
from task_allocation.methods.muas.problem import (
    ConstraintConfig,
    MUASProblemConfig,
    SelectiveMUASProblem,
)

NEST_KEY = 1_000_000


def _tour(sol_end_nest: int = 0) -> AllocationSolution:
    tour = UAVTour(
        uav_id=0,
        task_ids=[0],
        end_nest_id=sol_end_nest,
        start_x=0.0,
        start_y=0.0,
        start_z=0.0,
    )
    return AllocationSolution(
        tours=[tour], candidate_task_ids=[0], executed_task_ids=[0]
    )


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


def _problem(uav: UAV, *, cons: ConstraintConfig, max_range: float = 1e9) -> SelectiveMUASProblem:
    """单任务(150,0) + 单 UAV(0,0) + 单巢(0,0)；航程默认无界以隔离 endurance。"""
    uav.remaining_range = max_range
    t = Task(id=0, x=150.0, y=0.0, z=0.0, reward=5.0)
    n = Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)
    return SelectiveMUASProblem.from_entities(
        [t], [uav], [n],
        pairwise_cost=_pairwise([t], [uav], [n]),
        config=MUASProblemConfig(constraints=cons),
    )


def _uav(**kw) -> UAV:
    base = dict(id=0, x=0.0, y=0.0, z=0.0, speed=10.0, nest_id=0)
    base.update(kw)
    return UAV(**base)


# ── 1. 派生/退化简洁性：max_time=None ⇒ 不约束（向后兼容）─────────────


def test_endurance_none_no_violation():
    u = _uav(max_time=None)  # 不配置 ⇒ 约束退化为 0
    p = _problem(u, cons=ConstraintConfig(endurance=True, range=False, time_window=False))
    ok, det = p.check_feasible(_tour())
    assert ok is True
    assert det["hard_violation"] == 0.0
    assert not any(v.startswith("time_") for v in det["violations"])


# ── 2. 生效 + 标记航次不可行（核心修复）─────────────────────────────


def test_endurance_fires_and_marks_tour_infeasible():
    # 航程 300m / speed 10 = 30s；max_time=10s ⇒ 超 20s
    u = _uav(max_time=10.0)
    p = _problem(u, cons=ConstraintConfig(endurance=True, range=False, time_window=False))
    sol = _tour()
    ok, det = p.check_feasible(sol)
    assert ok is False
    assert det["hard_violation"] > 0.0
    assert "time_uav_0" in det["violations"]
    assert det["n_hard"] >= 1
    # 关键修复：超时航次被置为不可行，触发 horizon 返工/剔除
    # （旧实现只记惩罚不置 feasible，导致超时航次被放行交付）
    assert sol.tours[0].feasible is False
    assert det["feasible"] is False


def test_endurance_switch_off_no_violation():
    u = _uav(max_time=10.0)  # 即便配置了 max_time，关闭开关也不判违约
    p = _problem(u, cons=ConstraintConfig(endurance=False, range=False, time_window=False))
    ok, det = p.check_feasible(_tour())
    assert ok is True
    assert det["hard_violation"] == 0.0
    assert not any(v.startswith("time_") for v in det["violations"])


# ── 3. 与航程严格对称：两者可同时触发 ─────────────────────────────


def test_endurance_symmetric_with_range():
    # remaining_range=100(<300) 且 max_time=10(<30) ⇒ range 与 time 同违
    u = _uav(max_time=10.0)
    p = _problem(u, cons=ConstraintConfig(endurance=True, range=True, time_window=False),
                 max_range=100.0)
    ok, det = p.check_feasible(_tour())
    assert ok is False
    assert "range_uav_0" in det["violations"]
    assert "time_uav_0" in det["violations"]
    assert det["hard_violation"] > 0.0


# ── 4. 派生口径：满足航程 ⇒ 满足时间（派生下两约束等价）──────────────


def test_endurance_derived_consistent_with_range():
    # 不显式给 max_time ⇒ 派生 = max_range/speed；航程内航次必时间内
    u = _uav(max_time=None)
    p = _problem(u, cons=ConstraintConfig(endurance=True, range=True, time_window=False),
                 max_range=1e9)
    # 航程无界、派生 max_time 也对应无界 ⇒ 不违约
    ok, det = p.check_feasible(_tour())
    assert ok is True
    assert det["hard_violation"] == 0.0


# ── 5. 覆盖闸门：超时任务判 shortfall（非 skip）────────────────────


def _gate_problem(uav: UAV) -> SelectiveMUASProblem:
    t = Task(id=0, x=150.0, y=0.0, z=0.0, reward=5.0)
    n = Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)
    return SelectiveMUASProblem.from_entities(
        [t], [uav], [n],
        pairwise_cost=_pairwise([t], [uav], [n]),
        config=MUASProblemConfig(constraints=ConstraintConfig(endurance=True, range=True)),
    )


def test_gate_time_infeasible_is_shortfall_not_skip():
    # max_time=10s, speed=10 ⇒ 最大可飞 100m；单任务往返 300m 超时
    u = _uav(max_time=10.0, remaining_range=1e9)
    p = _gate_problem(u)
    routes = {0: []}          # 任务 0 未交付
    ends = {0: 0}
    stats = audit_coverage(p, routes, ends, task_ids=[0])
    assert stats.n_shortfall == 1, stats.to_dict()
    assert stats.n_skip == 0


def test_gate_time_feasible_is_skip_when_unassigned():
    # max_time=100s, speed=10 ⇒ 最大可飞 1000m；单任务往返 300m 时间可行
    u = _uav(max_time=100.0, remaining_range=1e9)
    p = _gate_problem(u)
    routes = {0: []}
    ends = {0: 0}
    stats = audit_coverage(p, routes, ends, task_ids=[0])
    assert stats.n_skip == 1, stats.to_dict()
    assert stats.n_shortfall == 0
