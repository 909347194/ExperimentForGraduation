# -*- coding: utf-8 -*-
"""解码后局部搜索 polish 测试（2026-10-19）。

契约：

1. **邻域生成**（纯函数）：relocate 覆盖「任务 × 其它机 × 插入位」；
   swap 只跨机；position 只机内且不含恒等移动；
2. **规范化**：越界丢弃、一任务一机去重（保留首次）、与 build_solution 同值；
3. **集成**：候选过同一修复链——
   relocate 能救回「被分错机」的任务（单步可改进场景）；
   已最优时零接受（自然收敛）；
   会把航次推超航程的 relocate 被修复链打回 → 不适应（幽灵解不进交付）；
   机内换序在时间窗关闭时直接省路径（position 移动有效）；
4. **开关**：polish_enabled=False 时原样返回；配置校验拒非法值。

动机背景：big-M 阶梯适应度下 DMDE 差分算子对换机/换序级改善不可见
（T9 换机省 27.9km 仅折 2.8 适应度单位 vs 单任务 2.8e5），Lamarckian
回写冻结归属——局部搜索直接在解空间补救，仅严格改进才接受。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import (  # noqa: E402
    build_pairwise_costs,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    ConstraintConfig,
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.local_search import (  # noqa: E402
    DEFAULT_MOVES,
    assignment_of,
    canonical_routes,
    iter_position,
    iter_relocate,
    iter_swap,
    normalize_assignment,
    polish_assignment,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000


def _task(tid: int, x: float, reward: float = 5.0,
          earliest: float = 0.0, latest: float = 1e6) -> Task:
    return Task(id=tid, x=x, y=0.0, z=0.0, reward=reward,
                earliest=earliest, latest=latest, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0, remaining_range: float = 1e7) -> UAV:
    return UAV(id=uid, x=x, y=0.0, z=0.0, remaining_range=remaining_range,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


def _pairwise(tasks, uavs, nests):
    import math

    pts = {t.id: t.position for t in tasks}
    pts.update({-(1 + u.id): u.position for u in uavs})
    pts.update({NEST_KEY + n.id: n.position for n in nests})
    keys = list(pts)
    return {
        (a, b): math.dist(pts[a], pts[b])
        for i, a in enumerate(keys) for b in keys[i + 1:]
    }


def _mk(tasks, uavs, nests, *, stage_cfg=None, **prob_kw):
    """构造 problem + evaluator（候选一律走 evaluator.evaluate 修复链）。"""
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(w_violation=0.01, **prob_kw),
    )
    ev = MUASAssignmentEvaluator(
        prob, participating=list(uavs), tasks=list(tasks), nests=list(nests),
        fleet_uavs=list(uavs), config=stage_cfg or MUASStageConfig(),
    )
    return prob, ev


# ---------------------------------------------------------------------------
# 1. 纯函数：规范化与邻域生成
# ---------------------------------------------------------------------------


def test_normalize_assignment_dedup_and_bounds():
    raw = [(0, 0), (0, 0), (5, 1), (1, 99), (1, 2)]
    out = normalize_assignment(raw, n_uavs=2, n_tasks=3)
    assert out == [(0, 0), (1, 2)], "越界应丢弃、重复任务保留首次、保序"


def test_routes_assignment_roundtrip():
    routes = {0: [0, 2], 1: [1]}
    a = assignment_of(routes)
    assert a == [(0, 0), (0, 2), (1, 1)]
    assert canonical_routes(a, n_uavs=2, n_tasks=3) == routes


def test_iter_relocate_neighborhood():
    routes = {0: [0, 1]}
    cands = list(iter_relocate(routes, n_uavs=2))
    assert {tuple(sorted((u, tuple(v)) for u, v in c.items())) for c in cands} == {
        ((0, (1,)), (1, (0,))),   # t0 → uav1
        ((0, (0,)), (1, (1,))),   # t1 → uav1
    }


def test_iter_swap_cross_uav_only():
    routes = {0: [0, 1], 1: [2]}
    cands = list(iter_swap(routes))
    # 跨机对换 2 个候选；同机对换（≡ position）不得出现
    assert len(cands) == 2
    for c in cands:
        assert sorted(c[0]) != sorted(c.get(1, [])) or c[0] != routes[0]
    assert any(c[0] == [2, 1] and c[1] == [0] for c in cands)
    assert any(c[0] == [0, 2] and c[1] == [1] for c in cands)


def test_iter_position_skips_identity():
    routes = {0: [0, 1, 2]}
    cands = list(iter_position(routes))
    assert len(cands) == 6, "3 任务 × 2 个非恒等插入位"
    assert all(c[0] != [0, 1, 2] for c in cands), "不得含恒等移动"


# ---------------------------------------------------------------------------
# 2. 集成：候选过同一修复链，仅严格改进才接受
# ---------------------------------------------------------------------------


def test_relocate_rescues_misassigned_task():
    """T9 型场景：任务被分给远机、近机空闲 → relocate 单步可改进。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 300.0)]
    tasks = [_task(0, 100.0), _task(1, 310.0)]
    prob, ev = _mk(tasks, uavs, nests)

    base = [(0, 0), (0, 1)]                      # 全压给 uav0（远）
    r_base = ev.evaluate(base)
    res = polish_assignment(ev, base, MUASStageConfig())

    assert res.n_accepted.get("relocate", 0) >= 1, (
        f"未接受任何 relocate：{res.n_accepted}"
    )
    assert res.fitness_gain > 0
    assert res.converged
    # 交付解：两任务都执行、路径严格低于基线、无硬违约
    sol = res.best_solution
    assert set(sol.executed_task_ids) == {0, 1}
    assert prob.path_cost(sol) < prob.path_cost(r_base.solution) - 1.0
    assert sol.feasible, "交付解不得含硬违约（幽灵 F1 保险丝）"


