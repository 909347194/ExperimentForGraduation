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

    注：空航次段（normalize 后无起始基因、只有终止基因）的归属按段序
    推断——``normalize_srp_segments`` 输出的段按 UAV 下标升序排列，故
    当前段已有终止基因后再出现的终止基因属于下一个空段，其 UAV 下标
    = 已见起始基因数。防止空段终点错挂到前一段（曾致 ``coded_ends``
    中前一段终点被覆盖）。
    """
    routes: dict[int, list[int]] = {}
    ends: dict[int, int | None] = {}
    current: int | None = None
    n_starts = 0  # 已见起始基因数（normalize 后段序即 uav_id）

    for g in genes:
        if g.uav_id >= 0:
            current = g.uav_id
            n_starts += 1
            routes.setdefault(current, []).append(g.target_id)
            ends.setdefault(current, None)
        elif g.uav_id == TOUR_UAV_ID:
            if current is None:
                continue  # 无归属的巡游基因，由规范化阶段回收
            routes.setdefault(current, []).append(g.target_id)
        elif g.uav_id == TERMINAL_UAV_ID:
            if current is None:
                continue
            if ends.get(current) is None:
                ends[current] = g.target_id
            else:
                # 当前段已有终止基因 → 属于下一个空航次段（段序排列）
                empty_uid = n_starts
                routes.setdefault(empty_uid, [])
                ends.setdefault(empty_uid, g.target_id)

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
    """修补终止基因的合法性（下标越界 / 缺失 / 不可达改派 / 空段归属）。

    在 :func:`normalize_srp_segments` 之后调用：规范化保证「每段至多一个
    终止基因、越界回退 0」，本函数保证终止基因**语义合法**：

    - 下标越界或缺失：改派该航次「末任务 → 巢」代价最小的**可达**机巢；
    - 下标合法但返航代价为 inf（末任务 → 巢不可达：地形遮挡 / 禁飞）：
      同样改派可达机巢；全部不可达时保留原值，由下游
      :meth:`_choose_end_nests` 的可达性判据处理；
    - 空航次段（normalize 后无起始基因、只有终止基因）：用该段 UAV
      **起点行**判代价（段序 = 已见起始基因数），不错认上一段——
      与 :func:`split_segments` 的空段归属逻辑一致。

    Args:
        genes:       基因序列（假定段结构已规范化）。
        cost_matrix: 扩展代价矩阵 ``(K+M, M+B)``。
        n_uavs:      UAV 数 K。
        n_targets:   任务数 M。
        n_nests:     机巢数 B。
        rng:         随机数生成器（可选，仅全不可达且下标越界时使用）。

    Returns:
        修补后的新基因列表（长度不变）。
    """
    if n_nests <= 0:
        return [g for g in genes if g.uav_id != TERMINAL_UAV_ID]

    out: list[Gene] = []
    seen_segment = False  # 当前段是否已有终止基因
    seg_has_task = False  # 当前段是否有任务（空前继 vs 末任务前驱）
    n_starts = 0  # 已见起始基因数（normalize 后段序即 uav_id）
    for g in genes:
        if g.uav_id != TERMINAL_UAV_ID:
            if g.uav_id >= 0:
                seen_segment = False
                seg_has_task = True
                n_starts += 1
            else:
                seg_has_task = True
            out.append(g)
            continue

        if seen_segment:
            # 当前段已有终止基因：规范化输入（normalize_srp_segments 保证
            # 每段至多一个）下属**下一空航次段**——空段无起始基因，故
            # 保留该基因并按空段判代价（段序 = 已见起始基因数）。
            # 与 split_segments 的空段归属逻辑严格一致。
            seg_has_task = False
            row = n_starts
        else:
            seen_segment = True
            if seg_has_task:
                prev_task = _last_task_of_segment(out)
                row = (n_uavs + prev_task) if prev_task is not None else None
            else:
                # 空航次段（normalize 后无起始基因）：行 = 该段 UAV 起点行，
                # 段序 = 已见起始基因数（前面的段 0..u-1 均带起始基因）
                row = n_starts
        costs = _nest_costs(cost_matrix, row, n_targets, n_nests)

        b = g.target_id
        # 越界 / 缺失 / 不可达（代价 inf）→ 改派可达最小代价巢
        need_fix = not (0 <= int(b) < n_nests)
        if not need_fix and costs is not None and not np.isfinite(costs[int(b)]):
            need_fix = True
        if need_fix:
            fixed = _pick_reachable_nest(costs, rng)
            if fixed is not None:
                b = fixed

        # cost 必须与 target 一致（越界/不可达改派后原 cost 属于别的巢）
        if 0 <= int(b) < n_nests and row is not None:
            cost = float(cost_matrix[row, n_targets + int(b)])
        else:
            cost = float(g.cost)
        out.append(
            Gene(
                uav_id=TERMINAL_UAV_ID,
                target_id=int(b),
                cost=cost,
            )
        )
    return out


def _last_task_of_segment(genes_so_far: Sequence[Gene]) -> int | None:
    """当前航次段内最后一个任务（前驱为起始基因或巡游基因的目标）。"""
    for g in reversed(genes_so_far):
        if g.uav_id >= 0 or g.uav_id == TOUR_UAV_ID:
            return g.target_id
    return None


def _nest_costs(
    cost_matrix: np.ndarray,
    row: int | None,
    n_targets: int,
    n_nests: int,
) -> list[float] | None:
    """「row → 各机巢」的真实代价列表（inf 保留，不转 0.0）。

    空航次（``row is None``，前驱是 UAV 起点）时调用方应传 UAV 行；
    此处仅在 row 为 None 时返回 None 表示无法计算。
    """
    if row is None:
        return None
    if row < 0 or row >= cost_matrix.shape[0]:
        return None
    return [
        float(cost_matrix[row, n_targets + b])
        if n_targets + b < cost_matrix.shape[1]
        else float("inf")
        for b in range(n_nests)
    ]


def _pick_reachable_nest(
    costs: list[float] | None,
    rng: np.random.Generator | None,
) -> int | None:
    """从候选代价中挑一个**可达**（有限代价）机巢；无可达候选返回 None。"""
    if not costs:
        return None
    finite = [(b, c) for b, c in enumerate(costs) if np.isfinite(c)]
    if not finite:
        return None
    best = min(finite, key=lambda kv: kv[1])[0]
    return int(best)


def _cheapest_nest(
    genes_all: list[Gene],
    genes_so_far: list[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    n_nests: int,
    rng: np.random.Generator | None,
) -> int:
    """为当前航次挑一个**可达**且代价最小的机巢（兼容旧接口）。

    无前驱任务（空航次）或全部不可达时退化为最小可用机巢下标。
    """
    prev_task = _last_task_of_segment(genes_so_far)
    if prev_task is None:
        return int(rng.integers(n_nests)) if rng is not None else 0

    row = n_uavs + prev_task
    costs = _nest_costs(cost_matrix, row, n_targets, n_nests)
    picked = _pick_reachable_nest(costs, rng)
    if picked is None:
        return 0
    return picked


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
