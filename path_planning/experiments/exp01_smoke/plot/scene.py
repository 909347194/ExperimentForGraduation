# -*- coding: utf-8 -*-
"""exp01_smoke 场景绘制基元：建筑 / 禁飞区 / OD 标记的统一画法。

各图模块只组合这里的基元 + 自己的图层，不重复造"画建筑"的轮子；
颜色一律取自 :mod:`theme.PALETTE`，保证各图视觉口径一致。

建筑用 **单个 PolyCollection** 一次性填充全部足迹（而非逐栋 ``ax.fill``）：

* 性能：整城 2 万栋也能秒级出图（逐栋 fill 会慢一个量级）；
* 可辨：带描边（``building_edge``）后，密集区不再是糊成一片的灰块，
  论文灰度打印时也能分出单栋轮廓。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from matplotlib.collections import PolyCollection

from .theme import PALETTE

__all__ = [
    "bgeom",
    "building_rings",
    "draw_buildings",
    "draw_dynamic_nofly",
    "draw_static_nofly",
    "draw_od",
]


def bgeom(poly):
    """多部件面取最大部件（画图用；判定不用它）。"""
    return poly if poly.geom_type == "Polygon" else max(poly.geoms, key=lambda x: x.area)


def building_rings(env, *, min_area: float = 0.0) -> list[np.ndarray]:
    """全部建筑的平面外环坐标（Nx2 数组列表），供 :class:`PolyCollection` 使用。

    min_area: 小于该面积的足迹丢弃（整城图降低噪声/耗时用；默认不过滤）。
    """
    rings: list[np.ndarray] = []
    for b in env.buildings:
        if min_area > 0.0 and b.poly.area < min_area:
            continue
        g = bgeom(b.poly)
        rings.append(np.asarray(g.exterior.coords, dtype=float))
    return rings


def draw_buildings(
    ax,
    env,
    *,
    face: Optional[str] = None,
    edge: Optional[str] = None,
    alpha: float = 0.9,
    lw: float = 0.25,
    zorder: int = 1,
    min_area: float = 0.0,
) -> Optional[PolyCollection]:
    """建筑足迹填充 + 描边（单次 PolyCollection）。

    压线≠碰撞——路径可合法飞越低于飞行高度的楼（碰撞判定见 metrics 的 audit）。
    返回 collection（便于上层改色/加 legend），无建筑时返回 None。
    """
    rings = building_rings(env, min_area=min_area)
    if not rings:
        return None
    coll = PolyCollection(
        rings,
        facecolors=face or PALETTE["building"],
        edgecolors=edge or PALETTE["building_edge"],
        linewidths=lw,
        alpha=alpha,
        zorder=zorder,
    )
    ax.add_collection(coll)
    # 显式更新数据范围：避免某些后端 add_collection 后坐标轴未自动 scale
    allpts = np.vstack(rings)
    ax.update_datalim(allpts)
    ax.autoscale_view()
    return coll


def draw_dynamic_nofly(ax, env, *, first_only: bool = True, lw: float = 1.2,
                       fill: bool = False, label: Optional[str] = "动态禁飞区(无限高)",
                       zorder: int = 2) -> None:
    """动态临时禁飞区轮廓（红虚线；fill=True 时淡红填充）。"""
    labeled = False
    for z in env.dynamic_nofly:
        for p in z.periods[:1] if first_only else z.periods:
            g = bgeom(p.poly)
            x, y = g.exterior.xy
            lab = label if (label and not labeled) else None
            if fill:
                ax.fill(x, y, color=PALETTE["nofly_dyn"], alpha=0.18, zorder=zorder)
            ax.plot(x, y, color=PALETTE["nofly_dyn"], lw=lw, ls="--",
                    alpha=0.75, zorder=zorder, label=lab)
            labeled = True


def draw_static_nofly(ax, env, *, label: str = "静态禁飞区(无限高)") -> None:
    """静态永久禁飞区（红填充 + 描边）。"""
    labeled = False
    for z in env.static_nofly:
        g = bgeom(z.poly)
        x, y = g.exterior.xy
        ax.fill(x, y, color=PALETTE["nofly_stat"], alpha=0.22, zorder=2)
        ax.plot(x, y, color=PALETTE["nofly_stat"], lw=1.6, zorder=2,
                label=label if not labeled else None)
        labeled = True


def draw_od(ax, origin, dest, *, ms: float = 7, zorder: int = 4) -> None:
    """起点（圆）/ 终点（方）标记。"""
    ax.plot([origin[0]], [origin[1]], "o", color=PALETTE["origin"], ms=ms, zorder=zorder)
    ax.plot([dest[0]], [dest[1]], "s", color=PALETTE["dest"], ms=ms, zorder=zorder)
