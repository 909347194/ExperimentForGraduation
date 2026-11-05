# -*- coding: utf-8 -*-
"""异构机队 per-UAV 化 + 服务休眠态（2026-11-04）聚焦测试。

覆盖六条设计点：
  A. full_charge_range 解析：per-UAV max_range 优先，None 回退全局缺省
  B. 永久性不可达判定用**机队最强**满电航程（短机不误杀长机可服务任务）
  C. prefilter 逐机可行性：∃u: 2·c ≤ margin·remaining_u（最近短机装不下 ≠ 不可服务）
  D. TaskPool 休眠态：DORMANT 不出池、不进候选、wake 回 PENDING、年龄不中断
  E. service_patience → 休眠；条件恢复（长航程机到位）→ 唤醒并可完成
  F. 换电恢复到**该机**满电航程（异构 per-UAV，非全局值）

pytest task_allocation/tests/test_heterogeneous_fleet.py 直接运行。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    Nest,
    ScoredTask,
    Task,
    TaskStatus,
    UAV,
    UAVStatus,
)
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig  # noqa: E402
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig  # noqa: E402
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    HorizonConfig,
    RollingHorizon,
)
from task_allocation.methods.selection.feasibility import (  # noqa: E402
    FeasibilityConfig,
    fleet_max_range,
    prefilter,
    static_unreachable,
)
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------


def _task(tid: int, x: float, y: float = 0.0, *, reward=5.0, latest=1e9) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=reward,
                arrival_time=0.0, earliest=0.0, latest=latest)


def _uav(uid: int, x: float, y: float = 0.0, *, remaining=1e9, max_range=None,
         speed=10.0, nest_id=0, status=UAVStatus.IDLE, max_time=None) -> UAV:
    return UAV(id=uid, x=x, y=y, z=0.0, remaining_range=remaining, speed=speed,
               max_time=max_time, max_range=max_range, nest_id=nest_id, status=status)


def _make_horizon(uavs, tasks, nests, *, uav_max_range=60000.0,
                  service_patience=2, n_cycles=1):
    pool = TaskPool(current_time=0.0)
    pool.add_new(list(tasks))
    fleet = UAVFleet(list(uavs), current_time=0.0)
    horizon_config = HorizonConfig(
        n_cycles=n_cycles, cycle_length=600.0, complete_after_cycle=True,
        recharge_at_nest=True, uav_max_range=uav_max_range,
        service_patience=service_patience,
    )
    return RollingHorizon(
        pool, fleet, nests, dem=None, estimator=None,
        horizon_config=horizon_config,
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=20, max_generations=40, seed=42),
        event_config=EventTriggerConfig(),
        nest_offset=1_000_000,
    )


# ---------------------------------------------------------------------------
# A. 满电航程解析
# ---------------------------------------------------------------------------


def test_full_charge_range_resolution() -> None:
    assert _uav(0, 0.0, max_range=50000.0).full_charge_range(60000.0) == 50000.0
    assert _uav(1, 0.0).full_charge_range(60000.0) == 60000.0  # None → 全局缺省
    assert _uav(2, 0.0).full_charge_range(None) is None        # 双缺省 → None
    # 异构：同构造批量机队各有各的满电航程
    u0, u1 = _uav(0, 0.0, max_range=20000.0), _uav(1, 0.0, max_range=80000.0)
    assert (u0.full_charge_range(60000.0), u1.full_charge_range(60000.0)) == (20000.0, 80000.0)
    print("[A] full_charge_range resolution: PASS")


def test_fleet_max_range_helper() -> None:
    uavs = [_uav(0, 0.0, max_range=20000.0), _uav(1, 0.0, max_range=None),
            _uav(2, 0.0, max_range=80000.0)]
    assert fleet_max_range(uavs, default=60000.0) == 80000.0   # 取最强（含缺省解析）
    assert fleet_max_range([_uav(0, 0.0)], default=60000.0) == 60000.0
    # 空队即便给了 default 也必须返回 None（判定放宽为「不筛」）；旧断言
    # `== 60000.0 or True` 恒真，钉不住任何语义，2026-11-04 审查后改为 is None。
    assert fleet_max_range([], default=60000.0) is None
    assert fleet_max_range([], default=None) is None
    # 全队 max_range=None 且无缺省 ⇒ 同样 None（不误回退成 0 / 全局值）
    assert fleet_max_range([_uav(0, 0.0), _uav(1, 0.0)], default=None) is None
    print("[A] fleet_max_range helper: PASS")


# ---------------------------------------------------------------------------
# B. 永久判定用机队最强航程
# ---------------------------------------------------------------------------


def test_geo_screen_uses_fleet_max() -> None:
    """短机 + 长机：短机够不着、长机够得着的任务**不得**判永久不可达。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]
    # 最近巢 0：往返 2d。任务 A：2d=60000（短机 40000 够不着、长机 80000 够得着）
    # 任务 B：2d=100000（最强机也够不着 → 永久不可达）
    task_a, task_b = _task(1, 30000.0), _task(2, 50000.0)
    short = _uav(0, 0.0, remaining=40000.0, max_range=40000.0)
    long_ = _uav(1, 0.0, remaining=80000.0, max_range=80000.0)

    fleet_max = fleet_max_range([short, long_], default=60000.0)
    assert fleet_max == 80000.0
    assert not static_unreachable(task_a, nests, fleet_max), \
        "长航程机可服务的任务被误判永久不可达（应按机队最强判定）"
    assert static_unreachable(task_b, nests, fleet_max), \
        "最强机也够不着的任务应判永久不可达"
    # 旧口径（全局 40000）会误杀任务 A——对照断言，钉住语义差异
    assert static_unreachable(task_a, nests, 40000.0), "对照：短机航程口径下 A 应被误杀"

    # 滚动层 _screen_unreachable 同口径：A 留、B 出池
    horizon = _make_horizon([short, long_], [task_a, task_b], nests,
                            uav_max_range=40000.0, n_cycles=1)
    hit = horizon._screen_unreachable([task_a, task_b])
    assert [t.id for t in hit] == [2], f"只应筛出 2 号，实际 {[t.id for t in hit]}"
    assert 1 in horizon.pool and 2 not in horizon.pool
    print("[B] geo screen uses fleet-max: PASS")


