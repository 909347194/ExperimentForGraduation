# -*- coding: utf-8 -*-
"""I01 任务池台账（输入）：30 个任务在哪、多高、能不能站住 —— 一张图看完任务池。

【输入层 · 设定 · 与算法无关 · 不带 TA/PP 标签】

这一族图记录的是"实验怎么摆的"：点位由任务产生侧投放，上层只负责接收。换任何
求解器这张图都一模一样，所以它不携带上层/下层标签——它是 TA 与 PP 共用的坐标系。

左：任务散布（按出生周期着色，圈上文字 = 清单序号 T1–T30，T# 即 tasks.json 里的 id）。
右：逐任务台账（作业高、地面、AGL、端点净空）。**表是主体，地图是索引**：
    图上看到 T19 在哪一片，表里立刻能查到它的地面、AGL 与净空。

为什么不再画"场景总览"那种大图：任务点只有 30 个，读者要的是"有哪些任务"
而不是"城市长什么样"。地形的起伏留给 PP03 的高度剖面去讲。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv
from ..base.declutter import declutter

NAME = "I01_task_pool"

_COLS = (  # (x, 对齐, 表头)
    (0.020, "left", "任务"),
    (0.190, "left", "周期"),
    (0.390, "right", "作业高 z"),
    (0.520, "right", "地面"),
    (0.630, "right", "AGL"),
    (0.760, "right", "净空"),
)
_TABLE_RIGHT = 0.840


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    inv = iv.build(ctx)
    scene = ctx.scenario
    fig = plt.figure(figsize=(15.2, 8.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.18, 1.0],
                          left=0.045, right=0.985, top=0.885, bottom=0.155, wspace=0.06)

    # ── 左：任务散布 ─────────────────────────────────────────────
    ax = fig.add_subplot(gs[0, 0])
    sc.draw_ground(ax, scene, downsample_m=40.0, alpha=0.30)
    sc.draw_buildings(ax, scene, stride=3, alpha=0.75, lw=0.15)

    for t in inv.tasks:
        c = th.CYCLE_COLOR.get(t.cycle, "#555555")
        ax.scatter(t.x, t.y, s=34, c=c, edgecolors="white",
                   linewidths=0.7, zorder=6, alpha=0.95)
        # 一个任务一个标号：T# 就是清单序号，不再有第二套"带号"。
        ax.annotate(t.tag, (t.x, t.y), textcoords="offset points", xytext=(9, 4),
                    ha="left", va="center", fontsize=7.6, zorder=9,
                    color="#333333",
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))

    for site in ctx.meta["context"]["all_nest_sites"]:
        if site["active"]:
            ax.scatter(site["x"], site["y"], marker="*", s=420, c="#111111",
                       edgecolors="white", linewidths=1.0, zorder=9)
        else:
            ax.scatter(site["x"], site["y"], marker="*", s=300, facecolors="none",
                       edgecolors="#8C8C8C", linewidths=1.0, zorder=9)
        ax.annotate(f"巢{site['id']}" + ("" if site["active"] else "（未启用）"),
                    (site["x"], site["y"]), textcoords="offset points", xytext=(11, -4),
                    fontsize=9.5, color="#111111" if site["active"] else th.MUTED, zorder=9,
                    bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.85))

    xs = [t.x for t in inv.tasks] + [s["x"] for s in ctx.meta["context"]["all_nest_sites"]]
    ys = [t.y for t in inv.tasks] + [s["y"] for s in ctx.meta["context"]["all_nest_sites"]]
    ax.set_xlim(min(xs) - 260, max(xs) + 260)
    ax.set_ylim(min(ys) - 260, max(ys) + 260)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("东向 x（m）")
    ax.set_ylabel("北向 y（m）")
    ax.set_title(f"任务散布：{len(inv.tasks)} 个任务点（T# = 清单序号，按投放周期着色）",
                 fontsize=11.5, pad=8, loc="left")
    sc.scale_bar(ax, 2000.0)
    ax.grid(alpha=0.18)

    handles = [Line2D([], [], marker="o", ls="none", color=th.CYCLE_COLOR.get(c, th.MUTED),
                      markeredgecolor="white", markersize=8, label=f"周期 {c} 任务点")
               for c in sorted({t.cycle for t in inv.tasks})]
    ax.legend(handles=handles, loc="lower right", fontsize=9, handletextpad=0.4,
              borderpad=0.5, framealpha=0.92, frameon=True, edgecolor="#DDDDDD")

    # ── 右：逐任务台账 ───────────────────────────────────────────
    # 纵向预算：N 行 + 1 行表头 + 2 条分隔线 = N+2 行。旧版每周期插一个整行标题
    # （+3 行），把行距压到 0.15 in，字全糊在一起——这里只在周期切换处画一条细线。
    tx = fig.add_subplot(gs[0, 1])
    tx.set_axis_off()
    tx.set_xlim(0, 1)
    tx.set_ylim(0.0, float(len(inv.tasks)) + 2.0)
    tx.set_title(f"任务池台账（{len(inv.tasks)} 个任务）", fontsize=12.5, fontweight="bold",
                 loc="left", pad=6, color="#1f2933")

    top = float(len(inv.tasks))
    for x, ha, name in _COLS:
        tx.text(x, top + 1.2, name, fontsize=8.2, ha=ha, va="center", color="#5A5A5A")
    tx.plot([0.0, _TABLE_RIGHT], [top + 0.55, top + 0.55], color="#B9B9B9", lw=0.9)

    for i, t in enumerate(inv.tasks):
        y = top - i
        if i % 2 == 0:
            tx.add_patch(Rectangle((0.0, y - 0.46), _TABLE_RIGHT, 0.92,
                                   facecolor="#F6F7F9", edgecolor="none", zorder=0))
        vals = (
            (t.tag, (0.020, "left", th.CYCLE_COLOR.get(t.cycle, "#1f2933"), "bold")),
            (f"周期 {t.cycle}", (0.185, "left", "#3c4148", "normal")),
            (f"{t.z:.1f}", (0.390, "right", "#0f4c81", "bold")),
            (f"{t.ground_z:.1f}", (0.520, "right", th.MUTED, "normal")),
            (f"{t.agl_m:.1f}", (0.630, "right", "#3c4148", "normal")),
            (f"{t.sdf_m:.1f}", (0.760, "right",
                               th.FAIL if t.sdf_m < 8.0 else "#3c4148", "normal")),
        )
        for text, (x, ha, color, weight) in vals:
            tx.text(x, y, text, fontsize=7.9, ha=ha, va="center", color=color,
                    fontweight=weight)
        if i + 1 < len(inv.tasks) and inv.tasks[i + 1].cycle != t.cycle:
            tx.plot([0.0, _TABLE_RIGHT], [y - 0.5, y - 0.5], color="#CFCFCF", lw=0.8)

    zmin = min(t.z for t in inv.tasks)
    zmax = max(t.z for t in inv.tasks)
    sdf_min = min(t.sdf_m for t in inv.tasks)
    # 编组不预设：位置数、投放周期数、AGL 是否齐平都从清单里数，别把 10×3 写死。
    n_pos = len({(round(t.x, 3), round(t.y, 3)) for t in inv.tasks})
    n_batch = len({int(t.cycle) for t in inv.tasks})
    agls = sorted({round(float(t.agl_m), 3) for t in inv.tasks})
    agl_text = (f"AGL 恒 {agls[0]:.1f} m" if len(agls) == 1
                else f"AGL {agls[0]:.1f}–{agls[-1]:.1f} m")

    th.fingerprint("任务池台账（输入）：任务在哪、多高、站不站得住",
                   "点位由产生侧投放，算法侧只接收；编号 T# = 清单序号，全文统一")
    th.footnote(fig,
                f"共 {len(inv.tasks)} 个任务（{n_pos} 个作业位置，"
                f"{n_batch} 个投放周期）："
                f"作业高 {zmin:.1f}–{zmax:.1f} m · {agl_text} · "
                f"最小端点净空 {sdf_min:.2f} m（阈值 8.0，全部合规）", y=0.035)
    declutter(fig, ax_filter=lambda a: a is ax)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