def test_polish_noop_when_optimal():
    """邻域已最优：零接受、自然收敛、gain=0（best==base）。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 300.0)]
    tasks = [_task(0, 100.0), _task(1, 310.0)]
    _, ev = _mk(tasks, uavs, nests)

    base = [(0, 0), (1, 1)]                      # 已按就近归属分好
    res = polish_assignment(ev, base, MUASStageConfig())
    assert res.n_accepted_total == 0
    assert res.fitness_gain == 0.0
    assert res.converged
    assert res.best_fitness == res.base_fitness


def test_polish_rejects_range_violating_relocate():
    """修复链把超航程 relocate 打回（任务被删/违约）→ 适应度变差 → 不接受。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0, remaining_range=1e7),
            _uav(1, 0.0, remaining_range=250.0)]
    tasks = [_task(0, 100.0), _task(1, 400.0)]
    prob, ev = _mk(tasks, uavs, nests)

    base = [(0, 0), (0, 1)]                      # uav0 串飞：全程 800 ≤ 1e7
    r_base = ev.evaluate(base)
    assert r_base.solution.feasible

    # 直接把 t1 甩给 uav1：往返 800 > 250 → 修复链打回原形——或判
    # range_uav_1 硬违约，或按「可达即执行」把 t1 改派回 uav0；两者
    # 都使该候选不可能更优（要么 W_hard 爆炸，要么回到基线形态）。
    r_bad = ev.evaluate([(0, 0), (1, 1)])
    if not r_bad.solution.feasible:
        tags = [str(v) for v in (
            r_bad.solution.meta.get("feasibility") or {}
        ).get("violations", [])]
        assert any(t.startswith("range_uav_1") for t in tags), (
            f"应出现 range_uav_1 硬违约标签：{tags}"
        )
    assert r_bad.fitness >= r_base.fitness - 1e-9, (
        "超航程候选经修复链后不得更优"
    )

    res = polish_assignment(ev, base, MUASStageConfig())
    assert res.n_accepted_total == 0, "不可行/亏本候选不得被接受"
    sol = res.best_solution
    assert set(sol.executed_task_ids) == {0, 1}, "不得因 polish 丢任务"
    assert prob.hard_stats(sol)[0] == 0, "交付解不得含硬违约"


