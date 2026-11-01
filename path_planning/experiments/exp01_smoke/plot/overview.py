# -*- coding: utf-8 -*-
"""fig_overview —— 场景俯视：建筑 / 可见性图节点 / 规划路径。

冒烟调试的主视图。注意：路径可合法飞越低于飞行高度的建筑，
压灰块 ≠ 碰撞（碰撞判定见 metrics.json 的 audit 字段）。

每条 OD 用 :data:`theme.OD_CYCLE` 里的独立颜色——之前的实现所有 OD 共用
``PALETTE["path"]``，图例里 6 条一模一样的蓝线，等于没有图例。
"""

from __future__ import annotations

import numpy as np

from .registry import PlotContext
from .scene import draw_buildings, draw_dynamic_nofly
from .theme import OD_CYCLE, PALETTE, plt
from . import theme


def plot_overview(ctx: PlotContext, out_dir: str) -> list[str]:
    env, graph, results = ctx.env, ctx.graph, ctx.results
    fig, ax = plt.subplots(figsize=theme.FIG_SQUARE)

    draw_buildings(ax, env)
    pts = np.asarray(graph.nodes)
    ax.scatter(pts[:, 0], pts[:, 1], s=2.5, c=PALETTE["node"], alpha=0.55,
               label="graph nodes", zorder=2)
    draw_dynamic_nofly(ax, env, first_only=False)

    for k, r in enumerate(results):
        col = OD_CYCLE[k % len(OD_CYCLE)]
        xy = r.get("path_xy")
        if xy:
            xs = [p[0] for p in xy]
            ys = [p[1] for p in xy]
            ax.plot(xs, ys, "-", lw=1.8, color=col, zorder=3,
                    label=f"OD{r['od_id']}({r['level']})")
        ax.plot([r["origin"][0]], [r["origin"][1]], "o", color=col,
                ms=7, mec="white", mew=0.8, zorder=4)
        ax.plot([r["dest"][0]], [r["dest"][1]], "s", color=col,
                ms=7, mec="white", mew=0.8, zorder=4)

    ax.set_aspect("equal")
    ax.set_title("exp01_smoke: buildings / graph nodes / planned paths\n"
                 "(俯视图：路径可合法飞越低于飞行高度的建筑，压灰块≠碰撞)")
    ax.legend(loc="upper right", fontsize=theme.FS["legend"], framealpha=0.9)
    path = theme.save_figure(fig, out_dir, "overview")
    return [path]
