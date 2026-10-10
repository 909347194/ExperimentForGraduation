# -*- coding: utf-8 -*-
"""terminal_nest.py — 航次终止基因（终点机巢）的编码支持

对应论文 §5(1)(2)：统一编码需同时表达「UAV 归属 + 任务访问顺序 + 终点机巢」，
终点机巢作为每架 UAV 航次的**特殊终止节点**，在 :math:`B_1,\\dots,B_K` 之间选择。

基因类型沿用 ``uav_id`` 哨兵值，避免改动 frozen 的 :class:`Gene`：

    ``uav_id >= 0``   起始基因（UAV → 任务）
    ``uav_id == -1``  巡游基因（任务 → 任务）
    ``uav_id == -2``  终止基因（末任务 / UAV 起点 → 机巢），``target_id`` 为机巢下标

每个 UAV 航次段末尾至多一个终止基因；其代价值随 ``cost_vector`` 一同参与
差分变异，因此终点机巢是**进化搜索的结果**，而非解码后的启发式补丁。

代价矩阵布局（扩展后 ``(K+M, M+B)``）：

    行 ``0..K-1``        UAV → 任务（前 M 列）/ UAV → 机巢（M: 列）
    行 ``K..K+M-1``      任务 → 任务（前 M 列）/ 任务 → 机巢（M: 列）
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from ..encoder import Gene

TOUR_UAV_ID = -1
TERMINAL_UAV_ID = -2

__all__ = [
    "TOUR_UAV_ID",
    "TERMINAL_UAV_ID",
    "split_segments",
    "normalize_srp_segments",
    "repair_terminal_genes",
    "extract_end_nests",
]


def split_segments(
    genes: Sequence[Gene],
) -> tuple[dict[int, list[int]], dict[int, int | None]]:
    """把基因序列解析为「每架 UAV 的任务序列 + 终点机巢」。

    Args:
        genes: 基因序列（允许结构已被扰动打乱）。

    Returns:
        ``(routes, ends)``；``routes`` 为 ``{uav_id: [task_idx, ...]}``，
        ``ends`` 为 ``{uav_id: nest_idx | None}``。
    """
    routes: dict[int, list[int]] = {}
    ends: dict[int, int | None] = {}
    current: int | None = None

    for g in genes:
        if g.uav_id >= 0:
            current = g.uav_id
            routes.setdefault(current, []).append(g.target_id)
            ends.setdefault(current, None)
        elif g.uav_id == TOUR_UAV_ID:
            if current is None:
                continue  # 无归属的巡游基因，由规范化阶段回收
            routes.setdefault(current, []).append(g.target_id)
        elif g.uav_id == TERMINAL_UAV_ID:
            if current is None:
                continue
            ends[current] = g.target_id

    return routes, ends


def _safe_cost(cm: np.ndarray, row: int, col: int) -> float:
    """带边界检查的代价读取；越界时返回 0.0 而不是抛异常。"""
    if row < 0 or col < 0 or row >= cm.shape[0] or col >= cm.shape[1]:
        return 0.0
    v = float(cm[row, col])
    return v if np.isfinite(v) else 0.0


def normalize_srp_segments(
    genes: Sequence[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    n_nests: int = 0,
    ends_override: dict[int, int] | None = None,
) -> list[Gene]:
    """把基因序列规范化为严格的航次分段结构。

    扰动（``swap`` / ``insert`` / ``reverse``）会把 ``uav_id >= 0`` 的起始基因
    卷进子序列，导致段结构错乱（实测出现过起始顺序 ``[0, 2, 1]``）。本函数在
    扰动后统一重建序列，保证：

    1. 每架 UAV 恰好一个起始基因，且按 UAV 编号升序排列；
    2. 巡游基因紧跟其所属 UAV 的起始基因；
    3. 每个航次段末尾至多一个终止基因（``n_nests > 0`` 时）；
    4. **不丢失任何任务**（孤立任务回收到任务数最少的段）。

    Args:
        genes:       待规范化的基因序列。
        cost_matrix: 扩展代价矩阵 ``(K+M, M+B)``。
        n_uavs:      UAV 数 K。
        n_targets:   任务数 M。
        n_nests:     机巢数 B；为 0 时不生成终止基因。
        ends_override: 外部指定的终点机巢 ``{uav_id: nest_idx}``，用于把反映射
            阶段选定的终点写入编码；未提供时沿用基因序列中已有的终止基因。

    Returns:
        规范化后的新基因列表。
    """
    routes, ends = split_segments(list(genes))
    if ends_override:
        ends.update(ends_override)

    # ---- 任务回收：防止扰动导致任务丢失 ----
    placed = {t for seg in routes.values() for t in seg}
    all_tasks = {g.target_id for g in genes if g.uav_id != TERMINAL_UAV_ID}
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
            # 空航次（允许 n_u = 0）：仍可为 UAV 指定终点机巢
            if n_nests > 0:
                b = ends.get(uav_id)
                if b is None or b >= n_nests:
                    b = 0
                out.append(
                    Gene(
                        uav_id=TERMINAL_UAV_ID,
                        target_id=int(b),
                        cost=_safe_cost(cost_matrix, uav_id, n_targets + b),
                    )
                )
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
        # 终止基因：末任务 → 机巢
        if n_nests > 0:
            b = ends.get(uav_id)
            if b is None or b >= n_nests:
                b = 0
            out.append(
                Gene(
                    uav_id=TERMINAL_UAV_ID,
                    target_id=int(b),
                    cost=_safe_cost(
                        cost_matrix, n_uavs + seg[-1], n_targets + b
                    ),
                )
            )

    return out


def repair_terminal_genes(
    genes: list[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    n_nests: int,
    rng: np.random.Generator | None = None,
) -> list[Gene]:
    """修补终止基因的合法性（机巢下标越界 / 缺失 / 重复）。

    - 下标越界或缺失：随机或取该航次代价最小的可用机巢；
    - 一段内重复出现多个终止基因：只保留最后一个。

    Args:
        genes:       基因序列（假定段结构已规范化）。
        cost_matrix: 扩展代价矩阵 ``(K+M, M+B)``。
        n_uavs:      UAV 数 K。
        n_targets:   任务数 M。
        n_nests:     机巢数 B。
        rng:         随机数生成器（可选）。

    Returns:
        修补后的新基因列表。
    """
    if n_nests <= 0:
        return [g for g in genes if g.uav_id != TERMINAL_UAV_ID]

    out: list[Gene] = []
    seen_segment = False  # 当前段是否已有终止基因
    for g in genes:
        if g.uav_id != TERMINAL_UAV_ID:
            if g.uav_id >= 0:
                seen_segment = False
            out.append(g)
            continue

        if seen_segment:
            continue  # 段内重复，丢弃
        seen_segment = True

        b = g.target_id
        if b < 0 or b >= n_nests:
            b = _cheapest_nest(genes, out, cost_matrix, n_uavs, n_targets, n_nests, rng)
        out.append(
            Gene(
                uav_id=TERMINAL_UAV_ID,
                target_id=int(b),
                cost=float(g.cost),
            )
        )
    return out


def _cheapest_nest(
    genes_all: list[Gene],
    genes_so_far: list[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    n_nests: int,
    rng: np.random.Generator | None,
) -> int:
    """为当前航次挑一个代价最小的机巢（前驱为段内最后一个任务）。

    无前驱任务（空航次）时退化为最小可用机巢下标。
    """
    prev_task: int | None = None
    for g in reversed(genes_so_far):
        if g.uav_id >= 0 or g.uav_id == TOUR_UAV_ID:
            prev_task = g.target_id
            break

    if prev_task is None:
        return int(rng.integers(n_nests)) if rng is not None else 0

    row = n_uavs + prev_task
    costs = [
        _safe_cost(cost_matrix, row, n_targets + b) for b in range(n_nests)
    ]
    return int(np.argmin(costs))


def extract_end_nests(
    genes: Sequence[Gene],
    uav_index_to_id: Sequence[int] | None = None,
) -> dict[int, int | None]:
    """从基因序列提取每架 UAV 的终点机巢。

    Args:
        genes:            基因序列（段结构应已规范化）。
        uav_index_to_id:  局部 UAV 下标 → 真实 uav_id 的映射；省略时返回下标键。

    Returns:
        ``{uav_id: nest_idx | None}``。
    """
    routes, ends = split_segments(list(genes))
    out: dict[int, int | None] = {}
    for uav_idx, b in ends.items():
        key = uav_index_to_id[uav_idx] if (
            uav_index_to_id is not None and uav_idx < len(uav_index_to_id)
        ) else uav_idx
        out[key] = b
    return out
