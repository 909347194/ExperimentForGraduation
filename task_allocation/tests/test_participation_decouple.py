# -*- coding: utf-8 -*-
"""参与机数 K 与任务数 M 解耦（select_participating_uavs）的常驻回归测试。

锁定的契约：

1. **SRP 不变式**：K ≤ M-1（显式 max_participating_uavs 同样裁剪；
   旧实现只裁 auto 不裁显式值，K≥M 会走进无终点编码的 balanced/overloaded 路径）；
2. **运营预算解耦**：sortie_budget（出动架次上限）与 M 无关地压低 K；
3. **出动经济性**（policy="marginal"）：按「该机是几个任务的最近机」降序逐个
   纳入，容量受限覆盖代价的边际下降 ≤ participation_cost γ 时**跳过该机继续**
   （gain 非单调：零增益机之后仍可能有正增益机，故不得 break）；
4. **退化兼容**：三旋钮缺省时行为与旧规则一致（尽量多派，上界 M-1）。

背景：旧 auto 规则在机队充裕时 K 恒等于 M-1，M-K 塌缩为常数（实测 ≡1），
巡游顺序搜索维度名存实亡，「顺序随 DMDE 联合优化」的主张失去实例支撑。

2026-10-12 修复（审查报告 docs/review_marginal_sortie_20261012.md）后锁定：
- **continue 而非 break**：旧实现在第一个零增益机处停机（docstring 却断言
  gain「单调非减」——est(j) 单调非缩只保证 gain≥0，不保证 gain 单调）；
- **排序键改为 argmin 份额降序**：旧键 cheapest_cost=min_t C(u,t) 会让
  「只因某任务恰好在旁、却对任何任务都不是最近机」的机排到前列；
- **估计器加单机服务容量 cap**：无容量口径把每架机当成能同时覆盖全部任务，
  gain 与真实服务能力脱节（γ 因此从不咬合）。
"""

from __future__ import annotations

import numpy as np
import pytest

from task_allocation.common.types import UAV
from task_allocation.methods.muas.solvers.muas_stage import (
    POLICY_CHEAPEST,
    POLICY_FIRST,
    POLICY_MARGINAL,
    MUASStageConfig,
    _resolve_tour_cap,
    _select_by_marginal_takeoff,
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


# ---------------------------------------------------------------------------
# 2026-10-12 修复回归（审查报告 docs/review_marginal_sortie_20261012.md）
# ---------------------------------------------------------------------------


def test_marginal_skips_zero_gain_uav_instead_of_break():
    """零增益机之后仍有正增益机：跳过继续，不是停机（旧 break 的修复）。

    u1 与 u0 完全同代价 ⇒ 对任何任务都不更近 ⇒ gain≡0；u2 独占 t1。
    旧实现在 u1 处 break 只得 [0]；修复后 continue 到 u2 ⇒ [0, 2]。
    """
    m = np.array([
        [10.0, 100.0],
        [10.0, 100.0],   # 与 u0 同代价 → 零增益
        [100.0, 10.0],   # 独占 t1 → 正增益
    ])
    ordered = _uavs(3)
    got = _select_by_marginal_takeoff(
        ordered, 0.0, m, {i: i for i in range(3)}, 2, tour_cap=None
    )
    assert [u.id for u in got] == [0, 2]


def test_marginal_orders_by_argmin_share():
    """排序键：argmin 份额降序——零份额机不得挤占正份额机的位置。

    u1 与 u0 同代价（旧键下按 id 排在 u2 前），但 u1 对任何任务都不是
    最近机（份额 0），u2 独占 t2（份额 1）。新排序把 u2 提上来 ⇒ [0, 2]。
    """
    m = np.array([
        [10.0, 100.0, 100.0],   # u0：t0/t1 最近机（份额 2）
        [10.0, 100.0, 100.0],   # u1：同代价但份额 0
        [100.0, 100.0, 10.0],   # u2：t2 最近机（份额 1）
    ])
    cfg = MUASStageConfig(participating_policy=POLICY_MARGINAL, participation_cost=0.0)
    got = select_participating_uavs(
        _uavs(3), n_tasks=3, config=cfg, uav_target=m, row_of_uav={i: i for i in range(3)},
    )
    assert [u.id for u in got] == [0, 2]


def test_marginal_capacity_makes_redundant_uav_productive():
    """容量估计器：cap=1 时「同代价冗余机」变 productive（旧口径判冗余）。"""
    m = np.array([[10.0, 10.0, 10.0], [10.0, 10.0, 10.0]])
    rows = {0: 0, 1: 1}
    ordered = _uavs(2)
    # 无容量（auto 由 remaining_range=1e6 推得巨大 cap）：u1 纯冗余 → 只派 1 架
    got = _select_by_marginal_takeoff(ordered, 0.0, m, rows, 3, tour_cap=None)
    assert [u.id for u in got] == [0]
    # cap=1：一架只能串 1 个任务 → u1 接管第 2 个 → 派 2 架
    got2 = _select_by_marginal_takeoff(ordered, 0.0, m, rows, 3, tour_cap=1)
    assert [u.id for u in got2] == [0, 1]


def test_marginal_tour_cap_auto_from_range():
    """auto 容量：显式值优先，否则按 min 剩余航程 / 平均单程推导。"""
    m = np.array([[10.0, 10.0, 10.0]])
    near = [UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=15.0, speed=10.0, nest_id=0)]
    far = [UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=1e6, speed=10.0, nest_id=0)]
    assert _resolve_tour_cap(near, m, 3, None) == 1      # int(15/10)-1=0 → clamp 1
    assert _resolve_tour_cap(far, m, 3, None) >= 3       # 航程充裕 → 不 binding
    assert _resolve_tour_cap(far, m, 3, 2) == 2          # 显式优先


def test_marginal_tour_cap_validation():
    """marginal_tour_cap 必须 >= 1 或为 None。"""
    with pytest.raises(ValueError):
        MUASStageConfig(marginal_tour_cap=0)
