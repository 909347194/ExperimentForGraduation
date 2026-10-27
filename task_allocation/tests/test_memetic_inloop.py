# -*- coding: utf-8 -*-
"""memetic M1 精英档案测试（2026-10-19）。

契约：

1. **代际钩子**（dmde_solver）：``gen_callback`` 每代被调一次（gen=1..G）；
   ``None`` ⇒ 纯 DMDE 零行为变化；回调一旦抛异常即被吞并停用，stderr
   警告一次（不静默、不每代重复），DMDE 数值确定性不受影响；
2. **RNG 无关性 / 无回归**：同种子下 polish_in_loop 开/关的 DMDE 曲线
   （``cost_history``）逐位相同——钩子 RNG-free、不改群体，故纯 DMDE 轨迹不变；
3. **档案机制**（run_selective_muas）：开启后 ``memetic_n_polish == G // K``、
   ``memetic_trajectory`` 长度 == ``len(cost_history) == G+1``、与曲线逐代对齐、
   首次 polish 前记 ``None``（+inf）；单次精英打磨失败只计数
   （``memetic_n_failed``），不得破坏上述对齐（审查加固，2026-10-19）；
4. **择优单调**：交付解取「收敛后 polish」与「档案最优」的更优者，
   ``final_fitness(on) <= final_fitness(off)``，且两口径均零硬违约；
5. **配置校验**：``memetic_K < 1`` 拒绝。

动机：DMDE 早平后路由优化留到收敛后；M1 把精英 polish 前移进搜索期
（每 K 代一次），使命中路由在搜索期内即被压低。不改基因型（无反映射）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import (  # noqa: E402
    build_pairwise_costs,
)
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import (  # noqa: E402
    DMDESolver,
    DMDEConfig,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 1. dmde_solver 代际钩子
# ---------------------------------------------------------------------------


class _FakeEval:
    """最小适应度评估器：归属下标加权和（供驱动 DE 循环，不涉修复链）。"""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, assignment, cost_matrix=None, n_uavs=None):
        self.calls += 1
        fit = (
            float(sum((a + 1) * (b + 1) for a, b in assignment))
            if assignment else 1e12
        )
        return types.SimpleNamespace(fitness=fit, solution=object())


def _tiny_cm(k: int, m: int) -> np.ndarray:
    return np.random.default_rng(0).random((k + m, m)) + 0.1


def test_gen_callback_invoked_every_generation():
    ev = _FakeEval()
    seen: list[int] = []
    cfg = DMDEConfig(
        pop_size=5, max_generations=7, seed=3,
        gen_callback=lambda g, ind: seen.append(g),
    )
    DMDESolver(config=cfg).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev
    )
    assert seen == list(range(1, 8)), f"回调应每代调一次（gen=1..G）：{seen}"


def test_gen_callback_none_is_pure_dmde():
    """gen_callback=None ⇒ 纯 DMDE，曲线长度 G+1、无异常。"""
    ev = _FakeEval()
    cfg = DMDEConfig(pop_size=5, max_generations=6, seed=3)  # 无 gen_callback
    res = DMDESolver(config=cfg).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev
    )
    assert len(res.cost_history) == 7, "初始 + 6 代"
    assert res.cost_history[-1] == res.best_fitness, "搜索期自洽"


def test_gen_callback_exception_swallowed():
    """钩子抛异常必须被吞，不得破坏 DMDE 数值确定性。"""
    ev = _FakeEval()

    def _boom(g, ind):
        raise RuntimeError("boom")

    cfg = DMDEConfig(pop_size=5, max_generations=5, seed=3, gen_callback=_boom)
    res = DMDESolver(config=cfg).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev
    )
    assert res is not None and len(res.cost_history) == 6


def test_gen_callback_exception_warns_once_and_disables(capsys):
    """回调抛异常：stderr 警告恰好一次、即停不再重试、曲线与无回调一致。

    审查加固（2026-10-19）：裸吞异常违反「回退永不静默」惯例；改为
    warn-once + 停用。本测试锁死「恰好一次警告 + 只尝试一次 + 数值
    确定性不受影响（与无回调同种子曲线逐位相同）」三条不变量。
    """
    ev = _FakeEval()
    calls = {"n": 0}

    def _boom(g, ind):
        calls["n"] += 1
        raise RuntimeError("boom")

    cfg = DMDEConfig(pop_size=5, max_generations=5, seed=3, gen_callback=_boom)
    res = DMDESolver(config=cfg).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev
    )
    captured = capsys.readouterr()
    assert calls["n"] == 1, "异常后即停用，不得每代重试已失败的回调"
    assert captured.err.count("[dmde][warn]") == 1, "警告恰好一次（不静默也不刷屏）"
    assert "[dmde][warn]" not in captured.out, "警告走 stderr，不污染 stdout"
    # 数值确定性：与无回调同种子跑逐位相同
    ev2 = _FakeEval()
    cfg2 = DMDEConfig(pop_size=5, max_generations=5, seed=3)
    res2 = DMDESolver(config=cfg2).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev2
    )
    assert res.cost_history == res2.cost_history, (
        "回调异常被吞并停用后，纯 DMDE 曲线必须与无回调逐位一致"
    )
    assert res.best_fitness == res2.best_fitness


# ---------------------------------------------------------------------------
# 2-4. run_selective_muas 集成：无回归 + 档案机制 + 择优单调
# ---------------------------------------------------------------------------


def _uav(uid: int, x: float) -> UAV:
    return UAV(id=uid, x=x, y=0.0, z=0.0, remaining_range=1e7,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


def _task(tid: int, x: float, reward: float = 5.0) -> Task:
    return Task(id=tid, x=x, y=0.0, z=0.0, reward=reward,
                earliest=0.0, latest=1e6, arrival_time=0.0)


def _scenario():
    """2 机（0 / 300）+ 3 任务（100 / 150 / 320）+ 1 巢：t2 近 uav1，
    DMDE 若全压 uav0 则 relocate 有改进空间。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 300.0)]
    tasks = [_task(0, 100.0), _task(1, 150.0), _task(2, 320.0)]
    return tasks, uavs, nests


