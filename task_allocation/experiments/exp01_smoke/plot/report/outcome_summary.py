# -*- coding: utf-8 -*-
"""图 ``outcome_summary``——结果总览：完成率轨迹与任务终局。

【report 层 · 结果分析】

回答 Q4「实验说明什么」的**核心图**：滚动闭环跑完，任务到底做得怎么样。

2×2 四联面板：

    (a) 完成率 CR 轨迹   累计到达 / 累计完成（柱）+ CR%（右轴）；
                         口径 = 累计完成 / 累计到达，终点与 summary.completion_rate 一致
    (b) 任务终局构成     到达任务的最终去向：完成 / 滞留池 / 到期作废 / 曾被闸门拦回
                         （拦回 = 解被判违约、不顺延完成、解绑回池——完成率不说谎的机制）
    (c) 双目标值         每周期 F1 收益 / F2 代价，不可行周期红字标记
    (d) 关键指标表       summary 全字段 + 单/多 seed 标注

数据来源：``metrics.json`` 的 cycles[] / summary / seeds。
本图对**调试期同样有用**：完成率 0、全周期不可行、闸门大量拦回等回归问题，
在这张图上一眼可见（而不是被平均进某个数字里）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import FIG_WIDE, FS, MONO_ZH, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_outcome_summary"]


def plot_outcome_summary(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    summary = metrics.get("summary") or {}
    if not cycles or not summary:
        return []

    n_cycles = len(cycles)
    cyc_labels = [f"C{c.get('cycle')}" for c in cycles]
    x = np.arange(n_cycles)
    w = 0.38

    # ── 数据准备 ─────────────────────────────────────────────────
    n_new = [int((c.get("pool_counts") or {}).get("n_new", 0)) for c in cycles]
    n_done = [int(c.get("n_completed", 0)) for c in cycles]
    cum_new = np.cumsum(n_new)
    cum_done = np.cumsum(n_done)
    cr_curve = [d / a if a else 0.0 for d, a in zip(cum_done, cum_new)]
    cr_final = cr_curve[-1] if cr_curve else 0.0

    done_ids: set[int] = set()
    expired_ids: set[int] = set()
    gated_ids: set[int] = set()
    for c in cycles:
        done_ids.update(int(i) for i in (c.get("completed_ids") or []))
        expired_ids.update(int(i) for i in (c.get("expired_ids") or []))
        gated_ids.update(int(i) for i in (c.get("infeasible_ids") or []))
    n_arrived = int(summary.get("n_arrived", 0))
    n_done_total = int(summary.get("n_completed", len(done_ids)))
    n_remain = int(summary.get("n_remaining_pool",
                               max(n_arrived - n_done_total - len(expired_ids), 0)))
    n_expired = len(expired_ids)
    n_gated = len(gated_ids - done_ids - expired_ids)

    f1 = [float((c.get("muas") or {}).get("f1_reward", 0.0)) for c in cycles]
    f2 = [float((c.get("muas") or {}).get("f2_cost", 0.0)) / 1000.0 for c in cycles]
    feas = [bool((c.get("muas") or {}).get("feasible", False)) for c in cycles]

    n_seeds = len(metrics.get("seeds") or [])
    feas_rate = float(summary.get("cycle_feasible_rate", 0.0))
    completion = float(summary.get("completion_rate", 0.0))

    fig, ((ax_a, ax_b), (ax_c, ax_d)) = plt.subplots(
        2, 2, figsize=(FIG_WIDE, 7.0))

    # ── (a) CR 轨迹 ──────────────────────────────────────────────
    ax_a.bar(x - w / 2, cum_new, w, color=PALETTE["remain"], alpha=0.75,
             label="累计到达", edgecolor="white", linewidth=0.4)
    ax_a.bar(x + w / 2, cum_done, w, color=PALETTE["executed"], alpha=0.9,
             label="累计完成", edgecolor="white", linewidth=0.4)
    style_axes(ax_a, title=f"(a) 完成率 CR 轨迹（终点 {cr_final:.1%}，"
                           f"summary 口径 {completion:.1%}）",
               xlabel="滚动周期", ylabel="任务数")
    ax_a.set_xticks(x, cyc_labels)
    ax_a2 = ax_a.twinx()
    ax_a2.plot(x, [v * 100 for v in cr_curve], "-o", color=PALETTE["cost"],
               lw=1.6, ms=4, label="CR%（右轴）")
    ax_a2.set_ylabel("CR（%）", color=PALETTE["cost"], fontsize=FS["base"])
    ax_a2.set_ylim(-4, 105)
    ax_a2.tick_params(axis="y", labelcolor=PALETTE["cost"])
    ax_a2.spines["right"].set_visible(True)
    ax_a2.grid(False)
    h1, l1 = ax_a.get_legend_handles_labels()
    h2, l2 = ax_a2.get_legend_handles_labels()
    ax_a.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=FS["legend"])

    # ── (b) 任务终局构成 ─────────────────────────────────────────
    parts = [
        ("已完成", n_done_total, PALETTE["executed"]),
        ("滞留池中", n_remain, PALETTE["deferred"]),
        ("到期作废", n_expired, PALETTE["release"]),
        ("闸门拦回", n_gated, "#b279a2"),
    ]
    parts = [(k, v, c) for k, v, c in parts if v > 0]
    if not parts:
        parts = [("无任务", 0, "#c9ced4")]
    bottom = 0
    for lab, v, col in parts:
        ax_b.bar([0], [v], 0.5, bottom=bottom, color=col, alpha=0.9,
                 edgecolor="white", linewidth=0.6, label=f"{lab}（{v}）")
        if v > 0:
            ax_b.text(0, bottom + v / 2, f"{lab}\n{v}", ha="center", va="center",
                      fontsize=FS["annotation"], color="white")
        bottom += v
    ax_b.set_xlim(-0.6, 0.6)
    ax_b.set_xticks([])
    style_axes(ax_b, title=f"(b) 任务终局构成（到达 {n_arrived} 个）",
               ylabel="任务数")
    ax_b.legend(loc="upper left", fontsize=FS["legend"],
                labelspacing=0.35, handlelength=1.2)

    # ── (c) 双目标值 ─────────────────────────────────────────────
    ax_c.bar(x - w / 2, f1, w, color=PALETTE["reward"], alpha=0.85,
             label="F1 收益", edgecolor="white", linewidth=0.4)
    ax_c.bar(x + w / 2, f2, w, color=PALETTE["cost"], alpha=0.85,
             label="F2 代价（km）", edgecolor="white", linewidth=0.4)
    for xi, ok in zip(x, feas):
        if not ok and f1:
            ax_c.annotate("不可行", (xi, max(f1) * 0.96),
                          textcoords="offset points", xytext=(0, 4),
                          ha="center", fontsize=FS["annotation"],
                          color=PALETTE["release"], fontweight=500)
    style_axes(ax_c, title="(c) 双目标值（每周期）", xlabel="滚动周期",
               ylabel="收益 / 代价")
    ax_c.set_xticks(x, cyc_labels)
    ax_c.legend(loc="upper right", fontsize=FS["legend"])

    # ── (d) 关键指标表 ───────────────────────────────────────────
    rows = [
        ("到达 / 完成任务", f"{n_arrived} / {n_done_total}"),
        ("完成率 CR", f"{completion:.1%}"),
        ("周期解可行率", f"{feas_rate:.0%}"),
        ("闸门拦回（事件次）", str(int(summary.get("n_infeasible_delivery", 0)))),
        ("总收益 F1", f"{summary.get('total_reward', 0.0):.1f}"),
        ("总路径代价", f"{summary.get('total_cost', 0.0) / 1000:.1f} km"),
        ("平均等待", f"{summary.get('avg_wait', 0.0):.0f} s"),
        ("平均可用机数", f"{summary.get('K_avail_mean', 0.0):.1f}"),
        ("求解总耗时", f"{summary.get('solve_time_ms', 0.0) / 1000:.1f} s"),
        ("运行口径", "单 seed（调试）" if n_seeds <= 1 else f"{n_seeds} seeds 均值"),
    ]
    ax_d.set_axis_off()
    ax_d.set_xlim(0, 1)
    ax_d.set_ylim(0, len(rows) + 1.2)
    ax_d.text(0.0, len(rows) + 0.55, "(d) 关键指标（summary）",
              fontsize=FS["title"], fontweight=500, va="center", color="#1f2933")
    for i, (k, v) in enumerate(rows):
        y = len(rows) - i - 0.5
        if i % 2 == 0:
            ax_d.add_patch(plt.Rectangle((0, y - 0.40), 1, 0.80,
                                         facecolor="#f5f6f7", edgecolor="none",
                                         zorder=0))
        warn = (k == "周期解可行率" and feas_rate < 0.999) or \
               (k == "完成率 CR" and completion < 0.05)
        ax_d.text(0.02, y, k, fontsize=FS["tick"], va="center",
                  family=MONO_ZH, color="#3c4148", zorder=2)
        ax_d.text(0.98, y, v, fontsize=FS["tick"], va="center", ha="right",
                  family=MONO_ZH,
                  color=PALETTE["release"] if warn else "#0f4c81", zorder=2)

    fig.suptitle("exp01_smoke：结果总览（完成率轨迹 · 终局构成 · 双目标 · 关键指标）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    footnote(fig, "口径：CR = 累计完成 / 累计到达（到达以入池周期计，"
                  "轨迹终点与 summary.completion_rate 同口径）；滞留 = 实验结束时"
                  "仍在任务池；闸门拦回 = 违约任务不顺延完成、解绑回池（事件次数）。"
                  "F1 = 任务收益和，F2 = 三维路径代价；红色数值为调试期回归信号。")
    return save_figure(fig, Path(out_dir) / "outcome_summary", formats, dpi=dpi)
