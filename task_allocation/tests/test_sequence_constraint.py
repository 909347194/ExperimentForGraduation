# -*- coding: utf-8 -*-
"""时序（任务执行顺序）约束单元测试（2026-10-29 落地）。

背景：``constraints.sequence`` 开关自 2026-10-18 起恒为 True，但 ``Task``
**从未提供** ``sequence_group`` 字段 ⇒ ``check_sequence_constraint`` 恒
返回 0，时序约束是**空壳**。本文件锁定补齐后的口径：

    1. 同航次内同链任务必须按 ``sequence_order`` 升序访问，逆序 = 违背；
    2. 违背经 ``ConstraintReport.add_seq`` 进**硬违约桶**（判物理不可行），
       与航程/续航同级，不是软惩罚；
    3. 无 ``sequence_group``（默认）时惰性退化为 0 —— 既有实验基线不变；
    4. 同一链被拆到不同 UAV 时**不判违约**（当前只判同航次内，如实锁定
       该口径边界，避免日后误读为「已支持跨机」）；
    5. 解码侧构造性修复 ``_sequence_fix`` 只调序、不增删任务；
    6. 合成任务链生成：默认关闭 ⇒ 不消耗随机数、不贴标签。
"""

from __future__ import annotations

from types import SimpleNamespace

from task_allocation.common.task_scenario import (
    SyntheticTaskConfig,
    build_synthetic_tasks,
)
from task_allocation.common.types import AllocationSolution, Nest, Task, UAV, UAVTour
from task_allocation.methods.muas.constraints import (
    check_sequence_constraint,
    sequence_violation_pairs,
)
from task_allocation.methods.muas.problem import SelectiveMUASProblem
from task_allocation.methods.muas.solvers.decode_repair import DecodeRepairMixin


def _uav(uid=0, remaining=1e6, speed=10.0, nest_id=0):
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, remaining_range=remaining,
               speed=speed, nest_id=nest_id)


def _task(tid, *, group=None, order=0, x=100.0, y=0.0):
    return Task(id=tid, x=x, y=y, z=0.0, reward=5.0,
                sequence_group=group, sequence_order=order)


def _tour(*tids, uid=0, nest=0):
    return UAVTour(uav_id=uid, task_ids=list(tids), end_nest_id=nest)


