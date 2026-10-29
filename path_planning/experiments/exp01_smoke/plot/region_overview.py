# -*- coding: utf-8 -*-
"""fig_region_overview —— 整个区域总览（俯视图）。

全城建筑分布热力 + 研究区域边界 + 静态/动态禁飞区 + 裁剪活跃窗口 + 规划路径，
左上角放大窗给出裁剪窗口内的清晰视图。解决"只画裁剪窗口、看不到整体"的问题。
"""

from __future__ import annotations

import json
import os

import numpy as np

from .registry import PlotContext
from .scene import bgeom, draw_static_nofly
from .theme import PALETTE, plt
from . import theme


def plot_region_overview(ctx: PlotContext, out_dir: str) -> list[str]:
    env_full, env, results = ctx.env_full, ctx.env, ctx.results
    fig, ax = plt.subplots(figsize=theme.FIG_WIDE)

    # 全区域建筑：极淡热力（密度背景），避免淹没路径
    bx = np.array([b.poly.centroid.x for b in env_full.buildings])
    by = np.array([b.poly.centroid.y for b in env_full.buildings])
    hb = ax.hexbin(bx, by, gridsize=55, cmap="YlOrRd", alpha=0.70, mincnt=1,
                   bins="log")
    cb = fig.colorbar(hb, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("建筑密度 (栋/格, 对数)")

    # 研究区域边界（region.geojson → 局部坐标）
    try:
        from shapely.geometry import shape

        from path_planning.utils.geometry import LocalCRS

        rg_path = os.path.join(ctx.scenario_dir, "region.geojson")
        with open(rg_path, encoding="utf-8") as f:
            rg = json.load(f)
        crs = LocalCRS(env.meta["crs"]["lon0"], env.meta["crs"]["lat0"])
        for feat in rg.get("features", []):
            g = crs.transform_geom(shape(feat["geometry"]))
            polys = g.geoms if g.geom_type == "MultiPolygon" else [g]
            for gp in polys:
                xs, ys = gp.exterior.xy
                ax.plot(xs, ys, color="#222222", lw=2.4, label="研究区域边界")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 区域边界加载失败: {e}")

    draw_static_nofly(ax, env)
    labeled = False
    for z in env.dynamic_nofly:
        for p in z.periods[:1]:
            g = bgeom(p.poly)
            xs, ys = g.exterior.xy
            ax.plot(xs, ys, color=PALETTE["nofly_dyn"], lw=1.6, ls="--",
                    label="动态禁飞区(无限高)" if not labeled else None)
            labeled = True

    # 裁剪活跃窗口（粗蓝框）+ 主图细路径
    cbx = np.array([b.poly.centroid.x for b in env.buildings])
    cby = np.array([b.poly.centroid.y for b in env.buildings])
    if len(cbx):
        x0, x1 = float(cbx.min()), float(cbx.max())
        y0, y1 = float(cby.min()), float(cby.max())
        ax.fill([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0],
                facecolor=PALETTE["crop"], alpha=0.15)
        ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0],
                color=PALETTE["crop"], lw=2.6, label="裁剪活跃窗口")
        for r in results:
            xy = r.get("path_xy")
            if xy:
                p2 = np.array(xy)
                ax.plot(p2[:, 0], p2[:, 1], "-", lw=1.0,
                        color=PALETTE["path"], alpha=0.7)
            ax.plot([r["origin"][0]], [r["origin"][1]], "o",
                    color=PALETTE["origin"], ms=4)
            ax.plot([r["dest"][0]], [r["dest"][1]], "s",
                    color=PALETTE["dest"], ms=4)

        # 左上角放大窗：清晰显示裁剪窗口内的建筑/禁飞/路径
        axins = ax.inset_axes([0.04, 0.56, 0.34, 0.34])
        for b in env.buildings:
            g = bgeom(b.poly)
            xs, ys = g.exterior.xy
            axins.fill(xs, ys, color="#d0d0d0", alpha=0.85, lw=0)
        labeled = False
        for z in env.dynamic_nofly:
            for p in z.periods[:1]:
                g = bgeom(p.poly)
                xs, ys = g.exterior.xy
                axins.plot(xs, ys, color=PALETTE["nofly_dyn"], lw=1.4, ls="--")
        for r in results:
            xy = r.get("path_xy")
            if xy:
                p2 = np.array(xy)
                axins.plot(p2[:, 0], p2[:, 1], "-", lw=1.8, color=PALETTE["path"],
                           label=f"OD{r['od_id']}({r['level']})" if r["od_id"] == 0 else None)
            axins.plot([r["origin"][0]], [r["origin"][1]], "o",
                       color=PALETTE["origin"], ms=7)
            axins.plot([r["dest"][0]], [r["dest"][1]], "s",
                       color=PALETTE["dest"], ms=7)
        axins.set_xlim(x0, x1)
        axins.set_ylim(y0, y1)
        axins.set_xticks([])
        axins.set_yticks([])
        axins.set_title("裁剪窗口放大", fontsize=theme.FS["footnote"])
        ax.indicate_inset_zoom(axins, edgecolor=PALETTE["crop"])

    ax.set_aspect("equal")
    ax.set_title("整个区域总览：全城建筑(密度热力) + 区域边界 + 静态/动态禁飞区"
                 "+ 蓝色裁剪窗口(规划活跃区) + 左上角放大窗")
    ax.legend(loc="lower left", fontsize=theme.FS["legend"])
    path = theme.save_figure(fig, out_dir, "region_overview")
    print(f"[exp01] 整区域总览图已写入 {path}")
    return [path]
