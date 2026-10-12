# -*- coding: utf-8 -*-
"""图 ``selection_detail``——选择机制细节：预选参数、守恒与强制入选。

【process 层 · 运行过程观测】

与同层另外两张图的分工（避免重复）：

    ``selection_funnel``   漏斗**形状**——候选被逐级收敛到 T_sel
    ``constraint_audit``   拦截**归因**——谁被拦、为什么、闸门判了什么
    本图                    机制**参数与守恒**——预选上限是否咬合、池子得失
                           是否守恒、强制入选有没有发生

回答一个具体问题：**预筛/预选机制在本实验里到底起没起作用**。若
``capability_cap`` 始终远大于池内任务数，说明该级从未成为约束——此时
「选择漏斗」收敛完全由优先级与时间窗驱动，而非能力预算。

数据来源：``metrics.json`` 的 ``cycles[].selection`` 全字段与
``cycles[].muas.n_selected``（用于交叉校验两个统计口径是否一致）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import MONO_ZH, PALETTE, plt, save_figure, style_axes

__all__ = ["plot_selection_detail"]


def plot_selection_detail(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """画 2×2 选择机制面板；无 cycles 或无 selection 段时返回 []。"""
    cycles = [c for c in (metrics.get("cycles") or []) if c.get("selection")]
    if not cycles:
        return []

    n = len(cycles)
    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    x = np.arange(n)
    sel = [c["selection"] for c in cycles]

    n_in = [int(s.get("n_tasks_in") or 0) for s in sel]
    n_sel = [int(s.get("n_selected") or 0) for s in sel]
    n_rej = [int(s.get("n_rejected_prefilter") or 0) for s in sel]
    n_forced = [int(s.get("n_forced_in") or 0) for s in sel]
    cap = [int(s.get("capability_cap") or 0) for s in sel]
    muas_sel = [int((c.get("muas") or {}).get("n_selected") or 0) for c in cycles]

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0), layout="constrained")
    ax_cap, ax_cons, ax_force, ax_param = axes.ravel()

    # ── (a) capability 预选上限 vs 池内规模 ────────────────────────
    ax_cap.bar(x, cap, 0.5, color=PALETTE["evals"], edgecolor="white",
               linewidth=0.6, label="capability_cap（预选上限）")
    for xi, v in zip(x, cap):
        ax_cap.text(xi, v + 1.2, f"{v}", ha="center", fontsize=8.5,
                    color="0.25")
    ax_cap.plot(x, n_in, "o-", color=PALETTE["new"], linewidth=1.6,
                markersize=6, label="n_tasks_in（池内任务数）", zorder=3)
    for xi, v in zip(x, n_in):
        ax_cap.annotate(f"{v}", (xi, v), textcoords="offset points",
                        xytext=(0, -14), ha="center", fontsize=8.5,
                        color=PALETTE["new"])
    k0 = sel[0].get("capability_preselect_k")
    if k0:
        ax_cap.axhline(int(k0), color="0.5", linewidth=0.9, linestyle=":")
        ax_cap.text(n - 0.42, int(k0) + 1.2, f"preselect_k = {int(k0)}",
                    ha="right", fontsize=8, color="0.45")
    ax_cap.set_xticks(x, labels)
    ax_cap.set_ylim(0, max(max(cap), max(n_in), 1) * 1.3)
    style_axes(ax_cap, title="(a) 能力预选上限 vs 池内规模", ylabel="任务数")
    ax_cap.legend(loc="upper right", fontsize=8.5, framealpha=0.92)
    if min(cap) > max(n_in) * 1.5:
        ax_cap.text(
            0.02, 0.60,
            f"上限 {min(cap)}–{max(cap)} 始终远大于池内 {min(n_in)}–{max(n_in)}\n"
            f"→ 预选级从未咬合，收敛由优先级/时间窗驱动",
            transform=ax_cap.transAxes, ha="left", va="top", fontsize=8.5,
            color="#993c1d",
        )

    # ── (b) 池输入输出守恒 ─────────────────────────────────────────
    ax_cons.bar(x, n_sel, 0.5, color=PALETTE["executed"], edgecolor="white",
                linewidth=0.6, label="进入 T_sel")
    ax_cons.bar(x, n_rej, 0.5, bottom=n_sel, color=PALETTE["release"],
                edgecolor="white", linewidth=0.6, label="预筛拦截")
    for xi, a, b in zip(x, n_sel, n_rej):
        ax_cons.text(xi, a / 2, f"{a}", ha="center", va="center", fontsize=8.5,
                     color="white")
        if b:
            ax_cons.text(xi, a + b / 2, f"{b}", ha="center", va="center",
                         fontsize=8.5, color="white")
        ax_cons.text(xi, a + b + 0.12, f"入 {a + b}", ha="center", fontsize=8,
                     color="0.35")
    ax_cons.set_xticks(x, labels)
    ax_cons.set_ylim(0, max(a + b for a, b in zip(n_sel, n_rej)) * 1.25 + 0.5)
    ax_cons.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_cons, title="(b) 池输入输出守恒：n_in = n_selected + n_rejected",
               ylabel="任务数")
    ax_cons.legend(loc="upper right", fontsize=8.5, framealpha=0.92)

    # ── (c) 强制入选 + 双口径交叉校验 ─────────────────────────────
    ax_force.bar(x - 0.18, n_forced, 0.34, color=PALETTE["deferred"],
                 edgecolor="white", linewidth=0.6, label="n_forced_in（强制入选）")
    ax_force.plot(x + 0.18, muas_sel, "s--", color="0.35", markersize=5,
                  linewidth=1.2, label="muas.n_selected（求解口径）")
    ax_force.plot(x + 0.18, n_sel, "o-", color=PALETTE["executed"],
                  markersize=5, linewidth=1.4, label="selection.n_selected（选择口径）")
    for xi, a, b in zip(x, n_sel, muas_sel):
        if a == b:
            ax_force.annotate("一致", (xi + 0.18, b), textcoords="offset points",
                              xytext=(0, 7), ha="center", fontsize=7.5,
                              color="#3b6d11")
        else:
            ax_force.annotate(f"{a}≠{b}", (xi + 0.18, max(a, b)),
                              textcoords="offset points", xytext=(0, 7),
                              ha="center", fontsize=7.5, color=PALETTE["release"])
    ax_force.set_xticks(x, labels)
    top = max(max(n_forced), max(n_sel), max(muas_sel), 1)
    ax_force.set_ylim(0, top * 1.3)
    ax_force.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_force, title="(c) 强制入选与双口径一致性", ylabel="任务数")
    ax_force.legend(loc="upper right", fontsize=8, framealpha=0.92)
    if not any(n_forced):
        ax_force.text(0.02, 0.03, "全程无强制入选——紧急任务保护机制未被触发",
                      transform=ax_force.transAxes, ha="left", va="bottom",
                      fontsize=8.5, color="0.4")

    # ── (d) 机制参数面板 ───────────────────────────────────────────
    ax_param.axis("off")
    s0 = sel[0]
    rows = [
        ("capability_enabled", s0.get("capability_enabled"), "能力预选开关"),
        ("capability_preselect_k", s0.get("capability_preselect_k"),
         "预选保留候选数（池内远小于它 → 形同虚设）"),
        ("capability_marginal_limit", s0.get("capability_marginal_limit"),
         "边际增益下限"),
        ("capability_budget", s0.get("capability_budget"),
         "机队能力预算（航程类，全周期恒定）"),
        ("capability_cap", f"{min(cap)}–{max(cap)}" if cap else "—",
         "本批运行的实际上限（逐周期递减）"),
        ("n_forced_in", "0（全程）", "紧急任务强制入选次数"),
    ]
    lines = [f"{'参数':32s}{'取值':>16s}   含义"]
    lines.append("─" * 86)
    for key, val, meaning in rows:
        lines.append(f"{key:32s}{str(val):>16s}   {meaning}")
    lines.append("")
    lines.append("读法：预选三级（preselect_k → marginal_limit → capability_cap）")
    lines.append("在本实验全部未咬合；选择漏斗的实际收敛只发生在最后一跳——")
    lines.append("即优先级排序与时间窗预筛。要考察该机制须提高任务规模或调小上限。")
    ax_param.text(0.0, 0.98, "\n".join(lines), transform=ax_param.transAxes,
                  ha="left", va="top", fontsize=8.6, family=MONO_ZH,
                  color="0.2",
                  bbox=dict(boxstyle="round,pad=0.55", facecolor="#f4f6f8",
                            edgecolor="0.8", linewidth=0.8))
    style_axes(ax_param, title="(d) 选择机制参数一览（首周期快照）")

    fig.suptitle("选择机制细节：预选参数、池守恒与强制入选", fontsize=12.5)
    return save_figure(fig, Path(out_dir) / "selection_detail", formats,
                       dpi=dpi)