# ---------------------------------------------------------------------------
# C. prefilter 逐机可行性
# ---------------------------------------------------------------------------


def test_prefilter_per_uav_reachability() -> None:
    """最近可用机（短程）装不下、更远长程机装得下 → 放行（旧口径误拒）。"""
    cfg = FeasibilityConfig()
    # 任务在 10000：u0 短程最近（1000，remaining 1500 → 往返 2000 > 1500 装不下）；
    # u1 长程较远（10000，remaining 25000 → 往返 20000 ≤ 25000 装得下）
    t = _task(1, 10000.0)
    u0 = _uav(0, 9000.0, remaining=1500.0, max_range=1500.0)
    u1 = _uav(1, 0.0, remaining=25000.0, max_range=25000.0)
    scored = [ScoredTask(task=t, priority=1.0, marginal_score=1.0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]

    passed, rejected = prefilter(list(scored), [u0, u1], cfg,
                                 nests=nests, max_range=fleet_max_range([u0, u1], 60000.0))
    assert [s.task.id for s in passed] == [1], \
        f"长航程机装得下的任务被误拒：{[s.filter_reason for s in rejected]}"

    # 无可用机装得下但几何可达（巢往返 ≤ 机队最强，长程机 FAULT）→ no_uav_in_range
    u2 = _uav(2, 0.0, remaining=5000.0, max_range=5000.0)
    u3_long_fault = _uav(3, 0.0, remaining=25000.0, max_range=25000.0,
                         status=UAVStatus.FAULT)
    scored2 = [ScoredTask(task=_task(2, 10000.0), priority=1.0, marginal_score=1.0)]
    passed2, rejected2 = prefilter(scored2, [u2, u3_long_fault], cfg,
                                   nests=nests,
                                   max_range=fleet_max_range([u2, u3_long_fault], 60000.0))
    assert not passed2 and rejected2[0].filter_reason == "no_uav_in_range"

    # 几何不可达（巢往返 > 机队最强）→ exceed_round_trip_range
    scored3 = [ScoredTask(task=_task(3, 50000.0), priority=1.0, marginal_score=1.0)]
    passed3, rejected3 = prefilter(scored3, [u2, u3_long_fault], cfg,
                                   nests=nests,
                                   max_range=fleet_max_range([u2, u3_long_fault], 60000.0))
    assert not passed3 and rejected3[0].filter_reason == "exceed_round_trip_range"
    print("[C] prefilter per-UAV reachability: PASS")


# ---------------------------------------------------------------------------
# D. 任务池休眠态
# ---------------------------------------------------------------------------


def test_pool_dormant_lifecycle() -> None:
    pool = TaskPool()
    t = _task(1, 100.0)
    pool.add_new([t])
    pool.begin_cycle(current_time=0.0)
    assert t in pool.planning_tasks()

    marked = pool.mark_dormant([1], reason="service")
    assert [m.id for m in marked] == [1]
    assert t.status == TaskStatus.DORMANT
    assert 1 in pool, "休眠任务必须留在池中（不出池）"
    assert 1 not in [x.id for x in pool.planning_tasks()], "休眠任务不得进 T_t"
    assert [d.id for d in pool.dormant_tasks()] == [1]

    # 下周期 T_t 不含休眠任务；快照计数正确
    snap = pool.begin_cycle(current_time=600.0)
    assert 1 not in snap.planning_ids
    assert snap.counts["n_dormant"] == 1

    # 休眠任务不允许直接进 SELECTED（必须先 wake）
    try:
        pool.mark_selected([1])
        raise AssertionError("休眠任务被误标 SELECTED")
    except ValueError:
        pass

    # 唤醒：回 PENDING、年龄（arrival_time）不中断、下周期重新进 T_t
    arrival = t.arrival_time
    woken = pool.wake([1])
    assert [w.id for w in woken] == [1] and t.status == TaskStatus.PENDING
    assert t.arrival_time == arrival, "唤醒不得重置年龄"
    snap2 = pool.begin_cycle(current_time=1200.0)
    assert 1 in snap2.planning_ids, "唤醒后任务应重新进入 T_t"
    print("[D] pool dormant lifecycle: PASS")


# ---------------------------------------------------------------------------
# E. service_patience → 休眠 → 条件恢复唤醒
# ---------------------------------------------------------------------------


def test_service_stall_dormant_then_wake() -> None:
    """短机够不着的任务：连续 service_patience 周期后休眠（不出池）；
    长航程机到位后唤醒并可完成。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5)]
    # 任务：巢往返 50000。u0 短程 20000（够不着）；u1 长程 80000 但 FAULT
    # → 几何可达（机队最强 80000）、当前无可用机服务 → 计 stall
    t = _task(1, 25000.0, reward=5.0, latest=1e9)
    u0 = _uav(0, 0.0, remaining=20000.0, max_range=20000.0, nest_id=0)
    u1 = _uav(1, 0.0, remaining=80000.0, max_range=80000.0, nest_id=0,
              status=UAVStatus.FAULT)
    horizon = _make_horizon([u0, u1], [t], nests,
                            uav_max_range=20000.0, service_patience=2, n_cycles=3)

    # 周期 1-2：无可用机服务 → 第 2 个周期触发休眠（patience=2）
    horizon.run_cycle()
    assert t.status != TaskStatus.DORMANT, "patience 未满不应休眠"
    horizon.run_cycle()
    assert t.status == TaskStatus.DORMANT, "连续 2 周期不可服务应休眠"
    assert 1 in horizon.pool, "休眠不得出池"
    assert horizon.records[-1].dormant_ids == [1], "休眠事件应记录在案"

    # 周期 3：长航程机恢复可用 → 唤醒、进 T_t、执行
    horizon.fleet.get(1).status = UAVStatus.IDLE
    rec = horizon.run_cycle()
    assert rec.woken_ids == [1], f"应唤醒 1 号，实际 {rec.woken_ids}"
    assert 1 in rec.executed_ids, "唤醒后任务应进入本周期执行"
    # 「→ complete」名实相符（2026-11-04 审查）：旧断言只到 executed_ids，
    # 完成闸门若回归（hard_violated 误判 → release 回池）仍会全过。补钉
    # completed_ids / 状态 DONE / 出池，守住「休眠→唤醒→完成」全链路末端。
    assert 1 in rec.completed_ids, f"应完成 1 号，实际 {rec.completed_ids}"
    assert t.status == TaskStatus.DONE, f"任务状态应为 DONE，实际 {t.status}"
    assert 1 not in horizon.pool, "完成任务应出池"
    # 换电按该机满电航程恢复（F 项顺带钉住）
    assert horizon.fleet.get(1).remaining_range == 80000.0
    print("[E] service-stall dormant → wake → complete: PASS")


# ---------------------------------------------------------------------------
# F. 换电 per-UAV
# ---------------------------------------------------------------------------


def test_recharge_per_uav() -> None:
    """降落后换电恢复到**各机自身**满电航程；max_range=None 回退全局值。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5),
             Nest(id=1, x=10000.0, y=0.0, z=0.0, capacity=5)]
    # 三台机飞三个近任务：异构满电 30000 / 80000 / None(全局 60000)
    u0 = _uav(0, 0.0, remaining=30000.0, max_range=30000.0, nest_id=0)
    u1 = _uav(1, 0.0, remaining=80000.0, max_range=80000.0, nest_id=0)
    u2 = _uav(2, 0.0, remaining=60000.0, max_range=None, nest_id=0)
    tasks = [_task(1, 1000.0), _task(2, 2000.0), _task(3, 3000.0)]
    horizon = _make_horizon([u0, u1, u2], tasks, nests,
                            uav_max_range=60000.0, n_cycles=1)
    rec = horizon.run_cycle()
    assert len(rec.executed_ids) == 3, f"三个近任务应全部执行，实际 {rec.executed_ids}"
    assert horizon.fleet.get(0).remaining_range == 30000.0
    assert horizon.fleet.get(1).remaining_range == 80000.0
    assert horizon.fleet.get(2).remaining_range == 60000.0  # None → 全局缺省
    print("[F] recharge per-UAV: PASS")


if __name__ == "__main__":
    test_full_charge_range_resolution()
    test_fleet_max_range_helper()
    test_geo_screen_uses_fleet_max()
    test_prefilter_per_uav_reachability()
    test_pool_dormant_lifecycle()
    test_service_stall_dormant_then_wake()
    test_recharge_per_uav()
    print("ALL PASS")
