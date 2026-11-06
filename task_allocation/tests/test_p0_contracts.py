# -*- coding: utf-8 -*-
"""P0 契约层测试（第二阶段重构，定稿 §8 P0 / §8.1）。

契约：

1. **ConstraintSpec 判据委托等价**：``judge_*`` 与既有判定函数逐位一致
   （包一层，不改判）；
2. **另写判据收口（四处漂移点）**：视图保留调用点现语义；漂移条件被显式
   锁死（时间窗元组口径、时序两视图等价、门禁容量账、采纳判据）；
3. **DecisionLedger 纯观测**：reason=hard|opt 必填且覆盖率 100%；非法
   reason 拒绝入账但计数可见；ledger 关闭时零事件；开/关两档求解结果
   **逐位一致**（bit-exact）；
4. **dmde_solver 回退不静默**：lamarckian_reencode 抛异常 → 回退行为不变
   （child 不换），但计数 +1、台账计数 +1（原 except: pass 已改）；
5. **选择一致性四元组**：dmde_select 事件带 selection_object=S_repair 与
   inherited_object（g_raw / g_repaired_reencoded）。
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
    Nest,
    Task,
    UAV,
    UAVStatus,
    UAVTour,
)
from task_allocation.methods.muas.constraints import spec as constraint_spec  # noqa: E402
from task_allocation.methods.muas.constraints.coop_constraints import (  # noqa: E402
    check_sequence_constraint,
    check_time_window_constraint,
    task_window,
)
from task_allocation.methods.muas.constraints.nest_capacity import (  # noqa: E402
    check_nest_capacity,
)
from task_allocation.methods.muas.constraints.single_constraints import (  # noqa: E402
    check_range_constraint,
    check_time_constraint,
)
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.decision_ledger import (  # noqa: E402
    DecisionLedger,
    REASON_HARD,
    REASON_OPT,
    reset_ledger,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.dmde_solver import (  # noqa: E402
    DMDEConfig,
    DMDESolver,
    lamarckian_fallback_stats,
    reset_lamarckian_fallback_stats,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 1. ConstraintSpec 判据委托等价
# ---------------------------------------------------------------------------


def _uav(uid: int = 0, remaining: float = 1000.0) -> UAV:
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, remaining_range=remaining, speed=10.0, nest_id=0)


def test_judge_range_equals_check_range_constraint():
    u = _uav(remaining=1000.0)
    for d in (0.0, 500.0, 1000.0, 1000.0 + 1e-7, 1500.0, 1e6):
        jr = constraint_spec.judge_range(u, d)
        rv = check_range_constraint(u, d, margin=1.0)
        assert jr.amount == rv
        assert jr.violated == (rv > 0.0)


def test_judge_endurance_equals_check_time_constraint():
    u = _uav()
    u.max_time = 50.0  # 1000m / 10m/s = 100s > 50s
    for d in (0.0, 400.0, 600.0):
        jr = constraint_spec.judge_endurance(u, d)
        tv = check_time_constraint(u, d)
        assert jr.amount == tv
        assert jr.violated == (tv > 0.0)


def test_judge_time_window_equals_model():
    tasks = {
        1: Task(id=1, x=10.0, y=0.0, z=0.0, earliest=0.0, latest=100.0),
        2: Task(id=2, x=20.0, y=0.0, z=0.0, earliest=0.0, latest=50.0),
    }
    uavs = {0: _uav()}
    tours = [UAVTour(uav_id=0, task_ids=[1, 2], end_nest_id=None)]
    jr = constraint_spec.judge_time_window(tours, tasks, uavs)
    pen, tags, late = check_time_window_constraint(tours, tasks, uavs)
    assert jr.amount == pen
    assert list(jr.tags) == tags
    assert jr.raw[2] == late


def test_judge_sequence_equals_model():
    tasks = {
        1: Task(id=1, x=0.0, y=0.0, z=0.0, sequence_group=1, sequence_order=0),
        2: Task(id=2, x=0.0, y=0.0, z=0.0, sequence_group=1, sequence_order=1),
    }
    tours = [UAVTour(uav_id=0, task_ids=[2, 1], end_nest_id=None)]
    jr = constraint_spec.judge_sequence(tours, tasks)
    pen, tags = check_sequence_constraint(tours, tasks)
    assert jr.amount == pen
    assert list(jr.tags) == tags
    assert jr.violated


def test_judge_nest_capacity_equals_model():
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=1, available=True)]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, nest_id=0, status=UAVStatus.IDLE),
        UAV(id=1, x=0.0, y=0.0, z=0.0, nest_id=0, status=UAVStatus.IDLE),
    ]
    assignments = {0: 0, 1: 0}  # 2 架都要落 0 号巢（容量 1）→ 超容
    jr = constraint_spec.judge_nest_capacity(uavs, nests, assignments)
    rep = check_nest_capacity(uavs, nests, assignments)
    assert jr.amount == rep.penalty
    assert jr.violated == (not rep.ok)
    assert list(jr.tags) == [
        f"nest_capacity_{nid}"
        for nid in rep.violations
        if rep.per_nest[nid]["overflow"] > 0
    ]


# ---------------------------------------------------------------------------
# 2. 四处另写判据收口（漂移点显式化）
# ---------------------------------------------------------------------------


def test_window_views_agree_without_time_window_tuple():
    t = Task(id=1, x=0.0, y=0.0, z=0.0, earliest=0.0, latest=123.0)
    assert constraint_spec.window_deadline_decode(t) == 123.0
    assert constraint_spec.window_deadline_model(t) == 123.0


def test_window_views_drift_only_with_time_window_tuple():
    # time_window 元组优先（model 口径），直读 latest（decode 口径）→ 漂移
    t = Task(id=1, x=0.0, y=0.0, z=0.0, earliest=0.0, latest=123.0)
    t.time_window = (0.0, 456.0)  # type: ignore[attr-defined]
    assert constraint_spec.window_deadline_model(t) == 456.0
    assert constraint_spec.window_deadline_decode(t) == 123.0
    assert constraint_spec.window_deadline_model(t) == task_window(t)[1]


def test_sequence_views_verdict_equivalent():
    # 同链乱序：decode（存在下降）与 model（逆序对 > 0）判罚等价
    cases = [
        [(1, 0.0), (1, 1.0)],
        [(1, 1.0), (1, 0.0)],
        [(1, 0.0), (1, 1.0), (1, 0.5)],
        [(1, 2.0), (1, 1.0), (1, 0.0)],
        [(1, 0.0), (2, 5.0), (1, 1.0)],
    ]
    for pairs in cases:
        dv = constraint_spec.sequence_decode_violated(pairs)
        mv = constraint_spec.sequence_model_inversions(pairs)
        assert dv == (mv > 0), f"两视图判罚应等价：{pairs} dv={dv} mv={mv}"


def test_range_gate_ok_equals_judge_range_verdict():
    u = _uav(remaining=1000.0)
    for d in (999.0, 1000.0, 1000.0 + 1e-7, 1001.0):
        assert constraint_spec.range_gate_ok(d, u) == (
            not constraint_spec.judge_range(u, d).violated
        ), f"两视图通过/违约判定应等价：d={d}"


def test_nest_capacity_gate_slot_ok_matches_gate_semantics():
    # 门禁口径容量账：起飞释放原巢名额、落地占终点巢名额
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=1, available=True)]
    uavs = [UAV(id=0, x=0.0, y=0.0, z=0.0, nest_id=0, status=UAVStatus.IDLE)]
    problem = SelectiveMUASProblem.from_entities(
        [], uavs, nests=nests, config=MUASProblemConfig()
    )
    loads = {0: 1}
    # 落回原巢：1 - 1 = 0 净增 → 1 <= 1 可行
    assert constraint_spec.nest_capacity_gate_slot_ok(problem, loads, 0, 0)
    # 落到同容量他巢（无他巢）→ 缺失巢判 False
    assert not constraint_spec.nest_capacity_gate_slot_ok(problem, loads, 99, 0)


def test_acceptance_views_match_legacy_expressions():
    old_exec, new_exec = {1, 2}, {1, 2}
    # cost_polish 现语义：航程门（米）裁决
    assert constraint_spec.accept_cost_polish_view(
        old_exec, new_exec, 0, 0, 0.0, 0.0, 90.0, 100.0, 1.0
    )
    # 航程未严格下降 → 拒
    assert not constraint_spec.accept_cost_polish_view(
        old_exec, new_exec, 0, 0, 0.0, 0.0, 100.0, 100.0, 1.0
    )
    # f 视图（P3 目标判据）：同数据下 f 必然同向（λ>0 时路径降 ⇒ f 降）
    assert constraint_spec.accept_f_view(9.0, 10.0, 1e-6, cand_n_hard=0, cur_n_hard=0)
    assert not constraint_spec.accept_f_view(10.0, 10.0, 1e-6, cand_n_hard=0, cur_n_hard=0)
    # 约束向量退化 → 一律拒
    assert not constraint_spec.accept_f_view(
        5.0, 10.0, 1e-6, cand_n_hard=1, cur_n_hard=0
    )


# ---------------------------------------------------------------------------
# 3. DecisionLedger 纯观测
# ---------------------------------------------------------------------------


def test_ledger_reason_mandatory_and_coverage():
    lg = DecisionLedger(enabled=True)
    lg.record(layer="decode", operator="x", constraint_id="c", action="a",
              object="o", reason=REASON_HARD)
    lg.record(layer="decode", operator="y", constraint_id="c", action="a",
              object="o", reason=REASON_OPT)
    snap = lg.snapshot()
    assert snap["n_total"] == 2
    assert snap["reason_coverage"] == 1.0
    assert snap["by_reason"] == {REASON_HARD: 1, REASON_OPT: 1}
    # 非法 reason：拒绝入账 + 计数可见
    lg.record(layer="decode", operator="z", constraint_id="c", action="a",
              object="o", reason="banana")
    snap = lg.snapshot()
    assert snap["n_total"] == 2
    assert snap["n_reason_missing"] == 1
    assert snap["reason_coverage"] == 1.0


def test_ledger_disabled_records_nothing():
    lg = DecisionLedger(enabled=False)
    assert lg.record(layer="decode", operator="x", constraint_id="c",
                     action="a", object="o", reason=REASON_HARD) is None
    assert lg.snapshot()["n_total"] == 0


def test_ledger_events_bounded_counters_full():
    lg = DecisionLedger(enabled=True, max_events=3)
    for i in range(10):
        lg.record(layer="decode", operator="x", constraint_id="c",
                  action="a", object=f"o{i}", reason=REASON_HARD)
    snap = lg.snapshot()
    assert snap["n_total"] == 10          # 计数恒全量
    assert snap["n_events_kept"] == 3     # 事件流有界
    assert [e.object for e in lg.events] == ["o7", "o8", "o9"]  # 只丢最旧


# ---------------------------------------------------------------------------
# 4/5. dmde_solver：回退不静默 + 选择一致性四元组
# ---------------------------------------------------------------------------


class _LamarckBoomEval:
    """lamarckian_reencode 必抛异常的评估器（驱动回退计数）。"""

    def __init__(self) -> None:
        self.ledger = DecisionLedger(enabled=True)
        self.calls = 0

    def evaluate(self, assignment, cost_matrix=None, n_uavs=None):
        self.calls += 1
        fit = float(sum((a + 1) * (b + 1) for a, b in assignment)) if assignment else 1e12
        return type("R", (), {"fitness": fit, "solution": object()})()

    def lamarckian_reencode(self, child, solution, cost_matrix, n_uavs, n_targets):
        raise RuntimeError("boom")


def test_lamarckian_fallback_counted_not_silent():
    reset_lamarckian_fallback_stats()
    ev = _LamarckBoomEval()
    before = lamarckian_fallback_stats()["count"]
    cfg = DMDEConfig(pop_size=6, max_generations=4, seed=1, lamarckian=True)
    DMDESolver(config=cfg).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev
    )
    after = lamarckian_fallback_stats()["count"]
    assert after > before, "lamarckian_reencode 异常应计数（原 except: pass 已改）"
    assert ev.ledger.family_counters.get("dmde:lamarck_fallback", 0) == after - before


def test_dmde_select_events_carry_consistency_tuple():
    reset_lamarckian_fallback_stats()

    class _Eval:
        def __init__(self) -> None:
            self.ledger = DecisionLedger(enabled=True)

        def evaluate(self, assignment, cost_matrix=None, n_uavs=None):
            fit = float(sum((a + 1) * (b + 1) for a, b in assignment)) if assignment else 1e12
            return type("R", (), {"fitness": fit, "solution": None})()

    ev = _Eval()
    cfg = DMDEConfig(pop_size=6, max_generations=3, seed=2)
    DMDESolver(config=cfg).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev
    )
    sel = ev.ledger.events_by("dmde_select")
    assert sel, "选择事件应入台账"
    for e in sel:
        assert e.reason == REASON_OPT
        assert e.selection_object == "S_repair"
        assert e.inherited_object in ("g_raw", "g_repaired_reencoded", "g_parent")
        assert e.gate_reason is not None
    snap = ev.ledger.snapshot()
    assert snap["reason_coverage"] == 1.0
    assert snap["n_accepted"] + snap["n_rejected"] == len(sel)


def _tiny_cm(k: int, m: int):
    import numpy as np

    return np.random.default_rng(0).random((k + m, m)) + 0.1


# ---------------------------------------------------------------------------
# 3b. 端到端：台账开/关逐位一致 + reason 覆盖率 100%
# ---------------------------------------------------------------------------


def _mini_scenario():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=3, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=3, available=True),
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
        Task(id=4, x=1200.0, y=900.0, z=0.0, reward=5.0, earliest=0.0, latest=1900.0),
    ]
    return tasks, uavs, nests


def _run_mini(ledger_enabled: bool):
    tasks, uavs, nests = _mini_scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    stage = run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(ledger_enabled=ledger_enabled),
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=7),
        nest_offset=NEST_KEY, current_time=0.0,
    )
    sol = stage.solution
    return (
        sorted(sol.executed_task_ids),
        round(float(sol.f1_reward), 9),
        round(float(sol.f2_cost), 6),
        [(t.uav_id, tuple(t.task_ids), t.end_nest_id, round(float(t.est_cost), 6))
         for t in sol.tours],
        round(float(stage.diagnostics["final_fitness"]), 9),
        round(float(stage.diagnostics["best_fitness"]), 9),
        int(stage.n_evals),
        [round(float(x), 9) for x in stage.cost_history],
    )


def test_ledger_on_off_bit_identical():
    assert _run_mini(ledger_enabled=True) == _run_mini(ledger_enabled=False)


def test_build_solution_ledger_reason_coverage_full():
    from task_allocation.methods.muas.solvers.evaluator import MUASAssignmentEvaluator

    tasks, uavs, nests = _mini_scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests=nests, pairwise_cost=cb.provider.pairwise, config=MUASProblemConfig()
    )
    ev = MUASAssignmentEvaluator(
        problem=problem,
        participating=[uavs[0], uavs[1]],
        tasks=tasks,
        nests=nests,
        fleet_uavs=uavs,
        config=MUASStageConfig(ledger_enabled=True),
    )
    assignment = ((0, 0), (0, 1), (0, 2), (1, 3), (1, 4))
    ev.build_solution(assignment)
    events = ev.ledger.events
    assert events, "build_solution 各算子动作应入台账"
    snap = ev.ledger.snapshot()
    assert snap["reason_coverage"] == 1.0
    assert snap["n_reason_missing"] == 0
    assert snap["by_reason"].keys() <= {REASON_HARD, REASON_OPT}
    # 关键算子均有记录（含 reason）
    ops = {e.operator for e in events}
    assert "best_order" in ops or "choose_end_nests" in ops
    for e in events:
        assert e.reason in (REASON_HARD, REASON_OPT)
        assert e.gate_reason, f"{e.operator}.{e.action} 应带闸门原因"
    # 历史计数器并入台账
    counters = snap["counters"]
    assert "order_winners" in counters and "writeback" in counters


# ---------------------------------------------------------------------------
# 注册表完整性
# ---------------------------------------------------------------------------


def test_registry_specs_complete():
    ids = set(constraint_spec.REGISTRY)
    assert {"unique_tasks", "range", "endurance", "time_window",
            "sequence", "nest_capacity"} <= ids
    for sid, s in constraint_spec.REGISTRY.items():
        assert s.constraint_id == sid
        assert s.hardness in ("hard", "soft")
        assert s.layer in ("encode", "layer1", "layer2", "layer3")
        assert callable(s.judge)
        assert s.disposition