def test_coverage_guard_tracks_current_best_not_initial():
    """回归①（2026-10-26）：覆盖守护必须锚定「当前最优解集」而非初始解集。

    场景（2 机 3 任务，脚本化假评价器模拟修复链行为）：
      base   [(0,0),(1,1)]           → exec {0,1},   f=100
      候选1  [(1,0),(1,1)]           → exec {0,1,2}, f=90   ← 修复链补回任务2
      候选2  [(0,1),(1,0)]           → exec {0,1},   f=80   ← 只降成本、丢任务2

    修复前：best_exec 在第 231 行冻结为初始 {0,1}，候选2 因 {0,1}⊇{0,1}
    通过守护被接受 → 交付 exec {0,1}、f=80（覆盖被静默侵蚀）。
    修复后（接受时同步 best_exec）：候选1 接受后 best_exec={0,1,2}，候选2
    因 {0,1}⊉{0,1,2} 被守护拒绝 → 交付 exec {0,1,2}、f=90。
    """
    from collections import namedtuple

    class _Sol:
        def __init__(self, executed):
            self.executed_task_ids = sorted(executed)

    class _Res:
        def __init__(self, fitness, executed):
            self.fitness = fitness
            self.solution = _Sol(executed)

    class _Eval:
        def __init__(self):
            self.participating = [0, 1]
            self.tasks = [0, 1, 2]
            self.n_evals = 0

        def evaluate(self, cand):
            self.n_evals += 1
            key = tuple(cand)
            if key == ((0, 0), (1, 1)):
                fit, ex = 100.0, {0, 1}
            elif key == ((1, 0), (1, 1)):
                fit, ex = 90.0, {0, 1, 2}      # 修复链新增任务2
            elif key == ((0, 1), (1, 0)):
                fit, ex = 80.0, {0, 1}         # 仅降成本、丢任务2
            else:
                fit, ex = 1e9, set()
            return _Res(fit, ex)

    class _ProblemCfg:
        lexicographic_coverage = True          # 触发 coverage_blind

    class _Problem:
        config = _ProblemCfg()

    ev = _Eval()
    ev.problem = _Problem()
    res = polish_assignment(
        ev, [(0, 0), (1, 1)], MUASStageConfig(polish_enabled=True)
    )
    # 守护必须守住修复链新增的完成量：交付解仍含任务2。
    assert set(res.best_solution.executed_task_ids) == {0, 1, 2}, (
        f"覆盖被侵蚀：交付执行集回退为 {res.best_solution.executed_task_ids}"
    )
    assert res.best_fitness == 90.0, (
        f"应停在携带新增任务的 f=90，而非丢任务后的 f=80：{res.best_fitness}"
    )


def test_position_move_reorders_when_window_off():
    """时间窗关闭时修复链不重排顺序 → position 移动直接省路径。"""
    nests = [Nest(id=0, x=1000.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0)]
    tasks = [_task(0, 1000.0), _task(1, 500.0)]
    _, ev = _mk(
        tasks, uavs, nests,
        constraints=ConstraintConfig(time_window=False),
    )

    base = [(0, 0), (0, 1)]                      # 先远后近：0→1000→500→1000
    r_base = ev.evaluate(base)
    res = polish_assignment(ev, base, MUASStageConfig())
    assert res.n_accepted.get("position", 0) >= 1, "应接受换序（省一半路径）"
    assert res.fitness_gain > 0
    assert set(res.best_solution.executed_task_ids) == {0, 1}
    # 打磨后顺序应为先近后远
    tour = next(t for t in res.best_solution.tours if t.task_ids)
    assert tour.task_ids == [1, 0]


# ---------------------------------------------------------------------------
# 3. 开关与配置校验
# ---------------------------------------------------------------------------


def test_polish_disabled_returns_base():
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 300.0)]
    tasks = [_task(0, 100.0), _task(1, 310.0)]
    _, ev = _mk(tasks, uavs, nests)

    base = [(0, 0), (0, 1)]
    res = polish_assignment(
        ev, base, MUASStageConfig(polish_enabled=False)
    )
    assert res.n_accepted_total == 0
    assert res.best_assignment == base
    assert res.converged


def test_stage_config_validates_polish_fields():
    with pytest.raises(ValueError, match="polish_moves"):
        MUASStageConfig(polish_moves=("teleport",))
    with pytest.raises(ValueError, match="polish_max_rounds"):
        MUASStageConfig(polish_max_rounds=0)
    with pytest.raises(ValueError, match="polish_max_evals"):
        MUASStageConfig(polish_max_evals=0)
    with pytest.raises(ValueError, match="polish_eps"):
        MUASStageConfig(polish_eps=-1.0)
    # 默认三移动齐全
    assert set(DEFAULT_MOVES) == {"relocate", "swap", "position"}
    assert set(MUASStageConfig().polish_moves) == set(DEFAULT_MOVES)
