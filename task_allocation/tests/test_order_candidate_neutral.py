# -*- coding: utf-8 -*-
"""``order_candidate_mode="neutral"`` 中性对照的契约测试（审查 P1 回归）。

锁定的契约：

    ``_best_order`` 的零迟到早退**仅对 gene 臂生效**。neutral 臂的语义是
    「基因原序不是候选」——零迟到时若仍走早退快返回，等于把基因序泄漏回
    对照臂，gene vs neutral 的配对对照会被系统性抹平（exp01 常态零迟到，
    泄漏正是在主流量路径上发生）。neutral 臂必须一律落入一般锦标赛，
    由 edd / earliest / slack / nn 候选裁决。

场景（2026-11-05 审查 P1 修复后搜索出的判别几何）：
    任务 t0(-24.9,-1.6, earliest=300) / t1(-25.3,21.4, earliest=700) /
    t2(26.7,-28.2, earliest=0)，时间窗全开（零迟到）。
    基因序 [0,1,2] 航程 158.65；最近邻恰为 [0,1,2]（不触发 nn 采纳）；
    而锦标赛候选 [2,0,1]（earliest 升序）航程 153.03 **严格更短**。
    ⇒ neutral 臂必须返回 [2,0,1]；泄漏 bug 会返回 [0,1,2]。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.config import ConstraintConfig  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000


def _task(tid: int, x: float, y: float, earliest: float = 0.0) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=5.0,
                earliest=earliest, latest=1e7, arrival_time=0.0)


def _uav(uid: int) -> UAV:
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, remaining_range=1e7,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


def _nest(nid: int) -> Nest:
    return Nest(id=nid, x=0.0, y=0.0, z=0.0, capacity=8)


def _pairwise(tasks, uavs, nests):
    pts = {t.id: t.position for t in tasks}
    pts.update({-(1 + u.id): u.position for u in uavs})
    pts.update({NEST_KEY + n.id: n.position for n in nests})
    keys = list(pts)
    return {
        (a, b): math.dist(pts[a], pts[b])
        for i, a in enumerate(keys) for b in keys[i + 1:]
    }


# 判别几何：见模块 docstring
_T0 = (-24.9, -1.6, 300.0)
_T1 = (-25.3, 21.4, 700.0)
_T2 = (26.7, -28.2, 0.0)


def _mk(*, order_candidate_mode: str, reorder_range_optimize: bool = False):
    tasks = [
        _task(0, _T0[0], _T0[1], earliest=_T0[2]),
        _task(1, _T1[0], _T1[1], earliest=_T1[2]),
        _task(2, _T2[0], _T2[1], earliest=_T2[2]),
    ]
    uavs = [_uav(0)]
    nests = [_nest(0)]
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(
            w_violation=0.01, constraints=ConstraintConfig(time_window=True),
        ),
    )
    ev = MUASAssignmentEvaluator(
        prob, participating=list(uavs), tasks=list(tasks), nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(
            order_candidate_mode=order_candidate_mode,
            reorder_range_optimize=reorder_range_optimize,
        ),
    )
    return ev


def _plen(seq) -> float:
    pos = {0: _T0[:2], 1: _T1[:2], 2: _T2[:2]}
    prev, s = (0.0, 0.0), 0.0
    for tid in seq:
        s += math.dist(prev, pos[tid])
        prev = pos[tid]
    return s + math.dist(prev, (0.0, 0.0))


def test_neutral_zero_lateness_enters_tournament():
    """neutral 臂零迟到不得快返回基因序——必须进锦标赛取更优序（P1 核心）。

    基因序 [0,1,2] 航程 158.65，最近邻恰为 [0,1,2]（不触发 nn 采纳路径）；
    锦标赛候选 [2,0,1] 航程 153.03 严格更短 ⇒ 必须返回 [2,0,1]。
    泄漏 bug 会直接返回 [0,1,2]。
    """
    ev = _mk(order_candidate_mode="neutral")
    seq = list(ev._best_order(0, [0, 1, 2]))
    assert seq == [2, 0, 1], (
        f"neutral 臂零迟到应进一般锦标赛取 [2,0,1]（153.03 < 158.65），"
        f"实际返回 {seq}（{_plen(seq):.2f}）——基因序泄漏回对照臂？"
    )


def test_neutral_zero_lateness_reorder_on_also_tournament():
    """reorder_range_optimize=True 不得让 neutral 臂走 nn 早退/快返回。"""
    ev = _mk(order_candidate_mode="neutral", reorder_range_optimize=True)
    seq = list(ev._best_order(0, [0, 1, 2]))
    assert seq == [2, 0, 1], (
        f"neutral + reorder_on 同样必须进锦标赛，实际 {seq}"
    )


def test_gene_zero_lateness_early_exit_unchanged():
    """gene 臂零迟到早退行为不变（对照：修复不得波及生产路径）。"""
    ev = _mk(order_candidate_mode="gene")
    seq = list(ev._best_order(0, [0, 1, 2]))
    assert seq == [0, 1, 2], (
        f"gene 臂零迟到应保持早退原序 [0,1,2]，实际 {seq}"
    )


def test_neutral_lateness_tournament_unchanged():
    """基因序带迟到时 neutral 臂本就走锦标赛——行为保持。"""
    # t1 latest 收紧到使基因序 [0,1,2] 必迟到；此时两条路径都进锦标赛。
    tasks = [
        _task(0, _T0[0], _T0[1], earliest=_T0[2]),
        Task(id=1, x=_T1[0], y=_T1[1], z=0.0, reward=5.0,
             earliest=_T1[2], latest=300.0, arrival_time=0.0),
        _task(2, _T2[0], _T2[1], earliest=_T2[2]),
    ]
    uavs, nests = [_uav(0)], [_nest(0)]
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(
            w_violation=0.01, constraints=ConstraintConfig(time_window=True),
        ),
    )
    ev = MUASAssignmentEvaluator(
        prob, participating=list(uavs), tasks=list(tasks), nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(order_candidate_mode="neutral"),
    )
    seq = list(ev._best_order(0, [0, 1, 2]))
    assert seq != [0, 1, 2], (
        f"基因序已迟到，锦标赛不应原样返回 [0,1,2]，实际 {seq}"
    )
