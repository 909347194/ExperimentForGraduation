# -*- coding: utf-8 -*-
"""exp01 端到端边界案例集（定位：正确性验证 + 口径标定）。

与 test_repair_reflow.py（修复链内部机制）、test_fitness_tiers.py
（适应度公式）互补，本文件锁**跨层边界**——单层测试各自正确、
层间接线错误时暴露的那些契约：

1. **权重快照落盘链**（2026-10-18 新增，治 auto big_m 漂移）：
   ``problem.evaluate → final.meta → stage.diagnostics → record.muas``
   一路透出，且 m/λ 与配置一致——审计 N 项的数据前提；
2. **边际可行正例**（T105 实证：往返 79,865m 仅剩 135m 余量被完成）：
   往返恰在满电内必须完成，不得被 margin 误杀；超出满电的走
   UNREACHABLE（物理口径），不是淘汰；
3. **动态余量不足留池**：remaining_range 装不下但满电装得下 →
   预筛拒（本周期不完成），任务留池等下周期，不记不可达；
4. **迟到交付全链**（迟到≠废飞协议）：迟到任务 completed ✓、
   infeasible_ids 不含 ✓、lateness_by_task 记录 ✓、闸门不弹回 ✓。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    Nest,
    Task,
    UAV,
    UAVStatus,
)
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig  # noqa: E402
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig  # noqa: E402
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    HorizonConfig,
    RollingHorizon,
)
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


def _nest(nid: int, x: float, capacity: int = 4) -> Nest:
    return Nest(id=nid, x=x, y=0.0, z=0.0, capacity=capacity, available=True)


def _task(tid: int, x: float, latest: float = 1e6) -> Task:
    return Task(
        id=tid, x=x, y=0.0, z=0.0, reward=5.0,
        arrival_time=0.0, earliest=0.0, latest=latest,
    )


def _mini_horizon(
    max_range: float = 80_000.0,
    remaining_range: float | None = None,
    speed: float = 10.0,
    *,
    late_reward_decay_seconds: float | None = 600.0,
    pop_size: int = 20,
    max_generations: int = 30,
) -> RollingHorizon:
    """1 巢 1 机；欧氏距离（无 DEM/estimator）。"""
    nests = [_nest(0, 0.0)]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0,
            remaining_range=(max_range if remaining_range is None
                             else remaining_range),
            speed=speed, nest_id=0, status=UAVStatus.IDLE)
    ]
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    return RollingHorizon(
        pool, fleet, nests, dem=None, estimator=None,
        horizon_config=HorizonConfig(
            n_cycles=1, cycle_length=600.0, complete_after_cycle=True,
            recharge_at_nest=True, uav_max_range=max_range,
        ),
        problem_config=MUASProblemConfig(
            lambda_cost=1.0e-4,
            w_violation=0.01,
            late_reward_decay_seconds=late_reward_decay_seconds,
        ),
        stage_config=MUASStageConfig(prune_unprofitable=True, lamarckian=True),
        solver_config=DMDEConfig(pop_size=pop_size,
                                 max_generations=max_generations, seed=42),
        event_config=EventTriggerConfig(),
        nest_offset=1_000_000,
    )


# ---------------------------------------------------------------------------
# 1) 权重快照落盘链（审计 N 项的数据前提）
# ---------------------------------------------------------------------------


def test_fitness_weights_flow_to_record_muas():
    """stage.diagnostics 必须带 fitness_weights（m/W_hard/w_late/λ/W_decay）。

    链路：``problem.evaluate`` 写 ``final.meta["fitness_weights"]`` →
    muas_stage 透出到 diagnostics → horizon 落到 ``record.muas`` →
    metrics.build_solution 写 solution.json 的 cycle。断任何一环，
    cost_history 的绝对值就不可独立重算（auto big_m 漂移复发）。
    """
    horizon = _mini_horizon()
    records = horizon.run(n_cycles=1, arrivals={1: [_task(0, x=10_000.0)]})
    rec = records[0]
    fw = rec.muas.get("fitness_weights")
    assert isinstance(fw, dict) and fw, (
        f"record.muas 缺 fitness_weights 快照：muas keys={sorted(rec.muas)}"
    )
    # 与配置一致（m 由 f1_weight 自动推导，只需存在且为正）
    assert fw.get("m", 0.0) > 0.0, f"m 应为正：{fw}"
    assert fw.get("lambda") == 1.0e-4, f"λ 口径漂移：{fw}"
    # 门禁口径（唯一主口径）：F1 纯完成计数、迟到不折减 ⇒ W_decay 快照为 None
    assert fw.get("W_decay") is None, f"门禁口径 W_decay 应为 None（F1 不折减）：{fw}"
    assert fw.get("w_late") == 0.01, f"w_late 口径漂移：{fw}"
    assert fw.get("w_hard", 0.0) > 0.0, f"W_hard 应为正：{fw}"


def test_fitness_weights_snapshot_matches_scalarization():
    """快照权重重算的末代 fitness ≡ 记录 best_fitness（N 项同款）。"""
    horizon = _mini_horizon()
    records = horizon.run(n_cycles=1, arrivals={1: [_task(0, x=10_000.0)]})
    rec = records[0]
    fw = rec.muas["fitness_weights"]
    assert fw, "应有权重快照"
    # 从 record 直接取：cost_history[-1] 与 muas.best_fitness 必须一致
    hist = rec.cost_history
    assert hist, "应有收敛轨迹"
    assert abs(hist[-1] - rec.muas["best_fitness"]) < 1e-3, (
        f"cost_history 末值 {hist[-1]} 与 muas.best_fitness "
        f"{rec.muas['best_fitness']} 不一致——两个落盘口径分裂"
    )


# ---------------------------------------------------------------------------
# 2) 边际可行正例（T105 实证）：往返恰在满电内必须完成
# ---------------------------------------------------------------------------


def test_marginal_feasible_round_trip_completed():
    """往返 78km ≤ 满电 80km → 完成；往返 82km > 80km → UNREACHABLE。

    T105 实证（2026-10-15）：往返 79,865m、剩余航程仅剩 135m 余量被
    完成；margin=0.95 曾把它误杀，修正 margin=1.0 后放行。本测试锁
    「恰好可及不被误杀、超限走物理不可达」的双边界。
    """
    horizon = _mini_horizon(max_range=80_000.0)
    near = _task(0, x=39_000.0)   # 往返 78km ≤ 80km → 可达
    far = _task(1, x=41_000.0)    # 往返 82km > 80km → 不可达
    records = horizon.run(n_cycles=1, arrivals={1: [near, far]})
    rec = records[0]
    assert 0 in rec.completed_ids, (
        f"边际可达任务被误杀：completed={rec.completed_ids}"
    )
    assert rec.unreachable_ids == [1], (
        f"超满电任务应判 UNREACHABLE：{rec.unreachable_ids}"
    )
    # 物理口径：不可达不是淘汰、不是废飞
    assert 1 not in rec.overdue_ids
    assert 1 not in rec.infeasible_ids


def test_dynamic_shortfall_defers_without_marking_unreachable():
    """余量不足（remaining < 往返 < 满电）→ 留池，不记不可达、不完成。

    与静态不可达的分层：静态判「永远不可达」（所有巢往返 > 满电），
    动态余量不足是「本周期装不下、满电后可能可飞」——预筛拒之，
    任务留池等下周期。这正是 T105「仅剩 135m 余量被完成」的另一半。
    """
    horizon = _mini_horizon(max_range=80_000.0, remaining_range=60_000.0)
    t = _task(0, x=39_000.0)  # 往返 78km：> 60km 余量、≤ 80km 满电
    records = horizon.run(n_cycles=1, arrivals={1: [t]})
    rec = records[0]
    assert 0 not in rec.completed_ids, "余量不足不应完成"
    assert rec.unreachable_ids == [], "满电可及 ≠ 静态不可达"
    assert 0 not in rec.overdue_ids, "不得记淘汰"
    assert 0 in rec.deferred_ids, "应留在池中（deferred）"


# ---------------------------------------------------------------------------
# 3) 迟到交付全链（迟到≠废飞协议）
# ---------------------------------------------------------------------------


def test_late_delivery_full_chain():
    """迟到任务：completed ✓、不弹回 ✓、lateness 记录 ✓、奖励折减 ✓。

    几何：任务 30km（往返 60km ≤ 满电 80km，可达），speed=1 m/s →
    飞行 30,000s，latest=1,000s ⇒ 迟到 29,000s（≫ W=600s，eff=0）
    ——最深的迟到也走完成通道。
    """
    horizon = _mini_horizon(speed=1.0)
    late = _task(0, x=30_000.0, latest=1_000.0)
    records = horizon.run(n_cycles=1, arrivals={1: [late]})
    rec = records[0]
    # ① 完成（迟到≠废飞）
    assert 0 in rec.completed_ids, "迟到任务必须计入完成"
    # ② 闸门不弹回（不是 infeasible_delivery/废飞）
    assert 0 not in rec.infeasible_ids, "迟到不得触发物理弹回"
    # ③ 迟到秒数记录（评价—执行一致，供审计 D/N 项）
    lb = {int(k): v for k, v in (rec.lateness_by_task or {}).items()}
    assert lb.get(0, 0.0) > 1_000.0, f"迟到秒数未记录或过小：{lb}"
    # ④ 迟到的奖励折减记在任务 meta（eff=0 时交付端全额归零）
    pool = horizon.pool
    finished = {t.id: t for t in pool.finished_tasks()}
    assert 0 in finished, "任务应已从活动池出账"
    assert float(finished[0].meta.get("lateness_seconds", 0.0)) > 1_000.0


def test_on_time_delivery_not_flagged_late():
    """对照：余量充足的任务不得被误记迟到（防「一律记迟到」反向错）。"""
    horizon = _mini_horizon(speed=10.0)
    ok = _task(0, x=10_000.0, latest=1e6)  # 10km → 1000s ≪ latest
    records = horizon.run(n_cycles=1, arrivals={1: [ok]})
    rec = records[0]
    assert 0 in rec.completed_ids
    assert rec.late_ids == [], f"按时任务被误记迟到：{rec.late_ids}"
    assert not rec.lateness_by_task
