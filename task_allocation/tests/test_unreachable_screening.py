# -*- coding: utf-8 -*-
"""静态物理不可达筛选（UNREACHABLE）聚焦测试——用户原则 2026-10-15。

原则：「任务不被完成只有物理约束被违反；例如最大航程，有些任务过于偏远
不被完成是正常的，符合我们理论的；我们不是解决所有的任务，而是在能力
范围内解决更多的当下的任务。」

落地（三处联动）：

  1. ``selection.feasibility.static_unreachable``：所有机巢往返航程都超过
     满电可及范围 ⇒ 物理不可达（任务/巢位固定 + 每周期满电，判定与周期
     状态无关，入池一次性完成）；
  2. ``task_pool.mark_unreachable``：UNREACHABLE 出池，单列计数，不混入
     淘汰；
  3. ``horizon._screen_unreachable``：每周期对 T_t 跑静态判定，命中即出池
     ——任务不再空转 churn（进选择→被规划器丢→超期，把「过于偏远」误导
     记成「饿死/淘汰」）。

同时锁定预筛的往返航程口径（2×travel vs margin×remaining_range）：
单程可达 ≠ 可服务（T52 实证：单程 39.9km 放行、往返 79.7km 实则不可达）。
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
    prefilter,
    static_unreachable,
)
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


def _nest(nid: int, x: float) -> Nest:
    return Nest(id=nid, x=x, y=0.0, z=0.0, capacity=4, available=True)


def _task(tid: int, x: float, latest: float = 1e6) -> Task:
    return Task(
        id=tid, x=x, y=0.0, z=0.0, reward=5.0,
        arrival_time=0.0, earliest=0.0, latest=latest,
    )


# ---------------------------------------------------------------------------
# 1) static_unreachable：静态物理判定
# ---------------------------------------------------------------------------


def test_static_unreachable_all_nests_too_far():
    """所有巢往返都超限 → True（「过于偏远」，合法未完成）。"""
    nests = [_nest(0, 0.0), _nest(1, 100000.0)]
    # 任务在 40000：巢0 往返 80000 > 0.95×60000=57000；巢1 往返 120000 同样超
    t = _task(0, x=40000.0)
    assert static_unreachable(t, nests, max_range=60000.0) is True


def test_static_reachable_if_any_nest_within_round_trip():
    """任一巢往返可及 → False（远巢不影响，近巢能服务）。"""
    nests = [_nest(0, 0.0), _nest(1, 20000.0)]
    t = _task(0, x=40000.0)  # 巢1 往返 40000 ≤ 57000 → 可达
    assert static_unreachable(t, nests, max_range=60000.0) is False


def test_static_unreachable_boundary_is_round_trip():
    """边界按往返（margin=1.0 物理上限）：单程 30000 → 往返 60000 =
    60000 恰可达；单程 30001 → 往返 60002 > 60000 不可达。"""
    nests = [_nest(0, 0.0)]
    assert static_unreachable(_task(0, x=30000.0), nests, 60000.0) is False
    assert static_unreachable(_task(0, x=30001.0), nests, 60000.0) is True


def test_static_unreachable_uses_distance_fn():
    """distance_fn 给出时按统一口径（DEM 三维）判定，不走欧氏。"""
    calls = []

    def scaled(a, b):
        calls.append(1)
        return abs(b[0] - a[0]) * 3.0  # 地形放大 3 倍

    nests = [_nest(0, 0.0)]
    t = _task(0, x=11000.0)  # 直线往返 22000 ≤ 60000；放大后 66000 > 60000
    assert static_unreachable(t, nests, 60000.0, distance_fn=scaled) is True
    assert calls, "distance_fn 应被调用"


def test_static_unreachable_degenerate_inputs():
    """无机巢或 max_range≤0 → 不判定（交给其它机制，不误杀）。"""
    assert static_unreachable(_task(0, x=1e9), [], 60000.0) is False
    assert static_unreachable(_task(0, x=1e9), [_nest(0, 0.0)], 0.0) is False


# ---------------------------------------------------------------------------
# 2) TaskPool.mark_unreachable：出池语义
# ---------------------------------------------------------------------------


def test_pool_mark_unreachable_moves_to_finished():
    """UNREACHABLE 出池：状态置位、进 finished、退出 planning_ids。"""
    tasks = [_task(0, x=100.0), _task(1, x=200.0)]
    pool = TaskPool(tasks, current_time=0.0)
    pool.begin_cycle(current_time=0.0)
    marked = pool.mark_unreachable([1])
    assert [t.id for t in marked] == [1]
    assert marked[0].status == TaskStatus.UNREACHABLE
    assert "unreachable_time" in marked[0].meta
    finished = {t.id: t.status for t in pool.finished_tasks()}
    assert finished[1] == TaskStatus.UNREACHABLE
    assert 1 not in [t.id for t in pool.planning_tasks()]
    assert 0 in [t.id for t in pool.planning_tasks()]


def test_pool_mark_unreachable_skips_executing():
    """执行中的任务不标记（已在飞，物理上已证明可达）。"""
    from task_allocation.common.types import AllocationSolution, UAVTour

    tasks = [_task(0, x=100.0), _task(1, x=200.0)]
    pool = TaskPool(tasks, current_time=0.0)
    pool.begin_cycle(current_time=0.0)
    sol = AllocationSolution(
        tours=[UAVTour.from_uav(
            UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=1e6,
                speed=10.0, nest_id=0),
            task_ids=[0], end_nest_id=0,
        )],
        candidate_task_ids=[0, 1],
        executed_task_ids=[0],
    )
    pool.apply_solution(sol)
    marked = pool.mark_unreachable([0, 1])
    assert [t.id for t in marked] == [1]  # 0 在飞，不动


def test_pool_mark_unreachable_unknown_id_ignored():
    """未知 / 已出池 id 静默跳过（调用方按 T_t 批量筛，不过滤异常）。"""
    tasks = [_task(0, x=100.0)]
    pool = TaskPool(tasks, current_time=0.0)
    pool.begin_cycle(current_time=0.0)
    assert pool.mark_unreachable([999]) == []
    assert pool.mark_unreachable([0, 999])[0].id == 0


# ---------------------------------------------------------------------------
# 2b) 不可达归因拆分（2026-10-25 口径对齐）
# ---------------------------------------------------------------------------


def test_pool_mark_unreachable_records_reason():
    """两类归因分别落 meta：geo（静态几何不可达）/ service（机队服务不可达）。

    完成率分母按两类合计排除；单列供归因——把资源约束误记成物理约束
    （或反之）会让完成率既非 100% 又无从解释（exp05 seed43 实证）。
    """
    tasks = [_task(0, x=100.0), _task(1, x=200.0)]
    pool = TaskPool(tasks, current_time=0.0)
    pool.begin_cycle(current_time=0.0)
    pool.mark_unreachable([0], reason="geo")
    pool.mark_unreachable([1], reason="service")
    reasons = {
        t.id: t.meta.get("unreachable_reason")
        for t in pool.finished_tasks()
        if t.status == TaskStatus.UNREACHABLE
    }
    assert reasons == {0: "geo", 1: "service"}


def test_prefilter_separates_geo_from_service_unreachable():
    """往返超限时按几何可达性分两类归因。

    - 几何可达（存在机巢往返 ≤ 满电航程）但最近**可用机**往返超限
      ⇒ ``no_uav_in_range``（机队服务不可达，典型为机队漂移致空巢）；
    - 所有机巢往返都超限 ⇒ ``exceed_round_trip_range``（真几何不可达）。

    travel 取自「最近可用 UAV」，而物理可达性应由「最近机巢」判定——
    两把尺子混同会让任务既不进 UNREACHABLE（分母照算）又每周期被拒
    （分子不计），完成率虚低且无法解释。
    """
    from task_allocation.common.types import ScoredTask

    cfg = FeasibilityConfig(current_time=0.0)
    nests = [_nest(0, 0.0)]  # 唯一机巢在原点
    max_range = 25000.0

    # 任务在 11km 处：巢往返 2×11000=22000 ≤ 25000 ⇒ 几何可达
    task = _task(0, x=11000.0)
    s = ScoredTask(task=task, priority=1.0)
    # 唯一可用机远在 40km 处、剩余航程 20km：往返 2×29000=58000 > 20000
    uav = UAV(id=0, x=40000.0, y=0.0, z=0.0, remaining_range=20000.0,
              speed=10.0, nest_id=0)
    passed, rejected = prefilter(
        [s], [uav], cfg, cost_hint={0: 29000.0},
        nests=nests, max_range=max_range,
    )
    assert not passed
    assert rejected[0].filter_reason == "no_uav_in_range"

    # 同场景但 max_range 收紧到 20km：巢往返 22000 > 20000 ⇒ 真几何不可达
    _, rejected2 = prefilter(
        [s], [uav], cfg, cost_hint={0: 29000.0},
        nests=nests, max_range=20000.0,
    )
    assert rejected2[0].filter_reason == "exceed_round_trip_range"


def test_prefilter_without_nests_keeps_geo_reason():
    """向后兼容：不传 nests/max_range 时归因保持 exceed_round_trip_range。

    旧调用方（含 pytest 既有基线）不因新增归因分离而改变行为。
    """
    from task_allocation.common.types import ScoredTask

    cfg = FeasibilityConfig(current_time=0.0)
    s = ScoredTask(task=_task(0, x=11000.0), priority=1.0)
    uav = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=20000.0,
              speed=10.0, nest_id=0)
    passed, rejected = prefilter([s], [uav], cfg)
    assert not passed and rejected[0].filter_reason == "exceed_round_trip_range"


def test_service_patience_config_default():
    """HorizonConfig.service_patience 默认 4（与 stable_patience 同族）。"""
    assert HorizonConfig().service_patience == 4


# ---------------------------------------------------------------------------
# 3) horizon._screen_unreachable：端到端
# ---------------------------------------------------------------------------


def _mini_horizon(max_range: float = 60000.0):
    """1 巢 1 机；近任务 x=10000（往返 20km 可达）、远任务 x=40000（往返
    80km > 0.95×60km 不可达）。"""
    nests = [_nest(0, 0.0)]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=max_range,
            speed=10.0, nest_id=0, status=UAVStatus.IDLE)
    ]
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    horizon = RollingHorizon(
        pool, fleet, nests, dem=None, estimator=None,
        horizon_config=HorizonConfig(
            n_cycles=1, cycle_length=600.0, complete_after_cycle=True,
            recharge_at_nest=True, uav_max_range=max_range,
        ),
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(prune_unprofitable=True, lamarckian=True),
        solver_config=DMDEConfig(pop_size=20, max_generations=30, seed=42),
        event_config=EventTriggerConfig(),
        nest_offset=1_000_000,
    )
    return horizon, pool


def test_horizon_screens_unreachable_before_selection():
    """远任务在选择前被筛出：record.unreachable_ids 记录、selection 的
    n_tasks_in 不含它、池里以 UNREACHABLE 终结（不是 EXPIRED）。"""
    horizon, pool = _mini_horizon()
    near, far = _task(0, x=10000.0), _task(1, x=40000.0)
    records = horizon.run(n_cycles=1, arrivals={1: [near, far]})
    rec = records[0]
    assert rec.unreachable_ids == [1]
    assert rec.selection["n_tasks_in"] == 1  # 只有近任务进选择
    finished = {t.id: t.status for t in pool.finished_tasks()}
    assert finished[1] == TaskStatus.UNREACHABLE
    # 近任务正常完成（未被误杀）
    assert 0 in rec.completed_ids
    assert rec.completed_ids == [0]


def test_horizon_keeps_reachable_tasks_untouched():
    """全部可达时不做任何筛选（unreachable_ids 为空）。"""
    horizon, _ = _mini_horizon()
    tasks = [_task(0, x=10000.0), _task(1, x=20000.0)]
    records = horizon.run(n_cycles=1, arrivals={1: tasks})
    assert records[0].unreachable_ids == []
    assert sorted(records[0].completed_ids) == [0, 1]


def test_horizon_screen_uses_hcfg_max_range():
    """静态判定的满电航程取自 HorizonConfig.uav_max_range（与充电恢复同源）。"""
    horizon, pool = _mini_horizon(max_range=100000.0)
    # x=40000：往返 80km ≤ 0.95×100km=95km → 现在可达，不应被筛
    far = _task(1, x=40000.0)
    records = horizon.run(n_cycles=1, arrivals={1: [_task(0, x=10000.0), far]})
    assert records[0].unreachable_ids == []
    assert sorted(records[0].completed_ids) == [0, 1]


def test_prefilter_round_trip_reason_matches_static():
    """预筛（动态 remaining_range）与静态判定同口径：剩余电量不足时
    预筛以 exceed_round_trip_range 拒（可下周期满电再试），静态判定
    才标记永久不可达——两层各司其职。"""
    uav = UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=50000.0, speed=10.0)
    t = _task(0, x=28000.0)  # 往返 56km：> 0.95×50km=47.5km（当期拒）
    from task_allocation.common.types import ScoredTask

    s = ScoredTask(task=t)
    cfg = FeasibilityConfig(current_time=0.0)
    passed, rejected = prefilter([s], [uav], cfg)
    assert not passed
    assert rejected[0].filter_reason == "exceed_round_trip_range"
    # 满电 60km 时同一任务静态可达（往返 56km ≤ 0.95×60=57km）——预筛
    # 拒绝（当期电量不足）≠ 永久不可达，两层各司其职
    assert static_unreachable(t, [_nest(0, 0.0)], max_range=60000.0) is False