def _run(polish_in_loop: bool, memetic_K: int = 2):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, dem=None, estimator=None,
                              nest_offset=NEST_KEY)
    return run_selective_muas(
        tasks, uavs, nests, cb,
        fleet_uavs=uavs,
        problem_config=MUASProblemConfig(w_time=0.0),
        stage_config=MUASStageConfig(
            polish_in_loop=polish_in_loop, memetic_K=memetic_K
        ),
        solver_config=DMDEConfig(pop_size=12, max_generations=6, seed=1),
        current_time=0.0,
    )


def test_memetic_off_matches_pure_dmde_curve():
    """关：memetic 诊断全空，行为即非 memetic 基线。"""
    st = _run(polish_in_loop=False)
    d = st.diagnostics
    assert d["memetic_enabled"] is False
    assert d["memetic_n_polish"] == 0
    assert d["memetic_trajectory"] == []
    assert d["memetic_final_fitness"] is None


def test_memetic_curve_identical_on_off():
    """同种子下开关的 DMDE 搜索期曲线逐位相同（钩子 RNG-free、不改群体）。"""
    off = _run(polish_in_loop=False)
    on = _run(polish_in_loop=True, memetic_K=2)
    assert on.cost_history == off.cost_history, (
        "memetic 钩子不得扰动 DMDE 搜索期轨迹（RNG-free 不变量）"
    )


