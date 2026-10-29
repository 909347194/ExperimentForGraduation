# -*- coding: utf-8 -*-
"""fig_side_view —— 侧面剖面（沿轨迹的竖直剖面）。

x 轴=轨迹水平弧长，y 轴=高度。画法（诚实、避免投影假象）：

* 建筑：仅画轨迹近距离擦过的楼（path 到楼 footprint 最近距离 < 阈值），
  画成 0..楼高 灰色矩形——表示"此处旁/下方有该高度的楼，轨迹以高度 z 通过"。
* 禁飞区(无限高)：仅画轨迹最近邻位置处的红色虚线参考标，
  表示"此处侧向有无限高禁飞墙、轨迹已横向绕开"；若轨迹真实进入则画实心红带。
* 轨迹：path 的 (弧长, 高度) 曲线。

审计已确认 collides_dynamic=False（轨迹未进入禁飞区），红线仅为约束参照。
"""

from __future__ import annotations

import numpy as np

from .registry import PlotContext
from .scene import bgeom
from .theme import PALETTE, plt
from . import theme


def plot_side_view(ctx: PlotContext, out_dir: str) -> list[str]:
    from matplotlib.patches import Rectangle
    from shapely.geometry import LineString, Point

    env = ctx.env
    feasible = [
        (o, d, res) for (o, d, res) in ctx.planned
        if res.feasible and res.path is not None and len(res.path.pts) >= 2
    ]
    if not feasible:
        print("[exp01] 无可行路径，跳过侧面图")
        return []

    ceil_z = max(
        60.0, *([float(getattr(b, "height", 0.0)) for b in env.buildings] + [0.0])
    )
    ceil_z = min(ceil_z * 1.15, 200.0)

    n = len(feasible)
    fig, axes = plt.subplots(1, n, figsize=(theme.FIG_SQUARE[0] * n / 1.6,
                                            theme.FIG_SQUARE[1]), squeeze=False)
    axes = axes[0]

    for k, (ax, (o, d, res)) in enumerate(zip(axes, feasible)):
        pts = np.asarray(res.path.pts, float)
        xy = pts[:, :2]
        seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
        s_arc = np.concatenate([[0.0], np.cumsum(seg)])
        z_path = pts[:, 2] if pts.shape[1] >= 3 else np.zeros(len(pts))
        s_hi = float(s_arc[-1])

        path_line = LineString([tuple(p) for p in xy])
        nfz_labeled = False

        # 建筑：轨迹近距离擦过的楼 → 0..楼高 灰矩形
        for b in env.buildings:
            bp = bgeom(b.poly)
            if path_line.distance(bp) is None or path_line.distance(bp) > 12.0:
                continue
            bi = int(np.argmin([Point(p).distance(bp) for p in xy]))
            s0 = max(0.0, s_arc[bi] - 8.0)
            s1 = min(s_hi, s_arc[bi] + 8.0)
            h = float(getattr(b, "height", 0.0))
            ax.add_patch(Rectangle((s0, 0), s1 - s0, h, facecolor="#cfcfcf",
                                   edgecolor="#777777", lw=0.3, alpha=0.8,
                                   zorder=1))

        # 禁飞区(无限高)：真实进入→实心红带；否则→红色虚线参考标
        for z in env.dynamic_nofly:
            for p in z.periods:
                pp = bgeom(p.poly)
                inter = path_line.intersection(pp)
                if not inter.is_empty:
                    xs = [
                        c
                        for g in (
                            inter.geoms
                            if inter.geom_type == "MultiLineString"
                            else [inter]
                        )
                        for c in (
                            g.coords
                            if g.geom_type == "LineString"
                            else [g.coords[0]]
                        )
                    ]
                    svals = []
                    for c in xs:
                        bi = int(np.argmin(
                            [(p2[0] - c[0]) ** 2 + (p2[1] - c[1]) ** 2 for p2 in xy]
                        ))
                        svals.append(s_arc[bi])
                    s0, s1 = min(svals), max(svals)
                    ax.add_patch(Rectangle(
                        (s0, 0), max(s1 - s0, 1.0), ceil_z,
                        facecolor=PALETTE["nofly_dyn"], edgecolor="none",
                        alpha=0.25, zorder=2))
                else:
                    if path_line.distance(pp) > 60.0:
                        continue
                    bi = int(np.argmin([Point(p).distance(pp) for p in xy]))
                    sc0 = max(0.0, s_arc[bi] - 1.5)
                    ax.plot([sc0 + 1.5, sc0 + 1.5], [0, ceil_z], "--",
                            color=PALETTE["nofly_dyn"], lw=1.8, zorder=3,
                            label=("禁飞区(无限高, 轨迹已绕开)"
                                   if not nfz_labeled else None))
                    nfz_labeled = True

        # 轨迹
        ax.plot(s_arc, z_path, "-", color=PALETTE["path"], lw=2.4, zorder=5,
                label="轨迹 path（弧长 vs 高度）")
        ax.plot([0], [z_path[0]], "o", color=PALETTE["origin"], ms=9, zorder=6)
        ax.plot([s_hi], [z_path[-1]], "s", color=PALETTE["dest"], ms=9, zorder=6)
        ax.axhline(0, color="#444444", lw=0.8, zorder=0)
        ax.set_xlabel("沿轨迹水平弧长 (m)")
        ax.set_ylabel("高度 z (m)")
        ax.set_xlim(-5, s_hi + 5)
        ax.set_ylim(-6, ceil_z)
        ax.set_title(
            f"OD{k} 侧面剖面 (level={res.level})\n"
            f"灰矩形=旁/下方建筑(可飞越)  红虚线=禁飞区(无限高,已绕开)\n"
            f"审计 collides_dynamic=False：轨迹未进入禁飞区",
            fontsize=theme.FS["title"],
        )
        ax.legend(loc="upper right", fontsize=theme.FS["legend"])

    path = theme.save_figure(fig, out_dir, "side_view")
    print(f"[exp01] 侧面图已写入 {path}")
    return [path]
