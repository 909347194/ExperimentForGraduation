# -*- coding: utf-8 -*-
"""完成反馈的可行性闸门 + 惩罚量纲解耦（P0 / P1）的聚焦测试。

背景（审查发现的两个缺陷）：

  P0  滚动层 ``_advance_execution`` 对所有绑定任务无条件 ``pool.complete()``，
      既不读 ``solution.feasible`` 也不看违约标签 → 违约任务照样标 DONE，
      完成率指标说谎。本测试锁定「物理不可行任务不顺延完成、解绑回池、
      下周期重选」。

  2026-10-15 协议纠正「迟到不是废飞」：时间窗违约（tw_task）不再是硬
  违约——晚到任务正常完成（计入完成/吞吐，有效奖励已在适应度里按迟到
  折减），迟到秒数记入 ``record.late_ids`` / ``lateness_by_task``；
  闸门只拦截物理不可行（航程 / 续航 / 终点机巢 / 结构类）的交付。

  P1  ``scalar_fitness`` 把秒级约束惩罚与米级路径代价混进同一个 F2 再乘
      λ=1e-4，惩罚被压没 → 软约束形同虚设。本测试锁定 ``w_violation``
      把惩罚以「奖励当量 / 惩罚单位」直接计入适应度、不再乘 λ。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    AllocationSolution,
    Nest,
    Task,
    TaskStatus,
    UAV,
    UAVStatus,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
    build_cost_provider,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig  # noqa: E402
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig  # noqa: E402
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    CycleRecord,
    HorizonConfig,
    RollingHorizon,
)
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------


def _mini_horizon(gate: bool):
    """构造最小滚动时域：1 机巢 / 1 架机 / 2 个任务。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4, available=True)]
    uavs = [
        UAV(
            id=0,
            x=0.0,
            y=0.0,
            z=0.0,
            remaining_range=1e6,
            speed=10.0,
            nest_id=0,
            status=UAVStatus.IDLE,
        )
    ]
    tasks = [
        Task(
            id=i,
            x=100.0 * (i + 1),
            y=0.0,
            z=0.0,
            reward=5.0,
            arrival_time=0.0,
            earliest=0.0,
            latest=1e6,
        )
        for i in range(2)
    ]
    pool = TaskPool(tasks, current_time=0.0)
    pool.begin_cycle(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    horizon = RollingHorizon(
        pool,
        fleet,
        nests,
        dem=None,
        estimator=None,
        horizon_config=HorizonConfig(
            n_cycles=1,
            cycle_length=600.0,
            complete_after_cycle=True,
            completion_feasibility_gate=gate,
        ),
        problem_config=MUASProblemConfig(),
        stage_config=MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=5, seed=1),
        event_config=EventTriggerConfig(),
        nest_offset=1_000_000,
    )
    return horizon, pool, fleet, tasks


def _solution_with_violations(uavs, tags, lateness=None):
    """手工构造「已回写执行中」的解，并注入指定违约标签与迟到量表。"""
    from task_allocation.common.types import UAVTour

    sol = AllocationSolution(
        tours=[UAVTour.from_uav(uavs[0], task_ids=[0, 1], end_nest_id=0)],
        candidate_task_ids=[0, 1],
        executed_task_ids=[0, 1],
    )
    sol.meta["feasibility"] = {
        "feasible": False,
        "violations": list(tags),
        "penalty": 100.0,
        "lateness": dict(lateness or {}),
    }
    return sol


def _run_gate(gate: bool, tags, lateness=None):
    horizon, pool, fleet, _ = _mini_horizon(gate)
    uavs = fleet.all()
    sol = _solution_with_violations(uavs, tags, lateness)
    pool.apply_solution(sol)
    fleet.assign(0, [0, 1])  # 置 BUSY 并绑定两任务
    rec = CycleRecord(cycle=1, current_time=0.0)
    horizon._advance_execution(sol, rec)
    return rec, pool, fleet


# ---------------------------------------------------------------------------
# P0：完成闸门（2026-10-15：迟到不是废飞，只拦物理不可行）
# ---------------------------------------------------------------------------


def test_gate_on_tw_late_completes_not_waste():
    """迟到（tw_task）不再是废飞：正常完成、记迟到秒数、不弹回。"""
    rec, pool, fleet = _run_gate(True, ["tw_task_1"], lateness={1: 30.0})

    assert sorted(rec.completed_ids) == [0, 1], f"迟到任务也应完成：{rec.completed_ids}"
    assert rec.infeasible_ids == [], "迟到不应触发弹回"
    assert rec.late_ids == [1], f"任务 1 应记为迟到交付：{rec.late_ids}"
    assert rec.lateness_by_task == {"1": 30.0}

    # 迟到任务：DONE、带迟到秒数、无弹回标记
    t1 = pool.find(1)
    assert t1 is not None and t1.status == TaskStatus.DONE
    assert t1.meta.get("lateness_seconds") == 30.0
    assert t1.meta.get("infeasible_delivery") is None
    assert t1.meta.get("completed_at") is not None

    # UAV 侧：两个任务都已解绑（均完成）
    assert fleet.bound_tasks(0) == []


