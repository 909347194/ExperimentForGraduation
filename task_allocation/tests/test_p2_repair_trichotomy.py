# -*- coding: utf-8 -*-
"""P2-b 测试：修复三分流 + 迭代规则 + n_repair_failed。

契约（定稿 §3.1 目标规则 / §3.2 三分流 / §11.3 P2-b）：

1. **max_passes 进 config**：``repair_max_passes``（默认 6）/
   ``constructive_max_passes``（默认 3），非法取值拒绝；
2. **每轮快照**：build_solution 台账有 ``repair_pass`` 事件
   （reason=hard，from_state 为违反向量），事件数 ≤ max_passes；
3. **三分流**：evaluate 后 ``solution.meta["repair_status"]`` ∈
   {REPAIRED, REPAIR_FAILED}；REPAIR_FAILED（终态 n_hard>0）时
   ``evaluator.n_repair_failed`` 计数、台账 tick；
4. **交付闸**：终态 REPAIRED ⇒ diagnostics 全绿；终态硬违反 ⇒
   ``delivery_refused=True``、solution.meta 标记、不静默交付；
5. **默认零变化**：正常路径（终态 n_hard=0）行为与旧口径一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from task_allocation.common.types import Nest, Task, UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.evaluator import (  # noqa: E402
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000


def _scenario():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=2, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=2, available=True),
    ]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=15000.0, speed=12.0, nest_id=0),
        UAV(id=1, x=4000.0, y=0.0, z=0.0, remaining_range=15000.0, speed=11.0, nest_id=1),
    ]
    tasks = [
        Task(id=0, x=800.0, y=200.0, z=0.0, reward=5.0, earliest=0.0, latest=1500.0),
        Task(id=1, x=1600.0, y=-300.0, z=0.0, reward=4.0, earliest=0.0, latest=2000.0),
        Task(id=2, x=2400.0, y=400.0, z=0.0, reward=6.0, earliest=0.0, latest=1800.0),
        Task(id=3, x=3100.0, y=-200.0, z=0.0, reward=3.0, earliest=0.0, latest=2200.0),
    ]
    return tasks, uavs, nests


def _make_ev(**stage_kwargs):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests=nests, pairwise_cost=cb.provider.pairwise, config=MUASProblemConfig()
    )
    ev = MUASAssignmentEvaluator(
        problem=problem,
        participating=uavs,
        tasks=tasks,
        nests=nests,
        fleet_uavs=uavs,
        config=MUASStageConfig(**stage_kwargs),
    )
    return ev, problem, cb


# ---------------------------------------------------------------------------
# 1. config 迭代规则
# ---------------------------------------------------------------------------


def test_max_passes_config():
    assert MUASStageConfig().repair_max_passes == 6
    assert MUASStageConfig().constructive_max_passes == 3
    assert MUASStageConfig(repair_max_passes=2).repair_max_passes == 2
    with pytest.raises(ValueError):
        MUASStageConfig(repair_max_passes=0)
    with pytest.raises(ValueError):
        MUASStageConfig(constructive_max_passes=0)


def test_round_snapshots_in_ledger():
    ev, _, _ = _make_ev()
    ev.build_solution(((0, 0), (0, 1), (1, 2), (1, 3)))
    passes = [
        e for e in ev.ledger.events
        if e.operator == "repair_pass" and e.action == "pass_snapshot"
    ]
    assert passes, "每轮应有 violation_vector 快照进台账"
    for e in passes:
        assert e.reason == "hard"
        assert e.from_state.startswith("(") and e.from_state.endswith(")")
    assert len(passes) <= 6  # ≤ repair_max_passes
    # 轮数上限生效：收紧到 1 仍能出解且快照 ≤ 1
    ev1, _, _ = _make_ev(repair_max_passes=1)
    sol1 = ev1.build_solution(((0, 0), (0, 1), (1, 2), (1, 3)))
    n_snap1 = len([
        e for e in ev1.ledger.events
        if e.operator == "repair_pass" and e.action == "pass_snapshot"
    ])
    assert n_snap1 <= 1
    assert sol1 is not None


def test_fixpoint_nonzero_ticks_diagnostic(monkeypatch):
    """快照重复但向量非零 ⇒ 诊断 tick（不静默轮转；权威判定在终态）。"""
    ev, _, _ = _make_ev()
    monkeypatch.setattr(
        ev, "_round_violation_vector", lambda routes, end_nests: (1, 0, 0)
    )
    ev.build_solution(((0, 0), (0, 1), (1, 2), (1, 3)))
    snap = ev.ledger.snapshot()
    assert snap["family_counters"].get("decode:fixpoint_nonzero", 0) >= 1


# ---------------------------------------------------------------------------
# 2. 三分流（evaluate 标记 + 计数）
# ---------------------------------------------------------------------------


def test_repaired_status_on_feasible():
    ev, _, _ = _make_ev()
    res = ev.evaluate(((0, 0), (0, 1), (1, 2), (1, 3)))
    assert res.solution.meta["repair_status"] == "REPAIRED"
    assert ev.last_repair_status == "REPAIRED"
    assert ev.n_repair_failed == 0


def test_repair_failed_status_and_counter(monkeypatch):
    """终态 n_hard>0 ⇒ REPAIR_FAILED：meta 标记 + 计数 + 台账 tick。

    干净场地上修复链几乎总把违反消成「可行或暂缓」，故用评价端注入
    （tamper problem.evaluate 的 feasibility 元数据）测三分流接线本身。
    """
    ev, problem, _ = _make_ev()
    _orig_evaluate = problem.evaluate

    def _tampered_evaluate(solution):
        _orig_evaluate(solution)
        feas = solution.meta.setdefault("feasibility", {})
        feas["n_hard"] = 2  # 模拟终态硬违反
        return solution

    monkeypatch.setattr(problem, "evaluate", _tampered_evaluate)
    res = ev.evaluate(((0, 0), (0, 1), (1, 2), (1, 3)))
    assert res.solution.meta["repair_status"] == "REPAIR_FAILED"
    assert ev.last_repair_status == "REPAIR_FAILED"
    assert ev.n_repair_failed == 1
    assert ev.ledger.snapshot()["family_counters"].get("decode:repair_failed", 0) == 1
    # 第二个不同 assignment：计数继续累加
    ev.evaluate(((0, 0), (0, 1), (0, 2), (0, 3)))
    assert ev.n_repair_failed == 2


# ---------------------------------------------------------------------------
# 3. 交付闸（stage 级）
# ---------------------------------------------------------------------------


def _run(stage_cfg, seed=7):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    stage = run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=stage_cfg,
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=seed),
        nest_offset=NEST_KEY, current_time=0.0,
    )
    return stage


def test_delivery_gate_normal_path():
    """正常路径（终态 n_hard=0）：REPAIRED、不拒交付。"""
    stage = _run(MUASStageConfig())
    assert stage.diagnostics["repair_status"] == "REPAIRED"
    assert stage.diagnostics["delivery_refused"] is False
    assert stage.solution.meta.get("repair_status", "REPAIRED") == "REPAIRED"


def test_delivery_gate_refuses_repair_failed():
    """终态硬违反 ⇒ delivery_refused=True、solution 标记、计数入诊断。

    注入方式：shim 掉 stage_runner 模块的 SelectiveMUASProblem.from_entities，
    使 problem.evaluate 恒报 n_hard>0——模拟「搜索无可行交付解」的退化情形
    （big-M 下正常不会走到）。
    """
    import task_allocation.methods.muas.solvers.stage_runner as _sr

    orig_bound = SelectiveMUASProblem.from_entities  # classmethod 绑定态或静态函数

    class _Shim:
        @staticmethod
        def from_entities(*args, **kwargs):
            prob = orig_bound(*args, **kwargs)
            _orig_eval = prob.evaluate

            def _eval(sol):
                _orig_eval(sol)
                feas = sol.meta.setdefault("feasibility", {})
                feas["n_hard"] = 3
                feas["range_excess"] = 100.0
                return sol

            prob.evaluate = _eval  # type: ignore[method-assign]
            return prob

    orig_ref = _sr.SelectiveMUASProblem
    _sr.SelectiveMUASProblem = _Shim  # type: ignore[assignment]
    try:
        stage = _run(MUASStageConfig())
    finally:
        _sr.SelectiveMUASProblem = orig_ref  # type: ignore[assignment]

    assert stage.diagnostics["repair_status"] == "REPAIR_FAILED"
    assert stage.diagnostics["delivery_refused"] is True
    assert stage.solution.meta.get("repair_status") == "REPAIR_FAILED"
    assert stage.diagnostics["n_repair_failed"] >= 1
