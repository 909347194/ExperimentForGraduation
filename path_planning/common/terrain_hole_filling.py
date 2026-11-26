# -*- coding: utf-8 -*-
"""地形空洞填充：把米制地形网格里的 NaN 补成确定高程。

职责：为 ``DEMTerrain`` 提供 ``fill_terrain_holes``，返回新地形与统计。
边界：不读 DEM、不做重采样（``dem_geotiff.to_metric_terrain`` 的职责）。
相关：``scenario_loader.py``（加载时调用）、``methods/utils/dem_terrain.py``。

为什么必须补：``to_metric_terrain`` 对原始 DEM 的 nodata（本例 255）做的是
"置零 + 记无效权重"，四邻权重全为零时双线性插值得到 ``0/0 = NaN``。实测广州
158×161 网格有 **10.89%** 空洞。

不补的后果是**静默放行**：净空判据形如 ``z_clear + clearance <= ground``，与 NaN
比较恒为 False，等于告诉优化器"这里可以飞"。SDF 一旦在 NaN 区取值，损失面就是
一片虚假的自由空间，而这种错误不会以任何异常形式暴露。

两种填充口径（由调用方显式选择，函数不自作主张）：

1. ``water_level_m=None``（默认）——**最近邻扩散**：按四邻均值一圈圈外推。
   适合"缺值是数据洞、真实地形与周边连续"的情形。它会把水体糊成岸边高程。
2. ``water_level_m=2.0`` —— **水面高程填充**：所有空洞统一填该值。
   适合"空洞即水域"的情形（广州 DEM 的 nodata=255 就是北缘河道）。

为什么默认不是水面填充：只有调用方知道这批 nodata 是水体还是没有观测的地形。
把数据缺失当水面填低值，会在地形上凿出一条虚假低洼通道，比 NaN 更危险。所以
按铁律"口径由实验侧定"——谁主张这是水，谁显式配水面高程。

广州实测（157×172 原始 DEM，nodata=255，2549 px = 9.44%；30 m 网格 158×161，
2770 洞格）：2389 格直接落在 nodata 上，381 格是岸线过渡带（双线性支撑跨到
nodata），**没有一格落在 DEM 范围外**。最近邻把水面糊成 8.5–38.8 m（岸边陆地
高程），导致名义 AGL=10 m 的任务点实际离水面 16.5–46.8 m。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from path_planning.methods.utils.dem_terrain import DEMTerrain

__all__ = ["fill_terrain_holes"]


def fill_terrain_holes(
    terrain: DEMTerrain,
    *,
    water_level_m: float | None = None,
    max_iter: int = 2000,
) -> tuple[DEMTerrain, dict[str, Any]]:
    """补齐地形 NaN 空洞。

    Args:
        terrain:       待填充的米制地形
        water_level_m: 水面高程（米，绝对 ENU z）。给定则**所有**空洞统一填该值，
                       适用于"空洞即水域"的场景；``None`` 则退回最近邻扩散
        max_iter:      扩散轮次上限（仅最近邻模式使用，每轮把已确定值外推一圈）

    Returns:
        ``(填充后的地形, 统计 dict)``；无空洞时原样返回同一对象。

    Raises:
        ValueError: 整张网格全为空洞（无有效像素），或 ``water_level_m`` 非有限值。
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
        "water_level_m": None,
    }
    if not hole.any():
        return terrain, stats

    known = ~hole
    if not known.any():
        raise ValueError("地形网格全为空洞，无有效像素可作填充种子")

    if water_level_m is not None:
        return _fill_by_water_level(terrain, z, hole, known, water_level_m, stats)

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


def _fill_by_water_level(
    terrain: DEMTerrain,
    z: np.ndarray,
    hole: np.ndarray,
    known: np.ndarray,
    water_level_m: float,
    stats: dict[str, Any],
) -> tuple[DEMTerrain, dict[str, Any]]:
    """把所有空洞统一填成水面高程，并记录岸线台阶幅度。

    台阶统计的意义：水面与岸边陆地之间必然有高差，这是真实地形；但 30 m 网格
    表达不了真实岸坡，于是它在离散场上表现为近垂直墙。SDF 会据此在水边形成
    围栏，规划器无法贴水面穿行——这是已知且可接受的代价，必须量化留档，
    不能事后拿"绕远"去猜原因。
    """
    level = float(water_level_m)
    if not np.isfinite(level):
        raise ValueError(f"water_level_m 必须是有限值：{water_level_m!r}")

    filled = z.copy()
    filled[hole] = level
    out = DEMTerrain.from_array(filled, terrain.meta.bounds)

    # 岸线：洞格的四邻有效值。台阶 = 邻值 - 水面高程（陆地高于水面为正）。
    # 取的是**邻居**的高程而不是洞格自己的（洞格是 NaN），四个方向的 mask 与取值
    # 必须错开一行/列对齐。
    steps = [
        z[:-1, :][hole[1:, :] & known[:-1, :]] - level,   # 洞在下、邻在上
        z[1:, :][hole[:-1, :] & known[1:, :]] - level,    # 洞在上、邻在下
        z[:, :-1][hole[:, 1:] & known[:, :-1]] - level,   # 洞在右、邻在左
        z[:, 1:][hole[:, :-1] & known[:, 1:]] - level,    # 洞在左、邻在右
    ]
    shore = np.concatenate([s for s in steps if s.size]) if any(s.size for s in steps) else np.zeros(0)
    shore = shore[np.isfinite(shore)]

    stats.update(
        filled=int(hole.sum()),
        iterations=0,
        residual=0,
        method="water-level fill",
        water_level_m=level,
        shore_cells=int(shore.size),
        shore_step_median_m=float(np.median(shore)) if shore.size else 0.0,
        shore_step_max_m=float(shore.max()) if shore.size else 0.0,
    )
    return out, stats

