# -*- coding: utf-8 -*-
"""PP02 航次时刻表（PP）：每个周期里谁先起飞、何时到哪一站、谁整周期没动。

【PP 层 · 下层 path planning 的账 · 时序是求解产物】

起飞/落地时刻、各站到达时刻、单航次时长都由下层航迹给出（不是上层拍脑袋的排程），
因此归 PP 记账。整周期没有航次的机在这里聚成一行——那属于"有没有活派给它"的
分配事实，本图只如实标注，不替 TA 解读。

横轴 = **周期内时间**（0–1800 s，不是跨周期的 0–5400 s：单次航次只有
250–850 s，铺到全时长上大半是空白），纵轴 = 周期内的航次行。

一行一航次：粗线 ``[起飞, 落地]``，线上白圈是各站到达时刻（圈上数字 = 任务号，
上下交错摆放避免压字）；航次末右注 ``时长 · 航程 · 站数``。
**整周期没有航次的机**不单独占行，而是聚成一行"机3 无航次"——闲置是事实，
必须画出来，但四行空白会把真正有信息的三行挤扁。

与 PP01 分工：PP01 画"飞成什么形状"，本图回答"时间上怎么排、先后差多少"。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv

NAME = "PP02_tour_timing"


def _arrival_offsets(tour: iv.TourRow, inv: iv.Inventory) -> list[float]:
    """各站到达时刻（相对周期起点，秒）：航迹上离站点最近的采样点。"""
    tr = tour.track
    if not tr:
        return []
    pts = [(inv.task(t).x, inv.task(t).y) for t in tour.stops]
    idx = iv.stop_indices(tr, pts)
    t = tr["t"]
    return [float(t[i]) - tour.t0 for i in idx]


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    inv = iv.build(ctx)
    meta = sc.uav_meta()
    fleet = sorted(meta)
    cyc_len = float(ctx.cfg["loop"]["cycle_length_s"])
    stag = float(ctx.cfg["loop"]["depart_stagger_s"])
    start_of = {int(p["cycle"]): float(p["t_start"]) for p in ctx.trace["cycles"]}

    fig, ax = plt.subplots(figsize=(13.2, 5.8))
    row_h = 0.34
    y = 0.0
    yticks, ylabels = [], []

    for i, cyc in enumerate(inv.cycles):
        tours = sorted(inv.tours_of(cyc), key=lambda t: t.uav)
        flying = {t.uav for t in tours}
        idle = [u for u in fleet if u not in flying]
        rows: list[tuple[int | None, iv.TourRow | None]] = [(t.uav, t) for t in tours]
        if idle:
            rows.append((None, None))

        if i % 2 == 0:
            ax.axhspan(y - row_h * 0.62, y + len(rows) * row_h - row_h * 0.1,
                       color="#F5F7F9", zorder=0)
        for k, (uav, tour) in enumerate(rows):
            yy = y + k * row_h
            yticks.append(yy)
            if uav is None:
                ylabels.append("闲置")
                ax.plot([0, cyc_len], [yy, yy], color="#CFCFCF", lw=1.0, ls=(0, (2, 4)),
                        zorder=2)
                ax.text(cyc_len * 0.5, yy,
                        f"机 {'、'.join(map(str, idle))} 整周期无航次",
                        ha="center", va="center", fontsize=9.0, color="#8A8A8A", zorder=3,
                        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none"))
                continue

            ylabels.append(f"机{uav}")
            color = th.UAV_COLOR.get(uav, "#444444")
            a = max(0.0, tour.t0 - start_of[cyc])
            if not tour.ok:
                ax.plot([a, a], [yy - 0.10, yy + 0.10], color=th.FAIL, lw=3.2, zorder=5)
                ax.text(a + 26, yy,
                        f"未交付 · {tour.failure} · {tour.n_stops} 站全部未执行",
                        va="center", fontsize=9.0, color=th.FAIL, zorder=6)
                continue
            b = a + tour.duration_s
            ax.plot([a, b], [yy, yy], color=color, lw=7.0, solid_capstyle="round", zorder=4)
            for edge in (a, b):
                ax.plot([edge, edge], [yy - 0.11, yy + 0.11], color=color, lw=1.6,
                        alpha=0.9, zorder=5)
            # 任务号分两条泳道摆放：站与站到达太近时换道；两条都放不下就只留白圈，
            # 不硬挤成「2030」那种读不出号码的糊字。
            gap_t = cyc_len * 0.030
            lanes = [None, None]
            for j, off in enumerate(_arrival_offsets(tour, inv)):
                x = a + off
                ax.scatter([x], [yy], marker="o", s=30, facecolors="white",
                           edgecolors=color, linewidths=1.1, zorder=7)
                lane = next((k for k in (0, 1)
                             if lanes[k] is None or abs(x - lanes[k]) >= gap_t), None)
                if lane is None:
                    continue
                lanes[lane] = x
                ax.annotate(inv.tag(tour.stops[j]), xy=(x, yy),
                            xytext=(0, 5.0 if lane == 0 else -5.0),
                            textcoords="offset points",
                            ha="center", va="bottom" if lane == 0 else "top",
                            fontsize=7.6, color=color, zorder=8,
                            path_effects=[pe.withStroke(linewidth=2.4,
                                                        foreground="white")])
            ax.text(b + 30, yy,
                    f"{tour.duration_s:.0f} s · {tour.path_len_m / 1000:.2f} km · "
                    f"{tour.n_stops} 站", va="center", fontsize=9.0, color=color, zorder=6)

        ax.text(-0.004, y - 0.30, f"周期 {cyc}", transform=ax.get_yaxis_transform(),
                ha="left", va="bottom", fontsize=11.5, fontweight="bold",
                color=th.CYCLE_COLOR.get(cyc, th.MUTED))
        y += len(rows) * row_h + 0.30
        if i < len(inv.cycles) - 1:
            ax.plot([0, cyc_len], [y - 0.16, y - 0.16], color="#D5D5D5", lw=0.9, zorder=1)

    ax.set_yticks(yticks, ylabels)
    ax.tick_params(axis="y", labelsize=9.2, length=0, pad=14)
    ax.set_ylim(-0.62, y - 0.10)
    ax.invert_yaxis()
    ax.set_xlim(0, cyc_len)
    ax.set_xlabel("周期内时间（s）", labelpad=6)
    ax.set_xticks(range(0, int(cyc_len) + 1, 300))
    ax.grid(axis="x", alpha=0.22)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)

    handles = [Line2D([], [], color=th.UAV_COLOR.get(u, "#444444"), lw=6.0,
                      label=f"机 {u} 航次")
               for u in sorted({t.uav for t in inv.tours if t.ok})]
    handles += [
        Line2D([], [], marker="o", ls="none", markerfacecolor="white",
               markeredgecolor="#4A4A4A", markersize=6, label="到站时刻（标注任务号）"),
        Line2D([], [], color=th.FAIL, lw=3.2, label="整航次未交付（无航迹）"),
        Line2D([], [], color="#CFCFCF", lw=1.0, ls=(0, (2, 4)), label="整周期无航次"),
    ]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.012), ncol=4,
              fontsize=9.6, frameon=False, handlelength=1.9, columnspacing=2.0)

    idle_all = ctx.metrics.get("never_dispatched_uavs") or []
    note = [f"周期长 {cyc_len:.0f} s"]
    if stag > 0:
        note.append(f"同巢同周期错峰 {stag:.0f} s")
    note.append(f"全程无航次（每周期都闲置）：机 {'、'.join(map(str, idle_all))}" if idle_all
                else "所有机至少被派过一次")
    th.fingerprint("航次时刻表（PP）：谁先飞、何时到哪一站、谁整周期没动", "横轴是周期内时间（单航次 80–850 s），不是跨周期全时长", y=0.978)
    # 图内只留一行：分泳道、放不下只留圈这类画法细节搬去 CAPTIONS.md，别占版面。
    th.footnote(fig, "粗线 = 航次 [起飞, 落地]；白圈 = 到站时刻（标 T#）；"
                     "红色竖线 = 该航次整条未交付；" + "；".join(note), y=0.014)
    fig.subplots_adjust(left=0.135, right=0.978, top=0.735, bottom=0.185)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
