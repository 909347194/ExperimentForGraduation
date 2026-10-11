# -*- coding: utf-8 -*-
"""可行性判据的行进代价口径统一（T2 一致性）聚焦测试。

背景（同一判断里两套几何）：

  旧实现在 ``prefilter`` 里时间窗 ETA 用欧氏距离/速度、航程检查用欧氏距离，
  而 C_i(t)、ΔC 插入式估计、postcheck 预算、执行期 consume_range 全用三维
  地理代价 → 山区里 ETA 系统性低估，放进必然赶不上时间窗的任务，最终被
  完成闸门判违约回池（churn）。

本测试锁定新语义：

  travel = cost_hint[t.id]（三维代价）优先，缺省回退欧氏距离；
  航程检查（exceed_remaining_range）与时间窗（miss_time_window）共用 travel。

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
# 时间窗 ETA：三维代价 vs 欧氏距离
# ---------------------------------------------------------------------------


def test_eta_uses_cost_hint_not_distance():
    """任务欧氏距 100m（ETA 10s）但三维代价 5000m（ETA 500s），latest=100。

    旧口径（欧氏）会放行（10s < 100s）；新口径必须拒绝——山区绕行的
    真实飞行时间才是判据。
    """
    uav = _uav(rng=1e5)
    cfg = FeasibilityConfig(current_time=0.0)

    # 无 hint → 回退欧氏 → 放行（旧行为保持不变）
    s1 = ScoredTask(task=_task(0, latest=100.0))
    passed, rejected = prefilter([s1], [uav], cfg)
    assert len(passed) == 1 and not rejected

    # 有 hint → 按三维代价 → 拒绝
    s2 = ScoredTask(task=_task(0, latest=100.0))
    passed2, rejected2 = prefilter([s2], [uav], cfg, cost_hint={0: 5000.0})
    assert not passed2
    assert rejected2[0].filter_reason == "miss_time_window"


def test_eta_within_window_passes_with_cost_hint():
    """三维 ETA 500s 在 latest=1000 内 → 放行。"""
    uav = _uav(rng=1e5)
    s = ScoredTask(task=_task(0, latest=1000.0))
    passed, rejected = prefilter(
        [s], [uav], FeasibilityConfig(current_time=0.0), cost_hint={0: 5000.0}
    )
    assert len(passed) == 1 and not rejected


def test_fallback_without_hint_unchanged():
    """无 hint 时回退欧氏口径仍工作：100m / 10mps = 10s > latest 5s → 拒。"""
    uav = _uav(rng=1e5)
    s = ScoredTask(task=_task(0, latest=5.0))
    passed, rejected = prefilter([s], [uav], FeasibilityConfig(current_time=0.0))
    assert not passed
    assert rejected[0].filter_reason == "miss_time_window"


# ---------------------------------------------------------------------------
# 航程检查：同样改用三维代价
# ---------------------------------------------------------------------------


def test_range_check_uses_cost_hint():
    """欧氏距 100m 远小于 0.95×500m 航程，但三维代价 1000m 超出 → 拒。

    执行期 consume_range 扣的是三维航次代价，粗判口径必须与之一致。
    """
    uav = _uav(rng=500.0)
    s = ScoredTask(task=_task(0, latest=1e6))
    # 无 hint：100 < 0.95×500=475 → 放行
    passed, _ = prefilter([s], [uav], FeasibilityConfig(current_time=0.0))
    assert len(passed) == 1

    s2 = ScoredTask(task=_task(0, latest=1e6))
    passed2, rejected2 = prefilter(
        [s2], [uav], FeasibilityConfig(current_time=0.0), cost_hint={0: 1000.0}
    )
    assert not passed2
    assert rejected2[0].filter_reason == "exceed_remaining_range"


# ---------------------------------------------------------------------------
# 流水线端到端：cost_hint 贯通到 prefilter
# ---------------------------------------------------------------------------


def test_pipeline_prefilter_rejects_by_cost_eta():
    """run() 把 cost_hint 传进 prefilter：同位置两任务，窗口紧的被 ETA 拒。"""
    uav = _uav(rng=1e5)
    tasks = [_task(0, latest=100.0), _task(1, latest=1e6)]
    result = run(
        tasks,
        [uav],
        SelectionConfig(),
        cost_hint={0: 5000.0, 1: 5000.0},
    )
    d = result.diagnostics
    assert d["n_rejected_prefilter"] == 1
    reasons = {s.task.id: s.filter_reason for s in result.scored}
    assert reasons[0] == "miss_time_window"
    assert reasons[1] is None
    assert [t.id for t in result.selected] == [1]
