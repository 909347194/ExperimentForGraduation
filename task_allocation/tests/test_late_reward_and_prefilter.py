# -*- coding: utf-8 -*-
"""「迟到不是废飞」协议（2026-10-15）的两处落地聚焦测试。

协议内容（用户裁定）：
  ① 迟到交付算完成，但有效奖励按迟到时长线性折减
     ``eff = R × max(0, 1 − L/W)``（W = late_reward_decay_seconds）；
  ② 选择期预筛只拦物理不可达（往返航程超限/无机可用）——时间窗不再是
     拒绝理由，迟到完全由适应度（折减 + w_violation）裁决；时间窗预筛
     仅在 check_time_window=True 的消融口径保留；
  ③ 完成闸门只拦物理不可行（航程/续航/终点机巢/结构类）——见
     test_completion_gate.py。

本文件锁定 ①② 的数值口径；③ 在 test_completion_gate.py。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(str(REPO))

from task_allocation.common.types import (  # noqa: E402
    Nest,
    ScoredTask,
    Task,
    UAV,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
    build_cost_provider,
)
from task_allocation.methods.selection.feasibility import (  # noqa: E402
    FeasibilityConfig,
    prefilter,
)


# ---------------------------------------------------------------------------
# ① 迟到奖励折减（F1 口径）
# ---------------------------------------------------------------------------


def _problem(late_decay, latest, **extra):
    """1 机 / 1 任务：直线 100m、速度 10 → 到达 10s。

    latest=5 → 迟到 5s（tw_task_0，lateness={0: 5.0}）。
    """
    tasks = [
        Task(id=0, x=0.0, y=100.0, z=0.0, reward=4.0,
             arrival_time=0.0, earliest=0.0, latest=latest)
    ]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=1e6,
            speed=10.0, nest_id=0)
    ]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4, available=True)]
    return SelectiveMUASProblem(
        tasks=tasks,
        uavs=uavs,
        nests=nests,
        costs=build_cost_provider(tasks, nests, uavs),
        config=MUASProblemConfig(
            lambda_cost=1e-4,
            w_penalty=1.0,
            w_violation=0.01,
            big_m=1.0,  # 锁字典序权重，公式数值口径确定
            late_reward_decay_seconds=late_decay,
            **extra,
        ),
    )


def _fitness(p):
    sol = p.solution_from_assignment([(0, 0)])
    p.evaluate(sol)
    return p.f1_reward(sol), p.scalar_fitness(sol), sol


def test_late_reward_decay_linear():
    """迟到 5s：W=10 → eff=4×(1−0.5)=2；W=None → 历史全额 4。"""
    f1_dec, _, _ = _fitness(_problem(10.0, latest=5.0))
    assert f1_dec == 2.0, f1_dec
    f1_full, _, _ = _fitness(_problem(None, latest=5.0))
    assert f1_full == 4.0, f1_full


def test_late_reward_decay_clamped_at_zero():
    """迟到 ≥ W → 折减到 0（不出现负奖励）。"""
    f1, _, _ = _fitness(_problem(5.0, latest=5.0))
    assert f1 == 0.0, f1
    f1b, _, _ = _fitness(_problem(2.0, latest=5.0))
    assert f1b == 0.0, f1b


def test_on_time_reward_undiscounted():
    """按时交付（latest=1000 > 到达 10s）不折减，任何 W 下全额。"""
    for w in (None, 10.0, 2.0):
        f1, _, _ = _fitness(_problem(w, latest=1000.0))
        assert f1 == 4.0, (w, f1)


def test_fitness_uses_discounted_reward():
    """覆盖个数第一键（默认）：F1 不进搜索主阶梯，适应度不随折减变化。

    消融口径（coverage_count_first=False）：适应度必须吃折减后的 F1：
    同解仅 W 不同 → 差 = -(2−4) = +2。
    """
    # 默认（覆盖个数第一键）：折减只动 F1 指标，不动适应度
    _, f_dec_new, _ = _fitness(_problem(10.0, latest=5.0))
    _, f_full_new, _ = _fitness(_problem(None, latest=5.0))
    assert abs(f_dec_new - f_full_new) < 1e-9, (f_dec_new, f_full_new)
    # 消融（−m·F1_eff 旧口径）：适应度吃折减 F1
    _, f_dec, _ = _fitness(
        _problem(10.0, latest=5.0, coverage_count_first=False)
    )
    _, f_full, _ = _fitness(
        _problem(None, latest=5.0, coverage_count_first=False)
    )
    assert abs((f_dec - f_full) - 2.0) < 1e-9, (f_dec, f_full)


# ---------------------------------------------------------------------------
# ② 选择期预筛：只拦物理不可达（用户原则 2026-10-15）
# ---------------------------------------------------------------------------


def _scored(latest):
    return ScoredTask(
        task=Task(id=0, x=100.0, y=0.0, z=0.0, reward=5.0,
                  earliest=0.0, latest=latest, arrival_time=0.0)
    )


def _uav(rng=1e5, speed=10.0):
    return UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=rng, speed=speed)


def test_prefilter_never_rejects_on_time_by_default():
    """默认口径：无论多晚都不因时间窗拒绝——迟到由适应度折减定价。

    ETA 500s、latest=100（迟到 400s，W=600 内奖励折减 33%）仍放行；
    用户原则「任务不被完成只有物理约束被违反」，时间窗不是物理约束。
    """
    s = _scored(latest=100.0)
    cfg = FeasibilityConfig(current_time=0.0)
    assert cfg.check_time_window is False
    passed, rejected = prefilter([s], [_uav()], cfg, cost_hint={0: 5000.0})
    assert len(passed) == 1 and not rejected


def test_prefilter_zero_reward_late_task_still_admitted():
    """迟到 ≥ W（奖励清零）的任务同样放行：值不值得服由适应度裁决，
    预筛不代劳。物理可达就没有拒绝理由。"""
    s = _scored(latest=100.0)  # ETA 500s → 迟到 400s；W=300 时奖励已清零
    cfg = FeasibilityConfig(current_time=0.0)
    passed, rejected = prefilter([s], [_uav()], cfg, cost_hint={0: 5000.0})
    assert len(passed) == 1 and not rejected


def test_prefilter_rejects_round_trip_range_only():
    """唯一新增的物理拒绝：往返航程超限（2×单程 > margin×remaining）。"""
    s = _scored(latest=1e6)
    cfg = FeasibilityConfig(current_time=0.0)
    # 单程 5000m、满电 8000m：往返 10000 > 0.95×8000=7600 → 拒
    passed, rejected = prefilter([s], [_uav(rng=8000.0)], cfg, cost_hint={0: 5000.0})
    assert not passed
    assert rejected[0].filter_reason == "exceed_round_trip_range"
    # 单程 3000m：往返 6000 ≤ 7600 → 放行
    s2 = _scored(latest=1e6)
    passed2, rejected2 = prefilter(
        [s2], [_uav(rng=8000.0)], cfg, cost_hint={0: 3000.0}
    )
    assert len(passed2) == 1 and not rejected2


def test_prefilter_ablation_time_gate_still_available():
    """消融口径（check_time_window=True）保留时间窗拒绝，W 边界同旧义：
    ETA 500s、latest=100、W=300 → 500 > 100+300 → 拒。"""
    s = _scored(latest=100.0)
    cfg = FeasibilityConfig(
        current_time=0.0, check_time_window=True, late_tolerance_seconds=300.0
    )
    passed, rejected = prefilter([s], [_uav()], cfg, cost_hint={0: 5000.0})
    assert not passed
    assert rejected[0].filter_reason == "miss_time_window"


def test_prefilter_ablation_time_gate_passes_tight_task():
    """消融口径下「紧但可能迟到」仍放行：ETA 500 ≤ latest 100 + W 600。"""
    s = _scored(latest=100.0)
    cfg = FeasibilityConfig(
        current_time=0.0, check_time_window=True, late_tolerance_seconds=600.0
    )
    passed, rejected = prefilter([s], [_uav()], cfg, cost_hint={0: 5000.0})
    assert len(passed) == 1 and not rejected
