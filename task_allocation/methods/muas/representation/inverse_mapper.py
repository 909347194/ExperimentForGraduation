# -*- coding: utf-8 -*-
"""inverse_mapper.py — 连续到离散的反映射协调器 φ'（公式 3-7）

改进记录：
    - 温度自适应反映射：top_k 随温度动态调节（早期探索多、后期开发多）。
    - 匹配后扰动：swap + reassign，概率与温度正相关。
    - SRP 巡游扰动：swap/insert/reverse，打破贪心顺序。
    - 基因长度：balanced/overloaded = n_uavs，SRP = M（方案 A，无终止基因）。

方案 A 口径：反映射只解码「UAV 归属 + 访问顺序」；终点机巢不进编码，
由解码后内层最小费用流精确求解（见 solvers.muas_stage._choose_end_nests）。
"""

from __future__ import annotations

import numpy as np

from .encoder import Gene, Individual
from .repair_rules.nearest_match import nearest_match_adaptive
from .repair_rules.unique_filter import mask_balanced, mask_overloaded
from .repair_rules.invalid_mutator import repair_invalid
from .repair_rules.terminal_nest import (
    TOUR_UAV_ID,
    normalize_srp_segments,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 主入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def inverse_phi(
    cost_vector: np.ndarray,
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    model_type: str,
    rng: np.random.Generator | None = None,
    temperature: float = 0.5,
) -> Individual:
    """反映射：将连续代价值向量还原为离散个体。

    对应公式 (3-7) 和算法 3.1 的 17-26 行。

    改进：引入温度参数 temperature (0.0~1.0)，
    控制反映射的随机程度：
    - temperature=1.0: 强探索（大 top_k、高扰动概率）
    - temperature=0.0: 纯开发（贪心匹配、无扰动）

    Args:
        cost_vector:   差分后的连续代价值向量。
        cost_matrix:   原始代价矩阵。
        n_uavs:        UAV 数量。
        n_targets:     目标数量。
        model_type:    分配模型类型。
        rng:           随机数生成器。
        temperature:   温度值 (0.0 ~ 1.0)。

    Returns:
        可行的子代个体。
    """
    if rng is None:
        rng = np.random.default_rng()

    if model_type == "srp":
        return _inverse_phi_srp(
            cost_vector, cost_matrix, n_uavs, n_targets, rng, temperature
        )
    else:
        return _inverse_phi_standard(cost_vector, cost_matrix, n_uavs, n_targets, model_type, rng, temperature)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 标准反映射 (balanced / overloaded)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _inverse_phi_standard(
    cost_vector: np.ndarray,
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    model_type: str,
    rng: np.random.Generator,
    temperature: float = 0.5,
) -> Individual:
    """标准反映射（balanced / overloaded）。

    改进：
    1. 使用温度自适应最近邻匹配（top_k 随温度衰减）。
    2. 匹配后以温度相关概率执行随机扰动（交换/重分配）。
    """
    cm_work = cost_matrix.copy().astype(float)
    mask = np.zeros_like(cm_work, dtype=bool)
    # balanced / overloaded 只产生 (uav, target) 分配，因此只允许在
    # 「UAV 行 × 任务列」子矩阵内匹配：
    #   row >= n_uavs      是巡游关系的目标行（只属于 SRP 的 uav_id=-1）
    #   col >= n_targets   是越界列（方案 A 代价矩阵只有任务列；此处防御性屏蔽，
    #                      防止未来布局扩展时 nearest_match_adaptive 取到非任务列）
    mask[n_uavs:, :] = True
    mask[:, n_targets:] = True

    genes: list[Gene] = []
    invalid_indices: list[int] = []

    for idx, cv in enumerate(cost_vector):
        if not np.isfinite(cv) or cv < 0:
            invalid_indices.append(idx)
            continue

        # 温度自适应匹配（softmax 采样，距离近的概率更高）
        result = nearest_match_adaptive(cv, cm_work, mask, temperature=temperature, rng=rng)
        if result is None:
            invalid_indices.append(idx)
            continue

        row, col, cost = result
        genes.append(Gene(uav_id=row, target_id=col, cost=cost))

        if model_type == "balanced":
            mask_balanced(mask, row, col)
        elif model_type == "overloaded":
            mask_overloaded(mask, row, col)

    # 匹配后扰动：以温度相关概率随机交换两对分配
    if temperature > 0.1 and len(genes) >= 2:
        _perturb_genes(genes, cm_work, n_uavs, model_type, temperature, rng)

    # 规则 3.6: 无效值随机修补
    if invalid_indices:
        repaired = repair_invalid(
            cm_work, mask, invalid_indices, model_type, n_uavs
        )
        for uav_id, tgt_id, cost in repaired:
            genes.append(Gene(uav_id=uav_id, target_id=tgt_id, cost=cost))

    # N>M 后处理：确保每个目标至少被一个 UAV 访问
    if model_type == "overloaded":
        _ensure_all_targets_covered(genes, cm_work, n_uavs, n_targets, rng)

    # N=M 保险：确保目标一一对应（修复任何可能的重复/漏分配）
    if model_type == "balanced":
        _repair_balanced_unique(genes, cm_work)

    return Individual(genes=genes, model_type=model_type)


def _repair_balanced_unique(
    genes: list[Gene],
    cost_matrix: np.ndarray,
) -> None:
    """balanced 模型保险：修复重复目标为缺失目标，保证一一对应。

    正常情况下反映射的掩码机制已经保证互斥，此处作为防御性检查，
    防止任何上游扰动破坏 N=M 的一一对应关系。
    """
    seen: set[int] = set()
    dup_idx: list[int] = []
    for i, g in enumerate(genes):
        if g.target_id in seen:
            dup_idx.append(i)
        else:
            seen.add(g.target_id)

    if not dup_idx:
        return

    n_targets = cost_matrix.shape[1]
    missing = [t for t in range(n_targets) if t not in seen]

    for i in dup_idx:
        if not missing:
            break
        g = genes[i]
        # 在缺失目标中选择对该 UAV 代价最小的一个
        best_t = min(missing, key=lambda t: cost_matrix[g.uav_id, t])
        genes[i] = Gene(
            uav_id=g.uav_id, target_id=best_t,
            cost=float(cost_matrix[g.uav_id, best_t]),
        )
        missing.remove(best_t)


def _ensure_all_targets_covered(
    genes: list[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    rng: np.random.Generator,
) -> None:
    """N>M 后处理：确保每个目标至少被一个 UAV 访问。

    对应规则 3.2（N>M）：每个目标至少被分配一个 UAV。
    如果某个目标未被覆盖，将代价最高的重复 UAV 重分配给该目标。
    """
    covered_targets = set(g.target_id for g in genes)
    uncovered = [t for t in range(n_targets) if t not in covered_targets]

    if not uncovered:
        return

    # 找到被分配了多个 UAV 的目标（可以替换掉一个）
    target_count: dict[int, int] = {}
    for g in genes:
        target_count[g.target_id] = target_count.get(g.target_id, 0) + 1

    replaceable = [t for t, c in target_count.items() if c > 1]

    for tgt in uncovered:
        if not replaceable:
            break

        # 从可替换目标中选一个，替换其代价最高的 UAV
        src_tgt = rng.choice(replaceable)
        src_genes = [i for i, g in enumerate(genes) if g.target_id == src_tgt]
        if not src_genes:
            continue

        # 选代价最高的基因替换
        worst_idx = max(src_genes, key=lambda i: genes[i].cost)
        old_gene = genes[worst_idx]
        new_cost = float(cost_matrix[old_gene.uav_id, tgt])
        genes[worst_idx] = Gene(uav_id=old_gene.uav_id, target_id=tgt, cost=new_cost)

        # 更新可替换列表
        target_count[src_tgt] -= 1
        target_count[tgt] = target_count.get(tgt, 0) + 1
        if target_count[src_tgt] <= 1:
            replaceable.remove(src_tgt)


def _perturb_genes(
    genes: list[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    model_type: str,
    temperature: float,
    rng: np.random.Generator,
) -> None:
    """对已匹配的基因执行随机扰动。

    扰动类型：
    - swap: 随机交换两个基因的目标分配。
    - reassign: 随机将一个基因重新匹配到其他可行位置。

    扰动概率与温度正相关。
    """
    n_genes = len(genes)
    if n_genes < 2:
        return

    # 扰动概率：温度 1.0 → 40%，温度 0.1 → 2%
    perturb_prob = temperature * 0.4

    # swap 扰动
    if rng.random() < perturb_prob and n_genes >= 2:
        i, j = rng.choice(n_genes, size=2, replace=False)
        if genes[i].uav_id >= 0 and genes[j].uav_id >= 0:
            # 交换目标
            genes[i], genes[j] = Gene(
                uav_id=genes[i].uav_id, target_id=genes[j].target_id,
                cost=float(cost_matrix[genes[i].uav_id, genes[j].target_id])
            ), Gene(
                uav_id=genes[j].uav_id, target_id=genes[i].target_id,
                cost=float(cost_matrix[genes[j].uav_id, genes[i].target_id])
            )

    # reassign 扰动：仅 overloaded 模型允许（目标可被多个 UAV 重复执行）。
    # balanced 模型必须保持 UAV↔Target 一一对应，reassign 会把某个目标
    # 重复指派给多个 UAV，破坏互斥约束（导致部分目标漏分配）。
    if rng.random() < perturb_prob and model_type == "overloaded":
        idx = rng.integers(n_genes)
        g = genes[idx]
        if g.uav_id >= 0:
            # 找同 UAV 可达的其他目标
            row = g.uav_id
            candidates = []
            for col in range(cost_matrix.shape[1]):
                if col != g.target_id and cost_matrix[row, col] < np.inf:
                    candidates.append(col)
            if candidates:
                new_col = rng.choice(candidates)
                genes[idx] = Gene(
                    uav_id=row, target_id=new_col,
                    cost=float(cost_matrix[row, new_col])
                )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SRP 反映射 (N<M 巡游模型)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _inverse_phi_srp(
    cost_vector: np.ndarray,
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    rng: np.random.Generator,
    temperature: float = 0.5,
) -> Individual:
    """SRP 反映射（N<M 巡游模型）。

    扰动后强制做段结构规范化（见 :func:`normalize_srp_segments`）。

    访问顺序对齐原文（Ming 2017 §4.1 Rule 1 + Rule 2(c)）：
    1. 巡游段 ``[K, M)`` 的每一位独立地在「当前已访问目标」的行上做最近邻
       匹配（详见 :func:`_match_tour_edge`），使**顺序由 DE 的连续向量决定**，
       而非历史实现中的「按任务编号顺序 + 贪心最近追加」。
    2. 匹配后再执行扰动（swap/insert/reverse），进一步打破确定性。

    方案 A 口径：只解码「UAV 归属 + 访问顺序」，基因总数恒为 ``M``；
    终点机巢由解码后内层最小费用流求解，不进编码。
    """
    cm_work = cost_matrix.copy().astype(float)
    mask = np.zeros_like(cm_work, dtype=bool)
    genes: list[Gene] = []

    # ── 第一阶段：UAV → 初始目标 ──
    assigned_targets: set[int] = set()
    uav_first_target: dict[int, int] = {}

    for i in range(min(n_uavs, len(cost_vector))):
        cv = cost_vector[i]
        row = i

        if not np.isfinite(cv) or cv < 0:
            available = [t for t in range(n_targets) if t not in assigned_targets]
            tgt = rng.choice(available) if available else rng.integers(n_targets)
        else:
            # 温度自适应：在未分配目标中 softmax 采样
            available = [t for t in range(n_targets) if t not in assigned_targets]
            if not available:
                tgt = rng.integers(n_targets)
            else:
                # 不可达（inf/nan）候选必须先排除出采样，与
                # ``nearest_match_adaptive`` 的 inf 防护同口径：
                # |inf - cv| = inf → logits = -inf；当该 UAV 对**所有**未分配
                # 任务都不可达时 logits.max() = -inf，(-inf) - (-inf) = nan
                # → probs 全 NaN → rng.choice 抛 "Probabilities contain NaN"。
                # 全不可达时健壮兜底（随机挑一个），由解码端 _repair_range
                # 改派给可达机——可达性裁决在解码端，不在采样端。
                reach = [t for t in available if np.isfinite(cm_work[row, t])]
                if not reach:
                    tgt = int(available[rng.integers(len(available))])
                else:
                    diffs = np.abs(
                        np.asarray([cm_work[row, t] for t in reach], dtype=float) - cv
                    )
                    if temperature < 0.1 or len(reach) == 1:
                        tgt = reach[int(np.argmin(diffs))]
                    else:
                        tau = max(0.01, temperature * 2.0)
                        spread = float(np.std(diffs))
                        if not np.isfinite(spread) or spread <= 1e-8:
                            # 零方差/异常方差：退化为纯开发，避免污染 softmax
                            tgt = reach[int(np.argmin(diffs))]
                        else:
                            logits = -diffs / spread / tau
                            logits -= logits.max()
                            probs = np.exp(logits)
                            probs /= probs.sum()
                            tgt = reach[int(rng.choice(len(reach), p=probs))]

        cost = float(cm_work[i, tgt])
        genes.append(Gene(uav_id=i, target_id=tgt, cost=cost))
        assigned_targets.add(tgt)
        uav_first_target[i] = tgt
        mask[:, tgt] = True

    # ── 第二阶段：巡游顺序（Target → Target）──
    remaining_targets = [t for t in range(n_targets) if t not in assigned_targets]

    # 各 UAV 路线段当前最后一个基因在 genes 中的下标。
    # 巡游基因必须插入到所属 UAV 路线段的末尾（与 PopulationEncoder 的
    # 交错式基因顺序保持一致），而不是追加到全局列表末尾：
    # Individual.assignment 按「最近一个 uav_id>=0 的基因」归属巡游目标，
    # 追加到末尾会把所有巡游目标错误地挂到最后一架 UAV 上，
    # 导致子代 fitness 系统性劣于父代、进化零接受（S2 停滞的根因）。
    uav_end_index: dict[int, int] = {i: i for i in range(len(genes))}

    # Ming 2017 §4.1 Rule 2(c)「迭代行替换」+ Rule 1「最近邻匹配」。
    #
    #   每个巡游基因位 j ∈ [0, M-K) 拥有自己的连续代价值 cv = cost_vector[K + j]；
    #   匹配所用的**行**由上一轮已访问目标决定 —— 即把「UAV 起点行」替换为
    #   「当前已访问目标行」（原文：uses current visited target point as the
    #   UAV's new start point），列限定为未访问任务。
    #
    # 于是「接下来访问哪个任务」与「挂在哪条航线尾」都由 DE 的连续向量决定，
    # 访问顺序真正进入进化搜索空间。旧实现此处只做「按任务编号顺序取任务 +
    # 贪心最近追加」，完全不读 cost_vector[K, M)，使该区段成为死维度。
    #
    # 复杂度：每位的候选矩阵为 K×(剩余任务数)，整体 O(K·(M-K)^2)；数值全部由
    # numpy 完成，规模被第一阶段的 Top-αK 选中集合封顶。
    for j in range(len(remaining_targets)):
        cv_idx = n_uavs + j
        cv = (
            float(cost_vector[cv_idx])
            if 0 <= cv_idx < len(cost_vector)
            else float("nan")
        )

        uav_id, tgt, cost = _match_tour_edge(
            cv=cv,
            cost_matrix=cm_work,
            n_uavs=n_uavs,
            uav_last_target=uav_first_target,
            candidates=remaining_targets,
            temperature=temperature,
            rng=rng,
        )
        if uav_id < 0 or tgt < 0:
            break  # 无可行候选（退化情形），交由规范化阶段回收

        # 插入到 uav_id 路线段的末尾
        insert_at = uav_end_index[uav_id] + 1
        genes.insert(insert_at, Gene(uav_id=TOUR_UAV_ID, target_id=tgt, cost=cost))
        # 后移受插入影响的各 UAV 段结束下标
        for u, end in uav_end_index.items():
            if end >= insert_at:
                uav_end_index[u] = end + 1
        uav_end_index[uav_id] = insert_at
        uav_first_target[uav_id] = tgt
        remaining_targets.remove(tgt)

    # ── 第三阶段：巡游扰动 ──
    if temperature > 0.1:
        _perturb_srp_tour(genes, cm_work, n_uavs, n_targets, temperature, rng)

    # ── 第四阶段：段结构规范化 ──
    # reverse / insert 扰动会把 uav_id >= 0 的起始基因卷进子序列
    # （实测出现过起始顺序 [0, 2, 1]），必须先重建严格的航次分段。
    # 方案 A：不生成终止基因，基因总数恒为 M。
    genes = normalize_srp_segments(genes, cm_work, n_uavs, n_targets)

    return Individual(genes=genes, model_type="srp")


def _match_tour_edge(
    cv: float,
    cost_matrix: np.ndarray,
    n_uavs: int,
    uav_last_target: dict[int, int],
    candidates: list[int],
    temperature: float,
    rng: np.random.Generator,
) -> tuple[int, int, float]:
    """Rule 2(c) 的单次迭代：在「已替换为当前已访问目标」的行上匹配一条巡游边。

    对应 Ming 2017 §4.1 Rule 1（最近邻匹配）与 Rule 2(c)（迭代行替换）：

    - 行集合 = ``{n_uavs + last_target[u]}``，即每架 UAV 的**当前已访问最后
      一个任务**所在的行（原文所谓 "new start point"）；
    - 列集合 = 尚未访问的任务；
    - 每个巡游基因位的连续代价值 ``cv`` 在这张 K×|cand| 子矩阵上做最近邻匹配
      （``temperature >= 0.1`` 时退化为温差 softmax 采样），同时决定「访问哪个
      任务」与「接入哪条航线」。

    Args:
        cv:             该巡游基因位的连续代价值（来自 DE 差分/交叉后的向量）。
        cost_matrix:    扩展代价矩阵 ``(K+M, M+B)``。
        n_uavs:         UAV 数 K。
        uav_last_target:``{uav_id: 该航线当前最后一个任务下标}``。
        candidates:     未访问任务下标列表。
        temperature:    温度；``< 0.1`` 时纯开发（取最近），否则 softmax 采样。
        rng:            随机数生成器。

    Returns:
        ``(uav_id, target_idx, cost)``；无可行候选时返回 ``(-1, -1, inf)``。
    """
    if not candidates or not uav_last_target:
        return -1, -1, float("inf")

    n_rows, n_cols = cost_matrix.shape
    # 行/列必须成对裁剪，否则 flat 下标无法还原回 (uav, target)
    pairs = [
        (u, n_uavs + uav_last_target[u])
        for u in sorted(uav_last_target)
        if 0 <= n_uavs + uav_last_target[u] < n_rows
    ]
    cols = [c for c in candidates if 0 <= c < n_cols]
    if not pairs or not cols:
        return -1, -1, float("inf")

    rows = [r for _, r in pairs]
    uav_ids = [u for u, _ in pairs]

    costs = np.asarray(cost_matrix[np.ix_(rows, cols)], dtype=float)
    costs = np.where(np.isfinite(costs), costs, np.inf)

    if np.isfinite(cv) and cv >= 0.0:
        diffs = np.abs(costs - cv)
    else:
        # Rule 3：无效值（nan / 负数 / 越界）退化为最小代价，保住健壮兜底语义
        diffs = costs.copy()

    flat_size = int(diffs.size)
    spread = float(np.std(diffs)) if flat_size else float("nan")
    if temperature < 0.1 or not np.isfinite(spread) or spread <= 1e-8:
        # np.argmin 在多维数组上返回的即扁平下标
        flat = int(np.argmin(diffs))
    else:
        tau = max(0.01, temperature * 2.0)
        # 必须摊平成 1 维权重，否则 rng.choice 的 p 参数会报错
        logits = (-diffs / spread / tau).ravel()
        logits -= logits.max()
        probs = np.exp(logits)
        probs /= probs.sum()
        flat = int(rng.choice(flat_size, p=probs))

    row_i, col_i = divmod(flat, len(cols))
    return uav_ids[row_i], cols[col_i], float(costs[row_i, col_i])


def _perturb_srp_tour(
    genes: list[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
    n_targets: int,
    temperature: float,
    rng: np.random.Generator,
) -> None:
    """对 SRP 巡游顺序执行扰动。

    扰动类型（随机选择一种）：
    - swap: 随机交换两个巡游基因的目标。
    - insert: 将一个基因插入到另一位置。
    - reverse: 反转一段子序列。

    扰动概率与温度正相关。
    """
    # 找出巡游基因（uav_id == -1）
    tour_indices = [i for i, g in enumerate(genes) if g.uav_id == -1]
    if len(tour_indices) < 2:
        return

    perturb_prob = temperature * 0.5
    if rng.random() >= perturb_prob:
        return

    op = rng.choice(['swap', 'insert', 'reverse'])

    if op == 'swap' and len(tour_indices) >= 2:
        i, j = rng.choice(tour_indices, size=2, replace=False)
        gi, gj = genes[i], genes[j]
        # 交换目标；所有巡游边的前驱都可能随之改变，随后统一重算代价。
        genes[i] = Gene(uav_id=-1, target_id=gj.target_id, cost=gj.cost)
        genes[j] = Gene(uav_id=-1, target_id=gi.target_id, cost=gi.cost)

    elif op == 'insert' and len(tour_indices) >= 2:
        src = rng.choice(tour_indices)
        dst = rng.choice(tour_indices)
        if src != dst:
            g = genes.pop(src)
            # 重新计算插入位置
            dst_adjusted = dst if dst < src else dst - 1
            genes.insert(dst_adjusted, g)

    elif op == 'reverse' and len(tour_indices) >= 3:
        i, j = sorted(rng.choice(tour_indices, size=2, replace=False))
        # 反转子序列
        sub = genes[i:j+1]
        sub.reverse()
        genes[i:j+1] = sub

    # insert/reverse（以及非相邻 swap）会改变被移动边之后各边的前驱。
    # Gene.cost 是连续空间中的编码值，必须和新的巡游顺序保持一致。
    _recalculate_srp_tour_costs(genes, cost_matrix, n_uavs)


def _recalculate_srp_tour_costs(
    genes: list[Gene],
    cost_matrix: np.ndarray,
    n_uavs: int,
) -> None:
    """Update every target-to-target gene cost from its current predecessor.

    SRP genes are ordered route legs: a direct ``UAV -> target`` gene starts a
    route and every following ``uav_id == -1`` gene is a ``target -> target``
    leg.  Rebuild immutable :class:`Gene` instances so no cost can remain tied
    to the order before a perturbation.
    """
    previous_target: int | None = None
    for index, gene in enumerate(genes):
        if gene.uav_id >= 0:
            previous_target = gene.target_id
            continue

        if previous_target is not None:
            genes[index] = Gene(
                uav_id=TOUR_UAV_ID,
                target_id=gene.target_id,
                cost=float(cost_matrix[n_uavs + previous_target, gene.target_id]),
            )
        previous_target = gene.target_id
