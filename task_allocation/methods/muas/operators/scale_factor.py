# -*- coding: utf-8 -*-
"""scale_factor.py — 动态缩放因子 F（公式 3-11）

对应论文：
    公式 (3-11):
        F(t) = 2*CR(t),          if CR(t) >= rand[0,1]
        F(t) = (2 - CR(t)) / 2,  if CR(t) < rand[0,1]

    缩放因子 F 控制差异变量变异步长的增长幅度。
    与动态交叉率 CR 联动，使进化过程中差分比例因子也是动态改变的。
"""

from __future__ import annotations

import numpy as np


def dynamic_scale_factor(cr: float, rng: np.random.Generator | None = None) -> float:
    """计算动态缩放因子 F。

    对应公式 (3-11)。

    Args:
        cr: 当前代的动态交叉率。
        rng: 随机数生成器（可选）。

    Returns:
        缩放因子 F。
    """
    if rng is None:
        rng = np.random.default_rng()
    r = rng.random()
    if cr >= r:
        return 2.0 * cr
    else:
        return (2.0 - cr) / 2.0


def dynamic_scale_factor_batch(
    cr: float,
    size: int,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """批量计算动态缩放因子。

    Args:
        cr:   当前代的动态交叉率。
        size: 批量大小。
        rng:  随机数生成器。

    Returns:
        缩放因子数组。
    """
    if rng is None:
        rng = np.random.default_rng()
    rs = rng.random(size)
    return np.where(rs <= cr, 2.0 * cr, (2.0 - cr) / 2.0)
