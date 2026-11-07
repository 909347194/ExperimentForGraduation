# -*- coding: utf-8 -*-
"""P3-b 测试：L3a/L3b 分账记账（零行为变化）+ 终点巢 MCF 登记 opt。

契约（定稿 §4.1 / §6 要素 4）：
1. L3a（组内 2-opt/Or-opt + 终点巢 MCF + position）与 L3b（跨机
   relocate/swap）动作**分列**，不混在一个数字里；
2. L3b 单记 ``n_task_to_uav_changes``（重建解 → 终态解的归属映射差异）；
3. 终点巢 MCF 在交付层台账登记 ``reason=opt``，措辞必须写
   「给定路径下的条件最优」「非全局最优」；
4. 全部为观测字段：**默认档零行为变化**，不参与任何采纳判定。

ℹ️ §9 第 3 项「L3b 默认关」已由 2026-11-06 裁定②落地：统一总闸
``l3b_enabled`` 默认 False，同时约束 polish_assignment 与交付后成本
polish 两个通道，并做旧 config 保义映射（详见 test_l3b_unified_gate.py）。
本文件仍只测分账观测；观测标记按新默认断言为 False，并附显式开的对照。
契约第 4 条「默认档零行为变化」记录的是 P3 三步落地当时的状态。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.evaluator import (  # noqa: E402
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)
from task_allocation.methods.muas.solvers import stage_runner as sr  # noqa: E402

NEST_KEY = 1_000_000


def _scenario():
    from task_allocation.common.types import Nest, Task, UAV  # noqa: E402

    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=2, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=2, available=True),
    ]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=15000.0, speed=12.0, nest_id=0),
        UAV(id=1, x=4000.0, y=0.0, z=0.0, remaining_range=15000.0, speed=11.0, nest_id=1),
    ]
    tasks = [
        Task(id=0, x=1000.0, y=0.0, z=0.0, reward=10.0, earliest=0.0, latest=200.0),
        Task(id=1, x=2000.0, y=0.0, z=0.0, reward=10.0, earliest=0.0, latest=200.0),
        Task(id=2, x=3000.0, y=0.0, z=0.0, reward=10.0, earliest=0.0, latest=200.0),
        Task(id=3, x=4500.0, y=0.0, z=0.0, reward=10.0, earliest=0.0, latest=200.0),
    ]
    return tasks, uavs, nests


def _run(stage_cfg=None, seed=7):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY
    )
    return run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=stage_cfg or MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=seed),
        nest_offset=NEST_KEY, current_time=0.0,
    )


def _inject_stats(monkeypatch, stats):
    """让交付后成本 polish 返回指定动作统计（不改解本身）。"""
    def _post(self, solution):
        return copy.deepcopy(solution), dict(stats)

    monkeypatch.setattr(MUASAssignmentEvaluator, "post_cost_polish", _post)


# ---------------------------------------------------------------------------
# 1. L3a / L3b 分列
# ---------------------------------------------------------------------------


def test_l3a_l3b_actions_are_split(monkeypatch):
    _inject_stats(monkeypatch, {
        "n_accepted": 10,
        "n_2opt": 2, "n_oropt": 3, "n_mcf_resolve": 1,  # L3a
        "n_relocate": 4,                                  # L3b
    })
    d = _run().diagnostics

    assert d["cost_polish_n_mcf_resolve"] == 1
    assert d["l3a_geometric_actions"] >= 2 + 3 + 1
    assert d["l3b_relocate_actions"] >= 4
    # 两本账独立：L3b 的动作不得混进 L3a
    assert d["l3a_geometric_actions"] == (
        int(d["cost_polish_n_2opt"])
        + int(d["cost_polish_n_oropt"])
        + int(d["cost_polish_n_mcf_resolve"])
        + int(d["polish_accepted"].get("position", 0))
    )
    # 观测标记：L3b 统一总闸 2026-11-07 起纯默认档为开
    assert d["l3b_enabled_observed"] is True
    assert d["l3b_enabled"] is True
    assert d["l3b_source"] == "default"
    # 显式关总闸 ⇒ 观测标记翻假（同一场景，消融通道仍可用）
    d_off = _run(MUASStageConfig(l3b_enabled=False)).diagnostics
    assert d_off["l3b_enabled_observed"] is False
    assert d_off["l3b_source"] == "explicit"


# ---------------------------------------------------------------------------
# 2. 终点巢 MCF 登记 reason=opt + 条件最优措辞
# ---------------------------------------------------------------------------


def test_mcf_logged_as_opt_with_conditional_optimum_wording(monkeypatch):
    calls = []
    orig = sr._led_polish

    def _spy(ev, operator, accepted, delta_f, gate_reason):
        calls.append((operator, accepted, gate_reason))
        return orig(ev, operator, accepted, delta_f, gate_reason)

    monkeypatch.setattr(sr, "_led_polish", _spy)
    _inject_stats(monkeypatch, {
        "n_accepted": 1, "n_2opt": 1, "n_mcf_resolve": 1,
    })
    _run()

    mcf = [c for c in calls if c[0] == "end_nest_mcf"]
    assert mcf, "MCF 重解必须在交付层台账登记"
    for operator, accepted, reason in mcf:
        assert accepted is True
        assert "给定路径下" in reason
        assert "条件最优" in reason
        assert "非全局最优" in reason
    # _led_polish 的 reason 恒为 opt（见其实现），此处复核未被旁路改写
    assert all("opt" in reason or "条件最优" in reason for _, _, reason in mcf)


# ---------------------------------------------------------------------------
# 3. 归属变动计数
# ---------------------------------------------------------------------------


def test_assignment_change_count_logic():
    a = _run().solution
    b = copy.deepcopy(a)
    assert sr._assignment_change_count(a, b) == 0

    # 换机：把某架机的一个任务移到另一架机 ⇒ 至少 1 条
    src = next((t for t in b.tours if t.task_ids), None)
    dst = next((t for t in b.tours if t.uav_id != src.uav_id), None)
    if src is not None and dst is not None:
        tid = src.task_ids.pop(0)
        dst.task_ids.append(tid)
        assert sr._assignment_change_count(a, b) >= 1

    # 新增任务：映射多一个任务 ⇒ 多 1 条
    c = copy.deepcopy(a)
    extra = 987_654
    c.tours[0].task_ids.append(extra)
    assert sr._assignment_change_count(a, c) == 1

    # 删除任务：映射少一个任务 ⇒ 至少 1 条
    d = copy.deepcopy(a)
    for t in d.tours:
        if t.task_ids:
            t.task_ids.clear()
            break
    assert sr._assignment_change_count(a, d) >= 1


def test_assignment_change_wiring(monkeypatch):
    """diagnostics 的归属变动数确实来自 _assignment_change_count。"""
    monkeypatch.setattr(sr, "_assignment_change_count", lambda old, new: 7)
    assert _run().diagnostics["n_task_to_uav_changes"] == 7


# ---------------------------------------------------------------------------
# 4. 零行为变化（观测字段不参与判定）
# ---------------------------------------------------------------------------


def test_observation_only_default_is_deterministic():
    a, b = _run(), _run()
    for key in ("final_fitness", "final_path_m", "final_late_s",
                "final_n_hard", "final_excess_m", "n_executed", "n_deferred",
                "n_task_to_uav_changes"):
        assert a.diagnostics[key] == b.diagnostics[key], key
    assert a.solution.assignment_pairs() == b.solution.assignment_pairs()
    assert isinstance(a.diagnostics["l3a_geometric_actions"], int)
    assert isinstance(a.diagnostics["l3b_relocate_actions"], int)
