# -*- coding: utf-8 -*-
"""TA01 任务分配账（TA）：上层把 30 个任务派给了谁、排在第几站、哪个周期执行。

【TA 层 · 上层 task allocation 的账 · 只记"分配已经定下的东西"】

这一族图不画航迹、不画高度，只记分配本身：任务→机号、航次内第几站、属于哪个
周期，以及失败航次承载的任务是否退回上层任务池。这条航次最后有没有真飞出来由
PP 侧判定（PP01），代价估得准不准由 S01 结账——本图不越界替它们回答。

一行一个任务（纵轴 = 清单序号 1…N，T# 即 id），一列一个周期。格子里的字是
``机号·第几站``，颜色是这一周期的结局：

* **灰「在池」** 该周期没进任何航次（还没轮到 / 没被选中）；
* **橙「机4·6」** 派进了航次，但整航次没排出可交付航迹 —— 任务随航次退回池（↩）；
* **绿「机6·3」** 真到了，是该机航次的第 3 站。

右侧「终局」列给出跨周期结算：到达 / 未到达 / 未派。

这张图替代旧的"任务闭环矩阵"：旧图只有三种色块，看不出**是谁飞的、第几站、
哪一航次失败后任务又回到池里**；本图把这三件事压进同一个格子，于是"某航次失败
→ 若干任务回池 → 下周期重飞几个、丢几个"这条主线一眼可见（列数随实际周期数）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv

NAME = "TA01_task_assignment"

_ARRIVED_FACE = "#D6E9D2"
_FAILED_FACE = "#F6DCC4"
_POOL_FACE = "#EDEDED"
_DONE_FACE = "#F2F7F1"

_STATE_TEXT = {
    iv.ARRIVED: ("到达", "#2E6B3E"),
    iv.FAILED: ("未到达", "#A0430F"),
    iv.POOL: ("未派", "#7A7A7A"),
}


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Patch, Rectangle

    inv = iv.build(ctx)
    cycles = inv.cycles
    n_col = len(cycles)
    n_row = len(inv.tasks)

    # 周期数不预设：单周期时只有一列，"终局"紧邻，图不用摆成三列那么宽；
    # 但 30 行 + 底部"到达/未到达"小结仍需 ~7.9 in 内容宽，给到 8.6 in 才不裁字。
    fig, ax = plt.subplots(figsize=(10.4 if n_col >= 2 else 8.6, 8.6))
    ax.set_xlim(-0.62, n_col + 0.62)
    ax.set_ylim(-2.2, n_row + 2.3)
    ax.set_axis_off()

    def ypos(i: int) -> float:
        return n_row - 0.5 - i

    # ── 列头 ─────────────────────────────────────────────────────
    for j, c in enumerate(cycles):
        plan = next(p for p in ctx.trace["cycles"] if int(p["cycle"]) == c)
        ax.text(j, n_row + 1.55, f"周期 {c}", ha="center", va="center",
                fontsize=12, fontweight="bold", color=th.CYCLE_COLOR.get(c, th.MUTED))
        ax.text(j, n_row + 0.85, f"{plan['t_start']:.0f}–{plan['t_end']:.0f} s",
                ha="center", va="center", fontsize=8.6, color=th.MUTED)
    ax.text(n_col + 0.02, n_row + 1.55, "终局", ha="center", va="center",
            fontsize=11.5, fontweight="bold", color="#333333")
    ax.plot([n_col - 0.5, n_col - 0.5], [-1.9, n_row + 0.6],
            color="#C9C9C9", lw=0.9, ls=(0, (4, 3)))

    # ── 行：任务 × 周期 ──────────────────────────────────────────
    for i, t in enumerate(inv.tasks):
        y = ypos(i)
        if t.id % 2 == 0:
            ax.add_patch(Rectangle((-0.55, y - 0.44), n_col + 1.1, 0.88,
                                   facecolor="#FAFAFA", edgecolor="none", zorder=0))
        ax.text(-0.50, y, t.tag, ha="left", va="center", fontsize=8.0,
                color=th.CYCLE_COLOR.get(t.cycle, "#333333"), fontweight="bold")

        for j, c in enumerate(cycles):
            cell = inv.cell(t.id, c)
            if cell.state == iv.ABSENT:
                ax.plot([j - 0.34, j + 0.34], [y, y], color="#E2E2E2", lw=0.9,
                        ls=(0, (2, 2)), zorder=1)
                continue
            if cell.state == iv.DONE:
                # 已交付，此后不再进池：只留一条极淡的底色，不写字，避免与"在池"混淆
                ax.add_patch(Rectangle((j - 0.42, y - 0.38), 0.84, 0.76,
                                       facecolor=_DONE_FACE, edgecolor="none", zorder=2))
                ax.text(j, y, "已交付", ha="center", va="center", fontsize=7.6,
                        color="#A9BFA6", zorder=3)
                continue
            face = {iv.ARRIVED: _ARRIVED_FACE, iv.FAILED: _FAILED_FACE,
                    iv.POOL: _POOL_FACE}[cell.state]
            edge = "#B9D3B0" if cell.state == iv.ARRIVED else (
                "#D9A777" if cell.state == iv.FAILED else "#DCDCDC")
            ax.add_patch(Rectangle((j - 0.42, y - 0.38), 0.84, 0.76, facecolor=face,
                                   edgecolor=edge, lw=0.9, zorder=2))
            if cell.state == iv.POOL:
                ax.text(j, y, "在池", ha="center", va="center", fontsize=8.0,
                        color="#8A8A8A", zorder=3)
                continue
            ax.text(j, y, f"机{cell.uav}·{cell.stop}", ha="center", va="center",
                    fontsize=8.2, color="#2A2A2A" if cell.state == iv.ARRIVED else "#7A3B0C",
                    zorder=3)
            if cell.state == iv.FAILED:
                ax.plot([j - 0.42, j - 0.42], [y - 0.38, y + 0.38], color=th.FAIL,
                        lw=2.2, zorder=4)
            if cell.reopened:  # 任务随失败的航次退回池：右上角红点（字形 ↩ 在雅黑里缺字）
                ax.scatter([j + 0.30], [y + 0.24], s=16, c=th.FAIL,
                           edgecolors="white", linewidths=0.5, zorder=6)

        final = inv.final_state(t.id)
        label, color = _STATE_TEXT[final]
        ax.text(n_col + 0.02, y, label, ha="center", va="center", fontsize=8.6,
                color=color, fontweight="bold" if final != iv.POOL else "normal")

    # 周期分块的中缝：让"哪几行属于同一投放周期"看得出来（不假设每周期行数恒定）
    for c in cycles[:-1]:
        last = max(i for i, t in enumerate(inv.tasks) if t.cycle == c)
        y_sep = ypos(last) - 0.5
        ax.plot([-0.55, n_col + 0.35], [y_sep, y_sep], color="#C9C9C9", lw=0.9)

    # ── 底部：逐周期流量小结 ─────────────────────────────────────
    for j, c in enumerate(cycles):
        cells = [inv.cell(t.id, c) for t in inv.tasks]
        n_arr = sum(1 for x in cells if x.state == iv.ARRIVED)
        n_fail = sum(1 for x in cells if x.state == iv.FAILED)
        n_pool = sum(1 for x in cells if x.state == iv.POOL)
        # DONE 既不算到达（那是别的周期）也不算在池（它已交付、不再进池）
        ax.text(j, -0.75, f"到达 {n_arr}", ha="center", va="center", fontsize=9.6,
                color="#2E6B3E", fontweight="bold")
        ax.text(j, -1.75, f"未到达 {n_fail} · 在池 {n_pool}", ha="center", va="center",
                fontsize=8.6, color=th.MUTED)
    n_done = sum(1 for t in inv.tasks if inv.final_state(t.id) == iv.ARRIVED)
    ax.text(n_col + 0.02, -1.25, f"{n_done}/{len(inv.tasks)}", ha="center", va="center",
            fontsize=10.5, fontweight="bold", color="#2E6B3E")

    # ── 图例 ─────────────────────────────────────────────────────
    handles = [
        Patch(facecolor=_ARRIVED_FACE, edgecolor="#B9D3B0", label="到达（有真实航迹）"),
        Patch(facecolor=_FAILED_FACE, edgecolor="#D9A777", label="未到达（整航次失败，任务退回池）"),
        Patch(facecolor=_POOL_FACE, edgecolor="#DCDCDC", label="在池（该周期未进任何航次）"),
        Patch(facecolor=_DONE_FACE, edgecolor="none", label="已交付（此后不再进池）"),
    ]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.085),
              ncol=4, fontsize=9.2, frameon=False, handlelength=1.2, columnspacing=1.3)

    th.fingerprint("任务分配账（TA）：30 个任务被谁、在第几站、于哪个周期执行", "格子 = 上层分配结果（机号·第几站）；是否真交付看 PP01，回池与补飞看 S01", y=0.985)
    th.footnote(fig, "格内 = 机号·站序；红点 = 航次失败、任务退池；虚线格 = 尚未出生", y=0.012)
    # 底部要同时容下逐周期小结、图例和脚注：单周期一行时三者挤在一起，必须多留白。
    fig.subplots_adjust(left=0.055, right=0.975, top=0.895, bottom=0.135)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
