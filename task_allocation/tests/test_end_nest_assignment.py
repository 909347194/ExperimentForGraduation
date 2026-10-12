# -*- coding: utf-8 -*-
"""方案 A 内层子问题：终点机巢分配精确求解器（solve_end_nest_assignment）。

锁定的契约：

1. **最优性**：随机小规模实例上，求解器返回的「返航代价 + 容量罚」目标值
   与暴力枚举一致（容量可行解存在时即硬约束最优解）；
2. **容量争夺**：两架 UAV 同抢唯一泊位时，按全局最优拆分（而非同巢超卖）；
3. **起飞让名额**：参与规划的 UAV 从当前归属巢让出泊位后可「原地不动」；
4. **候选过滤**：禁用 / 不可用 / 不可达（inf 代价）的机巢不被选中；
5. **溢出兜底**：泊位被非规划 UAV 占满时仍返回分配，由 check_nest_capacity 计罚；
6. **默认配置**：``MUASStageConfig.end_nest_in_encoding`` 默认 False（方案 A）；
7. **陷阱规避**：返航代价必须进入标量适应度（同路线不同终点 → 适应度不同）；
8. **漏洞可见化**：非空航次 end_nest_id=None → 计违约，而非返航零成本静默漏计；
9. **结构性可行**：存在容量可行解时绝不溢出（远处空巢 vs 近处满巢的对抗），
   "容量可行最优"不依赖罚则权重。
"""

from __future__ import annotations

import itertools

import pytest

from task_allocation.common.types import AllocationSolution, Nest, Task, UAV, UAVTour
from task_allocation.methods.muas.constraints.nest_capacity import (
    DEFAULT_CAPACITY_UNIT_PENALTY,
    check_nest_capacity,
    solve_end_nest_assignment,
)
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig


def _uav(uid: int, nest_id: int | None) -> UAV:
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, remaining_range=1e6, speed=10.0,
               nest_id=nest_id)


def _penalized_cost(assign: dict[int, int | None],
                    costs: dict[int, dict[int, float]],
                    uavs: list[UAV], nests: list[Nest]) -> float:
    """目标值 = Σ 返航代价 + check_nest_capacity 的容量罚。

    注：求解器溢出边另有结构性保底（不改变本目标的最优解——两者都是
    "溢出数优先、返航代价次之"的字典序），本函数仅用于对齐报告口径。
    """
    total = 0.0
    for uid, b in assign.items():
        if b is not None:
            total += costs.get(uid, {}).get(b, 0.0)
    report = check_nest_capacity(uavs, nests, assign)
    return total + report.penalty


def _brute_force(planning: list[int],
                 candidates: dict[int, dict[int, float]],
                 uavs: list[UAV], nests: list[Nest],
                 fixed: dict[int, int | None]) -> float:
    """枚举所有候选组合，返回最小惩罚目标值。"""
    keys = [u for u in planning if candidates.get(u)]
    best = float("inf")
    for combo in itertools.product(*(sorted(candidates[u]) for u in keys)):
        assign = dict(fixed)
        assign.update(dict(zip(keys, combo)))
        best = min(best, _penalized_cost(assign, candidates, uavs, nests))
    return best


# ---------------------------------------------------------------------------
# 1. 最优性：随机小实例 vs 暴力枚举
# ---------------------------------------------------------------------------


def test_optimality_vs_brute_force():
    import random

    rng = random.Random(20261011)
    for _trial in range(30):
        n_uav, n_nest = 4, 3
        nests = [
            Nest(id=b, x=0.0, y=0.0, z=0.0,
                 capacity=rng.randint(1, 3),
                 available=rng.random() > 0.15)
            for b in range(n_nest)
        ]
        uavs = [_uav(u, nest_id=rng.randrange(n_nest)) for u in range(n_uav)]
        planning = list(range(n_uav))
        costs: dict[int, dict[int, float]] = {}
        for u in planning:
            costs[u] = {
                b: float(rng.randint(10, 500))
                for b in range(n_nest)
                # 候选空间与求解器对齐：可达（有限代价）且机巢可用；
                # 不可用 / 不可达由 test_excluded_unavailable_unreachable_filtered 专测
                if rng.random() > 0.2 and nests[b].available
            }
            if not costs[u]:  # 保证每架至少一个候选，避免两边对空集语义不一致
                b = next((b for b in range(n_nest) if nests[b].available), 0)
                costs[u] = {b: float(rng.randint(10, 500))}

        got = solve_end_nest_assignment(uavs, nests, costs,
                                        planning_uav_ids=planning)
        assert set(got) == set(planning)
        got_cost = _penalized_cost(got, costs, uavs, nests)

        fixed = {}
        brute = _brute_force(planning, costs, uavs, nests, fixed)
        assert got_cost == pytest.approx(brute, abs=1e-6), (
            f"trial={_trial}: solver={got_cost} brute={brute} assign={got}"
        )


