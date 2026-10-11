# -*- coding: utf-8 -*-
"""scale_factor.py — 动态缩放因子 F（公式 3-11）

对应论文（Zhao Ming 等, 2017, 离散映射差分进化 DMDE）公式 (3-11)：
    F(t) = CR(t),          if rand[0,1] <= CR(t)
    F(t) = (1 - CR(t))/2,   if rand[0,1] >  CR(t)

说明：
    F 与动态交叉率 CR 联动，使差分比例因子在整个进化过程中动态变化：
    前期 CR 高（探索为主）→ F ≈ CR，步长接近 1；
    后期 CR 低（开发为主）→ F 落到 (1-CR)/2，步长进一步收窄以细化搜索。
    因此 F 的取值范围为 [0, 1]。

    判据严格按原文写作 `rand <= CR`（scalar 与 batch 两个入口保持一致）。

    注：GMR（世代灭绝，公式 3-12）在实现上额外引入了阈值 delta，
        使灭绝主要发生在搜索后期，属本文的实现取舍，不改变本文件的 F 定义。
"""

from __future__ import annotations

import numpy as np


def dynamic_scale_factor(cr: float, rng: np.random.Generator | None = None) -> float:
    """计算单个动态缩放因子 F（公式 3-11）。

    Args:
        cr:  当前代的动态交叉率。
        rng: 随机数生成器（可选，缺省新建）。

    Returns:
        缩放因子 F，取值范围 [0, 1]。
    """
    if rng is None:
        rng = np.random.default_rng()
    r = rng.random()
    if r <= cr:
        return float(cr)
    return float((1.0 - cr) / 2.0)


def dynamic_scale_factor_batch(
    cr: float,
    size: int,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """批量计算动态缩放因子 F（公式 3-11）。

    与 :func:`dynamic_scale_factor` 使用同一判据 `rand <= CR`，逐元素独立采样。

    Args:
        cr:   当前代的动态交叉率。
        size: 批量大小。
        rng:  随机数生成器（可选，缺省新建）。

    Returns:
        长度为 ``size`` 的缩放因子数组，取值范围 [0, 1]。
    """
    if rng is None:
        rng = np.random.default_rng()
    rs = rng.random(size)
    return np.where(rs <= cr, float(cr), (1.0 - float(cr)) / 2.0)
