# -*- coding: utf-8 -*-
"""exp01_smoke 场景绘制基元：建筑 / 禁飞区 / OD 标记的统一画法。

各图模块只组合这里的基元 + 自己的图层，不重复造"画建筑"的轮子；
颜色一律取自 :mod:`theme.PALETTE`，保证 5 张图视觉口径一致。
"""

from __future__ import annotations

from typing import Optional

from .theme import PALETTE

__all__ = ["bgeom", "draw_buildings", "draw_dynamic_nofly", "draw_static_nofly",
           "draw_od"]


def bgeom(poly):
    """多部件面取最大部件（画图用；判定不用它）。"""
    return poly if poly.geom_type == "Polygon" else max(poly.geoms, key=lambda x: x.area)


def draw_buildings(ax, env, *, face: Optional[str] = None, alpha: float = 0.7,
                   lw: float = 0.0, zorder: int = 1) -> None:
    """建筑轮廓填充（默认浅灰；压线≠碰撞——可合法飞越低于飞行高度的楼）。"""
    for b in env.buildings:
        g = bgeom(b.poly)
        x, y = g.exterior.xy
        ax.fill(x, y, facecolor=face or PALETTE["building"], alpha=alpha,
                lw=lw, zorder=zorder)


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
