# -*- coding: utf-8 -*-
"""capability.py — 机队能力承载数推导。

职责：由可出动机群的剩余航程预算与粗代价分位数推导能力上界 cap(t)，并合成选择数 n_sel。
边界：cap 只约束选择数，不约束候选池规模；单任务可达性与机群预算硬闸门由 feasibility 负责。
相关：selection/feasibility.py、selection/priority.py、selection/pipeline.py。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from task_allocation.common.types import Task, UAV

__all__ = [
    "CapabilityScaleConfig",
    "coarse_task_costs",
    "fleet_range_budget",
    "capability_task_cap",
    "merge_caps",
    "effective_cap",
]


@dataclass
class CapabilityScaleConfig:
    """能力驱动规模的旋钮（yaml ``selection.capability:`` 段）。

    ``sortie_budget`` / ``max_participating_uavs`` 与 ``MUASStageConfig`` 同名
    旋钮对齐（即②③被动空闲的成因）；滚动层会取两侧的较小者自动同步，
    保证二阶段打开吞吐上限时一阶段记账自动跟着收紧。
    """

    # 关闭后退回纯机数名额（⌈αK⌉ / ⌈max_ratio·K⌉），供消融对比
    enabled: bool = True
    # 预算的额外折扣：给未来紧急任务 / 执行中的不确定性留余量
    # （与 feasibility.budget_factor 相乘；postcheck 的硬闸门仍按 budget_factor）
    budget_scale: float = 1.0
    # 任务粗代价的分位数：0.5=中位数；调低→cap 更宽松，调高→更保守
    quantile: float = 0.5
    # cap 下限：至少保留的任务数（保持"总能评出一个候选"的退化行为）
    min_cap: int = 1
    # ② 起降/充电吞吐上限 S_t：B(t) 只计前 S_t 架的航程（None=不限）
    sortie_budget: int | None = None
    # ③ 参与机数硬上限：同上，与②取交（None=不限）
    max_participating_uavs: int | None = None
    # 机数名额与 cap(t) 的合成方式：
    #   "intersect"（默认）取交集 min{名额, cap(t)}：能力只能收紧，绝不超名额；
    #   "primary"  以 cap(t) 为主定规模、名额降为兜底：充裕时允许超名额；
    #   "single"   单闸门：n_sel = min{cap(t), n_compute}，机数名额彻底退出
    #              选择路径（quota 只作 diagnostics 记录，不参与合成）。
    mode: str = "intersect"
    # 仅 mode="single" 生效：算力闸门 —— 本周期二阶段最多允许求解多少条任务，
    # 单位是「条/周期」。它与机队规模无关：二阶段预算（pop×gen + polish 上限）
    # 逐周期固定，机队变大只改变可行域形状，而单次评估开销随进入求解的任务条数
    # 增长（组合规模阶乘级、链上插入位置平方级）⇒ 耗时由 |S| 而非机队架数决定。
    # 取值由 exp02 实测的 (n_sel → 单周期墙钟) 曲线反推：60 s ≈ 25 条、
    # 400 s ≈ 48 条。None = 不设算力上限（规模完全由 cap(t) 决定）。
    n_compute: int | None = None
    # 仅 mode="single" 生效：候选窗口倍率（相对 n_sel）。
    #   None（推荐）= 全池参与边际选择：Δ 判据在整池上挑 n_sel，P_i 只负责
    #                 排序与并列裁决，不再充当"第二道闸门"；
    #   给值       = 只取 P_i 前 ⌈ratio·n_sel⌉ 条作为候选（隔离"优先级窗口"
    #                 自身造成的漏选效应，用于消融）。
    candidate_window_ratio: float | None = None
    # 仅 mode="primary" 生效：相对机数名额的最大扩张倍数，防止 c̃ 过小时规模失控。
    # None = 不设扩张上限（规模完全由 cap(t) 决定）。
    max_expand_factor: float | None = None
    # 候选池计算预算上限（仅约束 n_cand）：候选与 cap(t) 解耦后，n_cand 不再被
    # 能力收紧，富余多少完全由"计算预算 + 排序质量"决定；此旋钮即"计算预算"上限，
    # 防止机制3 的 O(n_cand·n_sel·insert) 随 ⌈α·K⌉ 失控。None = 不设计算上限
    # （仅受 ⌈α·K⌉ 与任务总数 N 约束）。
    max_candidate_pool: int | None = None
    # 候选规模上界：即便本模块 ``enabled=False``，也按吞吐上界
    #   cap(t)=⌊Σ remaining_range / 单任务中位代价⌋
    # 收敛候选规模，避免 "2.5×机数" 永远不瓶颈、aging 排序失效。
    # 默认 False —— 仅显式开启时改变（关闭能力模块的）消融路径行为。
    fallback_when_disabled: bool = False


def coarse_task_costs(
    tasks: Sequence[Task],
    uavs: Sequence[UAV],
    cost_hint: dict[int, float] | None = None,
) -> list[float]:
    """每任务的粗执行代价（与基础优先级 C_i(t) 同口径）。

    ``cost_hint``（task_id -> 三维代价）优先；缺省时取最近可用 UAV 的欧氏距离。
    无可用 UAV 时返回 1e6 哨兵值（priority 经本函数取 C_i(t)，口径同源）。
    """
    avail = [u for u in uavs if u.is_available]
    out: list[float] = []
    for t in tasks:
        if cost_hint and t.id in cost_hint:
            out.append(float(cost_hint[t.id]))
            continue
        if not avail:
            out.append(1e6)
            continue
        tx, ty, tz = t.position
        out.append(
            float(
                min(
                    ((u.x - tx) ** 2 + (u.y - ty) ** 2 + (u.z - tz) ** 2) ** 0.5
                    for u in avail
                )
            )
        )
    return out


def fleet_range_budget(
    uavs: Sequence[UAV],
    budget_factor: float = 1.0,
    *,
    sortie_budget: int | None = None,
    max_participating_uavs: int | None = None,
) -> float:
    """机群能力预算 B(t)：**可起飞子集**的剩余航程之和 × 预算系数。

    Args:
        sortie_budget:        ② 起降/充电吞吐上限 S_t；None 不限。
        max_participating_uavs: ③ 参与机数硬上限；与 ② 取交。

    至多 ``k_op = min(|U_avail|, S_t, max_participating)`` 架能起飞，
    按剩余航程降序取前 k_op 求和（乐观上界：实际 cheapest 优先派机
    可能更少，偏差由 budget_scale 吸收）。无上限时退化为全量求和。
    """
    avail = [u for u in uavs if u.is_available]
    k_op = len(avail)
    for cap_v in (sortie_budget, max_participating_uavs):
        if cap_v is not None:
            k_op = min(k_op, max(0, int(cap_v)))
    if k_op <= 0:
        return 0.0
    ranges = sorted((max(u.remaining_range, 0.0) for u in avail), reverse=True)
    return float(budget_factor * sum(ranges[:k_op]))


def merge_caps(a: int | None, b: int | None) -> int | None:
    """两个可选上限取交（非 None 的较小者；全 None 则 None）。

    用于滚动层把 ``MUASStageConfig`` 的②③旋钮同步进能力预算：
    两侧都配时以更严者为准。
    """
    vals = [v for v in (a, b) if v is not None]
    return min(vals) if vals else None


def capability_task_cap(
    costs: Sequence[float],
    budget: float,
    config: CapabilityScaleConfig | None = None,
) -> int:
    """能力预算可承载的任务数上界 cap(t)。

    Args:
        costs:  各任务粗执行代价（长度 = 候选池规模）。
        budget: ``fleet_range_budget`` 得到的 B(t)。
        config: 旋钮；缺省取中位数分位、下限 1。

    Returns:
        ``max(min_cap, ⌊B(t) / c̃⌋)``。``costs`` 为空时返回 ``min_cap``。
        全零代价时 c̃ 回退 1.0，避免除零。
    """
    cfg = config or CapabilityScaleConfig()
    if not costs:
        return int(cfg.min_cap)
    arr = np.asarray(costs, dtype=float)
    c_ref = float(np.quantile(arr, float(cfg.quantile)))
    if not np.isfinite(c_ref) or c_ref < 1e-9:
        c_ref = 1.0
    cap = int(np.floor(max(float(budget), 0.0) / c_ref))
    return max(int(cfg.min_cap), cap)


def effective_cap(
    quota: int,
    cap: int | None,
    config: CapabilityScaleConfig | None = None,
) -> int:
    """把「机数名额 quota」与「能力承载数 cap(t)」合成为最终规模上界。

    仅用于**选择数 n_sel**（名额 = ⌈max_ratio·K⌉ 或 ``max_selected``）。
    候选池 n_cand 已与 cap(t) 解耦，由 pipeline 直接以 ⌈α·K⌉ 生成、不经本函数，
    故本函数只产出"紧承诺"规模，保证选择口径一致。

    Args:
        quota: 机数名额（⌈max_ratio·K⌉ / ``max_selected``）。``mode="single"``
            时该值只落 diagnostics，不参与合成。
        cap:   :func:`capability_task_cap` 的能力承载数；``None`` 表示能力未启用。
        config: 旋钮；``mode`` 决定合成方式。

    Returns:
        ``mode="intersect"``：``max(1, min(quota, cap))`` —— 能力只能收紧。
        ``mode="primary"``  ：``max(1, cap)``，并按
            ``max_expand_factor × quota`` 封顶（``max_expand_factor`` 为 None 不封顶）。
        ``mode="single"``   ：``max(1, min(cap, n_compute))`` —— 能力门槛与算力门槛
            取小，机数名额不参与（``cap`` 为 None 时退回 quota 兜底）。

    Raises:
        ValueError: ``mode`` 不在 ``intersect`` / ``primary`` / ``single``
            （防配置拼写错误静默降级）。
    """
    cfg = config or CapabilityScaleConfig()
    mode = str(cfg.mode)
    if mode not in ("intersect", "primary", "single"):
        raise ValueError(
            f"unknown capability mode {mode!r}; "
            "expect 'intersect', 'primary' or 'single'"
        )
    quota = max(1, int(quota))
    if cap is None:
        return quota
    cap = max(1, int(cap))
    if mode == "single":
        n = cap
        if cfg.n_compute is not None:
            n = min(n, max(1, int(cfg.n_compute)))
        return max(1, n)
    if mode == "primary":
        n = cap
        if cfg.max_expand_factor is not None:
            n = min(n, int(np.ceil(float(cfg.max_expand_factor) * quota)))
        return max(1, n)
    return max(1, min(quota, cap))