# ---------------------------------------------------------------------------
# 2. 容量争夺：全局最优拆分
# ---------------------------------------------------------------------------


def test_capacity_contention_forces_optimal_split():
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=1),
             Nest(id=1, x=0.0, y=0.0, z=0.0, capacity=1)]
    uavs = [_uav(0, nest_id=None), _uav(1, nest_id=None)]
    costs = {
        0: {0: 10.0, 1: 400.0},   # UAV0 强烈偏好巢 0
        1: {0: 20.0, 1: 100.0},   # UAV1 也偏好巢 0，但去巢 1 代价更低
    }
    got = solve_end_nest_assignment(uavs, nests, costs,
                                    planning_uav_ids=[0, 1])
    # 最优拆分：UAV0 巢0(10) + UAV1 巢1(100) = 110；同巢超卖 = 1e4 罚
    assert got == {0: 0, 1: 1}
    assert _penalized_cost(got, costs, uavs, nests) == pytest.approx(110.0)


# ---------------------------------------------------------------------------
# 3. 起飞让名额：原地不动不占新泊位
# ---------------------------------------------------------------------------


def test_release_semantics_stay_in_place():
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=1)]
    uavs = [_uav(0, nest_id=0)]          # 唯一 UAV 归属巢 0，且要参与规划
    costs = {0: {0: 50.0}}
    got = solve_end_nest_assignment(uavs, nests, costs, planning_uav_ids=[0])
    assert got == {0: 0}
    rep = check_nest_capacity(uavs, nests, got)
    assert rep.violations == [] and rep.penalty == 0.0


# ---------------------------------------------------------------------------
# 4. 候选过滤：禁用 / 不可用 / 不可达不被选中
# ---------------------------------------------------------------------------


def test_excluded_unavailable_unreachable_filtered():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=5),           # 最便宜但被禁用
        Nest(id=1, x=0.0, y=0.0, z=0.0, capacity=5),           # 次便宜但不可用
        Nest(id=2, x=0.0, y=0.0, z=0.0, capacity=5),
        Nest(id=3, x=0.0, y=0.0, z=0.0, capacity=5),           # 不可达
    ]
    nests[1].available = False
    uavs = [_uav(0, nest_id=None)]
    costs = {0: {0: 10.0, 1: 20.0, 2: 30.0, 3: float("inf")}}
    got = solve_end_nest_assignment(uavs, nests, costs,
                                    planning_uav_ids=[0],
                                    excluded_nest_ids={0})
    assert got == {0: 2}


# ---------------------------------------------------------------------------
# 5. 溢出兜底：泊位被非规划 UAV 占满时仍返回分配（计罚兜底）
# ---------------------------------------------------------------------------


def test_overflow_fallback_when_full():
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=1)]
    # 巢 0 的泊位被一架非规划（BUSY 语义上不参与）UAV 占满
    uavs = [_uav(9, nest_id=0), _uav(0, nest_id=None)]
    costs = {0: {0: 70.0}}
    got = solve_end_nest_assignment(uavs, nests, costs, planning_uav_ids=[0])
    assert got == {0: 0}
    rep = check_nest_capacity(uavs, nests, got)
    # 溢出 1 架，罚则与求解器的溢出边同量纲
    assert rep.violations == [0]
    assert rep.penalty == pytest.approx(DEFAULT_CAPACITY_UNIT_PENALTY)


# ---------------------------------------------------------------------------
# 6. 默认配置：方案 B（pro 分支主线）
# ---------------------------------------------------------------------------


def test_default_config_is_plan_b():
    """pro 分支主线口径：终点机巢默认进编码（方案 B）。

    master 分支该默认值为 False（方案 A 分层求解）；pro 分支落实方案 B，
    类默认置 True，exp01 config.yaml 亦显式 true。方案 A 对照由显式传
    ``end_nest_in_encoding=False`` 获得（exp04 消融）。
    """
    assert MUASStageConfig().end_nest_in_encoding is True


