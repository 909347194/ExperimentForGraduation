# -*- coding: utf-8 -*-
"""能力驱动规模（论文 3.3 的 min{αK, cap(t)}）聚焦测试。

背景（第一阶段规模决策的能力解耦）：

  旧规则 候选数 = ⌈α·K_avail⌉、选择数 = ⌈max_ratio·K_avail⌉ —— 纯机数名额，
  机队航程充裕时欠选、航程紧张时过筛。本测试锁定新规则：

    cap(t) = max(min_cap, ⌊B(t) / c̃⌋)
    B(t)   = budget_scale · budget_factor · Σ remaining_range(可用机)
    候选数 = min(⌈α·K_avail⌉, cap(t))
    选择数 = min(⌈max_ratio·K_avail⌉, cap(t))

  以及三条配套语义：
    - capability.enabled=False 时退回纯机数名额（消融开关）
    - 事件驱动 forced_task_ids 仍绕过候选上限（含能力上界）
    - 粗代价口径与基础优先级 C_i(t) 一致（cost_hint 优先）
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
    UAVStatus,
)
from task_allocation.methods.selection.capability import (  # noqa: E402
    CapabilityScaleConfig,
    capability_task_cap,
    coarse_task_costs,
    fleet_range_budget,
)
from task_allocation.methods.selection.pipeline import (  # noqa: E402
    SelectionConfig,
    run,
)
from task_allocation.methods.selection.priority import (  # noqa: E402
    PriorityConfig,
    top_alpha_preselect,
)
from task_allocation.methods.selection.marginal_gain import (  # noqa: E402
    MarginalConfig,
    select_by_marginal_gain,
)


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------


def _task(tid: int, x: float, y: float, reward: float = 5.0) -> Task:
    return Task(
        id=tid, x=x, y=y, z=0.0, reward=reward,
        earliest=0.0, latest=1e6, arrival_time=0.0,
    )


def _uav(uid: int, rng: float, x: float = 0.0, y: float = 0.0) -> UAV:
    return UAV(id=uid, x=x, y=y, z=0.0, remaining_range=rng, speed=10.0)


def _scored(tasks: list[Task]) -> list[ScoredTask]:
    """按列表序构造 ScoredTask（模拟已按 P_i(t) 降序排好）。"""
    return [ScoredTask(task=t, priority=float(len(tasks) - i)) for i, t in enumerate(tasks)]


# ---------------------------------------------------------------------------
# capability_task_cap / fleet_range_budget / coarse_task_costs
# ---------------------------------------------------------------------------


def test_cap_follows_budget():
    """预算可承载数 = ⌊B / c̃⌋：预算 250、中位代价 100 → cap = 2。"""
    cfg = CapabilityScaleConfig()
    assert capability_task_cap([100.0] * 10, 250.0, cfg) == 2
    assert capability_task_cap([100.0] * 10, 500.0, cfg) == 5


def test_cap_quantile_and_min_cap():
    """quantile 可调；预算为 0 时保底 min_cap。"""
    cfg = CapabilityScaleConfig(quantile=0.9, min_cap=1)
    # 0.9 分位（线性插值 ≈ 820）→ ⌊2000/820⌋ = 2（中位 500 时会是 4）
    assert capability_task_cap([100.0, 500.0, 900.0], 2000.0, cfg) == 2
    assert capability_task_cap([100.0] * 5, 0.0, cfg) == 1


def test_cap_zero_cost_fallback():
    """全零代价时 c̃ 回退 1.0，避免除零。"""
    cfg = CapabilityScaleConfig()
    assert capability_task_cap([0.0] * 5, 10.0, cfg) == 10


def test_fleet_budget_and_costs():
    uavs = [_uav(0, 1000.0), _uav(1, 500.0), _uav(2, 99999.0)]
    uavs[2].status = UAVStatus.BUSY  # 不可用 → 不计入预算
    assert fleet_range_budget(uavs, 1.0) == 1500.0
    assert fleet_range_budget(uavs, 0.5) == 750.0

    tasks = [_task(0, 300.0, 0.0), _task(1, 0.0, 400.0)]
    # cost_hint 优先；缺省回退最近可用 UAV 欧氏距离
    costs = coarse_task_costs(tasks, uavs, cost_hint={0: 123.0})
    assert costs[0] == 123.0
    assert abs(costs[1] - 400.0) < 1e-9


# ---------------------------------------------------------------------------
# top_alpha_preselect：min(⌈αK⌉, cap) 取交
# ---------------------------------------------------------------------------


def test_top_alpha_hard_cap_binds():
    tasks = [_task(i, float(i), 0.0) for i in range(10)]
    scored = _scored(tasks)
    # 无 cap：⌈2.5×4⌉ = 10 全放行
    assert len(top_alpha_preselect(scored, n_avail_uavs=4, alpha=2.5)) == 10
    # 能力承载 3 → 只留 3
    assert len(top_alpha_preselect(scored, 4, 2.5, hard_cap=3)) == 3


def test_top_alpha_without_hard_cap_keeps_alpha_rule():
    tasks = [_task(i, float(i), 0.0) for i in range(10)]
    scored = _scored(tasks)
    got = top_alpha_preselect(scored, n_avail_uavs=2, alpha=2.5)
    assert len(got) == 5  # ⌈2.5×2⌉，纯机数名额（消融对照口径）


# ---------------------------------------------------------------------------
# select_by_marginal_gain：选择数同受能力上界
# ---------------------------------------------------------------------------


def test_marginal_limit_capped_by_capability():
    uavs = [_uav(0, 10000.0), _uav(1, 10000.0)]
    tasks = [_task(i, float(100 * (i + 1)), 0.0) for i in range(6)]
    scored = _scored(tasks)
    # 无 cap：limit = ⌈2.0×2⌉ = 4
    got = select_by_marginal_gain(scored, uavs, MarginalConfig())
    assert len(got) == 4
    # 能力承载 2 → limit = min(4, 2) = 2
    got2 = select_by_marginal_gain(scored, uavs, MarginalConfig(), capability_cap=2)
    assert len(got2) == 2


# ---------------------------------------------------------------------------
# 流水线端到端
# ---------------------------------------------------------------------------


def _pipeline_setup(rng_each: float = 1000.0):
    """2 机 @原点，5 任务散布在 500~900m（粗代价 = 距离，中位 540）。

    任务属性全同（reward/时间窗/等待/协同），因此 P_i(t) 严格按代价升序，
    距离 900m 的任务 4 必排最后一名，供强制纳入测试使用。
    """
    uavs = [_uav(0, rng_each), _uav(1, rng_each)]
    xs = [500.0, 520.0, 540.0, 560.0, 900.0]
    tasks = [_task(i, x, 0.0) for i, x in enumerate(xs)]
    return tasks, uavs


def test_pipeline_cap_reduces_candidates_and_selection():
    tasks, uavs = _pipeline_setup(rng_each=1000.0)
    cfg = SelectionConfig()
    result = run(tasks, uavs, cfg)
    d = result.diagnostics
    # B = 2000，c̃≈540 → cap = 3 < ⌈2.5×2⌉ = 5
    assert d["capability_enabled"] is True
    assert d["capability_cap"] == 3
    assert d["capability_budget"] == 2000.0
    assert d["n_after_priority"] == 3
    assert d["n_after_marginal"] <= 3
    assert len(result.selected) <= 3


def test_pipeline_abundant_budget_keeps_alpha_cap():
    """预算充裕时 cap 不生效，规模仍由 ⌈αK⌉ 名额决定。"""
    tasks, uavs = _pipeline_setup(rng_each=1e6)
    result = run(tasks, uavs, SelectionConfig())
    d = result.diagnostics
    assert d["capability_cap"] >= 5  # 远大于任务数
    assert d["n_after_priority"] == 5  # ⌈2.5×2⌉ 名额截断


def test_pipeline_disabled_keeps_count_behavior():
    """开关关闭 → 退回纯机数名额（即使预算紧张也不做能力收编）。"""
    tasks, uavs = _pipeline_setup(rng_each=1000.0)
    cfg = SelectionConfig()
    cfg.capability.enabled = False
    result = run(tasks, uavs, cfg)
    d = result.diagnostics
    assert d["capability_enabled"] is False
    assert "capability_cap" not in d
    assert d["n_after_priority"] == 5  # ⌈2.5×2⌉，与能力无关


def test_forced_tasks_bypass_capability_cap():
    """事件驱动强制纳入任务绕过候选上限（含能力上界），仍进后续过滤。"""
    tasks, uavs = _pipeline_setup(rng_each=1000.0)
    cfg = SelectionConfig()
    cfg.forced_task_ids = {4}  # 优先级最低、必被 cap 裁掉的任务
    result = run(tasks, uavs, cfg)
    d = result.diagnostics
    assert d["capability_cap"] == 3
    assert d["n_forced_in"] == 1
    assert d["n_after_priority"] == 4  # cap(3) + 强制纳入(1)
    by_id = {s.task.id: s for s in result.scored}
    assert by_id[4].filter_reason != "not_in_top_alpha"
