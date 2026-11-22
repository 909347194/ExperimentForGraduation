# -*- coding: utf-8 -*-
"""地形空洞填充：把米制地形网格里的 NaN 按最近邻扩散补齐。

职责：为 ``DEMTerrain`` 提供 ``fill_terrain_holes``，返回新地形与统计。
边界：不读 DEM、不做重采样（``dem_geotiff.to_metric_terrain`` 的职责）。
相关：``scenario_loader.py``（加载时调用）、``methods/utils/dem_terrain.py``。

为什么必须补：``to_metric_terrain`` 对原始 DEM 的 nodata（本例 255）做的是
"置零 + 记无效权重"，四邻权重全为零时双线性插值得到 ``0/0 = NaN``。实测广州
158×161 网格有 **10.89%** 空洞，其中约 900 格落在**内部**（行 131–138 一整块），
其余是边界外圈一列/一行。

不补的后果是**静默放行**：净空判据形如 ``z_clear + clearance <= ground``，与 NaN
比较恒为 False，等于告诉优化器"这里可以飞"。SDF 一旦在 NaN 区取值，损失面就是
一片虚假的自由空间，而这种错误不会以任何异常形式暴露。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from path_planning.methods.utils.dem_terrain import DEMTerrain

__all__ = ["fill_terrain_holes"]


def fill_terrain_holes(
    terrain: DEMTerrain,
    *,
    max_iter: int = 2000,
) -> tuple[DEMTerrain, dict[str, Any]]:
    """按最近邻扩散补齐地形 NaN 空洞。

    Args:
        terrain:  待填充的米制地形
        max_iter: 扩散轮次上限（每轮把已确定值外推一圈）

    Returns:
        ``(填充后的地形, 统计 dict)``；无空洞时原样返回同一对象。

    Raises:
        ValueError: 整张网格全为空洞（无有效像素可作种子）。
    """
    meta = terrain.meta
    h, w = meta.height, meta.width
    left, bottom, right, top = meta.bounds
    res_x, res_y = meta.resolution

    # 节点定义与 DEMTerrain.query_elevations_batch 一致：节点 i 在 x = left + i*res_x
    xs = left + np.arange(w) * res_x
    ys = top - np.arange(h) * res_y
    gx, gy = np.meshgrid(xs, ys)
    z = np.asarray(
        terrain.query_elevations_batch(gx.ravel(), gy.ravel()), dtype=np.float64
    ).reshape(h, w)

    hole = ~np.isfinite(z)
    stats: dict[str, Any] = {
        "cells": int(z.size),
        "holes": int(hole.sum()),
        "hole_ratio": float(hole.mean()),
        "filled": 0,
        "iterations": 0,
        "residual": 0,
        "method": "nearest-neighbour diffusion",
    }
    if not hole.any():
        return terrain, stats

    known = ~hole
    if not known.any():
        raise ValueError("地形网格全为空洞，无有效像素可作填充种子")

    filled = z.copy()
    it = 0
    for it in range(1, max_iter + 1):
        acc = np.zeros_like(filled)
        cnt = np.zeros_like(filled)
        # 四邻域逐方向累加。用切片而非 np.roll：roll 会环绕，把右缘值灌到左缘，
        # 在多孔边界上凭空造出一条错误的等值带
        acc[1:, :] += np.where(known[:-1, :], filled[:-1, :], 0.0)
        cnt[1:, :] += known[:-1, :]
        acc[:-1, :] += np.where(known[1:, :], filled[1:, :], 0.0)
        cnt[:-1, :] += known[1:, :]
        acc[:, 1:] += np.where(known[:, :-1], filled[:, :-1], 0.0)
        cnt[:, 1:] += known[:, :-1]
        acc[:, :-1] += np.where(known[:, 1:], filled[:, 1:], 0.0)
        cnt[:, :-1] += known[:, 1:]
        newly = (~known) & (cnt > 0)
        filled[newly] = acc[newly] / cnt[newly]
        known |= newly
        if known.all():
            break

    residual = int((~known).sum())
    if residual:
        # 理论上不会发生（连通网格必然扩散全图）；真发生说明存在与有效区不连通的
        # 孤岛，用全局均值兜底并如实上报残量
        filled[~known] = float(np.mean(filled[known]))

    out = DEMTerrain.from_array(filled, (left, bottom, right, top))
    stats.update(filled=int(hole.sum()), iterations=it, residual=residual)
    return out, stats
