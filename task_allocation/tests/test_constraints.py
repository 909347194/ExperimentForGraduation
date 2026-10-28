# -*- coding: utf-8 -*-
"""constraints 包单元测试（参考借鉴 Experiments-For-Dissertation 的模块化约束）。

锁定三点：
    1. single_constraints：航程 / 飞行时间 违背量计算正确；
    2. coop_constraints：时间窗违背量 + 标签；时序 / 同时到达为可选能力（缺字段则惰性 0）；
    3. evaluator.ConstraintReport：total_violation 为唯一判据 + as_details 向后兼容
       （violations / penalty / nest_capacity 口径不变，供 horizon / metrics 消费）。

同时验证 problem.check_feasible 在迁入 constraints 包后行为与历史一致。
"""

from __future__ import annotations

from task_allocation.common.types import AllocationSolution, Nest, Task, UAV, UAVTour
from task_allocation.methods.muas.constraints import (
    ConstraintReport,
    check_range_constraint,
    check_sequence_constraint,
    check_sync_constraint,
    check_time_constraint,
    check_time_window_constraint,
)
from task_allocation.methods.muas.problem import MUASProblemConfig, SelectiveMUASProblem


def _uav(uid=0, remaining=1000.0, speed=10.0, max_time=None, nest_id=0, pos=(0.0, 0.0, 0.0)):
    x, y, z = pos
    return UAV(
        id=uid,
        x=x,
        y=y,
        z=z,
        remaining_range=remaining,
        speed=speed,
        max_time=max_time,
        nest_id=nest_id,
    )


def test_single_range():
    u = _uav(0, remaining=100.0)
    assert check_range_constraint(u, 120.0) == 20.0
    assert check_range_constraint(u, 80.0) == 0.0
    # margin 收缩
    assert check_range_constraint(u, 95.0, margin=0.9) == 5.0


def test_single_time():
    # 无 max_time → 惰性 0
    assert check_time_constraint(_uav(0), 150.0) == 0.0
    # 有 max_time：150m / 10m/s = 15s > 10s → 违背 5s
    u = _uav(0, speed=10.0, max_time=10.0)
    assert check_time_constraint(u, 150.0) == 5.0
    assert check_time_constraint(u, 50.0) == 0.0


def test_coop_window():
    tasks = {0: Task(id=0, x=0.0, y=500.0, z=0.0, latest=10.0)}
    uavs = {0: _uav(0, speed=10.0)}
    tour = UAVTour(uav_id=0, task_ids=[0], end_nest_id=0,
                   start_x=0.0, start_y=0.0, start_z=0.0)
    pen, tags, late = check_time_window_constraint(
        [tour], tasks, uavs, current_time=0.0, service_time=0.0
    )
    assert pen == 40.0  # 500m / 10 = 50s > latest 10 → 40
    assert tags == ["tw_task_0"]
    assert late == {0: 40.0}


def test_coop_window_optional_fields():
    # 支持参考项目的 time_window 元组写法
    tasks = {0: Task(id=0, x=0.0, y=500.0, z=0.0)}
    tasks[0].time_window = (None, 10.0)  # type: ignore[attr-defined]
    uavs = {0: _uav(0, speed=10.0)}
    tour = UAVTour(uav_id=0, task_ids=[0], end_nest_id=0,
                   start_x=0.0, start_y=0.0, start_z=0.0)
    pen, tags, late = check_time_window_constraint([tour], tasks, uavs)
    assert pen == 40.0 and tags == ["tw_task_0"]
    assert late == {0: 40.0}


def test_coop_sequence_and_sync_inert():
    tasks = {0: Task(id=0, x=0.0, y=0.0, z=0.0)}
    tour = UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)
    assert check_sequence_constraint([tour], tasks) == (0.0, [])
    assert check_sync_constraint([tour], tasks) == (0.0, [])


