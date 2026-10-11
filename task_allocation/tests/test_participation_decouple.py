# -*- coding: utf-8 -*-
"""参与机数 K 与任务数 M 解耦（select_participating_uavs）的常驻回归测试。

锁定的契约：

1. **SRP 不变式**：K ≤ M-1（显式 max_participating_uavs 同样裁剪；
   旧实现只裁 auto 不裁显式值，K≥M 会走进无终点编码的 balanced/overloaded 路径）；
2. **运营预算解耦**：sortie_budget（出动架次上限）与 M 无关地压低 K；
3. **出动经济性**（policy="marginal"）：按代价升序逐个纳入，
   覆盖代价边际下降 ≤ participation_cost γ 即停；第一架无条件纳入；
4. **退化兼容**：三旋钮缺省时行为与旧规则一致（尽量多派，上界 M-1）。

背景：旧 auto 规则在机队充裕时 K 恒等于 M-1，M-K 塌缩为常数（实测 ≡1），
巡游顺序搜索维度名存实亡，「顺序随 DMDE 联合优化」的主张失去实例支撑。
"""

from __future__ import annotations

import numpy as np

from task_allocation.common.types import UAV
from task_allocation.methods.muas.solvers.muas_stage import (
    POLICY_CHEAPEST,
    POLICY_FIRST,
    POLICY_MARGINAL,
    MUASStageConfig,
    select_participating_uavs,
)


def _uavs(n: int) -> list[UAV]:
    return [
        UAV(id=i, x=0.0, y=0.0, z=0.0, remaining_range=1e6, speed=10.0, nest_id=0)
        for i in range(n)
    ]


def test_k_clamped_to_m_minus_1():
    """显式上限也必须裁剪到 M-1（修复 K≥M 缺口 + 不变式）。"""
    cfg = MUASStageConfig(max_participating_uavs=5)
    got = select_participating_uavs(_uavs(8), n_tasks=4, config=cfg)
    assert len(got) == 3  # min(5, M-1=3, 8)

    # 旧缺口：max_participating_uavs ≥ M 时不得产生 K ≥ M
    cfg2 = MUASStageConfig(max_participating_uavs=10, participating_policy=POLICY_FIRST)
    got2 = select_participating_uavs(_uavs(10), n_tasks=4, config=cfg2)
    assert len(got2) == 3


def test_sortie_budget_caps_k_independently_of_m():
    """出动架次上限与任务数无关地决定 K（K 从 M 解耦的主通道）。"""
    cfg = MUASStageConfig(sortie_budget=3, participating_policy=POLICY_FIRST)
    got = select_participating_uavs(_uavs(8), n_tasks=10, config=cfg)
    assert len(got) == 3  # M-1=9 但只出动 3 架 → M-K=7 个巡游基因


def test_single_task_degenerate():
    cfg = MUASStageConfig()
    got = select_participating_uavs(_uavs(5), n_tasks=1, config=cfg)
    assert len(got) == 1


def test_default_keeps_legacy_fill():
    """三旋钮缺省：尽量多派、上界 M-1（与旧行为一致）。"""
    cfg = MUASStageConfig(participating_policy=POLICY_FIRST)
    got = select_participating_uavs(_uavs(8), n_tasks=10, config=cfg)
    assert len(got) == 8  # min(M-1=9, 8)
    got2 = select_participating_uavs(_uavs(24), n_tasks=10, config=cfg)
    assert len(got2) == 9  # 顶格 M-1（旧 exp01 形态：M-K=1）


# ---------------------------------------------------------------------------
# 边际出动决策
# ---------------------------------------------------------------------------

# 6 任务 / 4 机：u0/u1/u2 各覆盖一个便宜任务，u3 完全冗余
# est(1)=610  est(2)=520(gain 90)  est(3)=430(gain 90)  est(4)=430(gain 0)
_M = 6
_UAV_TARGET = np.array([
    [10.0, 100.0, 100.0, 100.0, 100.0, 100.0],   # u0
    [100.0, 10.0, 100.0, 100.0, 100.0, 100.0],   # u1
    [100.0, 100.0, 10.0, 100.0, 100.0, 100.0],   # u2
    [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],   # u3（冗余）
])
_ROWS = {i: i for i in range(4)}


def _marginal(gamma: float, n_tasks: int = _M) -> list[UAV]:
    cfg = MUASStageConfig(
        participating_policy=POLICY_MARGINAL,
        participation_cost=gamma,
    )
    return select_participating_uavs(
        _uavs(4), n_tasks=n_tasks, config=cfg,
        uav_target=_UAV_TARGET, row_of_uav=_ROWS,
    )


def test_marginal_stops_at_zero_gain():
    """gain=0 的冗余机不纳入（γ=0 同样停）。"""
    assert [u.id for u in _marginal(gamma=0.0)] == [0, 1, 2]


def test_marginal_stops_by_takeoff_cost():
    """γ 介于 0 与 90 之间：三架全纳入、第四架冗余被拒。"""
    assert [u.id for u in _marginal(gamma=50.0)] == [0, 1, 2]


def test_marginal_cost_dominates():
    """γ 高于单架边际贡献 90 → 只出动第一架。"""
    assert [u.id for u in _marginal(gamma=200.0)] == [0]


def test_marginal_always_at_least_one():
    assert len(_marginal(gamma=1e18)) == 1
