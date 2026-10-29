# -*- coding: utf-8 -*-
"""fig_trajectory —— trajectory = path + 时间参数化。

path 按到达时刻着色（viridis + colorbar），明确 trajectory = path + 时间维。
注意：当前时间是匀速占位（uniform_placeholder），非 Lin2021 §2.4 时间最优轨迹。
仅画可行且平滑成功（有 path/trajectory）的 OD。
"""

from __future__ import annotations

import numpy as np

from .registry import PlotContext
from .scene import draw_buildings, draw_dynamic_nofly, draw_od
from .theme import plt
from . import theme


def plot_trajectory(ctx: PlotContext, out_dir: str) -> list[str]:
    env, results = ctx.env, ctx.results
    usable = [
        r for r in results
        if r.get("feasible") and not r.get("smooth_failed")
        and r.get("path_xy") and r.get("trajectory")
    ]
    plot_items = [
        (r, np.array(r["path_xy"]), np.array(r["trajectory"]["times_s"]))
        for r in usable
    ]

    fig, ax = plt.subplots(figsize=theme.FIG_SQUARE)
    draw_buildings(ax, env)
    draw_dynamic_nofly(ax, env, first_only=False)

    if plot_items:
        cmap = plt.get_cmap("viridis")
        from matplotlib.colors import Normalize

        tmin = min(arr_t.min() for _, _, arr_t in plot_items)
        tmax = max(arr_t.max() for _, _, arr_t in plot_items)
        norm = Normalize(vmin=tmin, vmax=tmax)
        sc = None
        for r, p2, arr_t in plot_items:
            sc = ax.scatter(p2[:, 0], p2[:, 1], c=arr_t, cmap=cmap, norm=norm,
                            s=9, zorder=3)
            draw_od(ax, r["origin"], r["dest"])
        if sc is not None:
            cbar = fig.colorbar(sc, ax=ax)
            cbar.set_label("到达时刻 time (s)  [匀速占位，非时间最优]")

    ax.set_aspect("equal")
    ax.set_title("trajectory = path + 时间参数化（颜色=到达时刻，当前为匀速占位）")
    path = theme.save_figure(fig, out_dir, "trajectory")
    return [path]
