# -*- coding: utf-8 -*-
"""P1 测试：CompareSpec 统一比较算子 + projection_only 剥解码期目标改写。

契约（定稿 §8 P1 / §6 要素 1、2）：

1. **CompareSpec 语义**：penalty = 现行硬贪婪；deb 三规则；epsilon 容忍档
   比较（占位调度）；lagaros 乘积式 F_sel（Eq. 9b/8a–d 近似口径显式）；
   全部只作用于内部选择，不改 f；
2. **projection_only**：解码层台账 `reason=opt` 动作数 = 0（目标改写剥净）；
   `_best_order` 不再产 NN 候选 / 航程裁决；终点机巢走最近合法巢投影
   （容量账与 check_nest_capacity 同口径）；确定性（两次解码逐位一致）；
3. **默认档 bit-exact**：projection_only=False + compare_mode=penalty 下
   与 P0 基线逐位一致（probe 外，本文件以小型签名对照兜底）；
4. **开关门控**：compare_mode/epsilon0/lagaros_variant 非法取值拒绝。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from task_allocation.common.types import (  # noqa: E402
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
from task_allocation.methods.muas.compare_spec import (  # noqa: E402
    CandidateFeatures,
    CompareSpec,
    extract_features,
    make_compare_spec,
)
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.decision_ledger import (  # noqa: E402
    REASON_OPT,
    DecisionLedger,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.dmde_solver import (  # noqa: E402
    DMDEConfig,
    DMDESolver,
)
from task_allocation.methods.muas.solvers.evaluator import (  # noqa: E402
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 1. CompareSpec 语义
# ---------------------------------------------------------------------------


def _feat(f, n_hard=0, viol=0.0, n_v=0):
    return CandidateFeatures(fitness=f, n_hard=n_hard, violation=viol, n_violations=n_v)


def test_penalty_mode_is_hard_greedy():
    s = CompareSpec(mode="penalty")
    assert s.should_replace(_feat(1.0), _feat(2.0))
    assert not s.should_replace(_feat(2.0), _feat(2.0))  # 严格小于
    assert not s.should_replace(_feat(3.0), _feat(2.0))


def test_deb_three_rules():
    s = CompareSpec(mode="deb")
    # 规则 1：可行优于不可行（即使 f 更差）
    assert s.should_replace(_feat(100.0, n_hard=0), _feat(1.0, n_hard=1, viol=5.0))
    assert not s.should_replace(_feat(1.0, n_hard=1, viol=5.0), _feat(100.0, n_hard=0))
    # 规则 2：两可行比 f
    assert s.should_replace(_feat(1.0), _feat(2.0))
    # 规则 3：两不可行比违反量（同量比条数、再比 f）
    assert s.should_replace(_feat(9.0, n_hard=1, viol=1.0), _feat(1.0, n_hard=1, viol=2.0))
    assert s.should_replace(_feat(9.0, n_hard=1, viol=1.0), _feat(1.0, n_hard=2, viol=1.0))
    assert s.should_replace(_feat(1.0, n_hard=1, viol=1.0), _feat(2.0, n_hard=1, viol=1.0))


def test_epsilon_eq12_state_machine():
    """P2-e：ε 调度实装 Eq.12（Fan 2018）状态机。"""
    s = CompareSpec(mode="epsilon")
    s.set_epsilon_max_fes(1000)  # TC = 0.4·1000 = 400
    # 未 reset：epsilon_at 返回 0（与占位兼容，不崩）
    assert s.epsilon_at(0) == 0.0
    # ε(0) = ϕ_θ
    s.reset_epsilon(10.0)
    assert s.epsilon_at() == 10.0
    # FEs=100 (< TC), rk=0 (< α): 收紧 ε = 10·(1 − 100/400)^5
    v = s.tick_epsilon(100, 0.0, 5.0)
    assert v == pytest.approx(10.0 * (0.75 ** 5))
    # FEs=300 (< TC), rk=0.8 (≥ α): 放松到 (1+τ)·ϕ_max = ϕ_max
    v2 = s.tick_epsilon(300, 0.8, 7.0)
    assert v2 == pytest.approx(7.0)
    # FEs=500 (≥ TC=400): 归 0
    v3 = s.tick_epsilon(500, 0.0, 9.0)
    assert v3 == 0.0
    assert s.epsilon_at() == 0.0


def test_epsilon_should_replace_respects_state():
    """epsilon 模式：should_replace 用当前 ε 状态判定容忍档。"""
    s = CompareSpec(mode="epsilon")
    s.set_epsilon_max_fes(10000)
    s.reset_epsilon(3.0)  # ε = 3
    # 两者违反量均 ≤ ε ⇒ 视为可行，按 f 比较（cand 更优 ⇒ 替换）
    assert s.should_replace(_feat(1.0, n_hard=1, viol=2.0), _feat(2.0, n_hard=1, viol=2.0), gen=0)
    # 候选违反量 5 > ε=3 ⇒ 按违反量比较（候选违反更大 ⇒ 不替换）
    assert not s.should_replace(_feat(1.0, n_hard=1, viol=5.0), _feat(2.0, n_hard=1, viol=2.0), gen=0)


def test_lagaros_fsel_product_rescaling():
    s = CompareSpec(mode="lagaros")  # Eq. 8a：p = 违反量
    s.set_best_feasible(10.0)
    # 可行：F_sel = f
    assert s.f_sel(_feat(3.0)) == 3.0
    # 不可行：F_sel = max(f_best_feasible, f) × p_violation
    assert s.f_sel(_feat(3.0, n_hard=1, viol=2.0)) == pytest.approx(10.0 * 2.0)
    # 候选 F_sel 更低则替换（f 更差但违反量小得多）
    assert s.should_replace(
        _feat(15.0, n_hard=1, viol=0.5), _feat(3.0, n_hard=1, viol=2.0)
    )
    assert not s.should_replace(
        _feat(50.0, n_hard=1, viol=1.0), _feat(3.0, n_hard=1, viol=2.0)
    )
    # 8b 变体含条数项（n_constraints 显式）
    s8b = CompareSpec(mode="lagaros", lagaros_variant="8b", n_constraints=10)
    assert s8b.p_violation(_feat(0.0, n_hard=1, viol=2.0, n_v=5)) == pytest.approx(
        2.0 * (1.0 + 5.0 / 10.0)
    )
    # 8a 不含条数项（勘误 4：原文 8a 不含违反条数）
    assert s.p_violation(_feat(0.0, n_hard=1, viol=2.0, n_v=5)) == pytest.approx(2.0)


def test_extract_features_from_meta():
    sol = AllocationSolution(tours=[], candidate_task_ids=[], executed_task_ids=[])
    sol.meta = {
        "feasibility": {
            "n_hard": 2,
            "hard_violation": 7.5,
            "violations": ["range_uav_0", "time_uav_1"],
        }
    }
    f = extract_features(42.0, sol)
    assert (f.fitness, f.n_hard, f.violation, f.n_violations) == (42.0, 2, 7.5, 2)
    # solution=None：退化为纯 f 特征
    g = extract_features(42.0, None)
    assert (g.n_hard, g.violation) == (0, 0.0)


def test_compare_spec_validation():
    with pytest.raises(ValueError):
        CompareSpec(mode="banana")
    with pytest.raises(ValueError):
        CompareSpec(mode="epsilon", epsilon0=-1.0)
    with pytest.raises(ValueError):
        CompareSpec(lagaros_variant="8e")


# ---------------------------------------------------------------------------
# 2. projection_only：解码层 opt 动作归零 + 确定性 + 容量投影
# ---------------------------------------------------------------------------


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


def _make_evaluator(**stage_kwargs):
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
    return ev, problem


def test_projection_only_zero_opt_actions():
    ev, _ = _make_evaluator(projection_only=True)
    ev.build_solution(((0, 0), (0, 1), (1, 2), (1, 3)))
    decode_opt = [
        e for e in ev.ledger.events
        if e.layer == "decode" and e.reason == REASON_OPT
    ]
    assert not decode_opt, f"projection_only 下解码层不应有 opt 动作：{decode_opt}"


def test_projection_only_deterministic():
    ev, _ = _make_evaluator(projection_only=True)
    s1 = ev.build_solution(((0, 0), (0, 1), (1, 2), (1, 3)))
    s2 = ev.build_solution(((0, 0), (0, 1), (1, 2), (1, 3)))
    sig1 = [(t.uav_id, tuple(t.task_ids), t.end_nest_id) for t in s1.tours]
    sig2 = [(t.uav_id, tuple(t.task_ids), t.end_nest_id) for t in s2.tours]
    assert sig1 == sig2


def test_projection_nest_capacity_respected():
    ev, problem = _make_evaluator(projection_only=True)
    sol = ev.build_solution(((0, 0), (0, 1), (1, 2), (1, 3)))
    # 最近合法巢投影：容量账不超卖（本场景 2+2 容量、2 架有航次 → 必可行）
    ends = {t.uav_id: t.end_nest_id for t in sol.tours if t.task_ids}
    assert ends, "应有航次"
    from task_allocation.methods.muas.constraints.nest_capacity import (
        check_nest_capacity,
    )

    rep = check_nest_capacity(problem.fleet_uavs or problem.uavs, problem.nests, ends)
    assert rep.ok, f"投影后不得超容：{rep.as_dict()}"


def test_projection_only_skips_consolidate_and_nn():
    # 对照：同 assignment 下 projection_only 与默认链的台账差异
    ev_off, _ = _make_evaluator()
    ev_on, _ = _make_evaluator(projection_only=True)
    assign = ((0, 0), (0, 1), (1, 2), (1, 3))
    ev_off.build_solution(assign)
    ev_on.build_solution(assign)
    ops_off = {e.operator for e in ev_off.ledger.events}
    ops_on = {e.operator for e in ev_on.ledger.events}
    assert "consolidate" not in ops_on
    for e in ev_on.ledger.events_by("best_order"):
        assert "winner=nn" not in str(e.to_state)
        assert e.reason != REASON_OPT


# ---------------------------------------------------------------------------
# 3. 默认档 bit-exact 兜底 + 端到端开关
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
    sol = stage.solution
    return (
        sorted(sol.executed_task_ids),
        round(float(sol.f2_cost), 6),
        [(t.uav_id, tuple(t.task_ids), t.end_nest_id) for t in sol.tours],
        round(float(stage.diagnostics["final_fitness"]), 9),
    )


def test_default_arms_bit_identical():
    # penalty + projection 关 = 现行链（两次独立运行逐位一致）
    assert _run(MUASStageConfig()) == _run(MUASStageConfig())


def test_compare_arms_run_and_are_deterministic():
    for mode, extra in (
        ("deb", {}),
        ("epsilon", {"epsilon0": 100.0, "epsilon_tc": 20}),
        ("lagaros", {"lagaros_variant": "8b", "n_constraints": 8}),
    ):
        cfg = MUASStageConfig(compare_mode=mode, **extra)
        assert _run(cfg) == _run(cfg), f"{mode} 案应确定性"


def test_projection_arm_runs():
    cfg = MUASStageConfig(projection_only=True, compare_mode="deb")
    assert _run(cfg) == _run(cfg)


def test_stage_config_validation():
    with pytest.raises(ValueError):
        MUASStageConfig(compare_mode="banana")
    with pytest.raises(ValueError):
        MUASStageConfig(epsilon_tc=0)
    with pytest.raises(ValueError):
        MUASStageConfig(lagaros_variant="8z")
