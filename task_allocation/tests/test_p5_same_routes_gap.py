# -*- coding: utf-8 -*-
"""P5 exp11：同解换终点反事实的指标口径测试。

守护 ``exp11_multi_depot/metrics.py`` 的三条算术：
1. ``same_routes_gap_m`` = Σreturn_leg_if_home_m − Σreturn_leg_m（不改路径、只换终点）；
2. ``same_routes_gap_per_sortie_m`` 以**航次数**为分母（与主口径一致）；
3. ``n_cycles_home_overflow`` 只统计「home 分配本身超容」的周期数，
   它把该差额限定为**费用标量**，不能当可行性证据。

该字段由 ``stage_runner`` 的纯观测诊断提供，不参与任何采纳/约束判定；
本测试只钉住实验层的聚合口径，防止后续改名或漏项。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.experiments.exp11_multi_depot.metrics import (  # noqa: E402
    collect_run_metrics,
)
from task_allocation.methods.rolling.horizon import CycleRecord  # noqa: E402


def _run(leg: float, if_home: float, overflow: int, n_end: int = 1):
    rec = CycleRecord(
        cycle=1,
        current_time=0.0,
        muas={
            "final_path_m": 1000.0,
            "final_fitness": 100.0,
            "final_n_hard": 0,
            "return_leg_m": leg,
            "return_leg_if_home_m": if_home,
            "home_endpoint_overflow": overflow,
            "end_nests": {str(i): 1 for i in range(n_end)} if n_end else {},
            "cost_polish_accept": {"mode": "path", "accepted": False, "drift": False},
            "cost_polish_accept_mode": "path",
        },
    )
    task = SimpleNamespace(meta={"completed_at": 1.0})
    pool = SimpleNamespace(finished_tasks=lambda: [task])
    batch = SimpleNamespace(all_tasks=lambda: [task])
    return collect_run_metrics(
        [rec], pool=pool, batch=batch, seed=7, mode="path",
        wall_seconds=1.0, initial_home={0: 0},
    )


def test_gap_is_cost_of_forcing_home_on_same_routes():
    m = _run(leg=200.0, if_home=800.0, overflow=0)
    assert m["total_return_leg_m"] == 200.0
    assert m["total_return_leg_if_home_m"] == 800.0
    assert m["same_routes_gap_m"] == 600.0
    assert m["same_routes_gap_by_cycle"] == [600.0]
    # 1 个航次 ⇒ 每航次差 = 总差
    assert m["same_routes_gap_per_sortie_m"] == 600.0


def test_home_arm_gap_is_zero_by_construction():
    m = _run(leg=200.0, if_home=200.0, overflow=0)
    assert m["same_routes_gap_m"] == 0.0
    assert m["same_routes_gap_per_sortie_m"] == 0.0


def test_home_overflow_cycles_are_counted_not_hidden():
    m = _run(leg=200.0, if_home=900.0, overflow=2)
    assert m["n_cycles_home_overflow"] == 1
    # 超容不改变费用差额的算术，只改变它可否被当作可行性证据
    assert m["same_routes_gap_m"] == 700.0


def test_gap_per_sortie_divides_by_sorties():
    m = _run(leg=600.0, if_home=1200.0, overflow=0, n_end=2)
    assert m["n_sorties"] == 2
    assert m["same_routes_gap_per_sortie_m"] == 300.0
