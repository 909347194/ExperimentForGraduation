# -*- coding: utf-8 -*-
"""P3-a 测试：采纳判据统一为 f（定稿 §4.2），开关对照、默认不静默替换。

契约：
1. ``cost_polish_accept_mode`` 默认 ``"f"``（统一判据）；``"path"`` 仅为
   历史 config 兼容别名，行为与 f 相同；非法取值拒绝。
2. ``cost_polish_eps_f`` 是 **f 单位**（1 f ≈ 10 m 航程 ≈ 100 s 迟到），
   与 ``cost_polish_eps=1.0``（米）不是同一单位、不复用旧值。
3. ``accept_f_view`` 的约束向量 (n_hard, 执行集, 迟到秒, 航程超限)
   逐分量不退化；任一退化即拒，即使 f 更低。
4. 注入「航程严格下降但 f 变差」的候选：path 档采纳、f 档拒绝，
   ``diagnostics["cost_polish_accept"]`` 记录两视图与 drift 供 P4 对照。
5. 默认档（无注入）与自身逐位一致；path 档的采纳结果 == path 视图。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from task_allocation.methods.muas.constraints import spec as constraint_spec  # noqa: E402
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


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------


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
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY
    )
    return run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=stage_cfg,
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=seed),
        nest_offset=NEST_KEY, current_time=0.0,
    )


def _inject_path_better_f_worse(monkeypatch) -> None:
    """注入一个「航程 −100 m、f +50」的人工候选。

    ``post_cost_polish`` 返回的解与原交付解逐位相同（深拷贝），只在
    ``meta["_p3_marker"]`` 上打标；problem 的 ``path_cost`` /
    ``scalar_fitness`` 对带标对象分别偏移 −100 / +50。约束向量（n_hard /
    执行集 / 迟到 / 超限）由深拷贝保持不退化，于是：

    - path 视图：航程严格下降 > 1.0 m ⇒ 采纳（旧语义的缺陷通道）；
    - f 视图：f 变差 50 ⇒ 拒绝。
    """
    def _post(self, solution):
        cand = copy.deepcopy(solution)
        cand.meta["_p3_marker"] = True
        # 用「交付解当前 f」做基准，避免 problem.evaluate(cand) 重评带来的
        # 口径漂移把固定偏移量吃掉（实测同解深拷贝重评可差 ~100）。
        cand.meta["_p3_base_f"] = float(self.problem.scalar_fitness(solution))
        return cand, {"n_accepted": 1, "n_2opt": 1}

    orig_scalar = SelectiveMUASProblem.scalar_fitness
    orig_path = SelectiveMUASProblem.path_cost

    def _scalar(self, sol):
        if sol.meta.get("_p3_marker"):
            return float(sol.meta["_p3_base_f"]) + 50.0
        return orig_scalar(self, sol)

    def _path(self, sol):
        v = orig_path(self, sol)
        return v - 100.0 if sol.meta.get("_p3_marker") else v

    monkeypatch.setattr(MUASAssignmentEvaluator, "post_cost_polish", _post)
    monkeypatch.setattr(SelectiveMUASProblem, "scalar_fitness", _scalar)
    monkeypatch.setattr(SelectiveMUASProblem, "path_cost", _path)


# ---------------------------------------------------------------------------
# 1. config
# ---------------------------------------------------------------------------


def test_config_defaults_and_validation():
    cfg = MUASStageConfig()
    assert cfg.cost_polish_accept_mode == "f"
    assert cfg.cost_polish_accept_mode_effective == "f"
    assert cfg.cost_polish_eps_f == 1e-6
    # 单位辨析：旧阈值是米，新阈值是 f 单位，二者不得静默等同
    assert cfg.cost_polish_eps == 1.0
    assert cfg.cost_polish_eps_f != cfg.cost_polish_eps

    with pytest.raises(ValueError, match="cost_polish_accept_mode"):
        MUASStageConfig(cost_polish_accept_mode="legacy")
    with pytest.raises(ValueError, match="cost_polish_eps_f"):
        MUASStageConfig(cost_polish_eps_f=-1.0)


# ---------------------------------------------------------------------------
# 2. accept_f_view 的约束向量
# ---------------------------------------------------------------------------


def test_accept_f_view_requires_non_degrading_constraint_vector():
    base = dict(
        cand_n_hard=0, cur_n_hard=0,
        cand_exec={1, 2, 3}, cur_exec={1, 2, 3},
        cand_late=0.0, cur_late=0.0,
        cand_excess=0.0, cur_excess=0.0,
    )
    # 正常：f 严格改进
    assert constraint_spec.accept_f_view(90.0, 100.0, 1e-6, **base) is True
    # ε 边界：恰好等于阈值不算严格改进
    assert constraint_spec.accept_f_view(99.0, 100.0, 1.0, **base) is False
    # f 未改进
    assert constraint_spec.accept_f_view(101.0, 100.0, 1e-6, **base) is False

    for key, value in (
        ("cand_n_hard", 1),
        ("cand_exec", {1, 2}),          # 执行集减少
        ("cand_late", 5.0),             # 迟到增加
        ("cand_excess", 5.0),           # 航程超限增加
    ):
        bad = dict(base)
        bad[key] = value
        assert constraint_spec.accept_f_view(90.0, 100.0, 1e-6, **bad) is False, key

    # criteria_drift 旧调用口径（不传 excess）仍兼容
    assert constraint_spec.accept_f_view(
        90.0, 100.0, 1e-6,
        cand_n_hard=0, cur_n_hard=0,
        cand_exec={1}, cur_exec={1},
        cand_late=0.0, cur_late=0.0,
    ) is True


# ---------------------------------------------------------------------------
# 3. 统一判据：path 别名也必须挡住「航程降但 f 变差」的候选
# ---------------------------------------------------------------------------


def test_path_alias_rejects_f_worse_candidate(monkeypatch):
    """2026-11-07 收口：path 只是兼容别名，生效判据恒为 f。"""
    _inject_path_better_f_worse(monkeypatch)
    stage = _run(MUASStageConfig(cost_polish_accept_mode="path"))
    acc = stage.diagnostics["cost_polish_accept"]

    assert stage.diagnostics["cost_polish_accept_mode"] == "path"
    assert stage.diagnostics["cost_polish_accept_mode_effective"] == "f"
    assert acc["mode_effective"] == "f"
    assert acc["path_view"] is True
    assert acc["f_view"] is False
    assert acc["drift"] is True
    assert acc["accepted"] is False             # 统一判据挡住旧缺陷通道
    assert acc["delta_path_m"] > 0
    assert acc["delta_f"] < 0                  # f 确实变差了
    assert stage.diagnostics["cost_polish_rejected"] == 1


def test_f_mode_rejects_f_worse_candidate(monkeypatch):
    _inject_path_better_f_worse(monkeypatch)
    stage = _run(MUASStageConfig(cost_polish_accept_mode="f"))
    acc = stage.diagnostics["cost_polish_accept"]

    assert stage.diagnostics["cost_polish_accept_mode"] == "f"
    assert acc["path_view"] is True
    assert acc["f_view"] is False
    assert acc["drift"] is True
    assert acc["accepted"] is False             # 统一判据挡住
    assert stage.diagnostics["cost_polish_rejected"] == 1
    # 交付解未被替换：不带注入标记
    assert not stage.solution.meta.get("_p3_marker", False)


def test_f_mode_accepts_genuine_f_improvement(monkeypatch):
    """反向注入：航程不变、f 变好 ⇒ f 档采纳、path 档不采纳。

    用于证明 f 档不是「一律拒绝」，而是按 f 裁决。
    """
    def _post(self, solution):
        cand = copy.deepcopy(solution)
        cand.meta["_p3_f_better"] = True
        cand.meta["_p3_base_f"] = float(self.problem.scalar_fitness(solution))
        return cand, {"n_accepted": 1, "n_2opt": 1}

    orig_scalar = SelectiveMUASProblem.scalar_fitness

    def _scalar(self, sol):
        if sol.meta.get("_p3_f_better"):
            return float(sol.meta["_p3_base_f"]) - 5.0
        return orig_scalar(self, sol)

    monkeypatch.setattr(MUASAssignmentEvaluator, "post_cost_polish", _post)
    monkeypatch.setattr(SelectiveMUASProblem, "scalar_fitness", _scalar)

    stage = _run(MUASStageConfig(cost_polish_accept_mode="f"))
    acc = stage.diagnostics["cost_polish_accept"]
    assert acc["f_view"] is True
    assert acc["path_view"] is False            # 航程未严格下降
    assert acc["accepted"] is True
    assert stage.solution.meta.get("_p3_f_better") is True


# ---------------------------------------------------------------------------
# 4. 默认档零行为变化
# ---------------------------------------------------------------------------


def test_default_mode_is_bit_exact_with_legacy_path_alias():
    a = _run(MUASStageConfig())
    b = _run(MUASStageConfig(cost_polish_accept_mode="path"))
    for key in ("final_fitness", "final_path_m", "final_late_s",
                "final_n_hard", "final_excess_m", "n_executed", "n_deferred"):
        assert a.diagnostics[key] == b.diagnostics[key], key
    assert a.solution.assignment_pairs() == b.solution.assignment_pairs()
    assert a.solution.executed_task_ids == b.solution.executed_task_ids
    assert a.diagnostics["cost_polish_accept_mode"] == "f"
    assert b.diagnostics["cost_polish_accept_mode"] == "path"
    assert a.diagnostics["cost_polish_accept_mode_effective"] == "f"
    assert b.diagnostics["cost_polish_accept_mode_effective"] == "f"
