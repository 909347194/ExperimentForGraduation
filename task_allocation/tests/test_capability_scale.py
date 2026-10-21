# -*- coding: utf-8 -*-
"""能力驱动规模（论文 3.3）聚焦测试。

背景（第一阶段规模决策的能力解耦）：

  旧规则 候选数 = ⌈α·K_avail⌉、选择数 = ⌈max_ratio·K_avail⌉ —— 纯机数名额，
  机队航程充裕时欠选、航程紧张时过筛。本测试锁定新规则：

    cap(t) = max(min_cap, ⌊B(t) / c̃⌋)
    B(t)   = budget_scale · budget_factor · Σ remaining_range(可用机)

  名额与 cap(t) 的合成有两种 mode（capability.effective_cap）：
    - intersect（默认）：min{名额, cap(t)} —— 能力只能收紧，绝不超名额
    - primary          ：cap(t) 为主、名额兜底 —— 充裕时允许超名额

  以及三条配套语义：
    - capability.enabled=False 时退回纯机数名额（消融开关）
    - 事件驱动 forced_task_ids 仍绕过候选上限（含能力上界）
    - 粗代价口径与基础优先级 C_i(t) 一致（cost_hint 优先）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    effective_cap,
    fleet_range_budget,
    merge_caps,
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
    assert len(top_alpha_preselect(scored, n_avail=4, alpha=2.5)) == 10
    # 能力承载 3 → 只留 3
    assert len(top_alpha_preselect(scored, 4, 2.5, hard_cap=3)) == 3


def test_top_alpha_without_hard_cap_keeps_alpha_rule():
    tasks = [_task(i, float(i), 0.0) for i in range(10)]
    scored = _scored(tasks)
    got = top_alpha_preselect(scored, n_avail=2, alpha=2.5)
    assert len(got) == 5  # ⌈2.5×2⌉，纯机数名额（消融对照口径）


# ---------------------------------------------------------------------------
# select_by_marginal_gain：选择数同受能力上界
# ---------------------------------------------------------------------------


def test_n_sel_capped_by_capability():
    uavs = [_uav(0, 10000.0), _uav(1, 10000.0)]
    tasks = [_task(i, float(100 * (i + 1)), 0.0) for i in range(6)]
    scored = _scored(tasks)
    # 无 cap：n_sel = ⌈2.0×2⌉ = 4
    got = select_by_marginal_gain(scored, uavs, MarginalConfig())
    assert len(got) == 4
    # 能力承载 2 → n_sel = min(4, 2) = 2
    got2 = select_by_marginal_gain(scored, uavs, MarginalConfig(), capability_cap=2)
    assert len(got2) == 2


# ---------------------------------------------------------------------------
# 记账口径：②③被动空闲的航程不得计入 B(t)
# ---------------------------------------------------------------------------


def test_budget_caps_by_sortie_budget():
    """② 起降/充电吞吐上限 S_t：4 架可用但只能飞 2 架 → 只计 2 架航程。"""
    uavs = [_uav(i, 1000.0) for i in range(4)]
    assert fleet_range_budget(uavs, 1.0) == 4000.0  # 无上限：全量
    assert fleet_range_budget(uavs, 1.0, sortie_budget=2) == 2000.0


def test_budget_caps_by_max_participating():
    """③ 参与机数硬上限：同效。"""
    uavs = [_uav(i, 1000.0) for i in range(4)]
    assert fleet_range_budget(uavs, 1.0, max_participating_uavs=1) == 1000.0


def test_budget_takes_stricter_cap_and_merge_caps():
    """②③ 取交；merge_caps 两侧配时以更严者为准。"""
    uavs = [_uav(i, 1000.0) for i in range(4)]
    assert (
        fleet_range_budget(uavs, 1.0, sortie_budget=3, max_participating_uavs=2)
        == 2000.0
    )
    assert merge_caps(None, None) is None
    assert merge_caps(3, None) == 3 and merge_caps(None, 2) == 2
    assert merge_caps(3, 2) == 2 and merge_caps(1, 5) == 1


def test_budget_caps_pick_largest_range():
    """乐观上界：被压地的是短航程机，预算按剩余航程降序取前 k_op。"""
    uavs = [_uav(0, 100.0), _uav(1, 200.0), _uav(2, 300.0), _uav(3, 400.0)]
    assert fleet_range_budget(uavs, 1.0, sortie_budget=2) == 700.0  # 400+300


def test_pipeline_budget_respects_sortie_cap():
    """流水线里 B(t) 与 cap(t) 随②自动收紧。"""
    tasks, uavs = _pipeline_setup(rng_each=1000.0)  # 2 机 × 1000
    cfg = SelectionConfig()
    cfg.capability.sortie_budget = 1
    result = run(tasks, uavs, cfg)
    d = result.diagnostics
    assert d["capability_budget"] == 1000.0  # 只计 1 架
    assert d["capability_cap"] == 1  # ⌊1000/540⌋
    assert d["n_after_priority"] == 1


def test_horizon_syncs_stage_caps_into_budget():
    """滚动层自动同步：打开二阶段②③旋钮，一阶段记账必须跟着收紧。

    不同步的旧口径会把 2 架的航程（10000）都算进 B(t)，
    即使 sortie_budget=1 明确只飞 1 架。
    """
    from task_allocation.common.types import Nest, UAVStatus
    from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
    from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig
    from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon
    from task_allocation.methods.task_pool import TaskPool
    from task_allocation.methods.uav_state import UAVFleet

    def _run(sortie):
        nest = Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4, available=True)
        uavs = [
            UAV(id=i, x=0.0, y=0.0, z=0.0, remaining_range=5000.0,
                speed=10.0, nest_id=0, status=UAVStatus.IDLE)
            for i in range(2)
        ]
        tasks = [
            Task(id=i, x=float(200 * (i + 1)), y=0.0, z=0.0, reward=5.0,
                 earliest=0.0, latest=1e6, arrival_time=0.0)
            for i in range(4)
        ]
        horizon = RollingHorizon(
            TaskPool(tasks, current_time=0.0), UAVFleet(uavs, current_time=0.0),
            [nest], dem=None, estimator=None,
            horizon_config=HorizonConfig(
                n_cycles=1, cycle_length=60.0, uav_max_range=5000.0,
                complete_after_cycle=False,
            ),
            stage_config=MUASStageConfig(sortie_budget=sortie),
            solver_config=DMDEConfig(pop_size=8, max_generations=5, seed=1),
        )
        return horizon.run_cycle(new_tasks=None, advance=False)

    # ② 打开：B(t) 只计 1 架 → 5000
    assert _run(sortie=1).selection["capability_budget"] == 5000.0
    # ② 关闭：全量 → 10000
    assert _run(sortie=None).selection["capability_budget"] == 10000.0


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
    assert by_id[4].filter_reason != "not_in_n_cand"


# ---------------------------------------------------------------------------
# effective_cap：机数名额与 cap(t) 的合成（intersect / primary）
# ---------------------------------------------------------------------------


def test_effective_cap_intersect_only_tightens():
    """intersect（默认）：能力只能收紧；充裕时仍受名额，绝不超名额。"""
    cfg = CapabilityScaleConfig(mode="intersect")
    assert effective_cap(5, 3, cfg) == 3  # 紧张 → 收编
    assert effective_cap(5, 100, cfg) == 5  # 充裕 → 仍受名额（不超名额）
    assert effective_cap(5, None, cfg) == 5  # 能力未启用 → 名额兜底


def test_effective_cap_primary_expands_and_shrinks():
    """primary：cap(t) 为主 —— 紧张收编、充裕允许超名额（双向）。"""
    cfg = CapabilityScaleConfig(mode="primary")
    assert effective_cap(5, 3, cfg) == 3
    assert effective_cap(5, 100, cfg) == 100
    assert effective_cap(5, None, cfg) == 5


def test_effective_cap_primary_max_expand_factor_clamps():
    cfg = CapabilityScaleConfig(mode="primary", max_expand_factor=2.0)
    assert effective_cap(5, 100, cfg) == 10  # ⌈2.0×5⌉ 封顶
    assert effective_cap(5, 7, cfg) == 7  # 未触顶 → 按 cap


def test_effective_cap_rejects_unknown_mode():
    """mode 拼写/大小写错误必须显式报错，而非静默降级成 intersect。"""
    cfg = CapabilityScaleConfig(mode="Primary")
    with pytest.raises(ValueError):
        effective_cap(5, 3, cfg)


# ---------------------------------------------------------------------------
# primary 模式端到端：能力充裕时候选/选择数可超过 ⌈αK⌉ 名额
# ---------------------------------------------------------------------------


def _many_tasks_setup(n: int = 12, rng_each: float = 1e6):
    """2 机 @原点、n 个任务散布 500~610m（粗代价中位 ≈ 555）。

    名额 ⌈2.5×2⌉ = 5 < n，便于区分「被名额截住」与「按能力扩容」。
    """
    uavs = [_uav(0, rng_each), _uav(1, rng_each)]
    tasks = [_task(i, 500.0 + 10.0 * i, 0.0) for i in range(n)]
    return tasks, uavs


def test_pipeline_intersect_never_exceeds_alpha_quota():
    """默认 intersect：任务再多也只取 ⌈αK⌉ = 5（名额是硬天花板）。"""
    tasks, uavs = _many_tasks_setup()
    result = run(tasks, uavs, SelectionConfig())
    assert result.diagnostics["n_after_priority"] == 5


def test_pipeline_primary_expands_beyond_alpha_quota():
    """primary + 充裕预算：候选数扩容到全部 12 个（超过名额 5）。"""
    tasks, uavs = _many_tasks_setup()
    cfg = SelectionConfig()
    cfg.capability.mode = "primary"
    result = run(tasks, uavs, cfg)
    d = result.diagnostics
    assert d["capability_cap"] >= 12
    assert d["capability_n_cand"] >= 12
    assert d["n_after_priority"] == 12  # 名额 5 → primary 下扩容


def test_pipeline_primary_max_expand_factor_clamps_expansion():
    """primary + max_expand_factor=2.0：最多扩到 ⌈2.0×5⌉ = 10。"""
    tasks, uavs = _many_tasks_setup()
    cfg = SelectionConfig()
    cfg.capability.mode = "primary"
    cfg.capability.max_expand_factor = 2.0
    result = run(tasks, uavs, cfg)
    assert result.diagnostics["n_after_priority"] == 10


def test_pipeline_primary_still_shrinks_when_tight():
    """primary 下预算紧张仍按 cap(t) 收编（与 intersect 同向，不失控）。"""
    tasks, uavs = _many_tasks_setup(rng_each=1000.0)  # B=2000, c̃≈555 → cap=3
    cfg = SelectionConfig()
    cfg.capability.mode = "primary"
    result = run(tasks, uavs, cfg)
    d = result.diagnostics
    assert d["capability_cap"] == 3
    assert d["n_after_priority"] == 3
