# -*- coding: utf-8 -*-
"""fig_route_vs_path —— route 走廊 vs path 平滑曲线。

route 用虚线+图节点标记（走廊，Lin2021 Fig.1(a)），
path 用实线（Chaikin 圆角 / Catmull-Rom 样条平滑，Fig.1(b)）。
注意：Dubins 平滑属已弃用的控制网络方案，本路线**未实现**（二期候选），图注不得写 Dubins。
route 所有可行 OD 都画（即使平滑失败、没有 path，也能看出 route 存在）。
"""

from __future__ import annotations

import numpy as np

from .registry import PlotContext
from .scene import draw_buildings, draw_dynamic_nofly, draw_od
from .theme import OD_CYCLE, PALETTE, plt
from . import theme


def plot_route_vs_path(ctx: PlotContext, out_dir: str) -> list[str]:
    env, results = ctx.env, ctx.results
    fig, ax = plt.subplots(figsize=theme.FIG_SQUARE)

    draw_buildings(ax, env)
    draw_dynamic_nofly(ax, env, first_only=False)

    for k, r in enumerate(results):
        if not r.get("feasible"):
            continue
        col = OD_CYCLE[k % len(OD_CYCLE)]
        rc = r.get("route_control_points")
        if rc:
            rc2 = np.array([[a, b] for a, b, c in rc])
            ax.plot(rc2[:, 0], rc2[:, 1], "--", color=PALETTE["route"],
                    lw=1.0, alpha=0.9, zorder=3)
            ax.plot(rc2[:, 0], rc2[:, 1], "x", color=PALETTE["route"], ms=3,
                    zorder=3)
        pxy = r.get("path_xy")
        if pxy:
            p2 = np.array(pxy)
            ax.plot(p2[:, 0], p2[:, 1], "-", lw=1.8, color=col, zorder=4,
                    label=f"OD{r['od_id']}({r['level']})")
        draw_od(ax, r["origin"], r["dest"])

    from matplotlib.lines import Line2D

    proxy = [
        Line2D([0], [0], ls="--", color=PALETTE["route"], lw=1.0, marker="x",
               ms=3, label="route 走廊（虚线+×=图节点序列）"),
        Line2D([0], [0], ls="-", color="#444444", lw=1.8,
               label="path 平滑曲线（实线，Chaikin/Catmull-Rom）"),
    ]
    h1, l1 = ax.get_legend_handles_labels()
    ax.legend(proxy + h1, [t.get_label() for t in proxy] + l1,
              loc="upper right", fontsize=theme.FS["legend"], framealpha=0.9)
    ax.set_aspect("equal")
    ax.set_title("route（虚线=图节点走廊） vs path（实线=Chaikin/Catmull-Rom 平滑）")
    path = theme.save_figure(fig, out_dir, "route_vs_path")
    return [path]
