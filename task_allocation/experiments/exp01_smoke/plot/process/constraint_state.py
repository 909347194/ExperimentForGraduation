# -*- coding: utf-8 -*-
"""图 ``constraint_state``——约束与时限：预筛归因 / 约束状态 / 罚项量纲 / 时间窗。

【process 层 · 求解过程 ⑥】

这是全实验最容易"看起来没问题、其实是空转"的地方，所以本图**刻意把
"没触发"也画出来**——空白本身就是要汇报的结论。由原 ``constraint_audit``
+ ``time_window`` 合并而来，面板信息无丢失。

六联面板::

    (a) 预筛拦截归因     filter_reasons 逐周期堆叠（被谁挡住、挡了几个）
    (b) 求解后约束状态   每周期可行 / 不可行 + 惩罚量级
    (c) 罚项 vs 路径代价  两条量纲对照（差 2–3 个数量级即坐实 M1「单一 w 无法标定两类量纲」）
    (d) 闸门拦截归因     infeasible_tags / 容量违约逐周期
    (e) 时间窗甘特       每任务窗口 [earliest, latest] + 完成时刻（earliest 恒为 0）
    (f) 窗口余量松紧     latest − completed_at（负 = 超期）

数据来源：``metrics.json`` 的 ``cycles[].selection.filter_reasons`` /
``cycles[].muas.{violations,penalty,feasible,f2_cost}`` /
``cycles[].{infeasible_tags,infeasible_ids,capacity_violations}``，
``solution.json`` 的 ``tasks[].{earliest,latest,status,completed_at}``。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from matplotlib.ticker import MaxNLocator

from ..theme import FS, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_constraint_state"]

#: 违约类型的中文名（未知类型原样显示）。优先匹配新结构 kind（去 ID 后前缀）。
_REASON_NAMES = {
    "tw_task": "任务时间窗超期",
    "unknown_task": "未知任务",
    "range_uav": "航程不可行",
    "time_uav": "飞行时间不可行",
    "nest_capacity": "机巢容量",
    "nest_unavailable": "机巢不可用",
    "bad_nest": "机巢异常",
    "end_nest_unreachable": "终点机巢不可达",
    "tour_infeasible_uav": "航次整体不可行",
    "miss_time_window": "时间窗超期",
    "time_window": "时间窗",
    "capability_budget": "能力预算",
    "range": "航程",
    "end_nest_reachability": "终点机巢可达",
    "capacity": "容量",
    "payload": "载荷",
    "energy": "能量",
}
_REASON_COLORS = ("#4c78a8", "#f58518", "#e45756", "#72b7b2", "#b279a2", "#9d755d")


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


def _fw(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def plot_constraint_state(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = _cycles(metrics)
    if not cycles:
        return []

    n = len(cycles)
    x = np.arange(n)
    labels = [f"周期 {c.get('cycle')}" for c in cycles]

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 7.4))
    ax_filt, ax_state, ax_scale, ax_gate, ax_tw, ax_slack = axes.ravel()

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
                        fontsize=FS["tick"], color="white",
                    )
            bottom = bottom + vals
        ymax = float(bottom.max())
        ax_filt.legend(loc="upper left", fontsize=FS["legend"], framealpha=0.9)
    else:
        ax_filt.text(0.5, 0.5, "预筛阶段无任何拦截记录", ha="center", va="center",
                     transform=ax_filt.transAxes, fontsize=FS["base"], color="0.45")
    ax_filt.set_xticks(x, labels)
    ax_filt.set_ylim(0, max(ymax, 1.0) * 1.22)
    ax_filt.yaxis.set_major_locator(MaxNLocator(integer=True))
    style_axes(ax_filt, title="(a) 预筛拦截归因（filter_reasons）", ylabel="任务数")

    # ── (b) 求解后约束状态 + 惩罚量级 ───────────────────────────
    feasible = [bool((c.get("muas") or {}).get("feasible", True)) for c in cycles]
    penalties = [float((c.get("muas") or {}).get("penalty") or 0.0) for c in cycles]
    n_infeas = [int(c.get("n_infeasible") or 0) for c in cycles]
    colors = [PALETTE["executed"] if f else PALETTE["release"] for f in feasible]
    ax_state.bar(x, [0.5] * n, 0.55, bottom=0.25, color=colors,
                 edgecolor="white", linewidth=0.8)
    for xi, f, p, ni in zip(x, feasible, penalties, n_infeas):
        ax_state.text(
            xi, 0.5, ("可行" if f else "不可行") + f"\n罚 {p:.0f}",
            ha="center", va="center", fontsize=FS["annotation"], color="white",
        )
        if ni:
            ax_state.text(xi, 0.82, f"拦截 {ni}", ha="center", va="bottom",
                          fontsize=FS["tick"], color=PALETTE["release"])
    ax_state.set_ylim(0, 1.02)
    ax_state.set_yticks([])
    ax_state.set_xticks(x, labels)
    all_feasible = all(feasible) and not any(n_infeas)
    if all_feasible:
        ax_state.text(
            0.5, 0.03, "全周期可行、零拦截 —— 须对照压力场景确认是「稳」还是「约束未咬合」",
            transform=ax_state.transAxes, ha="center", va="bottom",
            fontsize=FS["annotation"], color="#993c1d",
        )
    style_axes(ax_state, title="(b) 求解后约束状态（每周期）")

    # ── (c) 量纲对照：F2 路径代价（左轴） vs 惩罚项（右轴） ──────
    f2 = [float((c.get("muas") or {}).get("f2_cost") or 0.0) for c in cycles]
    bars = ax_scale.bar(
        x, [v / 1000.0 for v in f2], 0.5, label="F2 路径代价（左轴）",
        color=PALETTE["new"], edgecolor="white", linewidth=0.6,
    )
    for b, v in zip(bars, f2):
        ax_scale.text(
            b.get_x() + b.get_width() / 2, v / 1000.0 * 1.02, f"{v/1000:.0f}",
            ha="center", va="bottom", fontsize=FS["tick"], color="0.25",
        )
    ax_scale.set_ylim(0, max(v / 1000.0 for v in f2) * 1.30 if f2 else 1.0)
    ax_scale.set_ylabel("F2 路径代价 (km)", color=PALETTE["new"])
    ax_scale.tick_params(axis="y", labelcolor=PALETTE["new"])

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
            fontsize=FS["annotation"], color="#72243e",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 2},
        )
    else:
        ax_scale_r.set_ylim(0, max(penalties) * 1.30)
    ax_scale.set_xticks(x, labels)
    style_axes(ax_scale, title="(c) 量纲对照：路径代价 vs 惩罚项")
    h1, l1 = ax_scale.get_legend_handles_labels()
    h2, l2 = ax_scale_r.get_legend_handles_labels()
    ax_scale.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=FS["legend"])

    # ── (d) 闸门拦截归因 ───────────────────────────────────────
    tag_totals: OrderedDict[str, int] = OrderedDict()
    for c in cycles:
        tags = c.get("infeasible_tags") or {}
        if isinstance(tags, dict):
            for k, v in tags.items():
                if isinstance(v, (list, tuple, set)):
                    for raw in v:
                        kind = str(raw).rsplit("_", 1)[0] if "_" in str(raw) else str(raw)
                        tag_totals[kind] = tag_totals.get(kind, 0) + 1
                elif isinstance(v, dict):
                    for kk, vv in v.items():
                        tag_totals[str(kk)] = tag_totals.get(str(kk), 0) + (
                            int(vv or 0) if isinstance(vv, (int, float)) else 1)
                else:
                    tag_totals[str(k)] = tag_totals.get(str(k), 0) + (
                        int(v or 0) if isinstance(v, (int, float)) else 1)
        tag_keys = set(tags.keys()) if isinstance(tags, dict) else set()
        for tid in c.get("infeasible_ids") or []:
            if tid not in tag_keys:
                tag_totals["未标注原因"] = tag_totals.get("未标注原因", 0) + 1
        for _ in c.get("capacity_violations") or []:
            tag_totals["机巢容量违约"] = tag_totals.get("机巢容量违约", 0) + 1
    if tag_totals:
        keys = list(tag_totals)
        vals = [tag_totals[k] for k in keys]
        ax_gate.barh(np.arange(len(keys)), vals, 0.5,
                     color=[_REASON_COLORS[i % len(_REASON_COLORS)] for i in range(len(keys))],
                     edgecolor="white", linewidth=0.6)
        for i, v in enumerate(vals):
            ax_gate.text(v + max(vals) * 0.02, i, f"{v}", va="center", fontsize=FS["tick"])
        ax_gate.set_yticks(np.arange(len(keys)), [_REASON_NAMES.get(k, k) for k in keys])
        ax_gate.set_xlim(0, max(vals) * 1.2 + 0.5)
        style_axes(ax_gate, title="(d) 闸门拦截归因（infeasible_tags）", xlabel="任务数")
    else:
        ax_gate.text(
            0.5, 0.55, "闸门拦截 0 个任务", ha="center", va="center",
            transform=ax_gate.transAxes, fontsize=FS["base"], color="0.45",
        )
        ax_gate.text(
            0.5, 0.38, "（物理不可达 / 容量违约均未发生）",
            ha="center", va="center", transform=ax_gate.transAxes,
            fontsize=FS["footnote"], color="0.55",
        )
        ax_gate.set_xticks([])
        ax_gate.set_yticks([])
        style_axes(ax_gate, title="(d) 闸门拦截归因（infeasible_tags）")

    # ── (e) 时间窗甘特 ──────────────────────────────────────────
    tw_tasks = [t for t in (solution.get("tasks") or []) if t.get("latest") is not None]
    if tw_tasks:
        horizon = max(
            [_fw(c.get("current_time")) for c in cycles]
            + [_fw(t.get("completed_at")) for t in tw_tasks]
            + [_fw(t.get("latest")) for t in tw_tasks]
            + [0.0]
        )
        tw_sorted = sorted(tw_tasks, key=lambda t: _fw(t.get("latest")))
        m = len(tw_sorted)
        y = np.arange(m)
        for i, t in enumerate(tw_sorted):
            e = _fw(t.get("earliest"))
            l = _fw(t.get("latest"))
            done = str(t.get("status")) == "done" and t.get("completed_at") is not None
            ca = _fw(t.get("completed_at"), np.nan) if done else np.nan
            ax_tw.barh(
                i, max(l - e, 1e-6), left=e, height=0.62,
                color="#4c78a8", alpha=0.22, edgecolor="#4c78a8", linewidth=0.6, zorder=2,
            )
            if l <= horizon + 1e-9:
                ax_tw.plot([l, l], [i - 0.31, i + 0.31], color="#4c78a8",
                          linewidth=1.2, zorder=3)
            if done and np.isfinite(ca):
                ok = ca <= l + 1e-9
                ax_tw.scatter(
                    [ca], [i], s=40, marker="o", zorder=5,
                    color="#54a24b" if ok else "#e45756",
                    edgecolor="white", linewidth=0.8,
                )
            else:
                ax_tw.scatter(
                    [horizon], [i], s=30, marker=">", zorder=5,
                    color="#e45756", edgecolor="white", linewidth=0.7,
                )
        ax_tw.set_yticks(y, [f"#{t.get('id')}" for t in tw_sorted])
        ax_tw.invert_yaxis()
        ax_tw.set_xlim(0, horizon * 1.04)
        for t_line in [_fw(c.get("current_time")) for c in cycles]:
            ax_tw.axvline(t_line, color="0.65", linewidth=0.7, linestyle="--", zorder=1)
        handles = [
            plt.Line2D([], [], color="#4c78a8", alpha=0.55, linewidth=6,
                       label="可行窗口 [earliest, latest]"),
            plt.Line2D([], [], marker="o", linestyle="none", color="#54a24b",
                       markersize=7, label="窗口内完成"),
            plt.Line2D([], [], marker="o", linestyle="none", color="#e45756",
                       markersize=7, label="超期完成"),
            plt.Line2D([], [], marker=">", linestyle="none", color="#e45756",
                       markersize=7, label="期末仍未完成"),
        ]
        ax_tw.legend(handles=handles, loc="lower right", fontsize=FS["legend"],
                     framealpha=0.92, labelspacing=0.35, handlelength=1.3)
        style_axes(
            ax_tw, title=f"(e) 任务时间窗与完成时刻（{m} 个，按 latest 升序）",
            xlabel="时刻 (s)", ylabel="任务 id",
        )
    else:
        ax_tw.text(0.5, 0.5, "无时间窗数据", ha="center", va="center",
                   fontsize=FS["base"], color="0.45", transform=ax_tw.transAxes)
        style_axes(ax_tw, title="(e) 任务时间窗与完成时刻")

    # ── (f) 窗口余量松紧 ────────────────────────────────────────
    done_tasks = [
        t for t in tw_tasks
        if str(t.get("status")) == "done" and t.get("completed_at") is not None
    ]
    slack_note = ""
    if done_tasks:
        slacks, widths, cols = [], [], []
        for t in done_tasks:
            l = _fw(t.get("latest"))
            e = _fw(t.get("earliest"))
            ca = _fw(t.get("completed_at"))
            widths.append(max(l - e, 1e-6))
            slacks.append(l - ca)
            cols.append("#54a24b" if ca <= l + 1e-9 else "#e45756")
        order = np.argsort(slacks)
        ys = np.arange(len(order))
        ax_slack.barh(
            ys, [slacks[i] for i in order], 0.62,
            color=[cols[i] for i in order], edgecolor="white", linewidth=0.6,
        )
        ax_slack.set_yticks(ys, [f"#{done_tasks[i].get('id')}" for i in order])
        ax_slack.axvline(0, color="0.35", linewidth=1.0)
        ax_slack.invert_yaxis()
        slack_note = (f"全部 {len(done_tasks)} 个完成任务均在窗口内，"
                      f"最小余量 {min(slacks):.0f}s" if min(slacks) > 0 else
                      f"{sum(1 for s in slacks if s < 0)} 个超期完成")
        style_axes(ax_slack, title=f"(f) 窗口余量（latest − completed_at）",
                   xlabel="余量 (s，负=超期)")
    else:
        ax_slack.text(0.5, 0.5, "无已完成任务", ha="center", va="center",
                      fontsize=FS["base"], color="0.45", transform=ax_slack.transAxes)
        style_axes(ax_slack, title="(f) 窗口余量")

    fig.suptitle("exp01_smoke：约束与时限（预筛 · 可行 · 罚项量纲 · 时间窗）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    footnote(fig, "口径：空白通道本身即结论——零拦截须对照压力场景确认是「稳」还是"
                  "「约束未咬合」；时间窗 earliest 恒为 0（窗口自实验起算，非画错），"
                  "(e) 灰虚线 = 周期边界；"
                  f"{slack_note}。")
    return save_figure(fig, Path(out_dir) / "P11_constraint_state", formats, dpi=dpi)