# ---------------------------------------------------------------------------
# 9. 结构性可行：存在容量可行解时绝不溢出（对抗性）
# ---------------------------------------------------------------------------


def test_feasible_far_nest_beats_overflow_near_nest():
    """远处空巢 vs 近处满巢：必须选容量可行解，不得被罚则权重击穿。

    返航代价差 2e4 − 100 > 溢出罚 1e4：纯罚则权重下 MCF 会宁可把机挤进
    已满的近巢（100 + 1e4 < 2e4）；但落地期硬校验会拒绝该解 → 回池 churn。
    结构性保底后必须选远处空巢（容量可行最优结构性成立）。
    """
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=1),
             Nest(id=1, x=20000.0, y=0.0, z=0.0, capacity=1)]
    # 巢 0 的唯一泊位被非规划 UAV9 占满；UAV0 无归属、需选终点
    uavs = [_uav(9, nest_id=0), _uav(0, nest_id=None)]
    costs = {0: {0: 100.0, 1: 20000.0}}
    got = solve_end_nest_assignment(uavs, nests, costs, planning_uav_ids=[0])
    assert got == {0: 1}, f"必须选容量可行的远巢，得到 {got}"
    rep = check_nest_capacity(uavs, nests, got)
    assert rep.violations == [] and rep.penalty == 0.0


# ---------------------------------------------------------------------------
# 7. 陷阱规避：返航代价必须进入完整方案评价
# ---------------------------------------------------------------------------


def test_return_cost_participates_in_fitness():
    """同一路线、不同终点机巢 → 适应度必须不同（返航代价入评价）。

    这是方案 A 第三条要求（"不同任务路线会因其最优终点分配及相应返航成本
    而得到不同评价"）的直接契约：若返航不进适应度，终点分配对进化搜索
    就是零影响，退回"最后随便找个合法机巢"的陷阱实现。
    """
    tasks = [Task(id=0, x=0.0, y=500.0, z=0.0, reward=5.0)]
    uavs = [UAV(id=0, x=0.0, y=0.0, z=0.0,
                remaining_range=1e6, speed=10.0, nest_id=0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10),
             Nest(id=1, x=0.0, y=0.0, z=0.0, capacity=10)]
    # 起点→任务 = 100；任务→巢0 = 100（近）；任务→巢1 = 400（远）
    pairwise = {(-1, 0): 100.0, (0, 1_000_000): 100.0, (0, 1_000_001): 400.0}
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests, pairwise_cost=pairwise,
        config=MUASProblemConfig(w_time=0.0),
    )

    def sol(end):
        return AllocationSolution(
            tours=[UAVTour(uav_id=0, task_ids=[0], end_nest_id=end)],
            candidate_task_ids=[0], executed_task_ids=[0],
        )

    f_near = prob.scalar_fitness(sol(0))
    f_far = prob.scalar_fitness(sol(1))
    # λ=1：返航差 400-100 = 300 必须体现在标量适应度里（远巢更差）
    assert f_far - f_near == pytest.approx(300.0)
    assert f_far > f_near


# ---------------------------------------------------------------------------
# 8. 漏洞可见化：有航次却无终点机巢必须计违约
# ---------------------------------------------------------------------------


def test_missing_end_nest_is_penalized():
    """非空航次 end_nest_id=None → 违约可见，而非返航零成本静默漏计。"""
    tasks = [Task(id=0, x=0.0, y=500.0, z=0.0, reward=5.0)]
    uavs = [UAV(id=0, x=0.0, y=0.0, z=0.0,
                remaining_range=1e6, speed=10.0, nest_id=0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    pairwise = {(-1, 0): 100.0}
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests, pairwise_cost=pairwise,
        config=MUASProblemConfig(w_time=0.0),
    )
    sol = AllocationSolution(
        tours=[UAVTour(uav_id=0, task_ids=[0], end_nest_id=None)],
        candidate_task_ids=[0], executed_task_ids=[0],
    )
    ok, details = prob.check_feasible(sol)
    assert not ok
    assert "end_nest_missing_0" in details["violations"]
    assert details["penalty"] >= 1e3

    # 空航次（不飞）无返航义务：不计违约
    idle = AllocationSolution(
        tours=[UAVTour(uav_id=0, task_ids=[], end_nest_id=None)],
        candidate_task_ids=[0], executed_task_ids=[],
    )
    ok2, det2 = prob.check_feasible(idle)
    assert "end_nest_missing_0" not in det2["violations"]
