# -*- coding: utf-8 -*-
"""可行性判据的行进代价口径统一（T2 一致性）聚焦测试。

背景（同一判断里两套几何）：

  旧实现在 ``prefilter`` 里时间窗 ETA 用欧氏距离/速度、航程检查用欧氏距离，
  而 C_i(t)、ΔC 插入式估计、postcheck 预算、执行期 consume_range 全用三维
  地理代价 → 山区里 ETA 系统性低估，会放进必然赶不上时间窗的任务，徒增
  完成闸门的违约回池。

本测试锁定新语义：

  travel = cost_hint[t.id]（三维代价）优先，缺省回退欧氏距离；
  航程检查按**往返口径**（2×travel vs range_margin×remaining_range）：
  航次必须返航，单程可达 ≠ 可服务。
  时间窗预筛默认关闭（用户原则 2026-10-15：迟到是奖励定价问题不是参选
  资格），仅在 check_time_window=True 的消融口径保留。

代价语义链：一阶段粗判 ← cost_hint（三维矩阵）→ 二阶段航次评价/执行扣航程。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    ScoredTask,
    Task,
    UAV,
)
from task_allocation.methods.selection.feasibility import (  # noqa: E402
    FeasibilityConfig,
    prefilter,
)
from task_allocation.methods.selection.pipeline import (  # noqa: E402
    SelectionConfig,
    run,
)


def _task(tid: int, latest: float) -> Task:
    return Task(
        id=tid, x=100.0, y=0.0, z=0.0, reward=5.0,
        earliest=0.0, latest=latest, arrival_time=0.0,
    )


def _uav(rng: float, speed: float = 10.0) -> UAV:
    return UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=rng, speed=speed)


# ---------------------------------------------------------------------------
# 时间窗 ETA：默认不拒（消融口径 check_time_window=True 才拒）
# ---------------------------------------------------------------------------


def test_eta_no_longer_rejects_by_default():
    """默认口径：ETA 500s > latest 100s 也放行——迟到由适应度折减定价。

    用户原则（2026-10-15）「任务不被完成只有物理约束被违反」：时间窗
    不是物理约束，不能作为预筛拒绝理由。
    """
    uav = _uav(rng=1e5)
    s = ScoredTask(task=_task(0, latest=100.0))
    cfg = FeasibilityConfig(current_time=0.0)  # check_time_window 默认 False
    assert cfg.check_time_window is False
    passed, rejected = prefilter([s], [uav], cfg, cost_hint={0: 5000.0})
    assert len(passed) == 1 and not rejected


def test_eta_uses_cost_hint_when_ablation_enabled():
    """消融口径（check_time_window=True）：三维代价 5000m → ETA 500s >
    latest 100s → 拒。锁「时间判据也用三维代价」的口径统一。"""
    uav = _uav(rng=1e5)
    cfg = FeasibilityConfig(current_time=0.0, check_time_window=True)
    s = ScoredTask(task=_task(0, latest=100.0))
    passed, rejected = prefilter([s], [uav], cfg, cost_hint={0: 5000.0})
    assert not passed
    assert rejected[0].filter_reason == "miss_time_window"


def test_eta_within_window_passes_with_cost_hint():
    """三维 ETA 500s 在 latest=1000 内 → 放行（消融口径同样放行）。"""
    uav = _uav(rng=1e5)
    s = ScoredTask(task=_task(0, latest=1000.0))
    passed, rejected = prefilter(
        [s], [uav], FeasibilityConfig(current_time=0.0), cost_hint={0: 5000.0}
    )
    assert len(passed) == 1 and not rejected


def test_fallback_without_hint_unchanged():
    """无 hint 时回退欧氏口径仍工作；默认不因时间窗拒绝（旧口径会拒）。"""
    uav = _uav(rng=1e5)
    s = ScoredTask(task=_task(0, latest=5.0))
    passed, rejected = prefilter([s], [uav], FeasibilityConfig(current_time=0.0))
    assert len(passed) == 1 and not rejected


# ---------------------------------------------------------------------------
# 航程检查：往返口径 + 三维代价
# ---------------------------------------------------------------------------


def test_range_check_uses_cost_hint_round_trip():
    """往返口径：欧氏单程 100m（往返 200m < 0.95×500m）→ 放行；
    三维代价 1000m（往返 2000m > 475m）→ 拒。

    执行期 consume_range 扣的是三维航次代价（含返航），粗判口径必须与
    之一致：单程可达 ≠ 可服务。
    """
    uav = _uav(rng=500.0)
    s = ScoredTask(task=_task(0, latest=1e6))
    # 无 hint：往返 2×100=200 < 475 → 放行
    passed, _ = prefilter([s], [uav], FeasibilityConfig(current_time=0.0))
    assert len(passed) == 1

    s2 = ScoredTask(task=_task(0, latest=1e6))
    passed2, rejected2 = prefilter(
        [s2], [uav], FeasibilityConfig(current_time=0.0), cost_hint={0: 1000.0}
    )
    assert not passed2
    assert rejected2[0].filter_reason == "exceed_round_trip_range"


def test_range_round_trip_boundary():
    """往返恰好在物理边界（margin=1.0）：2×travel ≤ remaining_range 放行，
    超出即拒。"""
    # remaining_range 1000：单程 500 → 往返 1000 ≤ 1000 放行
    uav = _uav(rng=1000.0)
    s = ScoredTask(task=_task(0, latest=1e6))
    passed, _ = prefilter(
        [s], [uav], FeasibilityConfig(current_time=0.0), cost_hint={0: 500.0}
    )
    assert len(passed) == 1
    # 单程 501 → 往返 1002 > 1000 → 拒
    s2 = ScoredTask(task=_task(0, latest=1e6))
    passed2, rejected2 = prefilter(
        [s2], [uav], FeasibilityConfig(current_time=0.0), cost_hint={0: 501.0}
    )
    assert not passed2
    assert rejected2[0].filter_reason == "exceed_round_trip_range"


# ---------------------------------------------------------------------------
# 流水线端到端：cost_hint 贯通到 prefilter
# ---------------------------------------------------------------------------


def test_pipeline_prefilter_rejects_by_round_trip_range():
    """run() 把 cost_hint 传进 prefilter：同位置两任务，往返超航程的被拒。"""
    uav = _uav(rng=1e5)
    tasks = [_task(0, latest=100.0), _task(1, latest=1e6)]
    result = run(
        tasks,
        [uav],
        SelectionConfig(),
        cost_hint={0: 5000.0, 1: 5000.0},
    )
    d = result.diagnostics
    # 默认无时间预筛：latest=100 的 ETA 500s 也不再被拒；1e5 航程下往返
    # 10000m < 0.95×1e5 → 两个都过预筛
    assert d["n_rejected_prefilter"] == 0
    reasons = {s.task.id: s.filter_reason for s in result.scored}
    assert reasons[0] is None and reasons[1] is None


def test_pipeline_rejection_reason_histogram():
    """往返超航程的任务进 filter_reasons 直方图（图表取数口径）。"""
    uav = _uav(rng=1000.0)  # 往返上限 950
    tasks = [_task(0, latest=1e6), _task(1, latest=1e6)]
    result = run(
        tasks,
        [uav],
        SelectionConfig(),
        cost_hint={0: 400.0, 1: 600.0},  # 往返 800 过 / 1200 拒
    )
    d = result.diagnostics
    assert d["n_rejected_prefilter"] == 1
    assert d["filter_reasons"] == {"exceed_round_trip_range": 1}
    assert [t.id for t in result.selected] == [0]
