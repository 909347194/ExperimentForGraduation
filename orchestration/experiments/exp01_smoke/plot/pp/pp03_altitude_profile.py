# -*- coding: utf-8 -*-
"""PP03 航次高度剖面（PP）：每一航次沿航程怎么爬、飞多高、离地多少。

【PP 层 · 下层 path planning 的账 · 垂直维度的安全性】

一个航次一格：横轴 = 沿航迹的累计水平距离（km），纵轴 = 绝对高程 z（m）。
灰色填充是航迹正下方的地形/屋顶高度，两者之间的空隙就是**真实离地净空**。

为什么用它替掉原来的三维立体图：三维图里两万栋建筑把航线埋掉，读者只能看到
"一片灰里有一条线"；剖面把垂直维度单独抽出来画，跨岸陡升、贴楼飞行、任务高度层
这三件事反而一眼可见。想看空间形态用 PP01（平面航迹）或交互版 HTML。

四格共用同一纵轴，才能横向比较"这一航次飞得高不高"；横轴各自按实际航程伸缩，
并在每格标题里标出航程，避免用统一横轴把小航次压成一条竖线。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib.patheffects as pe

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv

NAME = "PP03_altitude_profile"


def _profile(tour: iv.TourRow, scene) -> tuple[list[float], list[float], list[float]] | None:
    """(沿程水平距离 km, 航迹 z, 正下方地面高度)。"""
    import numpy as np

    tr = tour.track
    if not tr or len(tr["x"]) < 2:
        return None
    x = np.asarray(tr["x"], float)
    y = np.asarray(tr["y"], float)
    z = np.asarray(tr["z"], float)
    seg = np.hypot(np.diff(x), np.diff(y))
    s = np.concatenate([[0.0], np.cumsum(seg)]) / 1000.0
    ground = np.asarray(scene.ground_height(x, y), float)
    return s.tolist(), z.tolist(), ground.tolist()


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    inv = iv.build(ctx)
    scene = ctx.scenario
    tours = [t for t in inv.tours if t.ok and t.track]
    if not tours:
        return []

    ncol = 2
    nrow = max(1, math.ceil(len(tours) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(12.4, 2.9 * nrow + 1.1), squeeze=False)

    z_top = max(max(t.track["z"]) for t in tours)
    y_hi = min(130.0, max(z_top * 1.18, 60.0))

    for k, tour in enumerate(tours):
        ax = axes[k // ncol][k % ncol]
        prof = _profile(tour, scene)
        if prof is None:
            continue
        s, z, ground = prof
        color = th.UAV_COLOR.get(tour.uav, "#444444")

        ax.fill_between(s, 0, ground, color="#D6D6D6", alpha=0.85, lw=0,
                        zorder=1, label="地形/屋顶")
        ax.plot(s, ground, color="#A8A8A8", lw=0.7, zorder=2)
        ax.plot(s, z, color=color, lw=1.5, zorder=4, label="航迹高度")
        ax.axhline(float(max(ground)) + 0.0, color="none")

        pts = [(inv.task(t).x, inv.task(t).y) for t in tour.stops]
        idx = iv.stop_indices(tour.track, pts)
        hv = [math.hypot(float(tour.track["x"][i]) - float(tour.track["x"][0]),
                         float(tour.track["y"][i]) - float(tour.track["y"][0])) / 1000.0
              for i in idx]
        # 用真实沿程距离给站点定位（与曲线同一坐标，不另算直线距离）
        sv = [s[i] for i in idx]
        ax.scatter(sv, [z[i] for i in idx], s=26, c=color, edgecolors="white",
                   linewidths=0.7, zorder=6)
        # 圈上标任务号 T#（不是第几站）；同一位置被复访（T17/T27 同点）就合成一个标签，
        # 再分两条泳道避开压字，两条都放不下只留圆点
        span = max(s) * 1.03
        clusters: list[dict] = []
        for tid, xs_, zs_ in zip(tour.stops, sv, [z[i] for i in idx]):
            hit = next((c for c in clusters
                        if abs(xs_ - c["x"]) < 0.02 * span and abs(zs_ - c["z"]) < 2.0), None)
            if hit is None:
                clusters.append({"x": xs_, "z": zs_, "tags": [inv.tag(tid)]})
            else:
                hit["tags"].append(inv.tag(tid))
        gap_x = 0.045 * span
        lanes = [None, None]
        for c in clusters:
            lane = next((k for k in (0, 1)
                         if lanes[k] is None or abs(c["x"] - lanes[k]) >= gap_x), None)
            if lane is None:
                continue
            lanes[lane] = c["x"]
            ax.text(c["x"], c["z"] + (3.2 if lane == 0 else -3.6), "/".join(c["tags"]),
                    ha="center", va="bottom" if lane == 0 else "top", fontsize=6.4,
                    color=color, zorder=7,
                    path_effects=[pe.withStroke(linewidth=2.2, foreground="white")])

        gap = float(np.min(np.asarray(z) - np.asarray(ground)))
        ax.set_xlim(-0.05, max(s) * 1.03)
        ax.set_ylim(0, y_hi)
        ax.set_title(f"周期 {tour.cycle} · 机{tour.uav} · {tour.n_stops} 站 · "
                     f"{tour.path_len_m / 1000:.2f} km · {tour.duration_s:.0f} s",
                     fontsize=10.6, pad=7, loc="left", color="#1f2933")
        ax.text(0.985, 0.06,
                f"高度 {min(z):.0f}–{max(z):.0f} m\n最小离地 {gap:.1f} m",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=8.2,
                color="#3c4148", zorder=8, linespacing=1.4,
                bbox=dict(boxstyle="round,pad=0.26", fc="white", ec="#DDDDDD", lw=0.6,
                          alpha=0.94))
        ax.set_ylabel("高程 z（m）")
        ax.set_xlabel("沿程水平距离（km）")
        ax.grid(alpha=0.18)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    for k in range(len(tours), nrow * ncol):
        axes[k // ncol][k % ncol].set_axis_off()

    handles = [
        plt.Line2D([], [], color="#A8A8A8", lw=6, alpha=0.6, label="地形/屋顶高度"),
        plt.Line2D([], [], color="#555555", lw=1.6, label="航迹高度"),
        plt.Line2D([], [], marker="o", ls="none", color="#555555", markersize=6,
                   markeredgecolor="white", label="任务站（圈上 = 任务号 T#）"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=9.6,
               bbox_to_anchor=(0.5, 0.035), frameon=False)

    th.fingerprint("航次高度剖面（PP）：怎么爬、飞多高、离地多少", "四格共用同一纵轴；灰填与航线的空隙即真实离地净空", y=0.985)
    th.footnote(fig, "灰填 = 航迹正下方的地形/屋顶高程（含建筑），与航迹之间的空隙即离地净空；"
                     "四格共用同一纵轴、横轴按实际航程伸缩", y=0.008)
    fig.subplots_adjust(left=0.065, right=0.985, top=0.832, bottom=0.155,
                        hspace=0.62, wspace=0.16)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
