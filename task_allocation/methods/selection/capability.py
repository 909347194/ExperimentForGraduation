# -*- coding: utf-8 -*-
"""capability.py — 机队能力预算与候选规模的能力驱动上界（论文 3.3）。

设计原则：**候选/选择规模跟着机队能力（剩余航程预算）走，而不是只跟机数走**。

旧规则只按机数定名额：

    候选任务数   n_cand = ⌈α · K⌉
    选择任务数   n_sel  = ⌈max_ratio · K⌉

（K = 可用 UAV 数；两个倍率乘的都是**机数**，与任务总数无关。）

在机队航程充裕时欠选（预算还剩很多却封顶）、航程紧张时过筛（先放进来再裁）。
本模块给出能力预算可承载的任务数上界：

    B(t)  = budget_scale · budget_factor · Σ_{u ∈ U_t^avail} remaining_range(u)
    c̃     = 粗代价的 quantile 分位数（默认中位数）
    cap(t) = max(min_cap, ⌊B(t) / c̃⌋)

名额与 cap(t) 的合成方式由 ``CapabilityScaleConfig.mode`` 决定（两种）：

  * ``intersect``（默认，保守）：取交集，能力只能**收紧**

        n_cand = min(⌈α · K⌉,         cap(t))
        n_sel  = min(⌈max_ratio · K⌉, cap(t))

    只治"过筛"（航程紧张时提前收编）；航程充裕时仍以机数名额为硬天花板，
    **不会超名额**，"欠选"问题依旧存在。

  * ``primary``（能力为主）：cap(t) 直接定规模，名额降为兜底

        n_cand = cap(t)，并按 max_expand_factor · ⌈α · K⌉ 封顶（未设则不封顶）
        n_sel  = 同理（相对 ⌈max_ratio · K⌉）

    航程紧张时按 cap(t) 收编、航程充裕时允许**超过名额**多选，
    两个方向的失真同时被修正。``max_expand_factor`` 防止 c̃ 过小时规模失控。

合成统一由 :func:`effective_cap` 负责，候选预筛选与边际选择共用同一口径。
两种模式下能力都只在【规模】这一维生效；
单任务可达性（prefilter）与机群预算硬闸门（postcheck）仍由 feasibility 负责，
本模块不重复其职责。

成本口径与 ``priority.compute_priorities`` 的 C_i(t) 一致：
``cost_hint``（滚动层三维代价矩阵到最近可用 UAV 的代价）优先，
否则最近可用 UAV 的欧氏距离，保证与基础优先级、postcheck 同一语义。

**记账口径与派机约束对齐（被动空闲不计预算）**：``sortie_budget``（起降/
充电吞吐上限）与 ``max_participating_uavs`` 会把可用机**强行压在地面**，
其航程是本周期确定用不了的幻影容量，计入 B(t) 会让 cap(t) 对着一个
不会兑现的容量校准候选规模。故 B(t) 只计**至多 S_t 架**的航程（按剩余
航程降序取前 k_op，仍是乐观上界——二阶段 cheapest 优先派机实际可能
更少，偏差由 ``budget_scale`` 吸收）。

两处**故意不计入**：

1. γ 出动经济性停机（``participating_policy="marginal"``）——那是二阶段的
   主动裁量（"能飞但不值得飞"），属架构设计的自由度，不是记账误差；
2. SRP 不变式 ``K ≤ N−1`` —— 依赖 N（待定的候选/任务数）有循环性，且任务少时
   cap 本就不受限，忽略无害。
"""

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
    #   "primary"  以 cap(t) 为主定规模、名额降为兜底：充裕时允许超名额。
    mode: str = "intersect"
    # 仅 mode="primary" 生效：相对机数名额的最大扩张倍数，防止 c̃ 过小时规模失控。
    # None = 不设扩张上限（规模完全由 cap(t) 决定）。
    max_expand_factor: float | None = None
    # Fix③：即便本模块 ``enabled=False``，也按吞吐上界
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
    无可用 UAV 时返回 1e6 哨兵值（与 ``priority._coarse_exec_cost`` 一致）。
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

    候选预筛选（名额 = ⌈α·K⌉ → n_cand）与边际选择（名额 = ⌈max_ratio·K⌉
    或 ``max_selected`` → n_sel）共用本函数，保证两处口径一致。

    Args:
        quota: 机数名额（⌈α·K⌉ 或 ⌈max_ratio·K⌉ / ``max_selected``）。
        cap:   :func:`capability_task_cap` 的能力承载数；``None`` 表示能力未启用。
        config: 旋钮；``mode`` 决定合成方式。

    Returns:
        ``mode="intersect"``：``max(1, min(quota, cap))`` —— 能力只能收紧。
        ``mode="primary"``  ：``max(1, cap)``，并按
            ``max_expand_factor × quota`` 封顶（``max_expand_factor`` 为 None 不封顶）。

    Raises:
        ValueError: ``mode`` 非 ``intersect`` / ``primary``（防配置拼写错误静默降级）。
    """
    cfg = config or CapabilityScaleConfig()
    mode = str(cfg.mode)
    if mode not in ("intersect", "primary"):
        raise ValueError(
            f"unknown capability mode {mode!r}; expect 'intersect' or 'primary'"
        )
    quota = max(1, int(quota))
    if cap is None:
        return quota
    cap = max(1, int(cap))
    if mode == "primary":
        n = cap
        if cfg.max_expand_factor is not None:
            n = min(n, int(np.ceil(float(cfg.max_expand_factor) * quota)))
        return max(1, n)
    return max(1, min(quota, cap))
