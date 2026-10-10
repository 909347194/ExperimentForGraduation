# -*- coding: utf-8 -*-
"""exp01_smoke 图 3：机巢—任务—航次分配地图 ``assignment_map``。

每个滚动周期一张子图（共享坐标），叠加三层信息：

    机巢   方块，面积 ∝ 容量，标注 id
    任务   圆点，面积 ∝ 收益；本周期执行的用彩色高亮，其余灰点作背景
    航次   UAV 起点 → 任务序列 → 终点机巢 的折线，颜色按 UAV 区分

用于直观验证 MUAS 输出的空间合理性（就近成组、终点机巢成形）。
数据来源：``results/solution.json`` 的 ``nests`` / ``tasks`` / ``cycles[].tours``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .theme import PALETTE, cycle_colors, plt, save_figure

__all__ = ["plot_assignment_map"]


def _uav_color(uav_id: int):
    return plt.cm.tab20(int(uav_id) % 20)


def _task_scatter_size(reward: float) -> float:
    return 26.0 + 14.0 * float(reward)


def plot_assignment_map(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png", "pdf"),
    *,
    dpi: int = 200,
) -> list[Path]:
    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    cycles = list(solution.get("cycles") or [])
    if not nests or not cycles:
        return []

    task_by_id = {int(t["id"]): t for t in tasks}
    nest_by_id = {int(n["id"]): n for n in nests}
    n_cycles = len(cycles)
    # 按周期号索引 metrics 的诊断（子图标题里的 T_exec / F2）
    metric_by_cycle = {
        int(c.get("cycle", -1)): c for c in (metrics.get("cycles") or [])
    }

    # 全局坐标范围（含机巢、任务、航次起点），保证各子图可比
    xs = [float(n["x"]) for n in nests] + [float(t["x"]) for t in tasks]
    ys = [float(n["y"]) for n in nests] + [float(t["y"]) for t in tasks]
    for c in cycles:
        for tour in c.get("tours") or []:
            start = tour.get("start") or [0.0, 0.0]
            xs.append(float(start[0]))
            ys.append(float(start[1]))
    pad_x = (max(xs) - min(xs)) * 0.10 or 1000.0
    pad_y = (max(ys) - min(ys)) * 0.10 or 1000.0
    xlim = (min(xs) - pad_x, max(xs) + pad_x)
    ylim = (min(ys) - pad_y, max(ys) + pad_y)

    fig, axes = plt.subplots(
        1, n_cycles, figsize=(4.6 * n_cycles, 5.0), squeeze=False, sharex=True, sharey=True
    )
    axes = axes.ravel()
    cycle_colors_n = cycle_colors(n_cycles)

    for idx, (ax, cyc) in enumerate(zip(axes, cycles)):
        color_c = cycle_colors_n[idx]

        # 本周期被执行的任务集合
        exec_ids = {
            int(tid)
            for tour in cyc.get("tours") or []
            for tid in (tour.get("task_ids") or [])
        }

        # ── 背景任务（本周期未执行） ──────────────────────────
        other = [t for t in tasks if int(t["id"]) not in exec_ids]
        if other:
            ax.scatter(
                [float(t["x"]) for t in other],
                [float(t["y"]) for t in other],
                s=[_task_scatter_size(t["reward"]) * 0.55 for t in other],
                c=PALETTE["grid"], edgecolors="none", zorder=2, label="任务（未执行）",
            )

        # ── 航次：起点 → 任务序列 → 终点机巢 ──────────────────
        for tour in cyc.get("tours") or []:
            uav_id = int(tour.get("uav_id", 0))
            color = _uav_color(uav_id)
            start = tour.get("start") or [0.0, 0.0, 0.0]

            pts_x = [float(start[0])]
            pts_y = [float(start[1])]
            for tid in tour.get("task_ids") or []:
                t = task_by_id.get(int(tid))
                if t is None:
                    continue
                pts_x.append(float(t["x"]))
                pts_y.append(float(t["y"]))
            end = nest_by_id.get(int(tour["end_nest_id"])) if tour.get("end_nest_id") is not None else None
            if end is not None:
                pts_x.append(float(end["x"]))
                pts_y.append(float(end["y"]))

            ax.plot(
                pts_x, pts_y, "-",
                color=color, linewidth=1.5, alpha=0.85,
                marker="o", markersize=3.2,
                label=f"UAV {uav_id}", zorder=3,
            )

        # ── 本周期执行的任务 ────────────────────────────────
        done = [t for t in tasks if int(t["id"]) in exec_ids]
        if done:
            ax.scatter(
                [float(t["x"]) for t in done],
                [float(t["y"]) for t in done],
                s=[_task_scatter_size(t["reward"]) for t in done],
                c=color_c, edgecolors="white", linewidths=0.8,
                zorder=4, label="本周期执行任务",
            )

        # ── 机巢 ────────────────────────────────────────────
        ax.scatter(
            [float(n["x"]) for n in nests],
            [float(n["y"]) for n in nests],
            s=[28.0 + 16.0 * float(n.get("capacity", 1)) for n in nests],
            marker="s", c=PALETTE["nest"], edgecolors="white",
            linewidths=1.0, zorder=5, label="机巢",
        )
        for n in nests:
            ax.annotate(
                str(n["id"]), (float(n["x"]), float(n["y"])),
                textcoords="offset points", xytext=(0, 7),
                ha="center", fontsize=7.5, color="#1f2933", zorder=6,
            )

        muas = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("muas", {})
        ax.set_title(
            f"周期 {cyc.get('cycle')} @ t={float(cyc.get('current_time', 0.0)):.0f}s\n"
            f"T_exec={len(exec_ids)}  F2={float(muas.get('f2_cost', 0.0)) / 1000.0:.1f} km"
        )
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("东向 x (m)")
        if idx == 0:
            ax.set_ylabel("北向 y (m)")

    # 图例只从第一张子图取，去重
    handles, labels = axes[0].get_legend_handles_labels()
    seen: dict[str, Any] = {}
    for h, l in zip(handles, labels):
        seen.setdefault(l, h)
    axes[0].legend(seen.values(), seen.keys(), loc="lower left", fontsize=7.5)

    fig.suptitle("exp01_smoke：机巢—任务—航次分配地图", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    stem = Path(out_dir) / "assignment_map"
    return save_figure(fig, stem, formats, dpi=dpi)
