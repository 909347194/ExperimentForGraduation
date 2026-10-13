# -*- coding: utf-8 -*-
"""terminal_nest.py — SRP 航次分段结构支持（方案 A 口径）

master 分支 = 方案 A（分层求解）：

    DMDE 编码只表达「UAV 归属 + 任务访问顺序」；终点机巢 **不进编码**，由
    解码后内层最小费用流（``constraints.nest_capacity.solve_end_nest_assignment``）
    在末态容量约束下精确求解，返航代价进入完整适应度评价（F2），间接作用于
    进化方向。

    因此本模块不产生「终止基因」，基因序列只有两类：

        ``uav_id >= 0``   起始基因（UAV → 任务）
        ``uav_id == -1``  巡游基因（任务 → 任务）

    SRP 不变式：每机一段、每段非空、每任务恰出现一次；基因总数恒为 ``M``。

「终点机巢进编码」的联合变体（终止基因 ``uav_id == -2``，``target_id`` 为
机巢下标，其代价随 ``cost_vector`` 末尾 K 个分量参与差分变异）已从 master
分支移除，在 pro 分支实现。恢复时以 git 历史中本模块含 ``TERMINAL_UAV_ID`` /
``repair_terminal_genes`` / ``normalize_srp_segments(n_nests=...)`` 的版本为准。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from ..encoder import Gene

TOUR_UAV_ID = -1

__all__ = [
    "TOUR_UAV_ID",
    "split_segments",
    "normalize_srp_segments",
]


def split_segments(
    genes: Sequence[Gene],
) -> dict[int, list[int]]:
    """把基因序列解析为「每架 UAV 的任务序列」。

    Args:
        genes: 基因序列（允许结构已被扰动打乱）。

    Returns:
        ``{uav_id: [task_idx, ...]}``。
    """
    routes: dict[int, list[int]] = {}
    current: int | None = None

    for g in genes:
        if g.uav_id >= 0:
            current = g.uav_id
            routes.setdefault(current, []).append(g.target_id)
        elif g.uav_id == TOUR_UAV_ID:
            if current is None:
                continue  # 无归属的巡游基因，由规范化阶段回收
            routes.setdefault(current, []).append(g.target_id)

    return routes


def _safe_cost(cm: np.ndarray, row: int, col: int) -> float:
    """带边界检查的代价读取；越界或不可达（inf/nan）时返回 0.0。

    **返回 0.0 是有意的工程取舍，不是"不可达即零代价"的语义声明**：

    * 本函数的返回值会进入 ``Individual.cost_vector`` 参与 DE 差分变异。
      若在此保留 ``inf``，``x_r1 + F·(x_r2 - x_r3)`` 会出现 ``inf - inf = nan``，
      nan 随即污染**整个**试验向量的所有分量，进化直接瘫痪。
    * 改用大哨兵值（如 1e9）虽能避开 nan，却会把该基因位的搜索目标拉向
      "代价最大"的方向，即**主动引导解码去选最远的任务** —— 比 0.0 更糟。
    * 取 0.0 时该位的目标值最小，解码端 :func:`_inverse_phi_srp` /
      :func:`_match_tour_edge` 会就近匹配，实际引导方向是"趋向最近可达任务"，
      且与它们内部的 ``np.isfinite`` 防护一致。

    **可达性的裁决权在解码/修复端**（``_match_tour_edge`` 的 finite 过滤、
    ``muas_stage._repair_range`` 的改派），编码层只负责不产生 nan。
    因此这里的 0.0 仅表示"该边在编码层不携带可用代价信息"。
    """
    if row < 0 or col < 0 or row >= cm.shape[0] or col >= cm.shape[1]:
        return 0.0
    v = float(cm[row, col])
    return v if np.isfinite(v) else 0.0


def normalize_srp_segments(
    genes: Sequence[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
) -> list[Gene]:
    """把基因序列规范化为严格的航次分段结构。

    扰动（``swap`` / ``insert`` / ``reverse``）会把 ``uav_id >= 0`` 的起始基因
    卷进子序列，导致段结构错乱（实测出现过起始顺序 ``[0, 2, 1]``）。本函数在
    扰动后统一重建序列，保证：

    1. 每架 UAV 恰好一个起始基因，且按 UAV 编号升序排列；
    2. 巡游基因紧跟其所属 UAV 的起始基因；
    3. **不丢失任何任务**（孤立任务回收到任务数最少的段）。

    方案 A 口径：不生成终止基因（终点机巢由解码后的内层最小费用流求解），
    基因总数恒为 ``M``。

    Args:
        genes:       待规范化的基因序列。
        cost_matrix: 代价矩阵 ``(K+M, M)``。
        n_uavs:      UAV 数 K。
        n_targets:   任务数 M。

    Returns:
        规范化后的新基因列表。
    """
    routes = split_segments(list(genes))

    # ---- 任务回收：防止扰动导致任务丢失 ----
    placed = {t for seg in routes.values() for t in seg}
    all_tasks = {g.target_id for g in genes}
    missing = sorted(all_tasks - placed)
    if missing and n_uavs > 0:
        # 挂到任务数最少的段，避免单段过长
        for t in missing:
            u = min(range(n_uavs), key=lambda k: len(routes.get(k, [])))
            routes.setdefault(u, []).append(t)

    # ---- 重建序列 ----
    out: list[Gene] = []
    for uav_id in range(n_uavs):
        seg = routes.get(uav_id, [])

        if not seg:
            # 空航次（允许 n_u = 0）：方案 A 下不产生任何基因
            continue

        # 起始基因：UAV → 首个任务
        out.append(
            Gene(
                uav_id=uav_id,
                target_id=int(seg[0]),
                cost=_safe_cost(cost_matrix, uav_id, seg[0]),
            )
        )
        # 巡游基因：任务 → 任务
        for j in range(1, len(seg)):
            out.append(
                Gene(
                    uav_id=TOUR_UAV_ID,
                    target_id=int(seg[j]),
                    cost=_safe_cost(cost_matrix, n_uavs + seg[j - 1], seg[j]),
                )
            )

    return out