def _problem(tasks, *, n_uav=1):
    """两任务小场景：显式 pairwise 使航程/时间窗都不触发，只留时序判据。"""
    uavs = [_uav(i) for i in range(n_uav)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    pairwise = {(-1, t.id): 100.0 for t in tasks}
    pairwise.update({(t.id, 1_000_000): 100.0 for t in tasks})
    for a in tasks:
        for b in tasks:
            if a.id != b.id:
                pairwise[(a.id, b.id)] = 100.0
    return SelectiveMUASProblem.from_entities(
        list(tasks), uavs, nests, pairwise_cost=pairwise
    )


# ── 1. 判据：同航次内按 sequence_order ──────────────────────────────

def test_sequence_violation_counted():
    # 任务 0 序号 1、任务 1 序号 0；按 [0, 1] 访问 ⇒ 序号下降 ⇒ 1 个逆序对
    tasks = {0: _task(0, group=7, order=1), 1: _task(1, group=7, order=0)}
    pen, tags = check_sequence_constraint([_tour(0, 1)], tasks)
    assert pen == 1.0
    assert tags == ["seq_group_7"]


def test_sequence_ok_when_ascending():
    tasks = {0: _task(0, group=7, order=1), 1: _task(1, group=7, order=0)}
    pen, tags = check_sequence_constraint([_tour(1, 0)], tasks)
    assert (pen, tags) == (0.0, [])


def test_sequence_order_beats_task_id():
    """判序依据必须是 sequence_order，不是任务 id（历史实现的缺陷）。"""
    # id 升序（0,1,2）但序号降序（2,1,0）⇒ 应判 3 个逆序对
    tasks = {
        0: _task(0, group=1, order=2),
        1: _task(1, group=1, order=1),
        2: _task(2, group=1, order=0),
    }
    pen, _tags = check_sequence_constraint([_tour(0, 1, 2)], tasks)
    assert pen == 3.0


def test_sequence_inert_without_group():
    """无 sequence_group（既有数据默认）⇒ 惰性 0，不引入任何新行为。"""
    tasks = {0: _task(0), 1: _task(1)}
    assert check_sequence_constraint([_tour(0, 1)], tasks) == (0.0, [])
    assert check_sequence_constraint([_tour(1, 0)], tasks) == (0.0, [])


def test_sequence_not_checked_across_tours():
    """口径边界：同一链被拆到不同 UAV 时不判违约（跨机按完成时刻比对未实现）。"""
    tasks = {0: _task(0, group=7, order=1), 1: _task(1, group=7, order=0)}
    tours = [_tour(0, uid=0), _tour(1, uid=1)]
    assert check_sequence_constraint(tours, tasks) == (0.0, [])


def test_sequence_order_fallback_to_id():
    """缺 sequence_order 属性时回退任务 id（历史约定，仅对鸭子类型对象生效）。"""
    t0 = SimpleNamespace(id=0, sequence_group=7)
    t1 = SimpleNamespace(id=1, sequence_group=7)
    t2 = SimpleNamespace(id=2, sequence_group=7)
    tasks = {0: t0, 1: t1, 2: t2}
    # 按 id 降序访问 ⇒ 违背
    pen, _tags = check_sequence_constraint([_tour(2, 1, 0)], tasks)
    assert pen == 3.0
    assert check_sequence_constraint([_tour(0, 1, 2)], tasks)[0] == 0.0


def test_violation_pairs_helper():
    tasks = {0: _task(0, group=7, order=1), 1: _task(1, group=7, order=0)}
    assert sequence_violation_pairs([0, 1], tasks) == [(0, 1)]
    assert sequence_violation_pairs([1, 0], tasks) == []


# ── 2. 硬违约分桶 ───────────────────────────────────────────────────

def test_sequence_violation_is_hard():
    """时序违反必须进硬违约桶（判物理不可行），不是软惩罚。"""
    tasks = [_task(0, group=7, order=1), _task(1, group=7, order=0)]
    prob = _problem(tasks)
    sol = AllocationSolution(
        tours=[_tour(0, 1)], candidate_task_ids=[0, 1], executed_task_ids=[0, 1]
    )
    ok, details = prob.check_feasible(sol)
    assert ok is False, "时序违反必须判物理不可行"
    assert "seq_group_7" in details["violations"]
    assert details["n_hard"] >= 1
    assert details["hard_violation"] > 0.0


def test_sequence_ok_is_feasible():
    tasks = [_task(0, group=7, order=1), _task(1, group=7, order=0)]
    prob = _problem(tasks)
    sol = AllocationSolution(
        tours=[_tour(1, 0)], candidate_task_ids=[0, 1], executed_task_ids=[0, 1]
    )
    ok, details = prob.check_feasible(sol)
    assert ok is True
    assert not [v for v in details["violations"] if v.startswith("seq_")]


def test_switch_off_disables_sequence():
    """constraints.sequence=False 时既不判违约也不计惩罚。"""
    tasks = [_task(0, group=7, order=1), _task(1, group=7, order=0)]
    prob = _problem(tasks)
    prob.config.constraints.sequence = False
    sol = AllocationSolution(
        tours=[_tour(0, 1)], candidate_task_ids=[0, 1], executed_task_ids=[0, 1]
    )
    ok, details = prob.check_feasible(sol)
    assert ok is True
    assert not [v for v in details["violations"] if v.startswith("seq_")]


# ── 3. 解码侧构造性修复 ─────────────────────────────────────────────

class _SeqFixStub(DecodeRepairMixin):
    """只挂时序修复所需的最小上下文（不启动整套解码管线）。"""

    def __init__(self, tasks, *, sequence=True):
        self._task_by_id = {t.id: t for t in tasks}
        self.problem = SimpleNamespace(
            config=SimpleNamespace(
                constraints=SimpleNamespace(sequence=sequence)
            )
        )


def test_sequence_fix_reorders_only_chain_members():
    tasks = [
        _task(0, group=7, order=1),
        _task(1),                     # 链外
        _task(2, group=7, order=0),
    ]
    stub = _SeqFixStub(tasks)
    assert stub._seq_violation([0, 1, 2]) is True
    fixed = stub._sequence_fix([0, 1, 2])
    # 链成员占据的槽位（0 与 2）不变，只把成员按序号重填 ⇒ 链外任务不动
    assert fixed == [2, 1, 0]
    assert stub._seq_violation(fixed) is False


def test_sequence_fix_keeps_task_set():
    tasks = [_task(0, group=7, order=2), _task(1, group=7, order=1),
             _task(2, group=7, order=0)]
    stub = _SeqFixStub(tasks)
    src = [2, 0, 1]
    fixed = stub._sequence_fix(src)
    assert sorted(fixed) == sorted(src), "修复只调序，不许增删任务"
    assert stub._seq_violation(fixed) is False


def test_sequence_fix_noop_when_disabled():
    tasks = [_task(0, group=7, order=1), _task(1, group=7, order=0)]
    stub = _SeqFixStub(tasks, sequence=False)
    assert stub._repair_sequence({0: [0, 1]}) == {0: [0, 1]}


def test_repair_sequence_applies_to_all_tours():
    tasks = [_task(0, group=7, order=1), _task(1, group=7, order=0)]
    stub = _SeqFixStub(tasks)
    out = stub._repair_sequence({0: [0, 1], 1: []})
    assert out == {0: [1, 0], 1: []}


# ── 4. 合成任务链生成 ───────────────────────────────────────────────

def _gen(n=12, **kw):
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0), Nest(id=1, x=1000.0, y=1000.0, z=0.0)]
    cfg = SyntheticTaskConfig(n_tasks=n, seed=7, **kw)
    return build_synthetic_tasks(nests, None, None, cfg)


