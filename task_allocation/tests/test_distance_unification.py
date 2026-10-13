# -*- coding: utf-8 -*-
"""距离口径统一（DEM 三维航程）的契约测试。

背景（docs/full_review_20261012.md P1-1）：
    同一条航段在仓内曾有三套长度 —— 代价矩阵走 DEM 垂直切面、评价端/ETA 走
    欧氏直线、第一阶段走 cost_hint 混合。直线恒 ≤ 地形航程 ⇒ ETA 系统性低估
    飞行时间，时间窗把「其实来不及」的任务判成来得及。

本测试锁定统一后的契约：

    1. 凡是把距离换算成**时间**的地方（航迹时间 F3、时间窗 ETA、补全判定的
       到达时刻）都必须走 ``CostProvider.travel``，不得再用直线；
    2. 第一阶段（最近可用机、插入代价、邻近增益）与第二阶段共用同一把尺子；
    3. 距离函数带记忆化：同一条边不重复估算（否则 DMDE 上万次评价会拖垮）；
    4. 无 DEM 时退回欧氏，**行为与改造前逐位一致**（测试基线不动）。
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
)
from task_allocation.methods.muas.constraints.coop_constraints import (  # noqa: E402
    check_time_window_constraint,
)
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain  # noqa: E402
from task_allocation.methods.muas.cost.vertical_section import (  # noqa: E402
    VerticalSectionCostEstimator,
    make_distance_fn,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
    build_cost_provider,
)
from task_allocation.methods.selection.feasibility import (  # noqa: E402
    FeasibilityConfig,
    _nearest_avail_uav,
)
from task_allocation.methods.selection.marginal_gain import (  # noqa: E402
    insert_delta_cost,
)


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------


def _uav(uid: int = 0, x: float = 0.0, y: float = 0.0, speed: float = 10.0) -> UAV:
    return UAV(
        id=uid,
        x=x,
        y=y,
        z=0.0,
        remaining_range=1e6,
        speed=speed,
        nest_id=0,
        status=UAVStatus.IDLE,
    )


def _task(tid: int = 0, x: float = 1000.0, latest: float | None = None) -> Task:
    return Task(
        id=tid,
        x=x,
        y=0.0,
        z=0.0,
        reward=5.0,
        arrival_time=0.0,
        earliest=0.0,
        latest=latest,
    )


def _tour(uav: UAV, task_ids: list[int]):
    from task_allocation.common.types import UAVTour

    return UAVTour.from_uav(uav, task_ids=task_ids, end_nest_id=uav.nest_id)


class _CountingEstimator:
    """记录被调用次数的估算器替身（用于验证记忆化）。"""

    def __init__(self, scale: float = 3.0) -> None:
        self.calls = 0
        self.scale = scale

    def estimate(self, start, end, weight: float = 1.0):
        self.calls += 1
        a = np.asarray(start, dtype=np.float64)
        b = np.asarray(end, dtype=np.float64)
        d = float(np.linalg.norm(b[:3] - a[:3])) * self.scale
        return type("R", (), {"distance": d, "cost": d * weight})()


# ---------------------------------------------------------------------------
# 1. 距离函数：记忆化 / 无 DEM 回退 / 对称
# ---------------------------------------------------------------------------


def test_distance_fn_memoizes_repeated_edges():
    """同一条边只估算一次（DMDE 上万次评价的性能前提）。"""
    est = _CountingEstimator(scale=2.0)
    fn = make_distance_fn(est)  # type: ignore[arg-type]
    a, b = (0.0, 0.0, 0.0), (1000.0, 0.0, 0.0)
    d1 = fn(a, b)
    d2 = fn(b, a)  # 反向查询应命中同一缓存
    d3 = fn(a, b)
    assert d1 == pytest.approx(2000.0)
    assert d2 == pytest.approx(d1)
    assert d3 == pytest.approx(d1)
    assert est.calls == 1, f"同一条边被重复估算 {est.calls} 次，记忆化失效"


def test_distance_fn_without_dem_is_euclid():
    """无 DEM：退回三维直线，与改造前的欧氏口径逐位一致。"""
    fn = make_distance_fn(None)
    a, b = (0.0, 0.0, 0.0), (3.0, 4.0, 0.0)
    assert fn(a, b) == pytest.approx(5.0)
    assert fn((0.0, 0.0, 0.0), (0.0, 0.0, 5.0)) == pytest.approx(5.0)


def test_distance_fn_shared_cache_across_cycles():
    """外部传入同一 cache 时跨调用复用（滚动层跨周期共享）。"""
    est = _CountingEstimator()
    cache: dict = {}
    f1 = make_distance_fn(est, cache=cache)  # type: ignore[arg-type]
    f2 = make_distance_fn(est, cache=cache)  # type: ignore[arg-type]
    f1((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    f2((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    assert est.calls == 1
    assert len(cache) == 1


def test_dem_distance_not_shorter_than_straight_line():
    """地形航程不得短于三维直线（三角不等式下界）。"""
    z = np.zeros((60, 60))
    z[25:35, :] = 800.0  # 横贯东西的山脊
    dem = DEMTerrain.from_array(z, bounds=(0.0, 0.0, 60 * 200.0, 60 * 200.0))
    est = VerticalSectionCostEstimator(dem_terrain=dem, min_clearance=50.0)
    a = (4000.0, 6000.0, 3600.0)
    b = (8000.0, 6000.0, 3600.0)
    straight = float(np.linalg.norm(np.array(b) - np.array(a)))
    assert est.distance(a, b) >= straight - 1e-6


# ---------------------------------------------------------------------------
# 2. CostProvider.travel 是唯一距离出口
# ---------------------------------------------------------------------------


def test_cost_provider_travel_uses_injected_distance_fn():
    """注入的距离函数生效；未注入时退回欧氏（旧行为）。"""
    tasks = [_task(0, 1000.0)]
    uavs = [_uav(0)]
    cp_plain = build_cost_provider(tasks, [], uavs)
    assert cp_plain.travel((0.0, 0.0, 0.0), (1000.0, 0.0, 0.0)) == pytest.approx(1000.0)

    fn = make_distance_fn(_CountingEstimator(scale=4.0))  # type: ignore[arg-type]
    cp_dem = build_cost_provider(tasks, [], uavs, distance_fn=fn)
    assert cp_dem.travel((0.0, 0.0, 0.0), (1000.0, 0.0, 0.0)) == pytest.approx(4000.0)


def test_travel_falls_back_on_estimator_error():
    """估算器抛异常不得让求解崩溃：退回欧氏并记录。"""

    def boom(a, b):
        raise RuntimeError("dem query failed")

    cp = build_cost_provider([_task(0)], [], [_uav(0)], distance_fn=boom)
    assert cp.travel((0.0, 0.0, 0.0), (1000.0, 0.0, 0.0)) == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# 3. 时间窗 ETA 与航迹时间走统一口径
# ---------------------------------------------------------------------------


def test_time_window_eta_uses_distance_fn():
    """同一航次：注入 10× 距离函数 → 由不违约变成违约。"""
    uav = _uav(0, speed=10.0)
    task = _task(0, x=1000.0, latest=50.0)  # 1000 m / 10 m/s = 100 s > 50 s
    tour = _tour(uav, [0])

    # 直线口径：距离被缩到 100 m → 10 s，不违约
    shrink = make_distance_fn(_CountingEstimator(scale=0.1))  # type: ignore[arg-type]
    pen_ok, tags_ok = check_time_window_constraint(
        [tour], {0: task}, {0: uav}, distance_fn=shrink
    )
    assert pen_ok == 0.0 and tags_ok == []

    # 统一口径（放大 10×）：距离 10000 m → 1000 s，违约且量级正确
    stretch = make_distance_fn(_CountingEstimator(scale=10.0))  # type: ignore[arg-type]
    pen_bad, tags_bad = check_time_window_constraint(
        [tour], {0: task}, {0: uav}, distance_fn=stretch
    )
    assert pen_bad == pytest.approx(1000.0 - 50.0)
    assert tags_bad == ["tw_task_0"]


def test_time_window_default_is_euclid_backward_compatible():
    """不传 distance_fn 时保持欧氏（既有单测基线不变）。"""
    uav = _uav(0, speed=10.0)
    task = _task(0, x=1000.0, latest=50.0)
    tour = _tour(uav, [0])
    pen, tags = check_time_window_constraint([tour], {0: task}, {0: uav})
    assert pen == pytest.approx(100.0 - 50.0)
    assert tags == ["tw_task_0"]


def test_tour_time_uses_travel_not_euclid():
    """F3 航迹时间必须由统一距离口径换算（不能再用直线）。"""
    uav = _uav(0, speed=10.0)
    task = _task(0, x=1000.0)
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    stretch = make_distance_fn(_CountingEstimator(scale=10.0))  # type: ignore[arg-type]

    problem = SelectiveMUASProblem(
        tasks=[task],
        uavs=[uav],
        nests=nests,
        costs=build_cost_provider([task], nests, [uav], distance_fn=stretch),
        config=MUASProblemConfig(default_service_time=0.0),
    )
    tour = _tour(uav, [0])
    # 直线：1000 m / 10 = 100 s（+返巢 0）；统一口径 10× → 10000/10 + 10000/10 = 2000 s
    assert problem.tour_time(tour) == pytest.approx(2000.0)


# ---------------------------------------------------------------------------
# 4. 第一阶段共用同一把尺子
# ---------------------------------------------------------------------------


def test_nearest_uav_follows_distance_fn_not_euclid():
    """「最近可用机」按地形航程选，而非直线（山谷两侧直线近、实际远）。"""
    near_by_line = _uav(0, x=1000.0)  # 直线最近
    far_by_line = _uav(1, x=5000.0)
    task = _task(0, x=0.0)

    u, d = _nearest_avail_uav(task, [near_by_line, far_by_line])
    assert u is not None and u.id == 0
    assert d == pytest.approx(1000.0)

    # 注入「越远越便宜」的反例距离函数 → 应改选 1 号机
    def inverted(a, b):
        return 1.0 / (1.0 + abs(a[0] - b[0]))

    u2, _ = _nearest_avail_uav(task, [near_by_line, far_by_line], distance_fn=inverted)
    assert u2 is not None and u2.id == 1


def test_insert_delta_cost_fallback_uses_distance_fn():
    """pairwise 缺项时兜底长度走统一口径，不与已有条目混用两把尺子。"""
    t = _task(1, x=1000.0)
    sel = [_task(0, x=0.0)]
    uavs = [_uav(0, x=0.0)]
    stretch = make_distance_fn(_CountingEstimator(scale=10.0))  # type: ignore[arg-type]

    base = insert_delta_cost(t, sel, uavs)
    dem = insert_delta_cost(t, sel, uavs, distance_fn=stretch)
    assert dem == pytest.approx(base * 10.0)


def test_prefilter_time_window_uses_unified_travel():
    """预筛的 ETA 走 cost_hint（三维代价）；cost_hint 即统一口径的产物。"""
    from task_allocation.methods.selection.feasibility import prefilter
    from task_allocation.common.types import ScoredTask

    task = _task(0, x=10000.0, latest=100.0)
    s = ScoredTask(task=task, priority=1.0)
    uav = _uav(0, speed=10.0)
    cfg = FeasibilityConfig(current_time=0.0)

    # 无 cost_hint：直线 10000 m / 10 = 1000 s > 100 s → 淘汰
    passed, rejected = prefilter([s], [uav], cfg)
    assert not passed and rejected[0].filter_reason == "miss_time_window"

    # cost_hint 给出很短的三维代价 → 通过（证明 ETA 用的是 travel 而非直线）
    s2 = ScoredTask(task=_task(0, x=10000.0, latest=100.0), priority=1.0)
    passed2, _ = prefilter([s2], [uav], cfg, cost_hint={0: 50.0})
    assert len(passed2) == 1
