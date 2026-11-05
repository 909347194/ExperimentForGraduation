# -*- coding: utf-8 -*-
"""2026-11-04 三项用户裁定的收口测试（滚动层口径 / 指标同源 / 异构逐机代价）。

背景：提交 33255e9（异构 per-UAV 化 + 服务休眠态 DORMANT）之后，用户裁定：

  1. **分母选 A**：停机时仍处于服务休眠的任务列入完成率分母排除项
     （「没服务，只要能服务，迟早被服务」）；
  2. **停机判据改主判据**：「不存在能被完成的任务没有被完成即停止；否则
     一直下去」，`stable_patience` 仅作零进展兜底，并强调「只改早停而不动
     分母……铁律照破」；
  3. **带顺序约束的任务应同周期完成，或同时暂缓**。

本文件补齐上一轮定向测试（test_metrics_aggregation.py 的 3 项分母用例）之外
的零覆盖区，重点钉住「实现存在」与「实现真的在干活」的区别：

  * 停机判据四个分支 + `run()` 的 `stop_reason` 落盘链路；
  * 顺序组纯函数、准入闸门、交付闸门、完成闸门连坐、无组零行为变化、
    已出池成员不永久阻塞；
  * prefilter 的 `pairwise_cost` 逐机 DEM 代价分支与四级回退优先级
    （此前 C 项只走欧氏回退，逐机表分支零覆盖）；
  * exp05 与 exp01 同源的完成率分母（裁定 A），含休眠单列指标非零；
  * `CycleRecord.as_dict()` 新增字段的序列化链路。

运行：``pytest task_allocation/tests/test_rolling_caliber_20261104.py -q``
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    AllocationSolution,
    Nest,
    ScoredTask,
    Task,
    TaskStatus,
    UAV,
    UAVStatus,
    UAVTour,
)
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig  # noqa: E402
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig  # noqa: E402
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    CycleRecord,
    HorizonConfig,
    RollingHorizon,
)
from task_allocation.methods.rolling.sequence_group import (  # noqa: E402
    expand_to_groups,
    group_id_of,
    group_members,
    has_groups,
    prune_incomplete_groups,
    prune_unwakeable_groups,
)
from task_allocation.methods.selection.feasibility import (  # noqa: E402
    FeasibilityConfig,
    prefilter,
)
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------


def _task(tid: int, x: float = 0.0, *, group: int | None = None) -> Task:
    return Task(
        id=tid, x=x, y=0.0, z=0.0, reward=5.0,
        arrival_time=0.0, earliest=0.0, latest=1e9,
        sequence_group=group,
    )


def _uav(uid: int, x: float = 0.0, *, remaining=1e9, max_range=None) -> UAV:
    return UAV(
        id=uid, x=x, y=0.0, z=0.0, remaining_range=remaining, speed=10.0,
        max_time=None, max_range=max_range, nest_id=0, status=UAVStatus.IDLE,
    )


def _nests() -> list[Nest]:
    return [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]


def _make_horizon(uavs, tasks, *, stop_when_stable=False, stable_patience=3,
                  n_cycles=1) -> RollingHorizon:
    pool = TaskPool(current_time=0.0)
    pool.add_new(list(tasks))
    fleet = UAVFleet(list(uavs), current_time=0.0)
    return RollingHorizon(
        pool, fleet, _nests(), dem=None, estimator=None,
        horizon_config=HorizonConfig(
            n_cycles=n_cycles, cycle_length=600.0,
            complete_after_cycle=True, recharge_at_nest=True,
            uav_max_range=60000.0, service_patience=2,
            stop_when_stable=stop_when_stable,
            stable_patience=stable_patience,
        ),
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=20, max_generations=40, seed=42),
        event_config=EventTriggerConfig(),
        nest_offset=1_000_000,
    )


class _PoolStub:
    """只提供 ``_converged`` / ``run()`` 所需切面的轻量任务池替身。"""

    def __init__(self, size: int = 0, dormant=()):
        self._size = int(size)
        self._dormant = list(dormant)

    def __len__(self) -> int:
        return self._size

    def dormant_tasks(self):
        return list(self._dormant)

    def active_tasks(self):
        return list(self._dormant)


def _load_module(name: str, rel_path: str):
    """按路径加载实验自带 metrics，避免与 exp01 的 ``import metrics`` 串模块。"""
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. 顺序组纯函数（用户裁定 3 的语义内核）
# ---------------------------------------------------------------------------


def _sid(tid: int, group: int | None):
    return SimpleNamespace(id=tid, sequence_group=group)


def test_sequence_group_helpers_atomicity():
    tasks = [_sid(1, 7), _sid(2, 7), _sid(3, None), _sid(4, 8)]
    groups = group_members(tasks)
    assert groups == {7: [1, 2], 8: [4]}
    assert has_groups(tasks)
    assert not has_groups([_sid(9, None), _sid(10, None)])
    assert group_id_of(tasks[0]) == 7 and group_id_of(tasks[2]) is None

    # 命中任一成员 ⇒ 整组一起动（无组 id 原样保留）
    assert expand_to_groups({1}, groups) == {1, 2}
    assert expand_to_groups({1, 3}, groups) == {1, 2, 3}
    assert expand_to_groups({3}, groups) == {3}

    # 唤醒侧：链里还有一个成员够不着 ⇒ 整条链继续休眠（按组独立判断）
    assert prune_unwakeable_groups({1, 2, 4, 3}, groups) == {1, 2, 4, 3}
    assert prune_unwakeable_groups({1, 3}, groups) == {3}       # 组7 半缺、组8 未参与
    assert prune_unwakeable_groups({1, 4, 3}, groups) == {3, 4}  # 组8 单成员完整 ⇒ 独立可唤醒

    # 交付侧：完整 / 未参与 / 部分 三分支
    assert prune_incomplete_groups({1, 2, 3}, groups) == set()
    assert prune_incomplete_groups({3}, groups) == set()
    assert prune_incomplete_groups({1, 3}, groups) == {1}
    assert prune_incomplete_groups({2, 3}, groups) == {2}   # 组7 另一侧成员同样撤下
    assert prune_incomplete_groups({4, 3}, groups) == set()  # 单成员组出现即完整


def test_sequence_group_helpers_no_group_is_noop():
    """无组旁路：所有 helper 在空组映射下返回输入副本，零行为变化。"""
    ids = {1, 2, 3}
    assert has_groups([_sid(1, None), _sid(2, None)]) is False
    assert group_members([_sid(1, None), _sid(2, None)]) == {}
    assert expand_to_groups(ids, {}) == ids
    assert expand_to_groups(ids, {}) is not ids      # 返回副本，不改入参
    assert prune_unwakeable_groups(ids, {}) == ids
    assert prune_incomplete_groups(ids, {}) == set()


# ---------------------------------------------------------------------------
# 2. 顺序组三处闸门（真实 RollingHorizon + 真实 TaskPool / UAVFleet）
# ---------------------------------------------------------------------------


def test_seq_admission_gate_defers_whole_group():
    """组内一员休眠 ⇒ 其余成员一并撤下，且是批次 defer（不打 DORMANT）。"""
    tasks = [_task(1, group=7), _task(2, group=7), _task(3)]
    h = _make_horizon([_uav(0)], tasks)
    h.pool.begin_cycle(current_time=0.0)
    h.pool.mark_dormant([1], reason="service")

    planning = h.pool.planning_tasks()
    assert [t.id for t in planning] == [2, 3]
    kept, dropped = h._seq_admission_gate(planning)
    assert [t.id for t in kept] == [3]
    assert dropped == [2]
    # 关键：这是调度原子性批次 defer，不是能力不足，故不打 DORMANT 标
    assert h.pool.get(2).status == TaskStatus.PENDING
    assert [d.id for d in h.pool.dormant_tasks()] == [1]
    assert h._seq_groups() == {7: [1, 2]}


def test_seq_admission_gate_complete_group_and_no_group_bypass():
    """整组齐全 ⇒ 原样放行；全无组 ⇒ 同一列表对象原样返回（零行为变化）。"""
    tasks = [_task(1, group=7), _task(2, group=7), _task(3)]
    h = _make_horizon([_uav(0)], tasks)
    h.pool.begin_cycle(current_time=0.0)
    planning = h.pool.planning_tasks()
    kept, dropped = h._seq_admission_gate(planning)
    assert [t.id for t in kept] == [1, 2, 3]
    assert dropped == []

    plain = [_task(11), _task(12)]
    h2 = _make_horizon([_uav(0)], plain)
    h2.pool.begin_cycle(current_time=0.0)
    p2 = h2.pool.planning_tasks()
    kept2, dropped2 = h2._seq_admission_gate(p2)
    assert kept2 is p2 and dropped2 == []


def test_seq_groups_ignores_out_of_pool_members():
    """已出池（UNREACHABLE）成员不再计入组，避免其余成员被永久阻塞。"""
    tasks = [_task(1, group=7), _task(2, group=7), _task(3)]
    h = _make_horizon([_uav(0)], tasks)
    h.pool.begin_cycle(current_time=0.0)
    h.pool.mark_unreachable([1], reason="geo")
    assert h._seq_groups() == {7: [2]}
    kept, dropped = h._seq_admission_gate(h.pool.planning_tasks())
    assert [t.id for t in kept] == [2, 3]
    assert dropped == []


def test_seq_delivery_gate_drops_partial_group():
    """MUAS 解里只装载了组的一部分 ⇒ 撤下已装载成员，整组批次 defer。"""
    tasks = [_task(1, group=7), _task(2, group=7), _task(3)]
    h = _make_horizon([_uav(0), _uav(1)], tasks)
    h.pool.begin_cycle(current_time=0.0)

    # 完整组：解里 1、2 都在 ⇒ 不动
    complete = AllocationSolution(
        tours=[UAVTour(uav_id=0, task_ids=[1, 2, 3])],
        executed_task_ids=[1, 2, 3],
    )
    assert h._seq_delivery_gate(complete) == []
    assert complete.tours[0].task_ids == [1, 2, 3]
    assert complete.executed_task_ids == [1, 2, 3]

    # 部分组：只装了 1 ⇒ 撤下 1，tours 与 executed_task_ids 同步收口
    partial = AllocationSolution(
        tours=[UAVTour(uav_id=0, task_ids=[1, 3, 9])],
        executed_task_ids=[1, 3, 9],
    )
    assert h._seq_delivery_gate(partial) == [1]
    assert partial.tours[0].task_ids == [3, 9]
    assert partial.executed_task_ids == [3, 9]


def test_seq_delivery_gate_no_group_bypass():
    h = _make_horizon([_uav(0)], [_task(1), _task(2)])
    h.pool.begin_cycle(current_time=0.0)
    sol = AllocationSolution(
        tours=[UAVTour(uav_id=0, task_ids=[1, 2])],
        executed_task_ids=[1, 2],
    )
    assert h._seq_delivery_gate(sol) == []
    assert sol.tours[0].task_ids == [1, 2]
    assert sol.executed_task_ids == [1, 2]


def test_completion_gate_group_rollback_is_not_wasted_flight():
    """完成闸门：组内一个成员物理不可行 ⇒ 整组一起暂缓。

    连坐成员本身没有违约，故不计 ``infeasible_ids``（废飞），只单列
    ``seq_group_rolled_back_ids``；无组任务照常完成。
    """
    tasks = [_task(1, group=7), _task(2, group=7), _task(3)]
    h = _make_horizon([_uav(0)], tasks)
    h.pool.begin_cycle(current_time=0.0)
    h.fleet.bind_tasks(0, [1, 2, 3])
    # 只让 1 号带物理违约标签（非 tw_task），2 号必然是「连坐」
    h._violated_task_ids = lambda sol: {1: ["range_uav_0"]}

    sol = AllocationSolution(
        tours=[UAVTour(uav_id=0, task_ids=[1, 2, 3], end_nest_id=None,
                       est_cost=0.0)],
        executed_task_ids=[1, 2, 3],
        meta={},
    )
    rec = CycleRecord(cycle=1, current_time=0.0)
    h._advance_execution(sol, rec)

    assert rec.infeasible_ids == [1], rec.infeasible_ids
    assert rec.seq_group_rolled_back_ids == [2], rec.seq_group_rolled_back_ids
    assert rec.completed_ids == [3], rec.completed_ids
    # 真违约与连坐都 release 回池；无组任务完成出池
    assert 1 in h.pool and 2 in h.pool and 3 not in h.pool
    assert h.pool.get(1).status == TaskStatus.PENDING
    assert h.pool.get(2).status == TaskStatus.PENDING
    assert h.pool.get(2).meta.get("release_reason") == "seq_group_defer"
    # 连坐成员不得出现在废飞归因里
    assert "2" not in rec.infeasible_tags


# ---------------------------------------------------------------------------
# 3. 停机判据（用户裁定 2）与 stop_reason 落盘
# ---------------------------------------------------------------------------


def test_converged_pool_empty_is_natural_stop():
    h = _make_horizon([_uav(0)], [], stop_when_stable=True, stable_patience=3)
    h.pool = _PoolStub(size=0)
    h.records = [CycleRecord(cycle=2, current_time=1200.0)]
    assert h._converged(2, last_arrival_cycle=1) is True
    assert h._conv_reason == "pool_empty"


def test_converged_arrival_phase_never_stops():
    """到达阶段（cycle <= 最后到达周期）池空也不算收敛，且计数清零。"""
    h = _make_horizon([_uav(0)], [], stop_when_stable=True, stable_patience=3)
    h.pool = _PoolStub(size=0)
    h.records = [CycleRecord(cycle=1, current_time=600.0)]
    h._conv_streak = 2
    assert h._converged(1, last_arrival_cycle=1) is False
    assert h._conv_streak == 0
    assert h._conv_last_sig is None
    assert h._conv_reason == ""


def test_converged_dormant_never_early_stops():
    """池里还有休眠任务 = 正在等条件恢复，禁用兜底、忠实「一直下去」。

    旧口径按「池大小连续 stable_patience 周期不变」判稳态，会把休眠任务
    锁死；这里连跑 10 个周期也必须恒不收敛。
    """
    dormant = [_task(1), _task(2)]
    h = _make_horizon([_uav(0)], dormant,
                      stop_when_stable=True, stable_patience=2)
    h.pool = _PoolStub(size=3, dormant=dormant)
    for cycle in range(2, 12):
        h.records.append(CycleRecord(cycle=cycle, current_time=600.0 * cycle))
        assert h._converged(cycle, last_arrival_cycle=1) is False
        assert h._conv_streak == 0, f"周期 {cycle} 误累加零进展计数"
        assert h._conv_reason == ""


def test_converged_no_progress_fallback():
    """兜底：无休眠但连续 stable_patience 周期零进展 → no_progress（非自然收敛）。"""
    h = _make_horizon([_uav(0)], [], stop_when_stable=True, stable_patience=3)
    h.pool = _PoolStub(size=2, dormant=[])
    results = []
    for cycle in range(2, 8):
        h.records.append(CycleRecord(cycle=cycle, current_time=600.0 * cycle))
        results.append(h._converged(cycle, last_arrival_cycle=1))
    assert results[:3] == [False, False, False], results
    assert results[3] is True and all(results[4:]), results
    assert h._conv_reason == "no_progress"


def test_converged_progress_resets_streak():
    """本周期有完成/唤醒/新休眠/新出池 ⇒ 零进展计数归零，不误停。"""
    h = _make_horizon([_uav(0)], [], stop_when_stable=True, stable_patience=2)
    h.pool = _PoolStub(size=2, dormant=[])
    for cycle in (2, 3):
        h.records.append(CycleRecord(cycle=cycle, current_time=600.0 * cycle))
        assert h._converged(cycle, 1) is False
    assert h._conv_streak == 1

    progressed = CycleRecord(cycle=4, current_time=2400.0)
    progressed.completed_ids = [7]
    h.records.append(progressed)
    assert h._converged(4, 1) is False
    assert h._conv_streak == 0

    # 唤醒同样算有进展（休眠/唤醒签名变化）
    woken = CycleRecord(cycle=5, current_time=3000.0)
    woken.woken_ids = [8]
    h.records.append(woken)
    assert h._converged(5, 1) is False
    assert h._conv_streak == 0


def test_converged_disabled_by_master_switch():
    h = _make_horizon([_uav(0)], [], stop_when_stable=False)
    h.pool = _PoolStub(size=0)
    h.records = [CycleRecord(cycle=2, current_time=1200.0)]
    assert h._converged(2, 1) is False
    assert h._conv_reason == ""


def test_run_writes_stop_reason_into_last_record():
    """run() 链路：收敛那一周期的 stop_reason 回填进 records[-1]。"""
    h = _make_horizon([_uav(0)], [],
                      stop_when_stable=True, stable_patience=2, n_cycles=8)
    h.pool = _PoolStub(size=0)

    def fake_run_cycle(new_tasks=None):
        rec = CycleRecord(cycle=len(h.records) + 1, current_time=0.0)
        h.records.append(rec)
        return rec

    h.run_cycle = fake_run_cycle
    recs = h.run(arrivals={1: []})
    # 周期 1 是到达阶段不收敛；周期 2 池空自然收敛
    assert len(recs) == 2, len(recs)
    assert recs[0].stop_reason == ""
    assert recs[1].stop_reason == "pool_empty"
    assert recs[1].as_dict()["stop_reason"] == "pool_empty"


def test_cycle_record_as_dict_new_fields():
    rec = CycleRecord(cycle=1, current_time=0.0)
    rec.stop_reason = "no_progress"
    rec.seq_group_rolled_back_ids = [9, 10]
    d = rec.as_dict()
    assert d["stop_reason"] == "no_progress"
    assert d["seq_group_rolled_back_ids"] == [9, 10]
    assert d["n_seq_group_rolled_back"] == 2


# ---------------------------------------------------------------------------
# 4. prefilter 的 pairwise_cost 逐机 DEM 分支与四级回退优先级
# ---------------------------------------------------------------------------


def _scored(tid: int):
    return [ScoredTask(task=_task(tid, x=1000.0), priority=1.0,
                       marginal_score=1.0)]


def test_prefilter_pairwise_cost_beats_cost_hint_and_distance_fn():
    """逐机 DEM 表优先级最高：表给大代价时应拒，即使 hint / 距离都给小值。

    若 ``_pair_cost`` 忽略 pairwise 而回退 cost_hint 或 distance_fn，
    两机都装得下 ⇒ 放行，本用例即失败。
    """
    cfg = FeasibilityConfig()
    u0, u1 = _uav(0, remaining=1000.0), _uav(1, remaining=1000.0)
    passed, rejected = prefilter(
        _scored(5), [u0, u1], cfg,
        cost_hint={5: 10.0},
        distance_fn=lambda a, b: 10.0,
        pairwise_cost={(-1, 5): 6000.0, (-2, 5): 6000.0},
    )
    assert passed == []
    assert [s.filter_reason for s in rejected] == ["exceed_round_trip_range"]


def test_prefilter_pairwise_cost_is_per_uav():
    """逐机差异化：短机装不下、长机装得下 ⇒ 放行；统一 hint 大值本会拒。"""
    cfg = FeasibilityConfig()
    u0 = _uav(0, remaining=100.0, max_range=100.0)
    u1 = _uav(1, remaining=100000.0, max_range=100000.0)
    passed, rejected = prefilter(
        _scored(5), [u0, u1], cfg,
        cost_hint={5: 60000.0},           # 2*60000 > 100000 ⇒ 统一口径两机都拒
        distance_fn=lambda a, b: 10.0,   # 若误回退距离 ⇒ 也会放行（由上一用例钉死）
        pairwise_cost={(-1, 5): 60000.0, (-2, 5): 100.0},
    )
    assert [s.task.id for s in passed] == [5], \
        f"逐机表下长机可服务却被拒：{[s.filter_reason for s in rejected]}"
    assert rejected == []


def test_prefilter_pairwise_cost_missing_key_falls_back_to_cost_hint():
    """键未命中的机回退 cost_hint，命中的机仍用逐机表（四级链完整）。"""
    cfg = FeasibilityConfig()
    u0 = _uav(0, remaining=100.0, max_range=100.0)
    u1 = _uav(1, remaining=100000.0, max_range=100000.0)
    passed, rejected = prefilter(
        _scored(5), [u0, u1], cfg,
        cost_hint={5: 100.0},
        pairwise_cost={(-1, 5): 60000.0},   # 只有 u0 命中；u1 缺项 → hint
    )
    assert [s.task.id for s in passed] == [5]
    assert rejected == []


def test_prefilter_cost_hint_beats_distance_fn_beats_euclid():
    """回退链：cost_hint > distance_fn > 欧氏直线。"""
    cfg = FeasibilityConfig()
    u0, u1 = _uav(0, remaining=1000.0), _uav(1, remaining=1000.0)

    # cost_hint 大值、distance_fn 小值：应拒（hint 优先）
    passed, rejected = prefilter(
        _scored(5), [u0, u1], cfg,
        cost_hint={5: 6000.0}, distance_fn=lambda a, b: 10.0,
    )
    assert passed == [] and rejected[0].filter_reason == "exceed_round_trip_range"

    # 无 hint、distance_fn 大值、几何位置很近：应拒（distance_fn 优先于欧氏）
    passed2, rejected2 = prefilter(
        _scored(5), [u0, u1], cfg,
        distance_fn=lambda a, b: 6000.0,
    )
    assert passed2 == [] and rejected2[0].filter_reason == "exceed_round_trip_range"


# ---------------------------------------------------------------------------
# 5. exp05 与 exp01 同源：裁定 A 的分母与休眠单列指标
# ---------------------------------------------------------------------------


class _SumTask:
    def __init__(self, tid, *, completed_at=None, unreachable=False):
        self.id = tid
        self.x = self.y = self.z = 0.0
        self.reward = 5.0
        self.arrival_time = 0.0
        self.earliest = 0.0
        self.latest = 1e9
        self.status = SimpleNamespace(
            value="unreachable" if unreachable else "done"
        )
        self.meta = {}
        if completed_at is not None:
            self.meta["completed_at"] = completed_at
        if unreachable:
            self.meta["unreachable_reason"] = "geo"


class _SumRecord:
    def __init__(self, cycle, *, executed=(), unreachable=(), dormant=(),
                 woken=(), pool_counts=None):
        self.cycle = cycle
        self.muas = {"feasible": True}
        self.selection = {"n_uav_avail": 2, "selected_ids": list(executed)}
        self.pool_counts = dict(pool_counts or {})
        self.executed_ids = list(executed)
        self.late_ids = []
        self.infeasible_ids = []
        self.infeasible_tags = {}
        self.lateness_by_task = {}
        self.unreachable_ids = list(unreachable)
        self.dormant_ids = list(dormant)
        self.woken_ids = list(woken)
        self.capacity_violations = []
        self.overdue_ids = []
        self.expired_ids = []


class _SumPool:
    def __init__(self, finished, remaining):
        self._finished = list(finished)
        self._remaining = remaining

    def finished_tasks(self):
        return list(self._finished)

    def __len__(self):
        return self._remaining


def test_exp05_final_denom_excludes_dormant_end_matches_exp01():
    """exp05 与 exp01 同源：η_final 分母排除停机时仍休眠的任务。

    6 个到达：T0/T1/T5 完成，T2 静态几何不可达（出池），T3/T4 停机时仍
    休眠（不出池）。分母 = 6 − 1 − 2 = 3，完成 3 ⇒ η_final = 1.0。
    旧 v2 口径（只减 unreachable）会得到 3/5 = 0.6，铁律照破。
    """
    exp05 = _load_module(
        "exp05_metrics_caliber_20261104",
        "task_allocation/experiments/exp05_baselines/metrics.py",
    )
    t0 = _SumTask(0, completed_at=600.0)
    t1 = _SumTask(1, completed_at=1200.0)
    t2 = _SumTask(2, unreachable=True)          # 几何不可达
    t3 = _SumTask(3)                            # 停机仍休眠
    t4 = _SumTask(4)                            # 停机仍休眠
    t5 = _SumTask(5, completed_at=1800.0)
    tasks = [t0, t1, t2, t3, t4, t5]
    records = [
        # 周期1：T3/T5 转休眠（2 次休眠事件），T2 判几何不可达
        _SumRecord(1, executed=[0], unreachable=[2], dormant=[3, 5]),
        # 周期2：T5 条件恢复唤醒
        _SumRecord(2, executed=[1], woken=[5]),
        # 周期3（末）：周期初已有 1 个休眠（T3），本周期 T4 新转休眠
        _SumRecord(3, executed=[5], dormant=[4],
                   pool_counts={"n_dormant": 1}),
    ]
    pool = _SumPool([t0, t1, t2, t5], remaining=2)
    batch = SimpleNamespace(all_tasks=lambda: list(tasks),
                            arrivals={1: list(tasks)})
    problem_model = SimpleNamespace(late_reward_decay_seconds=None)
    s = exp05.build_summary(
        records, pool=pool, batch=batch,
        problem_model=problem_model, wall_seconds=1.0,
    )

    assert s["n_arrived"] == 6, s
    assert s["n_completed"] == 3, s
    assert s["n_unreachable"] == 1, s
    assert s["n_dormant_events"] == 3, s        # 2 + 0 + 1
    assert s["n_woken"] == 1, s
    assert s["n_dormant_end"] == 2, s           # 1(周期初) + 1(末周期新增)
    assert abs(s["completion_rate_final"] - 1.0) < 1e-9, s
    # 分账恒等式（remain=0）：6 = 3 + 1 + 2
    assert (s["n_completed"] + s["n_unreachable"] + s["n_dormant_end"]) == 6, s
