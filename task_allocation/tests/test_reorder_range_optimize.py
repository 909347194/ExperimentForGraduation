# -*- coding: utf-8 -*-
"""重排期的航程（成本）优化 —— 2026-10-24 修复的常驻回归测试。

锁定的契约（历史缺陷，必须有常驻测试守住）：

    ``_best_order`` 的候选判据是 ``(迟到数, 迟到量, 航程)`` 字典序，**航程排第 3**。
    原早退写成「原序零迟到 → 直接 ``return list(tids)``」，等于把第 3 位判据整段
    作废：只要原序不迟到，航程就一字不动。而 exp01 各周期 ``final_late_s`` 常态
    为 0 ⇒ **航程从未被优化过**。

实测后果（见 docs/fitness_path_optimization_diagnosis_2026-10-24.md §3）：
    polish 增益 231,486 全部来自 F1（救回超期任务），反证若来自路径需
    ``Δpath`` 达 154 万 km，而实测总航程仅 435 km；搜索期 100 代里路径对总
    改进的贡献上限只有 1.12%。

修复语义 = **覆盖优先、成本随后**：执行集不变（F1 不动）、不引入迟到、不超
剩余航程，只在「仍零迟到 且 航程严格更短」时采纳最近邻序。因此不触碰主
适应度阶梯，可与任意口径（含覆盖门禁）叠加。
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


def _task(tid: int, x: float, y: float = 0.0, *, latest: float = 1e6) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=5.0,
                earliest=0.0, latest=latest, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0, y: float = 0.0) -> UAV:
    return UAV(id=uid, x=x, y=y, z=0.0, remaining_range=1e7,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


def _nest(nid: int, x: float = 0.0, y: float = 0.0) -> Nest:
    return Nest(id=nid, x=x, y=y, z=0.0, capacity=8)


def _pairwise(tasks, uavs, nests):
    pts = {t.id: t.position for t in tasks}
    pts.update({-(1 + u.id): u.position for u in uavs})
    pts.update({NEST_KEY + n.id: n.position for n in nests})
    keys = list(pts)
    return {
        (a, b): math.dist(pts[a], pts[b])
        for i, a in enumerate(keys) for b in keys[i + 1:]
    }


def _mk(tasks, uavs, nests, *, stage_cfg=None):
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(
            w_violation=0.01, w_time=0.0,
            constraints=ConstraintConfig(time_window=True),
        ),
    )
    ev = MUASAssignmentEvaluator(
        prob, participating=list(uavs), tasks=list(tasks), nests=list(nests),
        fleet_uavs=list(uavs), config=stage_cfg or MUASStageConfig(),
    )
    return prob, ev


def _path_of(seq, tasks, start=(0.0, 0.0, 0.0), nest=(0.0, 0.0, 0.0)) -> float:
    """外出链 + 返航腿（与 _best_order._evaluate 同口径）。"""
    pos = {t.id: t.position for t in tasks}
    length = 0.0
    prev = start
    for tid in seq:
        length += math.dist(prev, pos[tid])
        prev = pos[tid]
    return length + math.dist(prev, nest)


# ── 场景一：一维直线，原序是「跳跃序」，最近邻明显更短 ────────────────

def test_zero_lateness_still_optimizes_range():
    """零迟到时也必须压航程 —— 早退不得把航程一起退掉（本修复的核心）。

    UAV 在 x=0，任务 t0/t1/t2 分别在 x=30/10/20，时间窗全开（零迟到）。
    原序 [0,1,2] 外出 30+20+10=60、返航 20 ⇒ 80；
    最近邻 [1,2,0] 外出 10+10+10=30、返航 30 ⇒ 60。
    """
    tasks = [_task(0, 30.0), _task(1, 10.0), _task(2, 20.0)]
    uavs = [_uav(0)]
    nests = [_nest(0)]

    _, ev_on = _mk(tasks, uavs, nests,
                   stage_cfg=MUASStageConfig(reorder_range_optimize=True))
    _, ev_off = _mk(tasks, uavs, nests,
                    stage_cfg=MUASStageConfig(reorder_range_optimize=False))

    seq_off = ev_off._best_order(0, [0, 1, 2])
    seq_on = ev_on._best_order(0, [0, 1, 2])

    assert list(seq_off) == [0, 1, 2], (
        f"修复前（开关关闭）应逐位保持早退原序，实际 {list(seq_off)}"
    )
    assert list(seq_on) == [1, 2, 0], (
        f"修复后应采纳最近邻序，实际 {list(seq_on)}"
    )

    p_off = _path_of(seq_off, tasks)
    p_on = _path_of(seq_on, tasks)
    assert p_on < p_off - 1.0, (
        f"修复后航程必须严格更短：{p_on:.1f} vs {p_off:.1f}"
    )
    # 执行集不变 —— 覆盖优先的底线（F1 不动）
    assert sorted(seq_on) == sorted(seq_off) == [0, 1, 2]


# ── 场景二：最近邻会引入迟到时，必须放弃航程收益 ──────────────────────

def test_range_never_traded_for_lateness():
    """最近邻序若引入迟到，即使航程更短也**不得**采纳（迟到优先于航程）。

    二维布局：UAV(0,0)、A(10,0)、B(0,100)，speed=10。
        原序 [B, A]  ：B 到达 10.00 s，A 到达 20.05 s
        近邻序 [A, B]：A 到达  1.00 s，B 到达 11.05 s
    B.latest=10.5 ⇒ 原序零迟到、近邻序迟到 ⇒ 必须保留原序。
    """
    tasks = [_task(0, 0.0, 100.0, latest=10.5),   # B：远，卡在中间
             _task(1, 10.0, 0.0, latest=30.0)]    # A：近
    uavs = [_uav(0)]
    nests = [_nest(0)]

    _, ev_on = _mk(tasks, uavs, nests,
                   stage_cfg=MUASStageConfig(reorder_range_optimize=True))

    # 原序 [0(B), 1(A)] 零迟到；近邻序 [1(A), 0(B)] 会让 B 迟到
    seq = ev_on._best_order(0, [0, 1])
    assert list(seq) == [0, 1], (
        f"近邻序会引入迟到，必须保留原序，实际 {list(seq)}"
    )


# ── 场景三：改进低于阈值时不改序（吸收浮点噪声）────────────────────────

def test_range_improvement_below_eps_is_ignored():
    """航程改进小于 ``reorder_range_eps`` 时不改序（避免为噪声付出 ETA 重算）。

    把阈值抬到 1e6 米（远超任何可能收益）⇒ 必须退回原序。
    """
    tasks = [_task(0, 30.0), _task(1, 10.0), _task(2, 20.0)]
    uavs = [_uav(0)]
    nests = [_nest(0)]

    _, ev = _mk(tasks, uavs, nests,
                stage_cfg=MUASStageConfig(reorder_range_optimize=True,
                                          reorder_range_eps=1e6))
    seq = ev._best_order(0, [0, 1, 2])
    assert list(seq) == [0, 1, 2], (
        f"收益低于阈值时应退回原序，实际 {list(seq)}"
    )


# ── 场景四：默认关闭（A/B 实测为负优化，不得进生产口径）────────────────

def test_reorder_range_optimize_defaults_to_false():
    """默认必须为 False —— 2026-10-24 实测该修复在正式配置下是负优化。

    冒烟档（pop20/gen20）航程 −16.7%，但正式档（pop50/gen100、3 seed）
    主周期 km/任务持平（27.04 → 27.03），且多出 3 个尾巴周期 ⇒
    总航程 +4.0%、按时率 −2.4pp。故只作为对照开关保留，不进生产。
    """
    cfg = MUASStageConfig()
    assert cfg.reorder_range_optimize is False, "实测为负优化，默认必须关闭"
    assert cfg.reorder_range_eps == 1.0