def test_memetic_populates_archive_and_aligns_trajectory():
    on = _run(polish_in_loop=True, memetic_K=2)  # max_generations=6, K=2 → 3 次
    d = on.diagnostics
    assert d["memetic_enabled"] is True
    assert d["memetic_n_polish"] == 3, f"G//K = 6//2 = 3：{d['memetic_n_polish']}"
    traj = d["memetic_trajectory"]
    assert len(traj) == len(on.cost_history) == 7, "轨迹与曲线逐代对齐（G+1）"
    assert traj[0] is None, "gen0 首次 polish 前记 None（+inf）"
    assert traj[1] is None, "gen1 未触发（K=2）仍记 None"
    assert traj[2] is not None, "gen2 首次精英 polish 后有限"
    assert d["memetic_final_fitness"] is not None
    assert np.isfinite(d["memetic_final_fitness"])
    assert d["memetic_gain"] is not None and d["memetic_gain"] >= -1e-9


def test_memetic_final_never_worse_and_feasible():
    """择优单调：final_fitness(on) <= final_fitness(off)；两口径零硬违约。"""
    off = _run(polish_in_loop=False)
    on = _run(polish_in_loop=True, memetic_K=2)
    assert on.diagnostics["final_fitness"] <= off.diagnostics["final_fitness"] + 1e-6, (
        "memetic 档案择优不得劣于非 memetic（final_fitness 只降不升）"
    )
    assert off.diagnostics["final_n_hard"] == 0
    assert on.diagnostics["final_n_hard"] == 0, "memetic 不得引入硬违约"
    assert on.solution.feasible and off.solution.feasible


def test_memetic_inloop_failure_keeps_trajectory_aligned(monkeypatch):
    """钩子内 in-loop polish 抛异常：轨迹仍与曲线逐代对齐、失败只计数。

    审查加固（2026-10-19）：修复前钩子内异常会上抛给求解器吞掉，导致
    失败代及其后各代不再落档（trajectory 长度 < len(cost_history)），
    且回调停用后档案维护中断。修复后单次失败只计 memetic_n_failed，
    「每代必落一档」对齐不变量与 DMDE 曲线不变性均保持。
    """
    # 2026-10-27 muas_stage 拆分后，polish_assignment 的**消费方**在
    # stage_runner.run_selective_muas（门面只 re-export）；失败注入必须打在
    # 消费方命名空间上才生效（测试意图不变：注入 in-loop 失败验计数与对齐）。
    import task_allocation.methods.muas.solvers.stage_runner as ms

    real = ms.polish_assignment
    calls = {"n": 0}

    def flaky(evaluator, assignment, cfg):
        calls["n"] += 1
        if calls["n"] == 2:  # gen4 的 in-loop（gen2/gen6/收敛后为 1/3/4）
            raise RuntimeError("injected in-loop failure")
        return real(evaluator, assignment, cfg)

    off = _run(polish_in_loop=False)  # 对照跑先于 patch：其收敛后打磨不计入 calls
    monkeypatch.setattr(ms, "polish_assignment", flaky)
    on = _run(polish_in_loop=True, memetic_K=2)
    d = on.diagnostics
    assert d["memetic_enabled"] is True
    assert d["memetic_n_failed"] == 1, "单次精英打磨失败只计数"
    assert d["memetic_n_polish"] == 2, "gen2/gen6 两次成功（gen4 失败不计入）"
    assert calls["n"] == 4, "3 次 in-loop + 1 次收敛后均应透传"
    assert len(d["memetic_trajectory"]) == len(on.cost_history) == 7, (
        "失败代也必须落一档：轨迹与曲线逐代对齐（G+1）"
    )
    assert on.cost_history == off.cost_history, "钩子内失败不得扰动 DMDE 曲线"
    assert np.isfinite(d["memetic_final_fitness"]), "档案仍保有 gen2/gen6 成果"
    assert on.diagnostics["final_n_hard"] == 0, "失败注入不得引入硬违约"


# ---------------------------------------------------------------------------
# 5. 配置校验
# ---------------------------------------------------------------------------


def test_memetic_K_validation():
    with pytest.raises(ValueError, match="memetic_K"):
        MUASStageConfig(memetic_K=0)
    assert MUASStageConfig().polish_in_loop is False, "默认关（基线不动）"
    assert MUASStageConfig().memetic_K == 20
    assert MUASStageConfig().memetic_max_evals == 500