def test_chain_generation_off_by_default():
    """默认关闭：不贴任何标签（既有实验基线逐位不变）。"""
    tasks = _gen()
    assert all(t.sequence_group is None for t in tasks)
    assert all(t.sequence_order == 0 for t in tasks)
    assert check_sequence_constraint(
        [UAVTour(uav_id=0, task_ids=[t.id for t in reversed(tasks)])],
        {t.id: t for t in tasks},
    ) == (0.0, [])


def test_chain_generation_on():
    """开启后：链数正确、组内序号连续、组 id 唯一。"""
    tasks = _gen(n=12, sequence_chain_ratio=0.5, sequence_chain_len=2)
    grouped = [t for t in tasks if t.sequence_group is not None]
    assert len(grouped) == 6, f"12×0.5=6 个任务应在链内，实际 {len(grouped)}"
    groups: dict[int, list[int]] = {}
    for t in grouped:
        groups.setdefault(int(t.sequence_group), []).append(t.sequence_order)
    assert len(groups) == 3
    for _gid, orders in groups.items():
        assert sorted(orders) == [0, 1]
    # 组 id 全局唯一且取自链首任务 id
    assert all(g in {t.id for t in tasks} for g in groups)


def test_chain_generation_does_not_move_points():
    """贴标签不移动撒点：开关只影响标签，不影响坐标分布。"""
    off = _gen(n=12)
    on = _gen(n=12, sequence_chain_ratio=0.5, sequence_chain_len=2)
    assert [(t.id, t.x, t.y, t.z, t.reward, t.latest) for t in off] == [
        (t.id, t.x, t.y, t.z, t.reward, t.latest) for t in on
    ]


def test_chain_violation_after_generation_is_detectable():
    """生成的链被逆序访问时确实能被测出（端到端串起生成 → 判定）。"""
    tasks = _gen(n=12, sequence_chain_ratio=0.5, sequence_chain_len=2)
    by_id = {t.id: t for t in tasks}
    gid = next(t.sequence_group for t in tasks if t.sequence_group is not None)
    members = sorted(
        (t for t in tasks if t.sequence_group == gid),
        key=lambda t: t.sequence_order,
    )
    bad = [m.id for m in reversed(members)]
    pen, tags = check_sequence_constraint(
        [UAVTour(uav_id=0, task_ids=bad)], by_id
    )
    assert pen == 1.0 and tags == [f"seq_group_{gid}"]
    good = [m.id for m in members]
    assert check_sequence_constraint([UAVTour(uav_id=0, task_ids=good)], by_id) == (
        0.0,
        [],
    )
