# -*- coding: utf-8 -*-
"""灭绝重置随机流收口回归测试（审查报告 P0 #1）。

锁死的契约（方案 B：单通道 Generator，全局播种已删除）：

1. ``PopulationEncoder.generate(rng=...)`` 全部随机性取自注入生成器，
   **不触碰**全局 ``random`` / ``np.random`` 状态；同种子逐位可复现。
2. ``seed`` 参数已废弃为兼容别名（内部转 ``np.random.default_rng(seed)``，
   发 DeprecationWarning），同样不触碰全局播种。
3. ``extinction._random_cost_vector`` / ``apply_extinction`` 的随机流
   收口到注入的种子 ``rng``：全局随机状态被外来消费者污染时结果不变
   （历史缺陷：曾忽略 rng 走全局状态，固定 seed 不能逐位复现）。
4. 端到端：DMDESolver 同 seed 两次 solve 逐位一致；solve 全程
   （含初始种群）不触碰全局随机；且求解轨迹对「求解中途消费全局随机」
   完全免疫（覆盖灭绝触发路径）。

运行：PYTHONPATH=. python -m pytest task_allocation/tests/test_extinction_rng_reproduc.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.methods.muas.operators.extinction import (  # noqa: E402
    _random_cost_vector,
    apply_extinction,
)
from task_allocation.methods.muas.representation.encoder import (  # noqa: E402
    PopulationEncoder,
)
from task_allocation.methods.muas.solvers.dmde_solver import (  # noqa: E402
    DMDEConfig,
    DMDESolver,
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _global_random_state():
    """捕获全局 random / np.random 状态快照（可比较）。"""
    np_st = np.random.get_state()
    return (random.getstate(), np_st[0], np_st[1].copy(), np_st[2], np_st[3], np_st[4])


def _assert_global_state_unchanged(before) -> None:
    after = _global_random_state()
    assert after[0] == before[0], "全局 random.getstate() 被改动"
    assert after[1] == before[1], "np.random 算法名被改动"
    assert (after[2] == before[2]).all(), "np.random 内部状态被改动"
    assert after[3:] == before[3:], "np.random 计数器被改动"


def _pollute_global_random(n: int = 200) -> None:
    """模拟进程内其它全局随机消费者（基线求解器/诊断钩子/并行调用）。"""
    lst = list(range(50))
    for _ in range(n):
        random.random()
        random.shuffle(lst)
        random.randint(0, 99)
        np.random.random()
        np.random.shuffle(lst.copy())


def _tiny_cm(k: int, m: int) -> np.ndarray:
    return np.random.default_rng(0).random((k + m, m)) + 0.1


# ---------------------------------------------------------------------------
# 1. generate(rng=...)：收口 + 隔离 + 逐位可复现
# ---------------------------------------------------------------------------


def test_generate_rng_mode_bitwise_reproducible_and_isolated():
    cm = _tiny_cm(2, 3)
    enc = PopulationEncoder(cm, 2, 3)  # srp

    before = _global_random_state()
    pop_a = enc.generate(20, rng=np.random.default_rng(7))
    _assert_global_state_unchanged(before)   # generate 本身不得触碰全局状态
    _pollute_global_random()                 # 外来消费者污染全局状态
    pop_b = PopulationEncoder(cm, 2, 3).generate(20, rng=np.random.default_rng(7))

    va = np.array([ind.cost_vector for ind in pop_a])
    vb = np.array([ind.cost_vector for ind in pop_b])
    assert np.array_equal(va, vb), "同种子 rng 两次 generate 应逐位一致"


def test_generate_seed_and_rng_mutually_exclusive():
    enc = PopulationEncoder(_tiny_cm(2, 3), 2, 3)
    try:
        enc.generate(2, seed=1, rng=np.random.default_rng(0))
    except ValueError:
        return
    raise AssertionError("seed 与 rng 同时给定应抛 ValueError")


def test_generate_seed_alias_deprecated_but_reproducible():
    """seed 已废弃为兼容别名（内部转 default_rng(seed)）：告警 + 不碰全局。"""
    import warnings

    cm = _tiny_cm(3, 3)  # balanced
    before = _global_random_state()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pop_a = PopulationEncoder(cm, 3, 3).generate(10, seed=0)
        pop_b = PopulationEncoder(cm, 3, 3).generate(10, seed=0)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
        "seed 别名应发 DeprecationWarning"
    )
    _assert_global_state_unchanged(before)  # 方案 B：seed 别名也不播种全局
    va = np.array([ind.cost_vector for ind in pop_a])
    vb = np.array([ind.cost_vector for ind in pop_b])
    assert np.array_equal(va, vb), "seed 别名同 seed 应逐位一致"


# ---------------------------------------------------------------------------
# 2. 灭绝算子：随机流收口到注入 rng，对全局污染免疫
# ---------------------------------------------------------------------------


def test_random_cost_vector_uses_injected_rng():
    cm = _tiny_cm(2, 3)
    before = _global_random_state()
    v1 = _random_cost_vector(cm, 2, 3, "srp", np.random.default_rng(11))
    _assert_global_state_unchanged(before)   # 灭绝取个体不得触碰全局状态
    _pollute_global_random()
    v2 = _random_cost_vector(cm, 2, 3, "srp", np.random.default_rng(11))
    assert np.array_equal(v1, v2), "同种子 rng 的灭绝个体应逐位一致"


def test_apply_extinction_resistant_to_global_pollution():
    cm = _tiny_cm(2, 3)
    pop_fit = np.linspace(0.0, 1.0, 6)          # 0 最优、5 最差
    cost_vectors = np.stack(
        [PopulationEncoder(cm, 2, 3).generate(1, rng=np.random.default_rng(i))[0].cost_vector
         for i in range(6)]
    )

    before = _global_random_state()
    new_a, survived_a = apply_extinction(
        pop_fit, cost_vectors, 0, cm, 2, 3, "srp",
        env_pressure_max=0.3, rng=np.random.default_rng(21),
    )
    _assert_global_state_unchanged(before)   # 灭绝不得触碰全局状态
    _pollute_global_random(500)              # 全局被污染
    mid = _global_random_state()
    new_b, survived_b = apply_extinction(
        pop_fit, cost_vectors, 0, cm, 2, 3, "srp",
        env_pressure_max=0.3, rng=np.random.default_rng(21),
    )
    _assert_global_state_unchanged(mid)      # 第二次同样不得触碰全局状态

    assert survived_a == survived_b, "存活集应对全局污染免疫"
    assert np.array_equal(new_a, new_b), "灭绝重置代价值应对全局污染免疫"
    # 确认该场景确实触发了重置（否则本测试空转）
    reset = [i for i in range(6) if i not in survived_a]
    assert reset, "测试场景应至少重置 1 个个体（否则锁不住修复）"


# ---------------------------------------------------------------------------
# 3. 端到端：DMDESolver 同 seed 逐位一致 + 对全局污染免疫（覆盖灭绝触发）
# ---------------------------------------------------------------------------


class _TraceEval:
    """记录完整评价轨迹的评估器：轨迹是整条进化过程的指纹。"""

    def __init__(self) -> None:
        self.trace: list = []

    def evaluate(self, assignment, cost_matrix=None, n_uavs=None):
        self.trace.append(tuple(tuple(x) for x in assignment))
        fit = (
            float(sum((a + 1) * (b + 1) for a, b in assignment))
            if assignment
            else 1e12
        )
        return type("R", (), {"fitness": fit, "solution": None})()


def _solve_once(populate_polluter: bool) -> tuple[_TraceEval, object]:
    ev = _TraceEval()
    cfg = DMDEConfig(pop_size=8, max_generations=6, seed=42)
    if populate_polluter:
        # 每代诊断钩子消费全局随机——模拟进程内其它全局随机消费者
        def _polluter(gen, cv, best_idx):
            _pollute_global_random(50)

        cfg.population_callback = _polluter
    result = DMDESolver(config=cfg).solve(
        cost_matrix=_tiny_cm(2, 3), n_uavs=2, n_targets=3, fitness_evaluator=ev
    )
    return ev, result


def _assert_runs_identical(ev_a, res_a, ev_b, res_b, label: str) -> None:
    assert ev_a.trace == ev_b.trace, f"{label}：评价轨迹应逐位一致"
    assert res_a.best_assignment == res_b.best_assignment, f"{label}：最优解应一致"
    assert res_a.best_fitness == res_b.best_fitness, f"{label}：最优适应度应一致"
    assert res_a.cost_history == res_b.cost_history, f"{label}：收敛曲线应一致"


def test_dmde_solve_does_not_touch_global_random():
    """方案 B：solve 全程（含初始种群）不得触碰全局 random / np.random。"""
    before = _global_random_state()
    _solve_once(populate_polluter=False)
    _assert_global_state_unchanged(before)


def test_dmde_same_seed_bitwise_reproducible():
    ev_a, res_a = _solve_once(populate_polluter=False)
    ev_b, res_b = _solve_once(populate_polluter=False)
    assert ev_a.trace, "应有评价轨迹（否则锁不住契约）"
    _assert_runs_identical(ev_a, res_a, ev_b, res_b, "同 seed 两次 solve")


def test_dmde_trajectory_independent_of_global_random_pollution():
    """核心回归：求解中途的全局随机消费者不得影响结果（灭绝路径收口）。

    修复前：灭绝重置走全局 random，诊断钩子的污染性消费会使其分叉。
    """
    ev_clean, res_clean = _solve_once(populate_polluter=False)
    ev_dirty, res_dirty = _solve_once(populate_polluter=True)
    assert ev_clean.trace, "应有评价轨迹（否则锁不住契约）"
    _assert_runs_identical(
        ev_clean, res_clean, ev_dirty, res_dirty, "污染/干净两次 solve"
    )