def test_gate_on_blocks_physically_infeasible():
    """物理不可行（航程超限）仍弹回：不计完成、解绑回池、归因。"""
    rec, pool, fleet = _run_gate(True, ["range_uav_0"])

    assert rec.completed_ids == [], "物理不可行交付不应算完成"
    assert sorted(rec.infeasible_ids) == [0, 1], f"航程违约应波及整个航次：{rec.infeasible_ids}"
    assert rec.infeasible_tags.get("0") == ["range_uav_0"]

    t0 = pool.find(0)
    assert t0 is not None and t0.status != TaskStatus.DONE
    assert 0 in pool, "物理不可行任务应回到任务池（下周期按 RELEASE 参与选择）"
    assert t0.meta.get("infeasible_delivery") is True


def test_gate_mixed_tw_and_range_hard_wins():
    """同一任务同时带 tw 与 range 标签：硬性违约优先，仍弹回。"""
    rec, pool, _ = _run_gate(
        True, ["tw_task_1", "range_uav_0"], lateness={1: 12.0}
    )
    assert sorted(rec.infeasible_ids) == [0, 1]
    assert rec.completed_ids == []
    assert rec.late_ids == [], "弹回的任务不应同时记为迟到交付"


def test_gate_tw_without_lateness_map_still_completes():
    """meta 缺 lateness 表（旧存档/直构造）时迟到任务仍完成、不记迟到。"""
    rec, pool, _ = _run_gate(True, ["tw_task_1"])
    assert sorted(rec.completed_ids) == [0, 1]
    assert rec.late_ids == []
    assert pool.find(1).status == TaskStatus.DONE


def test_gate_off_keeps_legacy_behavior():
    """闸门关闭：回到历史行为（一律标完成），用于消融对比。"""
    rec, pool, _ = _run_gate(False, ["tw_task_1"])
    assert sorted(rec.completed_ids) == [0, 1]
    assert rec.infeasible_ids == []
    assert all(pool.find(i).status == TaskStatus.DONE for i in (0, 1))


def test_gate_maps_uav_level_violation():
    """UAV 级违约（航程 / 续航）归因到该航次内全部任务。"""
    rec, _, _ = _run_gate(True, ["range_uav_0"])
    assert rec.infeasible_ids == [0, 1], f"航程违约应波及整个航次：{rec.infeasible_ids}"
    assert rec.completed_ids == []


# ---------------------------------------------------------------------------
# P1：惩罚量纲解耦
# ---------------------------------------------------------------------------


def _problem(w_violation):
    tasks = [Task(id=0, x=0.0, y=100.0, z=0.0, reward=4.0, arrival_time=0.0)]
    uavs = [
        UAV(
            id=0,
            x=0.0,
            y=0.0,
            z=0.0,
            remaining_range=1e6,
            speed=10.0,
            nest_id=0,
        )
    ]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4, available=True)]
    return SelectiveMUASProblem(
        tasks=tasks,
        uavs=uavs,
        nests=nests,
        costs=build_cost_provider(tasks, nests, uavs),
        config=MUASProblemConfig(
            lambda_cost=1e-4, w_penalty=1.0, w_violation=w_violation,
            # legacy 加权和口径（2026-10-18 起四层准字典序的消融对照）：
            # lexicographic=False → f=-F1+λ·path+w_violation·penalty+w_time·F3，
            # 惩罚总量（硬+软）经 w_violation 直接计入。big_m=1 锁 m=1。
            lexicographic=False,
            big_m=1.0,  # 固定字典序权重=1，锁公式的数值口径
        ),
    )


def test_w_violation_decouples_penalty_from_lambda():
    """w_violation 给定时惩罚不再乘 λ；None 时沿用历史口径。"""
    pen = 1000.0

    def fitness(wv):
        p = _problem(wv)
        sol = p.solution_from_assignment([(0, 0)])
        p.evaluate(sol, penalty=pen)  # 显式把惩罚计入 F2 与 meta["penalty"]
        return p, sol, p.scalar_fitness(sol)

    p_leg, sol_leg, f_legacy = fitness(None)
    path = p_leg.path_cost(sol_leg)
    reward = p_leg.f1_reward(sol_leg)
    lam = 1e-4

    # 历史口径：惩罚随 F2 一起乘 λ → 1000 s 只值 0.1 个适应度单位
    assert abs(f_legacy - (-reward + lam * (path + pen))) < 1e-9, f_legacy

    # w_violation=0：惩罚完全不计（仅剩路径代价）
    _, _, f_zero = fitness(0.0)
    assert abs(f_zero - (-reward + lam * path)) < 1e-9, f_zero

    # w_violation=0.01：惩罚直接计入，1000 s → 10 个奖励当量（不再被 λ 压没）
    _, _, f_dec = fitness(0.01)
    assert abs(f_dec - (-reward + lam * path + 0.01 * pen)) < 1e-9, f_dec
    # DMDE 是最小化器：惩罚更重 → 适应度更大（= 更差），软约束才真正有拉力
    assert f_dec > f_legacy, "解耦后惩罚应显著抬高适应度（最小化器下即更差）"
    # 量化对照：1000 s 违约在历史口径下只值 0.1，解耦后值 10（相差两个量级）
    assert abs((f_dec - f_legacy) - (0.01 * pen - lam * pen)) < 1e-9
