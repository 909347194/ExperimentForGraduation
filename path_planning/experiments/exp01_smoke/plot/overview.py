# -*- coding: utf-8 -*-
"""fig_overview —— 场景俯视：建筑 / 控制点 / 规划路径。

冒烟调试的主视图。注意：路径可合法飞越低于飞行高度的建筑，
压灰块 ≠ 碰撞（碰撞判定见 metrics.json 的 audit 字段）。
"""

from __future__ import annotations

from .registry import PlotContext
from .scene import bgeom, draw_dynamic_nofly
from .theme import PALETTE, plt
from . import theme


def plot_overview(ctx: PlotContext, out_dir: str) -> list[str]:
    env, net, results = ctx.env, ctx.net, ctx.results
    fig, ax = plt.subplots(figsize=theme.FIG_SQUARE)

    for b in env.buildings:
        g = bgeom(b.poly)
        x, y = g.exterior.xy
        ax.fill(x, y, color=PALETTE["building"], alpha=0.7)

    pts = net.points
    ax.scatter(pts[:, 0], pts[:, 1], s=2, c=PALETTE["control"],
               label="control points")
    draw_dynamic_nofly(ax, env, first_only=False)

    for r in results:
        xy = r.get("path_xy")
        if xy:
            xs = [p[0] for p in xy]
            ys = [p[1] for p in xy]
            ax.plot(xs, ys, "-", lw=1.6, color=PALETTE["path"],
                    label=f"OD{r['od_id']}({r['level']})")
        ax.plot([r["origin"][0]], [r["origin"][1]], "o",
                color=PALETTE["origin"], ms=7)
        ax.plot([r["dest"][0]], [r["dest"][1]], "s",
                color=PALETTE["dest"], ms=7)

    ax.set_aspect("equal")
    ax.set_title("exp01_smoke: buildings / control points / planned paths\n"
                 "(俯视图：路径可合法飞越低于飞行高度的建筑，压灰块≠碰撞)")
    ax.legend(loc="upper right", fontsize=theme.FS["legend"])
    path = theme.save_figure(fig, out_dir, "overview")
    return [path]
