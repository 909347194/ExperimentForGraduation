# -*- coding: utf-8 -*-
"""S01 跨层闭环账（TA+PP）：可行性回灌怎么剔边、失败的账怎么被吸收。

【系统层 · 完整 TA+PP 闭环 · 不带上层/下层标签】

这一族回答的是"任何单层都答不了"的问题：下层报出的几何不可行航段有没有在本周期
被剔掉、失败航次退回池的任务下一周期补回来了几个。三格连读：

(a) **可行性台账**：逐周期"不可飞边 / 残留不可飞边 / 重解次数"。触发条件只有一个——
    交付解里出现几何不可行边；估计偏多少都不重解，所以这里不画倍率。
(b) **逐航次闭环账**：每航次"计划（上层估计）vs 实测（航迹弧长）"。
    失败的航次没有实测柱，只留斜纹计划柱并标"未交付"——它的账不是"飞短了"，
    而是"根本没飞出来"，画成 0 高柱会被误读。
(c) **累计到达**：回池 → 补飞 的兑现曲线。某周期失败航次退回池的站数、下一周期
    补飞回几个、还剩几个搁置，全按台账按实际周期写，不预设周期数量。

与 TA01/PP01 的分工：那两张图画"分配了什么、飞成什么样"；本图只结"跨层的账"。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv

NAME = "S01_loop_closure"

_EST_FACE = "#B9C6D2"
_GAP = "#A0430F"


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    inv = iv.build(ctx)
    rows = list(inv.cycle_rows)
    xs = np.arange(len(rows))

    # 单周期时 (a) 只剩一个点、没有收敛轨迹：把这一栏压窄，宽度让给 (b) 的逐航次对账。
    # 周期数不预设，所以宽度按实际周期数定，不写死。
    w_a = 1.0 if len(rows) >= 2 else 0.62
    fig = plt.figure(figsize=(14.2, 4.9))
    gs = fig.add_gridspec(1, 3, width_ratios=[w_a, 1.42, 1.05], left=0.055,
                          right=0.978, top=0.795, bottom=0.175, wspace=0.30)
    axs = [fig.add_subplot(gs[0, k]) for k in range(3)]
    ax_a, ax_b, ax_c = axs

    # ── (a) 可行性台账：不可飞边 / 残留不可飞边 / 重解次数 ─────────────
    removed = np.array([r.n_infeasible for r in rows], dtype=float)
    left = np.array([r.n_unresolved for r in rows], dtype=float)
    bw = 0.34
    ax_a.bar(xs - bw / 2, removed, bw, color=_GAP, zorder=3,
             label="本周期剔除")
    ax_a.bar(xs + bw / 2, left, bw, color=th.FAIL, hatch="///", zorder=3,
             edgecolor="white", label="剔除后仍残留")
    for x, r in zip(xs, rows):
        ax_a.annotate(f"重解 {r.n_resolves} 次", (x, max(r.n_infeasible, r.n_unresolved)),
                      textcoords="offset points", xytext=(0, 7), ha="center",
                      fontsize=8.6, fontweight="bold",
                      color=th.CYCLE_COLOR.get(r.cycle, th.INK))

    top = max(float(np.max(removed + left)) if len(rows) else 0.0, 1.0)
    ax_a.set_ylim(0, top * 1.55)
    ax_a.set_xlim(-0.55, len(rows) - 0.45)
    ax_a.set_xticks(xs, [f"周期 {r.cycle}" for r in rows])
    ax_a.set_yticks(np.arange(0, top + 0.5, max(1.0, np.floor(top / 4))))
    ax_a.set_ylabel("硬不可行边（条）")
    ax_a.set_title("(a) 可行性台账：什么触发了重解", fontsize=12, pad=8)
    ax_a.legend(loc="upper left", fontsize=8.4, frameon=False)
    ax_a.grid(axis="y", alpha=0.25)
    for side in ("top", "right"):
        ax_a.spines[side].set_visible(False)
    if not removed.any():
        ax_a.text(0.5, 0.62, "本轮回无几何不可行边\n（未触发重解；估计偏差不构成理由）",
                  transform=ax_a.transAxes, ha="center", va="center",
                  fontsize=8.8, color=th.MUTED,
                  bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#DCDCDC", lw=0.7))

    # ── (b) 逐航次闭环账 ──────────────────────────────────────────
    tickets = sorted(inv.tours, key=lambda t: (t.cycle, t.uav))
    pos = np.arange(len(tickets))
    w = 0.34
    for i, t in enumerate(tickets):
        c = th.CYCLE_COLOR.get(t.cycle, th.INK)
        ax_b.bar(pos[i] - w / 2 - 0.01, t.est_m / 1000.0, w, color=_EST_FACE,
                 edgecolor=c, lw=1.0, zorder=2)
        if t.ok:
            ax_b.bar(pos[i] + w / 2 + 0.01, t.path_len_m / 1000.0, w,
                     color=th.UAV_COLOR.get(t.uav, "#444"), zorder=3)
            r = t.ratio
            ax_b.text(pos[i], max(t.est_m, t.path_len_m) / 1000.0 + 0.22,
                      f"×{r:.2f}", ha="center", va="bottom", fontsize=9.2,
                      fontweight="bold",
                      color=th.OK_GREEN if abs(r - 1.0) < 0.15 else _GAP)
        else:
            ax_b.text(pos[i], t.est_m / 1000.0 + 0.22, "×— 未交付", ha="center",
                      va="bottom", fontsize=9.2, fontweight="bold", color=th.FAIL)
            ax_b.text(pos[i] - w / 2 - 0.01, 0.5 * t.est_m / 1000.0,
                      f"{t.n_stops} 站整航次无解\n{t.failure or ''}",
                      ha="center", va="center", fontsize=8.2, color=th.FAIL,
                      linespacing=1.4,
                      bbox=dict(boxstyle="round,pad=0.22", fc="white",
                                ec="#E7C3BB", lw=0.6, alpha=0.94))
    ax_b.axhline(0, color="#8C8C8C", lw=0.7)
    ax_b.set_xticks(pos, [f"C{t.cycle}\n机{t.uav}" for t in tickets], fontsize=8.8)
    ax_b.set_ylabel("航程（km）")
    ymax = max([t.est_m / 1000.0 for t in tickets] + [t.path_len_m / 1000.0 for t in tickets])
    ax_b.set_ylim(0, ymax * 1.30)
    ax_b.set_title("(b) 逐航次闭环账：计划 vs 实测", fontsize=12, pad=8)
    ax_b.grid(axis="y", alpha=0.22)
    for side in ("top", "right"):
        ax_b.spines[side].set_visible(False)
    # 六个机位时右上角图例正好压住"×1.14"读数，改成坐标区上沿的一行说明：
    # 柱顶在 1/1.30 = 0.77 处，0.99 这一行是空的，不会再和任何柱或读数打架。
    ax_b.text(0.035, 0.945, "浅蓝 = 计划（上层估计）　彩色 = 实测（按机号着色）　"
                          "×= 实测/估计（仅诊断）　斜纹 = 整航次未交付",
              transform=ax_b.transAxes, ha="left", va="top", fontsize=8.4,
              color=th.MUTED)

    # ── (c) 累计到达 + 回池→补飞 ────────────────────────────────
    cum = np.cumsum([r.n_arrived for r in rows])
    ax_c.step(xs, cum, where="post", color=th.INK, lw=1.8, zorder=3)
    ax_c.fill_between(xs, 0, cum, step="post", color="#EDF3EE", zorder=0)
    for x, v, r in zip(xs, cum, rows):
        ax_c.scatter([x], [v], s=70, color=th.CYCLE_COLOR.get(r.cycle, th.INK),
                     edgecolors="white", linewidths=1.0, zorder=4)
        ax_c.annotate(f"{int(v)}", (x, v), textcoords="offset points", xytext=(-4, 9),
                      ha="right", fontsize=9.6, fontweight="bold",
                      color=th.CYCLE_COLOR.get(r.cycle, th.INK))
        ax_c.annotate(f"当期 +{r.n_arrived}", (x, v), textcoords="offset points",
                      xytext=(-2, -16), ha="right", fontsize=8.4, color=th.MUTED)
    ax_c.axhline(inv.n_tasks, color=th.MUTED, lw=1.0, ls=(0, (4, 3)), zorder=1)
    ax_c.text(len(rows) - 0.45, inv.n_tasks + 0.4, f"任务池共 {inv.n_tasks} 个",
              ha="right", va="bottom", fontsize=8.6, color=th.MUTED)

    for k, r in enumerate(rows):
        if not r.n_reopened:
            continue
        nxt = rows[k + 1] if k + 1 < len(rows) else None
        back = sum(1 for tid in r.reopened_ids
                   if inv.arrived_cycle(tid) == (nxt.cycle if nxt else -1))
        lost = r.n_reopened - back
        n_fail = sum(1 for t in inv.tours_of(r.cycle) if not t.ok)
        txt = f"失败 {n_fail} 航次 → 回池 {r.n_reopened}"
        if nxt is not None:
            txt += f"\n下周期补飞 {back} · 搁置 {lost}"
        ax_c.annotate(txt, xy=(k, cum[k]), xytext=(k - 0.02, cum[k] + inv.n_tasks * 0.30),
                      fontsize=8.8, color=th.FAIL, ha="right", va="bottom",
                      arrowprops=dict(arrowstyle="->", color=th.FAIL, lw=1.0,
                                      connectionstyle="arc3,rad=0.2"),
                      bbox=dict(boxstyle="round,pad=0.26", fc="#FCF3F1",
                                ec="#E7C3BB", lw=0.6))

    never = [t.tag for t in inv.tasks if inv.final_state(t.id) == iv.POOL]
    ax_c.set_xticks(xs, [f"周期 {r.cycle}" for r in rows])
    ax_c.set_xlim(-0.45, len(rows) - 0.25)
    ax_c.set_ylim(0, inv.n_tasks * 1.16)
    ax_c.set_ylabel("累计到达任务数")
    ax_c.set_title("(c) 累计到达：回池的任务补回来了几个", fontsize=12, pad=8)
    ax_c.grid(axis="y", alpha=0.22)
    for side in ("top", "right"):
        ax_c.spines[side].set_visible(False)

    total_est = sum(t.est_m for t in inv.tours if t.ok) / 1000.0
    total_real = sum(t.path_len_m for t in inv.tours if t.ok) / 1000.0
    # 文案按实际数据写：本轮没剔边就说"未触发"，不能照抄"重解后周期 3 …"。
    m = ctx.metrics or {}
    n_res = int(m.get("total_resolves", 0) or 0)
    if n_res:
        fb_txt = (f"本轮重解 {n_res} 次（不可飞边 {int(m.get('infeasible_edges_written', 0) or 0)} 条、"
                  f"残留 {int(m.get('unresolved_infeasible_edges', 0) or 0)} 条）。")
    else:
        fb_txt = "本轮未触发重解（交付解里没有几何不可行边）。"
    th.fingerprint("跨层闭环账（TA+PP）：可行性回灌怎么剔边、失败的账怎么被吸收",
                   "重解条件：交付解含几何不可行边（severity=infeasible 且在白名单）", y=0.972)
    th.footnote(fig,
                f"可对账的 {sum(1 for t in inv.tours if t.ok)} 航次（不含整航次无解的航次）：计划合计 {total_est:.2f} km、"
                f"实测合计 {total_real:.2f} km，总体 ×{total_real / total_est:.2f}；"
                + fb_txt.rstrip("。")
                + (f"；始终未派 {len(never)} 个（{'/'.join(never)}）"
                   if never else "") + "。", y=0.028)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
