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
    POLICY_DEMAND,
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


# ---------------------------------------------------------------------------
# Fix④ 需求感知参与（policy="demand"）回归
# ---------------------------------------------------------------------------

# 4 机 / 4 任务：t0,t1,t2 在南簇（u0/u1/u2 近），t3 在最北离群、仅 u3 可达。
# cheapest 按「全局最小代价」排序会取 [0,1,2]（u3 因离簇远被挤出），t3 无近参与机；
# demand 贪心最大覆盖会纳入 u3（覆盖 t3）并丢弃一个冗余簇内机 u1。
_DM = np.array([
    [10.0, 10.0, 100.0, 500.0],   # u0：覆盖 t0,t1
    [10.0, 10.0, 100.0, 500.0],   # u1：覆盖 t0,t1（与 u0 冗余）
    [100.0, 100.0, 10.0, 500.0],  # u2：覆盖 t2
    [500.0, 500.0, 500.0, 10.0],  # u3：覆盖 t3（北角离群任务）
])
_DROWS = {i: i for i in range(4)}


def _demand(k: int, **kw) -> list[int]:
    # 显式覆盖半径 50m：_DM 中 10/100/500 三档里只有 10 算可达，
    # 从而真实体现「北角离群任务 t3 仅 u3 可达」的覆盖差异（默认
    # factor·range=5e5 会把所有代价都当成可达、覆盖全开，测不出区分度）。
    cfg = MUASStageConfig(
        participating_policy=POLICY_DEMAND, demand_coverage_radius=50.0, **kw
    )
    got = select_participating_uavs(
        _uavs(4), n_tasks=4, config=cfg, uav_target=_DM, row_of_uav=_DROWS,
    )
    assert len(got) <= k
    return [u.id for u in got]


def test_demand_covers_sparse_task_uav():
    """需求感知：宁可纳入离群任务的最近机 u3，也不保留冗余簇内机 u1。"""
    got = _demand(k=3)
    assert 3 in got, "北角离群任务 t3 的最近机 u3 必须被纳入"
    assert 0 in got and 2 in got  # 簇内仍各保留一架覆盖 t0/t1 与 t2


def test_demand_beats_cheapest_on_coverage():
    """对照 cheapest：同 K=3 下 cheapest 漏掉离群任务机。"""
    cheap = MUASStageConfig(participating_policy=POLICY_CHEAPEST)
    cheap_ids = [
        u.id for u in select_participating_uavs(
            _uavs(4), n_tasks=4, config=cheap, uav_target=_DM, row_of_uav=_DROWS,
        )
    ][:3]
    assert 3 not in cheap_ids  # cheapest 把 u3 挤出
    assert 3 in _demand(k=3)   # demand 保住 u3


def test_demand_respects_k_cap():
    """参与机数不超过 K 上限（SRP 不变式 + 运营预算）。"""
    cfg = MUASStageConfig(participating_policy=POLICY_DEMAND, max_participating_uavs=2)
    got = select_participating_uavs(
        _uavs(4), n_tasks=4, config=cfg, uav_target=_DM, row_of_uav=_DROWS,
    )
    assert len(got) <= 2


def test_demand_skips_zero_coverage_when_covered():
    """所有任务已被覆盖后，不再塞零覆盖的冗余机。"""
    # 把 u0/u1 设为全覆盖（阈值放宽到覆盖全部），u2/u3 高代价不可达
    m = np.array([
        [1.0, 1.0, 1.0, 1.0],
        [1.0, 1.0, 1.0, 1.0],
        [999.0, 999.0, 999.0, 999.0],
        [999.0, 999.0, 999.0, 999.0],
    ])
    cfg = MUASStageConfig(
        participating_policy=POLICY_DEMAND, demand_coverage_radius=50.0,
    )
    got = select_participating_uavs(
        _uavs(4), n_tasks=4, config=cfg, uav_target=m, row_of_uav={i: i for i in range(4)},
    )
    # u0/u1 已覆盖全部任务，u2/u3 零覆盖 → 只纳入一架
    assert [u.id for u in got] == [0]


def test_demand_explicit_radius_overrides_factor():
    """显式 demand_coverage_radius 优先于 factor·remaining_range。

    矩阵代价仅 3.0 / 100.0 两档；半径=5 时只认 3.0 的任务。若误用了
    factor·range=5e5，则 100.0 也会被当作可达，u0 能同时覆盖 t0,t1，
    只需 1 架即可，与「半径=5 需 2 架」矛盾。
    """
    m = np.array([
        [3.0, 100.0],
        [100.0, 3.0],
    ])
    rows = {i: i for i in range(2)}
    # 显式半径=5：u0 只覆盖 t0、u1 只覆盖 t1 → 需 2 架
    cfg_r = MUASStageConfig(
        participating_policy=POLICY_DEMAND, demand_coverage_radius=5.0,
    )
    got_r = select_participating_uavs(
        _uavs(2), n_tasks=4, config=cfg_r, uav_target=m, row_of_uav=rows,
    )
    assert len(got_r) == 2  # 各覆盖一个对角任务，缺一不可
    # 退化到 factor·range=5e5（默认）：u0 同时覆盖 t0,t1 → 1 架即够
    cfg_f = MUASStageConfig(participating_policy=POLICY_DEMAND)
    got_f = select_participating_uavs(
        _uavs(2), n_tasks=4, config=cfg_f, uav_target=m, row_of_uav=rows,
    )
    assert len(got_f) == 1


def test_demand_provision_serving_adds_uav_for_far_task():
    """Fix⑤：最小覆盖已完成、但单机架航程不足以同时服务多远任务时，补专属机。

    3 任务 / 3 机：t0 远北（仅 u0、u2 可达，各 30），t1、t2 居中（u0、u1 各 10）。
    u0 最小覆盖即覆盖全部 3 任务；但其往返代价 100 > 70×0.95=66.5 → 过载，
    把最远任务 t0 剥给最近空闲机 u2（到 t0=30），使各任务有可行航程。
    provisioning 关 → 只派 1 架（最小覆盖）；开 → 派 2 架（补 1 架专属机）。
    """
    m = np.array([
        [30.0, 10.0, 10.0],   # u0：t0=30, t1=10, t2=10
        [999.0, 10.0, 10.0],  # u1：t1=10, t2=10
        [30.0, 999.0, 999.0], # u2：t0=30（远北专属）
    ])
    rows = {i: i for i in range(3)}
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=70.0, speed=10.0, nest_id=0),
        UAV(id=1, x=0.0, y=0.0, z=0.0, remaining_range=70.0, speed=10.0, nest_id=1),
        UAV(id=2, x=0.0, y=0.0, z=0.0, remaining_range=70.0, speed=10.0, nest_id=2),
    ]
    cfg_off = MUASStageConfig(
        participating_policy=POLICY_DEMAND, demand_coverage_radius=50.0,
        demand_provision_serving=False,
    )
    got_off = select_participating_uavs(
        uavs, n_tasks=3, config=cfg_off, uav_target=m, row_of_uav=rows,
    )
    cfg_on = MUASStageConfig(
        participating_policy=POLICY_DEMAND, demand_coverage_radius=50.0,
        demand_provision_serving=True, demand_serve_margin=0.95,
    )
    got_on = select_participating_uavs(
        uavs, n_tasks=3, config=cfg_on, uav_target=m, row_of_uav=rows,
    )
    assert [u.id for u in got_off] == [0]       # 最小覆盖：1 架
    assert [u.id for u in got_on] == [0, 2]    # Fix⑤：补 1 架专属机（u2）

