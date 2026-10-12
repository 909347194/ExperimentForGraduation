# -*- coding: utf-8 -*-
"""图 ``time_window``——每个任务的时间窗，到底赶上没有。

【process 层 · 运行过程观测】

时间窗是整套方法里**唯一带绝对时限的硬约束**（``tasks[].latest``），
也是两组配置对照的全部差异所在——但在此之前它从未被画出来过。
本图回答三个问题：

    (a) **窗口在哪、完成在哪**：每任务一行，横条 = 可行窗口
        ``[earliest, latest]``，圆点 = 实际完成时刻；能直接看出
        "提前多少 / 卡点完成 / 超期未完成"
    (b) **松紧度**：``completed_at - latest`` 归一化到窗口长度的比值。
        越接近 0 越是踩线；为负才是超期。这张图是"时间窗有没有真的咬合"的
        直接证据（配合两组对照读）
    (c) **逐周期窗口压力**：本周期新到任务 vs 本周期内到期的任务——
        压力峰值出现在哪、有没有堆积

注意本实验 ``earliest`` 恒为 0（窗口一律从 0 起算），故 (a) 中所有横条
左端对齐在 0——**这不是画错**，而是当前任务生成口径的结果，已在图上注明。

数据来源：``results/solution.json`` 的 ``tasks[].{arrival_time, earliest,
latest, status, completed_at}``，周期边界取自 ``results/metrics.json`` 的
``cycles[].current_time``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_time_window"]

_C_DONE = "#54a24b"
_C_PENDING = "#e45756"
_C_WINDOW = "#4c78a8"


def _fw(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def plot_time_window(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """绘制时间窗满足度图；无任务时间窗数据时返回空列表。"""
    tasks = [
        t for t in (solution.get("tasks") or [])
        if t.get("latest") is not None
    ]
    if not tasks:
        return []

    cycles = list(metrics.get("cycles") or [])
    horizon = max(
        [_fw(c.get("current_time")) for c in cycles]
        + [_fw(t.get("completed_at")) for t in tasks]
        + [_fw(t.get("latest")) for t in tasks]
        + [0.0]
    )

    tasks_sorted = sorted(tasks, key=lambda t: _fw(t.get("latest")))
    n = len(tasks_sorted)

    fig = plt.figure(figsize=(11.5, 8.6), layout="constrained")
    gs = fig.add_gridspec(2, 2, width_ratios=(1.28, 1.0))
    ax_gantt = fig.add_subplot(gs[:, 0])
    ax_slack = fig.add_subplot(gs[0, 1])
    ax_press = fig.add_subplot(gs[1, 1])

    # ── (a) 时间窗甘特 ──────────────────────────────────────────
    y = np.arange(n)
    for i, t in enumerate(tasks_sorted):
        e = _fw(t.get("earliest"))
        l = _fw(t.get("latest"))
        done = str(t.get("status")) == "done" and t.get("completed_at") is not None
        ca = _fw(t.get("completed_at"), np.nan) if done else np.nan

        ax_gantt.barh(
            i, max(l - e, 1e-6), left=e, height=0.62,
            color=_C_WINDOW, alpha=0.22, edgecolor=_C_WINDOW, linewidth=0.6,
            zorder=2,
        )
        if l <= horizon + 1e-9:
            ax_gantt.plot([l, l], [i - 0.31, i + 0.31], color=_C_WINDOW,
                          linewidth=1.2, zorder=3)
        if done and np.isfinite(ca):
            ok = ca <= l + 1e-9
            ax_gantt.scatter(
                [ca], [i], s=46, marker="o", zorder=5,
                color=_C_DONE if ok else _C_PENDING,
                edgecolor="white", linewidth=0.8,
            )
            ax_gantt.plot([ca, ca], [i, i], color="none")
            ax_gantt.annotate(
                "", xy=(ca, i), xytext=(min(ca, l), i),
                arrowprops={"arrowstyle": "-", "color": "0.6", "linewidth": 0.8},
            )
        else:
            ax_gantt.scatter(
                [horizon], [i], s=34, marker=">", zorder=5,
                color=_C_PENDING, edgecolor="white", linewidth=0.7,
            )

    ax_gantt.set_yticks(y, [f"#{t.get('id')}" for t in tasks_sorted])
    ax_gantt.invert_yaxis()
    ax_gantt.set_xlim(0, horizon * 1.04)
    for t_line in [_fw(c.get("current_time")) for c in cycles]:
        ax_gantt.axvline(t_line, color="0.65", linewidth=0.7, linestyle="--", zorder=1)

    handles = [
        plt.Line2D([], [], color=_C_WINDOW, alpha=0.55, linewidth=6,
                   label="可行窗口 [earliest, latest]"),
        plt.Line2D([], [], marker="o", linestyle="none", color=_C_DONE,
                   markersize=7, label="窗口内完成"),
        plt.Line2D([], [], marker="o", linestyle="none", color=_C_PENDING,
                   markersize=7, label="超期完成"),
        plt.Line2D([], [], marker=">", linestyle="none", color=_C_PENDING,
                   markersize=7, label="期末仍未完成（箭头=实验结束）"),
        plt.Line2D([], [], color="0.65", linestyle="--", linewidth=0.9,
                   label="周期边界"),
    ]
    ax_gantt.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.95)
    style_axes(
        ax_gantt, title=f"(a) 任务时间窗与完成时刻（{n} 个任务，按 latest 升序）",
        xlabel="时刻 (s)", ylabel="任务 id",
    )

    # ── (b) 松紧度：完成时刻相对截止 ────────────────────────────
    done_tasks = [
        t for t in tasks
        if str(t.get("status")) == "done" and t.get("completed_at") is not None
    ]
    if done_tasks:
        slacks, widths, cols = [], [], []
        for t in done_tasks:
            l = _fw(t.get("latest"))
            e = _fw(t.get("earliest"))
            ca = _fw(t.get("completed_at"))
            widths.append(max(l - e, 1e-6))
            slacks.append(l - ca)
            cols.append(_C_DONE if ca <= l + 1e-9 else _C_PENDING)
        order = np.argsort(slacks)
        ys = np.arange(len(order))
        ax_slack.barh(
            ys, [slacks[i] for i in order], 0.62,
            color=[cols[i] for i in order], edgecolor="white", linewidth=0.6,
        )
        ax_slack.set_yticks(ys, [f"#{done_tasks[i].get('id')}" for i in order])
        ax_slack.axvline(0, color="0.35", linewidth=1.0)
        ax_slack.invert_yaxis()
        pos = [s for s in slacks if s >= 0]
        if pos and min(slacks) > 0:
            ax_slack.text(
                0.03, 0.5,
                f"全部 {len(done_tasks)} 个完成任务均在窗口内\n最小余量 {min(slacks):.0f}s",
                transform=ax_slack.transAxes, ha="left", va="center",
                fontsize=8.5, color="#3b6d11",
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85, "pad": 2},
            )
    else:
        ax_slack.text(0.5, 0.5, "无已完成任务", ha="center", va="center",
                      transform=ax_slack.transAxes, fontsize=10, color="0.45")
    style_axes(
        ax_slack, title="(b) 窗口余量（latest − completed_at，秒）",
        xlabel="余量 (s，负=超期)",
    )

    # ── (c) 逐周期窗口压力 ─────────────────────────────────────
    if cycles:
        t_arr = [_fw(t.get("arrival_time")) for t in tasks]
        t_lat = [_fw(t.get("latest")) for t in tasks]
        x = np.arange(len(cycles))
        bounds = [0.0] + [_fw(c.get("current_time")) for c in cycles]
        arrived, due = [], []
        for k in range(len(cycles)):
            lo, hi = bounds[k], bounds[k + 1]
            arrived.append(sum(1 for a in t_arr if lo - 1e-9 < a <= hi + 1e-9))
            due.append(sum(1 for l in t_lat if lo - 1e-9 < l <= hi + 1e-9))
        w = 0.36
        ax_press.bar(x - w / 2, arrived, w, label="本周期新到",
                     color=PALETTE["new"], edgecolor="white", linewidth=0.6)
        ax_press.bar(x + w / 2, due, w, label="本周期内到期",
                     color=PALETTE["release"], edgecolor="white", linewidth=0.6)
        for xi, a, d in zip(x, arrived, due):
            if a > 0:
                ax_press.text(xi - w / 2, a + 0.15, f"{a}", ha="center", fontsize=8, color="0.25")
            if d > 0:
                ax_press.text(xi + w / 2, d + 0.15, f"{d}", ha="center", fontsize=8, color="0.25")
        ax_press.set_xticks(x, [f"周期 {c.get('cycle')}" for c in cycles])
        ax_press.set_ylim(0, max(arrived + due) * 1.18 + 0.5)
        ax_press.legend(loc="upper right", framealpha=0.9)
    style_axes(ax_press, title="(c) 逐周期时间窗压力", ylabel="任务数")

    n_over = sum(
        1 for t in tasks
        if str(t.get("status")) == "done"
        and t.get("completed_at") is not None
        and _fw(t.get("completed_at")) > _fw(t.get("latest")) + 1e-9
    )
    n_stuck = sum(1 for t in tasks if str(t.get("status")) != "done")
    fig.suptitle(
        f"时间窗满足度：{len(done_tasks)} 已完成（超期 {n_over}） · {n_stuck} 期末未完成 · "
        f"earliest 恒为 0（窗口自实验起算）",
        fontsize=12,
    )

    return save_figure(fig, Path(out_dir) / "time_window", formats, dpi=dpi)
