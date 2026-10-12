# -*- coding: utf-8 -*-
"""图 ``constraint_audit``——约束到底咬合了没有。

【process 层 · 运行过程观测】

这是全实验最容易"看起来没问题、其实是空转"的地方，所以本图**刻意把
"没触发"也画出来**——空白本身就是要汇报的结论：

    (a) 预筛阶段归因：``filter_reasons`` 逐周期堆叠（被谁挡住、挡了几个）
    (b) 求解后约束状态：每周期可行/不可行 + ``penalty`` 量级
    (c) 罚项 vs 路径代价：两条量纲的对照（对应审查 M1「单一 w_violation
        无法同时标定两类量纲」——两条柱若差 2–3 个数量级，即坐实该问题）
    (d) 闸门拦截归因：``infeasible_tags`` / 容量违约逐周期

⚠️ 读图须警惕**惰性约束**：若某通道全场为 0，可能是"机制真的不需要触发"，
也可能是"参数根本没让它有机会触发"（审查 E1：容量占用 4 恰好等于上限却
从不越界、航程违约恒 0、α 预筛完全不生效）。本图会显式标注全零通道，
提示换压力场景复验，而不是把它当作"方法很稳"的证据。

数据来源：``results/metrics.json`` 的 ``cycles[].selection.filter_reasons``、
``cycles[].muas.{violations,penalty,feasible,f2_cost}``、
``cycles[].{infeasible_tags,infeasible_ids,capacity_violations,n_infeasible}``。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from matplotlib.ticker import MaxNLocator

from ..theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_constraint_audit"]

#: 违约类型的中文名（未知类型原样显示）
_REASON_NAMES = {
    "miss_time_window": "时间窗超期",
    "time_window": "时间窗",
    "capability_budget": "能力预算",
    "range": "航程",
    "end_nest_reachability": "终点机巢可达",
    "nest_capacity": "机巢容量",
    "capacity": "容量",
    "payload": "载荷",
    "energy": "能量",
}

_REASON_COLORS = ("#e45756", "#f58518", "#4c78a8", "#72b7b2", "#b279a2", "#9d755d")


def _cycles(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return list(metrics.get("cycles") or [])


def _reason_matrix(cycles: Sequence[dict[str, Any]]) -> tuple[list[str], np.ndarray]:
    """把逐周期 ``filter_reasons`` 汇总成 (原因列表, [原因 × 周期] 计数)。"""
    keys: OrderedDict[str, None] = OrderedDict()
    for c in cycles:
        reasons = (c.get("selection") or {}).get("filter_reasons") or {}
        if isinstance(reasons, dict):
            for k in reasons:
                keys.setdefault(str(k), None)

    names = list(keys)
    grid = np.zeros((len(names), len(cycles)), dtype=float)
    for i, c in enumerate(cycles):
        reasons = (c.get("selection") or {}).get("filter_reasons") or {}
        if not isinstance(reasons, dict):
            continue
        for j, k in enumerate(names):
            v = reasons.get(k, 0)
            grid[j, i] = float(v) if isinstance(v, (int, float)) else 0.0
    return names, grid


def plot_constraint_audit(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """绘制约束审计图；无任何周期数据时返回空列表。"""
    cycles = _cycles(metrics)
    if not cycles:
        return []

    n = len(cycles)
    x = np.arange(n)
    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0), layout="constrained")
    ((ax_filt, ax_state), (ax_scale, ax_gate)) = axes

    # ── (a) 预筛归因堆叠 ────────────────────────────────────────
    names, grid = _reason_matrix(cycles)
    ymax = 1.0
    if names:
        bottom = np.zeros(n)
        for i, key in enumerate(names):
            vals = grid[i]
            ax_filt.bar(
                x, vals, 0.55, bottom=bottom,
                label=_REASON_NAMES.get(key, key),
                color=_REASON_COLORS[i % len(_REASON_COLORS)],
                edgecolor="white", linewidth=0.6,
            )
            for xi, v, b in zip(x, vals, bottom):
                if v > 0:
                    ax_filt.text(
                        xi, b + v / 2, f"{v:.0f}", ha="center", va="center",
                        fontsize=8, color="white",
                    )
            bottom = bottom + vals
        ymax = float(bottom.max())
        ax_filt.legend(loc="upper left", framealpha=0.9)
    else:
        ax_filt.text(0.5, 0.5, "预筛阶段无任何拦截记录", ha="center", va="center",
                     transform=ax_filt.transAxes, fontsize=10, color="0.45")
    ax_filt.set_xticks(x, labels)
    ax_filt.set_ylim(0, max(ymax, 1.0) * 1.22)
    ax_filt.yaxis.set_major_locator(MaxNLocator(integer=True))
    style_axes(ax_filt, title="(a) 预筛拦截归因（filter_reasons）", ylabel="任务数")

    # ── (b) 求解后约束状态 + 惩罚量级 ───────────────────────────
    feasible = [
        bool((c.get("muas") or {}).get("feasible", True)) for c in cycles
    ]
    penalties = [float((c.get("muas") or {}).get("penalty") or 0.0) for c in cycles]
    n_infeas = [int(c.get("n_infeasible") or 0) for c in cycles]
    colors = [PALETTE["executed"] if f else PALETTE["release"] for f in feasible]
    ax_state.bar(x, [0.5] * n, 0.55, bottom=0.25, color=colors,
                 edgecolor="white", linewidth=0.8)
    for xi, f, p, ni in zip(x, feasible, penalties, n_infeas):
        ax_state.text(
            xi, 0.5, ("可行" if f else "不可行") + f"\n罚 {p:.0f}",
            ha="center", va="center", fontsize=8.5, color="white",
        )
        if ni:
            ax_state.text(xi, 0.82, f"拦截 {ni}", ha="center", va="bottom",
                          fontsize=8, color=PALETTE["release"])
    ax_state.set_ylim(0, 1.02)
    ax_state.set_yticks([])
    ax_state.set_xticks(x, labels)
    all_feasible = all(feasible) and not any(n_infeas)
    if all_feasible:
        ax_state.text(
            0.5, 0.03, "全周期可行、零拦截 —— 须对照压力场景确认是“稳”还是“约束未咬合”",
            transform=ax_state.transAxes, ha="center", va="bottom",
            fontsize=8.5, color="#993c1d",
        )
    style_axes(ax_state, title="(b) 求解后约束状态（每周期）")

    # ── (c) 量纲对照：F2 路径代价（左轴） vs 惩罚项（右轴） ──────
    f2 = [float((c.get("muas") or {}).get("f2_cost") or 0.0) for c in cycles]
    bars = ax_scale.bar(
        x, [v / 1000.0 for v in f2], 0.5, label="F2 路径代价（左轴）",
        color=PALETTE["cost"], edgecolor="white", linewidth=0.6,
    )
    for b, v in zip(bars, f2):
        ax_scale.text(
            b.get_x() + b.get_width() / 2, v / 1000.0 * 1.02, f"{v/1000:.0f}",
            ha="center", va="bottom", fontsize=8, color="0.25",
        )
    ax_scale.set_ylabel("F2 路径代价 (km)", color=PALETTE["cost"])
    ax_scale.tick_params(axis="y", labelcolor=PALETTE["cost"])

    ax_scale_r = ax_scale.twinx()
    ax_scale_r.plot(
        x, penalties, marker="D", markersize=7, linewidth=1.6,
        color="#b279a2", label="惩罚项（右轴）", zorder=4,
    )
    ax_scale_r.set_ylabel("惩罚项", color="#b279a2")
    ax_scale_r.tick_params(axis="y", labelcolor="#b279a2")
    ax_scale_r.grid(False)
    if max(penalties) == 0:
        ax_scale_r.set_ylim(-0.6, 0.6)
        ax_scale_r.text(
            0.5, 0.5, "惩罚项 = 0：本轮 w_violation 全程未起作用",
            transform=ax_scale_r.transAxes, ha="center", va="center",
            fontsize=8.5, color="#72243e",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 2},
        )
    else:
        ax_scale_r.set_ylim(bottom=0)
    ax_scale.set_xticks(x, labels)
    style_axes(ax_scale, title="(c) 量纲对照：路径代价 vs 惩罚项")
    h1, l1 = ax_scale.get_legend_handles_labels()
    h2, l2 = ax_scale_r.get_legend_handles_labels()
    ax_scale.legend(h1 + h2, l1 + l2, loc="upper right", framealpha=0.9)

    # ── (d) 闸门拦截归因 ───────────────────────────────────────
    tag_totals: OrderedDict[str, int] = OrderedDict()
    for c in cycles:
        tags = c.get("infeasible_tags") or {}
        if isinstance(tags, dict):
            for k, v in tags.items():
                tag_totals[str(k)] = tag_totals.get(str(k), 0) + int(v or 0)
        for tid in c.get("infeasible_ids") or []:
            tag_totals.setdefault("未标注原因", 0)
            tag_totals["未标注原因"] += 1
        for _ in c.get("capacity_violations") or []:
            tag_totals["机巢容量违约"] = tag_totals.get("机巢容量违约", 0) + 1
    if tag_totals:
        keys = list(tag_totals)
        vals = [tag_totals[k] for k in keys]
        ax_gate.barh(np.arange(len(keys)), vals, 0.5,
                     color=[_REASON_COLORS[i % len(_REASON_COLORS)] for i in range(len(keys))],
                     edgecolor="white", linewidth=0.6)
        for i, v in enumerate(vals):
            ax_gate.text(v + max(vals) * 0.02, i, f"{v}", va="center", fontsize=8.5)
        ax_gate.set_yticks(np.arange(len(keys)), [_REASON_NAMES.get(k, k) for k in keys])
        ax_gate.set_xlim(0, max(vals) * 1.2 + 0.5)
    else:
        ax_gate.text(
            0.5, 0.55, "闸门拦截 0 个任务", ha="center", va="center",
            transform=ax_gate.transAxes, fontsize=10, color="0.45",
        )
        ax_gate.text(
            0.5, 0.38, "（物理不可达 / 容量违约均未发生）",
            ha="center", va="center", transform=ax_gate.transAxes,
            fontsize=8.5, color="0.55",
        )
        ax_gate.set_xticks([])
        ax_gate.set_yticks([])
    style_axes(ax_gate, title="(d) 闸门拦截归因（infeasible_tags）")
    if tag_totals:
        ax_gate.set_xlabel("任务数")

    fig.suptitle("约束审计：拦截 · 可行 · 罚项 · 量纲", fontsize=12.5)

    return save_figure(fig, Path(out_dir) / "constraint_audit", formats, dpi=dpi)
