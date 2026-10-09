# -*- coding: utf-8 -*-
"""nearest_match.py — 规则 3.4：最近邻空间欧氏距离匹配

改进记录：
    - nearest_match_stochastic: top-k 采样匹配。
    - nearest_match_adaptive: 温度自适应匹配，
      top_k 随温度动态调节，早期探索多、后期开发多。
"""

from __future__ import annotations

import numpy as np

# top_k 的上下界
TOP_K_MIN = 1   # 纯贪心
TOP_K_MAX = 8   # 强随机


def nearest_match(
    target_value: float,
    cost_matrix: np.ndarray,
    mask: np.ndarray,
) -> tuple[int, int, float] | None:
    """纯贪心最近邻匹配（原始规则 3.4）。"""
    diff = np.abs(cost_matrix - target_value)
    diff[mask] = np.inf

    min_idx = np.unravel_index(np.argmin(diff), diff.shape)
    if diff[min_idx] == np.inf:
        return None

    row, col = min_idx
    cost = cost_matrix[row, col]
    return (int(row), int(col), float(cost))


def nearest_match_stochastic(
    target_value: float,
    cost_matrix: np.ndarray,
    mask: np.ndarray,
    top_k: int = 3,
    rng: np.random.Generator | None = None,
) -> tuple[int, int, float] | None:
    """随机化最近邻匹配（top-k 采样）。

    从距离最近的 top_k 个候选中随机选一个，
    引入多样性，避免纯贪心导致种群过快收敛。

    Args:
        target_value: 差分后的临时代价值。
        cost_matrix:  代价矩阵。
        mask:         布尔掩码。
        top_k:        候选数量。
        rng:          随机数生成器。

    Returns:
        (row, col, cost) 三元组，无可用匹配时返回 None。
    """
    if rng is None:
        rng = np.random.default_rng()

    diff = np.abs(cost_matrix - target_value)
    diff[mask] = np.inf

    # 找到所有可用位置
    available = np.argwhere(~mask)
    if len(available) == 0:
        return None

    # 按距离排序，取 top-k（使用与 available 相同的过滤条件）
    distances = diff[~mask & np.isfinite(cost_matrix)]
    sorted_indices = np.argsort(distances)
    k = min(top_k, len(sorted_indices))
    chosen_idx = rng.choice(sorted_indices[:k])

    row, col = available[chosen_idx]
    cost = cost_matrix[row, col]
    return (int(row), int(col), float(cost))


def temperature_to_top_k(temperature: float) -> int:
    """将温度值映射到 top_k 值。

    温度 1.0（初期）→ top_k = TOP_K_MAX（强随机）
    温度 0.0（末期）→ top_k = TOP_K_MIN（纯贪心）

    Args:
        temperature: 有限且位于 [0.0, 1.0] 的温度值。

    Returns:
        top_k 值。
    """
    if not np.isfinite(temperature) or not 0.0 <= temperature <= 1.0:
        raise ValueError("temperature must be a finite value in [0.0, 1.0]")

    return int(round(TOP_K_MIN + (TOP_K_MAX - TOP_K_MIN) * temperature))


def nearest_match_adaptive(
    target_value: float,
    cost_matrix: np.ndarray,
    mask: np.ndarray,
    temperature: float = 0.5,
    rng: np.random.Generator | None = None,
) -> tuple[int, int, float] | None:
    """温度自适应最近邻匹配。

    温度高时 top_k 大（探索），温度低时 top_k 小（开发）。
    在 top-k 候选中使用 softmax 采样而非均匀随机，
    使距离更近的候选有更高概率被选中。

    Args:
        target_value:  差分后的临时代价值。
        cost_matrix:   代价矩阵。
        mask:          布尔掩码。
        temperature:   有限的温度值 [0.0, 1.0]。0.0 是严格贪心；
                       1.0 使用最大的候选集和最平缓的 softmax。
        rng:           随机数生成器。

    Returns:
        (row, col, cost) 三元组。
    """
    if rng is None:
        rng = np.random.default_rng()

    # Keep the public boundary explicit.  In particular, do not silently turn
    # a negative, NaN, or above-range temperature into a different policy.
    temperature_to_top_k(temperature)

    if not np.isfinite(target_value):
        return None

    diff = np.abs(cost_matrix - target_value)
    valid = ~mask & np.isfinite(cost_matrix)
    diff[~valid] = np.inf

    available = np.argwhere(valid)
    if len(available) == 0:
        return None

    distances = diff[valid]
    sorted_indices = np.argsort(distances)

    top_k = temperature_to_top_k(temperature)
    k = min(top_k, len(sorted_indices))

    if temperature == 0.0 or k <= 1:
        # The zero-temperature limit is deterministic (strictly greedy), so
        # no softmax division by a near-zero temperature is performed.
        chosen_idx = sorted_indices[0]
    else:
        # softmax 采样：距离越小概率越高
        top_distances = distances[sorted_indices[:k]]
        spread = float(np.std(top_distances))
        if spread <= np.finfo(float).eps:
            # Equal-distance candidates have no preference; choose uniformly
            # rather than manufacturing a preference via an epsilon divisor.
            probs = np.full(k, 1.0 / k)
        else:
            # Temperature increases softness.  Shifting the logits prevents
            # overflow while preserving the softmax distribution.
            logits = -top_distances / spread / temperature
            logits -= logits.max()
            probs = np.exp(logits)
            probs /= probs.sum()
        chosen_local = rng.choice(k, p=probs)
        chosen_idx = sorted_indices[chosen_local]

    row, col = available[chosen_idx]
    cost = cost_matrix[row, col]
    return (int(row), int(col), float(cost))
