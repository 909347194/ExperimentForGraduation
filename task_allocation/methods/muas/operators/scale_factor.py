# -*- coding: utf-8 -*-
"""scale_factor.py — 动态缩放因子 F。

职责：按当前交叉率 CR 计算差分缩放因子（标量 batch 两个入口），使步长随进化由大变小，
取值范围 [0,1]。
边界：只算 F，不参与变异或选择。
相关：muas/operators/crossover.py（提供 CR）、mutation.py（使用 F）。"""

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
