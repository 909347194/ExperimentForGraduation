# -*- coding: utf-8 -*-
"""P2-d 测试：n_skip 分列 + λ_cov 覆盖保护 + 标定不等式 + w_late 显式化。

契约（定稿 §9 / P2-d）：
1. **extract_features 读 n_skip**：来自 ``meta["gate_stats"]["n_skip"]``；
2. **λ_cov 仅非 penalty 档生效**：deb 两可行比较用 ``f + λ_cov·n_skip``；
   penalty 档恒逐位等价（λ_cov 注入与否决策不变）；
3. **calibrate_lambda_cov**：返回 ``λ·Δpath_max + w_late·ΔL_max`` 上界，
   margin>1 时返回带裕度值；λ_cov 须严格超上界；
4. **stage_runner 分列**：``diagnostics["n_skip_eval"]``（polish 前）与
   ``diagnostics["n_skip_final"]``（终态）分别记、值可不同、来源一致；
5. **w_late 显式化**：``problem.late_weight()`` == ``config.w_violation``，
   取值不变（不改默认路径行为）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from task_allocation.methods.muas.compare_spec import (  # noqa: E402
    CandidateFeatures,
    CompareSpec,
    calibrate_lambda_cov,
    extract_features,
    make_compare_spec,
)
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


class _Sol:
    """极简 solution 替身，仅提供 meta（extract_features 所需）。"""

    def __init__(self, feas=None, gate=None):
        self.meta = {}
        if feas is not None:
            self.meta["feasibility"] = feas
        if gate is not None:
            self.meta["gate_stats"] = gate


def test_extract_features_reads_n_skip():
    sol = _Sol(
        feas={"n_hard": 0, "hard_violation": 0.0, "violations": []},
        gate={"n_skip": 3},
    )
    feat = extract_features(10.0, sol)
    assert feat.n_skip == 3
    # 缺 gate_stats 时回落 0
    assert extract_features(10.0, _Sol(feas={"n_hard": 0})).n_skip == 0
    # solution=None 时不崩，n_skip=0
    assert extract_features(10.0, None).n_skip == 0


def test_deb_lambda_cov_flips_feasible_decision():
    """两可行候选：候选 f 更低但 n_skip 更多 ⇒ 关 λ_cov 时采纳、开 λ_cov 时拒。

    设 cand: f=100, n_skip=0；cur: f=120, n_skip=5。
    - λ_cov=None：100<120 ⇒ 采纳（纯目标）。
    - λ_cov=10：eff(cand)=100, eff(cur)=120+50=170 ⇒ 100<170 ⇒ 仍采纳，
      因为 cur eff 更高（更差）。需反向设计：让 cand 多 skip、cur 少 skip
      且 cand f 略低，使 λ_cov 翻转。
    """
    cand = CandidateFeatures(fitness=100.0, n_hard=0, n_skip=5)
    cur = CandidateFeatures(fitness=120.0, n_hard=0, n_skip=0)

    off = make_compare_spec(mode="deb", lambda_cov=None)
    # 关：cand f 更低 ⇒ 采纳
    assert off.should_replace(cand, cur) is True
    # 开（λ_cov 足够大，使 cand 的 skip 惩罚压过 f 优势）：
    # eff(cand)=100+λ·5；eff(cur)=120。λ=5 ⇒ eff(cand)=125>120 ⇒ 拒。
    on = make_compare_spec(mode="deb", lambda_cov=5.0)
    assert on.should_replace(cand, cur) is False
    # λ=0.5 ⇒ eff(cand)=102.5<120 ⇒ 仍采纳（阈值行为正确）
    weak = make_compare_spec(mode="deb", lambda_cov=0.5)
    assert weak.should_replace(cand, cur) is True


def test_penalty_ignores_lambda_cov_bit_exact():
    """penalty 档：λ_cov 注入与否决策逐位一致（method-phase1 等价）。"""
    cand = CandidateFeatures(fitness=100.0, n_hard=0, n_skip=5)
    cur = CandidateFeatures(fitness=120.0, n_hard=0, n_skip=0)
    off = make_compare_spec(mode="penalty", lambda_cov=None)
    on = make_compare_spec(mode="penalty", lambda_cov=5.0)
    assert off.should_replace(cand, cur) == on.should_replace(cand, cur) is True


def test_lagaros_feasible_applies_cov_shift():
    """lagaros 可行侧（p_violation=0 ⇒ F_sel=f）：覆盖保护生效。"""
    cand = CandidateFeatures(fitness=100.0, n_hard=0, n_skip=5)
    cur = CandidateFeatures(fitness=120.0, n_hard=0, n_skip=0)
    off = make_compare_spec(mode="lagaros", lambda_cov=None)
    on = make_compare_spec(mode="lagaros", lambda_cov=5.0)
    assert off.should_replace(cand, cur) is True       # 纯 f：采纳
    assert on.should_replace(cand, cur) is False       # eff 翻转：拒


def test_calibrate_lambda_cov_bound_and_margin():
    """标定：裸上界 = λ·Δpath + w_late·ΔL；margin>1 严格超上界。"""
    lam, wv = 0.1, 0.01
    d_path, d_late = 5000.0, 100.0
    bound = lam * d_path + wv * d_late  # = 500 + 1 = 501
    assert calibrate_lambda_cov(lam, wv, d_path, d_late, margin=1.0) == pytest.approx(bound)
    with_margin = calibrate_lambda_cov(lam, wv, d_path, d_late, margin=1.1)
    assert with_margin > bound
    assert with_margin == pytest.approx(bound * 1.1)


def test_late_weight_explicit_and_unchanged():
    """problem.late_weight() 显式化入口，取值 == config.w_violation。"""
    cfg = MUASProblemConfig()
    prob = SelectiveMUASProblem.from_entities(
        tasks=[], uavs=[], nests=[], config=cfg
    )
    assert prob.late_weight() is cfg.w_violation  # 同一来源，不改值


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


def _run(stage_cfg, seed=7):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    return run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=stage_cfg,
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=seed),
        nest_offset=NEST_KEY, current_time=0.0,
    )


def test_stage_split_n_skip_eval_final():
    """stage_runner 分别记 n_skip_eval（polish 前）与 n_skip_final（终态）。"""
    stage = _run(MUASStageConfig())
    diag = stage.diagnostics
    assert "n_skip_eval" in diag and "n_skip_final" in diag
    # gate 修复下 voluntary_skip 恒 0 ⇒ 两处均为 0（正常路径值一致）
    assert diag["n_skip_eval"] == 0
    assert diag["n_skip_final"] == 0
    # 来源一致：均取自 gate_stats
    assert diag["gate_stats"].get("n_skip", 0) == diag["n_skip_final"]
    # 二者为独立字段（非同一绑定），确保分列语义（值可不同）
    assert isinstance(diag["n_skip_eval"], int) and isinstance(diag["n_skip_final"], int)
