# -*- coding: utf-8 -*-
"""TA02 分配结构与负载（TA）：每条航次装了什么、估计多少、机位/巢位均衡吗。

【TA 层 · 上层 task allocation 的账 · 结果相位】

(a) 一航次一条「估计航程条」：条长 = 上层算出来的 ``est_cost``（按一次性初值 scale，不随周期更新），
    条上的刻度 = 站序，刻度位置按「巢 → 各站」的平面距离比例标定。刻度只用来读
    顺序，不是真实弧长（真实弧长见 PP01）。右列固定对齐写站数、估计里程、相对满电
    航程的余量；失败航次用斜纹 + 红边，直接标"未交付"。整周期没派到活的机聚成一行
    "闲置"——闲置与"任务没派完"必须并排看，否则会误以为运力已经用光。
(b) 逐机累计派入站数，按"交付 / 未交付"分色：谁被压满、哪部分派了也白派。

与 TA01 分工：TA01 逐任务记账（这个任务去哪了），本图逐航次记账（这一航次装了多少）。
两者都只谈分配：不谈航迹形状（PP01），不谈代价准不准（S01）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv

NAME = "TA02_alloc_load"


def _station_fracs(tour: iv.TourRow, inv: iv.Inventory, nests: list[dict]) -> list[float]:
    """巢 → 各站的累计平面距离占比（下标 0 是机巢，1…n 是站序）。"""
    import numpy as np

    nest = next((s for s in nests if int(s["id"]) == tour.nest_id), None)
    if nest is None:
        return []
    pts = [(float(nest["x"]), float(nest["y"]))]
    pts += [(inv.task(t).x, inv.task(t).y) for t in tour.stops]
    d = [0.0]
    for a, b in zip(pts, pts[1:]):
        d.append(d[-1] + float(np.hypot(b[0] - a[0], b[1] - a[1])))
    total = d[-1] if d[-1] > 1e-9 else 1.0
    return [x / total for x in d]


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Patch

    inv = iv.build(ctx)
    meta = sc.uav_meta()
    nests = ctx.nest_sites
    cycles = list(inv.cycles)

    rows: list[tuple[str, object]] = []
    for c in cycles:
        for t in sorted(inv.tours_of(c), key=lambda x: x.uav):
            rows.append(("tour", t))
        idle = inv.cycle_row(c).idle_uavs
        if idle:
            rows.append(("idle", (c, idle)))

    est_km = [t.est_m / 1000.0 for t in inv.tours] or [1.0]
    f_max = max(est_km)
    x_max = 1.72 * f_max
    anno_x = 1.02 * f_max
    cycle_x = 1.46 * f_max
    sep_x = 1.42 * f_max

    fig = plt.figure(figsize=(13.8, 7.0))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.5, 1.0], left=0.135, right=0.975,
                          top=0.822, bottom=0.14, hspace=0.44)
    ax = fig.add_subplot(gs[0])
    axb = fig.add_subplot(gs[1])

    # ── (a) 一航次一条估计航程条 ───────────────────────────────────
    yticks, ylabels = [], []
    y = 0.0
    for kind, obj in rows:
        if kind == "idle":
            c, idle = obj  # type: ignore[misc]
            yticks.append(y)
            ylabels.append(f"C{c} 闲置")
            ax.text(0.02, y, f"机 {'、'.join(map(str, idle))} 整周期无航次",
                    va="center", fontsize=9.0, color=th.MUTED, zorder=6,
                    bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#DDDDDD",
                              lw=0.5))
            y += 1.0
            continue

        t: iv.TourRow = obj  # type: ignore[assignment]
        color = th.UAV_COLOR.get(t.uav, "#444444")
        e = t.est_m / 1000.0
        yticks.append(y)
        ylabels.append(f"C{t.cycle} 机{t.uav}·巢{t.nest_id}")
        if t.ok:
            ax.barh(y, e, height=0.56, color=color, alpha=0.82, edgecolor="none", zorder=3)
        else:
            ax.barh(y, e, height=0.56, color="#F3DAD5", edgecolor=th.FAIL, lw=1.1,
                    hatch="////", zorder=3)
        # 站序号：竖排（旋转 90°）一行放得下，密集处只留刻度不硬挤
        gap_x = 0.012 * x_max
        last = None
        tone = "#3C4148" if t.ok else th.FAIL
        for i, f in enumerate(_station_fracs(t, inv, nests)[1:], start=1):
            x = f * e
            ax.plot([x, x], [y - 0.30, y + 0.30], color=tone, lw=0.9, zorder=4)
            if last is not None and x - last < gap_x:
                continue
            last = x
            ax.text(x, y - 0.44, str(i), ha="center", va="center", rotation=90,
                    fontsize=6.6, color=tone, zorder=5,
                    bbox=dict(boxstyle="square,pad=0.06", fc="white", ec="none", alpha=0.55))
        cap = meta.get(t.uav, {}).get("max_range_m")
        note = f"{t.n_stops} 站 · {e:.2f} km"
        if cap:
            note += f" · 余量 {float(cap) / 1000.0 - e:.1f}"
        if not t.ok:
            note += " · 未交付"
        ax.text(anno_x, y, note, va="center", ha="left", fontsize=8.4,
                color=th.FAIL if not t.ok else "#3C4148", zorder=5)
        y += 1.0

    # 周期分组底纹（按周期序号奇偶，不是按行号）+ 右侧逐周期小结（不压在条上）
    y0 = 0.0
    for c in cycles:
        n = sum(1 for kind, obj in rows if (obj[0] if kind == "idle" else obj.cycle) == c)
        if (cycles.index(c) % 2) == 0:
            ax.axhspan(y0 - 0.5, y0 + n - 0.5, color="#F5F7F9", zorder=0)
        r = inv.cycle_row(c)
        mid = y0 + (n - 1) / 2.0
        ax.text(cycle_x, mid, f"派入 {r.n_assigned}\n估 {r.est_m / 1000:.1f} km\n"
                f"到达 {r.n_arrived}", va="center", ha="left", fontsize=8.8,
                color=th.CYCLE_COLOR.get(c, "#333333"), fontweight="bold", zorder=5,
                linespacing=1.5)
        ax.plot([sep_x, sep_x], [y0 - 0.45, y0 + n - 0.55], color="#D5D5D5", lw=0.7,
                zorder=1)
        y0 += n

    ax.set_yticks(yticks, ylabels)
    ax.tick_params(axis="y", labelsize=9.2, length=0, pad=4)
    ax.set_ylim(len(rows) - 0.35, -0.98)
    ax.set_xlim(0, x_max)
    ax.set_xlabel("上层估计航程（km，est_cost，初值 scale 不随周期更新）")
    ax.set_title("(a) 每航次装了什么：站序刻度 · 估计航程 · 容量余量 · 逐周期小结",
                 fontsize=12, pad=8)
    ax.grid(axis="x", alpha=0.18)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)

    # ── (b) 逐机累计：派入站数（交付 / 未交付） ──────────────────
    fleet = list(inv.fleet)
    delivered = [sum(t.n_stops for t in inv.tours if t.uav == u and t.ok) for u in fleet]
    undeliv = [sum(t.n_stops for t in inv.tours if t.uav == u and not t.ok) for u in fleet]
    n_tour = [sum(1 for t in inv.tours if t.uav == u) for u in fleet]
    yy = np.arange(len(fleet))
    axb.barh(yy, delivered, 0.55, color=[th.UAV_COLOR.get(u, "#444") for u in fleet],
             edgecolor="white", lw=0.5)
    axb.barh(yy, undeliv, 0.55, left=delivered, color="#F3DAD5", edgecolor=th.FAIL,
             hatch="////", lw=0.9)
    span = max((a + b) for a, b in zip(delivered, undeliv)) or 1
    axb.set_xlim(0, span * 1.42)
    for i, u in enumerate(fleet):
        tot = delivered[i] + undeliv[i]
        txt = (f"{n_tour[i]} 航次 · {tot} 站" +
               (f"（未交付 {undeliv[i]}）" if undeliv[i] else "") if tot else "0 航次 · 整场闲置")
        axb.text(tot + 0.3, i, txt, va="center", fontsize=9.0,
                 color="#333333" if tot else th.MUTED)
    axb.set_yticks(yy, [f"机{u} · 巢{meta[u]['nest_id']}" for u in fleet], fontsize=9.0)
    axb.invert_yaxis()
    axb.set_xlabel("派入任务数（站）", labelpad=4)
    axb.set_title("(b) 逐机累计：负载压在谁身上、哪部分派了也白派", fontsize=11.5, pad=6)
    axb.grid(axis="x", alpha=0.20)
    for side in ("top", "right", "left"):
        axb.spines[side].set_visible(False)

    idle_all = [u for u in inv.fleet if not any(t.uav == u for t in inv.tours)]
    th.fingerprint("分配结构与负载（TA）：每条航次装了什么、估计多少、机位/巢位均衡吗",
                   "条长 = 估计航程；刻度 = 站序；斜纹 = 派了但下层无解", y=0.975)
    th.footnote(fig,
                f"本 run 共 {len(inv.tours)} 航次（{sum(1 for t in inv.tours if t.ok)} 航次交付）、"
                f"派入 {sum(delivered) + sum(undeliv)} 站"
                + (f"；全程无航次的机 {'、'.join(map(str, idle_all))}" if idle_all else "")
                + "；刻度只读顺序，真实弧长见 PP01。", y=0.024)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