def test_evaluator_report():
    r = ConstraintReport()
    r.add_range("range_uav_0", 20.0)
    r.add_window(["tw_task_0"], 40.0, lateness={0: 40.0})
    r.add_misc("idle_uav_1", 100.0)
    assert r.total_violation == 160.0
    assert r.violation_breakdown()["range"] == 20.0
    assert not r.is_feasible
    d = r.as_details()
    assert d["penalty"] == 160.0
    assert d["violations"] == ["range_uav_0", "tw_task_0", "idle_uav_1"]
    assert "nest_capacity" in d


def _tiny_problem():
    tasks = [Task(id=0, x=0.0, y=500.0, z=0.0, reward=5.0, latest=10.0)]
    uavs = [_uav(0, remaining=1000.0, speed=10.0, nest_id=0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    # 固定航次代价，使范围约束不触发（仅时间窗约束生效）
    pairwise = {(-1, 0): 100.0, (0, 1_000_000): 100.0}
    return SelectiveMUASProblem.from_entities(
        tasks, uavs, nests, pairwise_cost=pairwise
    )


def test_check_feasible_window_violation():
    prob = _tiny_problem()
    tour = UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)
    sol = AllocationSolution(
        tours=[tour], candidate_task_ids=[0], executed_task_ids=[0]
    )
    ok, details = prob.check_feasible(sol)
    # 2026-10-18 硬/软分桶：时间窗迟到是软违约——物理可行（ok=True），
    # 迟到量经 violations / penalty / soft_violation 记录，由 T2 层计价。
    assert ok is True
    assert "tw_task_0" in details["violations"]
    # 仅时间窗违背：窗口惩罚 = 40，其余为 0
    assert details["penalty"] == 40.0
    assert details["soft_violation"] == 40.0
    assert details["n_hard"] == 0
    assert details["nest_capacity"]["violations"] == []


def test_check_feasible_empty_feasible():
    prob = _tiny_problem()
    ok, details = prob.check_feasible(prob.empty_solution())
    assert ok
    assert details["penalty"] == 0.0
    assert details["violations"] == []


def _bottleneck_problem():
    """单 UAV：起点(0,0) → 任务(500,0) → 终点机巢(0,0)，speed=10。

    航迹时间 = 500/10 + 500/10 = 100s；tour_time 校验统一口径。
    pairwise 显式设段代价=500，使几何与代价一致。
    """
    tasks = [Task(id=0, x=500.0, y=0.0, z=0.0, reward=5.0)]
    uavs = [_uav(0, remaining=10000.0, speed=10.0, nest_id=0)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    pairwise = {(-1, 0): 500.0, (0, 1_000_000): 500.0}
    cfg = MUASProblemConfig(big_m=1.0)  # 固定字典序权重=1，锁公式口径
    return SelectiveMUASProblem.from_entities(
        tasks, uavs, nests, pairwise_cost=pairwise, config=cfg
    )


def _one_task_solution() -> AllocationSolution:
    return AllocationSolution(
        tours=[UAVTour(uav_id=0, task_ids=[0], end_nest_id=0)],
        candidate_task_ids=[0],
        executed_task_ids=[0],
    )


def test_tour_time_deterministic():
    prob = _bottleneck_problem()
    sol = _one_task_solution()
    prob.evaluate(sol)
    # 航迹时间 = 100s（往返各 500m / 10m/s），tour_time 走统一 DEM 口径
    assert abs(prob.tour_time(sol.tours[0]) - 100.0) < 1e-6
    # 无窗/无容量违背
    assert sol.feasible


def main() -> None:
    test_single_range()
    test_single_time()
    test_coop_window()
    test_coop_window_optional_fields()
    test_coop_sequence_and_sync_inert()
    test_evaluator_report()
    test_check_feasible_window_violation()
    test_check_feasible_empty_feasible()
    test_tour_time_deterministic()
    test_scalar_fitness_bottleneck_term()
    print("OK: constraints package tests passed")


if __name__ == "__main__":
    main()
