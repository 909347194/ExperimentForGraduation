# -*- coding: utf-8 -*-
"""P3-c 测试：独立事后评价器（定稿 §4.3 前三条门槛）。

契约：
1. ``reconstruct_solution`` 丢弃全部派生 meta、从 tours 重算执行集；
2. 正常场景重建重评与 ``diagnostics`` **逐位一致**，``report.passed``；
3. 门槛失败路径：eliminating>0 / 自报数字被篡改 / 完成率<1 /
   严格档 coverage_gate / gate_mode / coverage_audit 都能被抓出；
4. 模块不接触搜索链缓存（只调用 problem 公开评价入口）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.independent_audit import (  # noqa: E402
    assert_audit_passed,
    independent_audit,
    reconstruct_solution,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

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


def _run(monkeypatch, stage_cfg=None):
    """跑一个周期，并捕获 stage_runner 内部实际使用的 problem 实例。"""
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY
    )
    captured = []
    # from_entities 是 classmethod：保存**已绑定类**的引用，调用时不再传 cls。
    orig = SelectiveMUASProblem.from_entities

    def _from_entities(*args, **kwargs):
        p = orig(*args, **kwargs)
        captured.append(p)
        return p

    monkeypatch.setattr(
        SelectiveMUASProblem, "from_entities", staticmethod(_from_entities)
    )
    stage = run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=stage_cfg or MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=7),
        nest_offset=NEST_KEY, current_time=0.0,
    )
    assert captured, "未捕获到 problem 实例"
    return stage, captured[-1]


# ---------------------------------------------------------------------------
# 1. 重建
# ---------------------------------------------------------------------------


def test_reconstruct_drops_derived_meta_and_recomputes_executed(monkeypatch):
    stage, _ = _run(monkeypatch)
    sol = stage.solution
    assert sol.meta, "正常交付解应带派生 meta（否则本测试无意义）"

    rebuilt = reconstruct_solution(sol)
    assert rebuilt.meta == {}
    assert rebuilt.executed_task_ids == sorted(
        {int(tid) for t in sol.tours for tid in t.task_ids}
    )
    assert rebuilt.candidate_task_ids == sol.candidate_task_ids
    assert [t.task_ids for t in rebuilt.tours] == [t.task_ids for t in sol.tours]
    assert [t.uav_id for t in rebuilt.tours] == [t.uav_id for t in sol.tours]
    # 评价派生量不复制
    assert all(t.meta == {} for t in rebuilt.tours)
    assert rebuilt.f1_reward == 0.0 and rebuilt.f2_cost == 0.0


# ---------------------------------------------------------------------------
# 2. 正常路径：三条门槛全过
# ---------------------------------------------------------------------------


def test_independent_audit_passes_normal_path(monkeypatch):
    stage, problem = _run(monkeypatch)
    report = independent_audit(
        stage.solution, problem,
        reported=stage.diagnostics,
        stage_config=MUASStageConfig(),
    )
    assert report.scope == "cycle"
    assert report.gate_mode is True
    assert report.coverage_audit is True
    assert report.coverage_gate is False      # 生产默认，严格档才强制
    assert report.n_hard == 0
    assert report.completion_rate == 1.0
    assert report.on_time_rate <= 1.0
    assert report.consistency, "给定 reported 时必须做逐位一致性检查"
    assert all(v["equal"] is True for v in report.consistency.values()), report.consistency
    assert report.passed, report.failures
    assert_audit_passed(report)               # 不抛异常


# ---------------------------------------------------------------------------
# 3. 门槛失败路径
# ---------------------------------------------------------------------------


def test_fail_on_eliminating(monkeypatch):
    stage, problem = _run(monkeypatch)
    report = independent_audit(stage.solution, problem, n_eliminating=2)
    assert not report.passed
    assert any("eliminating" in f for f in report.failures)
    with pytest.raises(AssertionError, match="独立交付验收未通过"):
        assert_audit_passed(report)


def test_fail_on_reported_mismatch(monkeypatch):
    stage, problem = _run(monkeypatch)
    bad = dict(stage.diagnostics)
    bad["final_path_m"] = float(bad["final_path_m"]) + 1.0
    report = independent_audit(stage.solution, problem, reported=bad)
    assert not report.passed
    assert report.consistency["final_path_m"]["equal"] is False
    assert any("final_path_m" in f for f in report.failures)


def test_fail_on_incomplete_cycle(monkeypatch):
    stage, problem = _run(monkeypatch)
    arrived = list(stage.solution.candidate_task_ids) + [999_999]
    report = independent_audit(stage.solution, problem, arrived_ids=arrived)
    assert report.scope == "provided"
    assert report.completion_rate < 1.0
    assert any("完成率" in f for f in report.failures)


def test_fail_on_strict_coverage_gate(monkeypatch):
    stage, problem = _run(monkeypatch)
    report = independent_audit(
        stage.solution, problem,
        stage_config=MUASStageConfig(),
        require_coverage_gate=True,
    )
    assert not report.passed
    assert any("coverage_gate" in f for f in report.failures)


def test_arrived_identity_and_unreachable_excluded(monkeypatch):
    stage, problem = _run(monkeypatch)
    arrived = list(stage.solution.candidate_task_ids)
    # 把一个已完成任务标为不可达：分母与 done 同步减 1，完成率仍 100%
    done = set(stage.solution.executed_task_ids)
    unreach = [next(iter(done))] if done else []
    report = independent_audit(
        stage.solution, problem,
        arrived_ids=arrived, unreachable_ids=unreach,
    )
    assert report.n_arrived == report.n_done + report.n_unreachable + report.n_remain
    assert report.completion_rate == 1.0
    assert report.n_unreachable == len(unreach)
