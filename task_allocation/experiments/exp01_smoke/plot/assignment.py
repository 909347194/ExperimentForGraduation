# -*- coding: utf-8 -*-
"""exp01_smoke 图 4：机巢—任务—航次空间分配图 ``assignment_map``。

每个滚动周期**一行**（全场景约 90 km × 90 km，横排会被压扁看不清），
共享坐标，把分配结果落在地理底图上：

    机巢     实心方块，面积 ∝ 容量，标注「巢 {id}」
    任务     圆点，面积 ∝ 收益；**按承运 UAV 着色**，一眼看出「哪几件是同一架机做的」；
             本周期未执行的画成空心灰点作背景，均标注「T{任务 id}」
    航次     有向箭头：UAV 起点 ▲ → 任务 1 → 任务 2 → … → 终点机巢，
             箭头方向即执行顺序，颜色与承运 UAV 一致
    终点巢   终点机巢外套一圈同色圆环，异巢终止一眼可见

分工：分配结构（谁做什么、什么顺序、降哪个巢）看 ``allocation_gantt``；
本图回答「这件事发生在哪」。

数据来源：``results/solution.json`` 的 ``nests`` / ``tasks`` / ``cycles[].tours``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from matplotlib.lines import Line2D

from .theme import PALETTE, plt, save_figure

__all__ = ["plot_assignment_map"]

#: 与 allocation_gantt 保持一致：按 UAV 稳定着色（tab20 前 20 色循环）
UAV_PALETTE = (
    "#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2",
    "#b279a2", "#ff9da6", "#9d755d", "#8c6bb1", "#2ca02c",
    "#d62728", "#17becf", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#393b79", "#637939", "#843c39", "#5254a3",
)


def uav_color(uav_id: int):
    return UAV_PALETTE[int(uav_id) % len(UAV_PALETTE)]


def _task_size(reward: float) -> float:
    return 22.0 + 11.0 * float(reward)


#: 标签避让：按 id 轮转偏移，避免同一机巢簇内标签堆在一起
_LABEL_OFFSETS = (
    (0, -13), (12, 7), (-12, 7), (11, -9), (-11, -9),
    (0, 14), (15, 0), (-15, 0), (10, 12), (-10, -13),
)


def _label_offset(key: int):
    return _LABEL_OFFSETS[int(key) % len(_LABEL_OFFSETS)]


#: 标签统一加白色衬底，压在航线/圆点上也能读
_HALO = dict(boxstyle="round,pad=0.16", fc="white", ec="none", alpha=0.72)


def _arrow(ax, p_from, p_to, color: str, *, lw: float = 2.1) -> None:
    """画一段有向航段（箭头不压住端点标记）。"""
    ax.annotate(
        "", xy=p_to, xytext=p_from,
        arrowprops=dict(
            arrowstyle="-|>", color=color, linewidth=lw,
            shrinkA=2.0, shrinkB=8.0, mutation_scale=26, alpha=0.95,
        ),
        zorder=3,
    )


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
    metric_by_cycle = {int(c.get("cycle", -1)): c for c in (metrics.get("cycles") or [])}
    n_cycles = len(cycles)

    # 全局坐标范围（含机巢、任务、航次起点），保证各行可比
    xs = [float(n["x"]) for n in nests] + [float(t["x"]) for t in tasks]
    ys = [float(n["y"]) for n in nests] + [float(t["y"]) for t in tasks]
    for c in cycles:
        for tour in c.get("tours") or []:
            start = tour.get("start") or [0.0, 0.0]
            xs.append(float(start[0]))
            ys.append(float(start[1]))
    pad_x = (max(xs) - min(xs)) * 0.07 or 1000.0
    pad_y = (max(ys) - min(ys)) * 0.07 or 1000.0
    xlim = (min(xs) - pad_x, max(xs) + pad_x)
    ylim = (min(ys) - pad_y, max(ys) + pad_y)

    fig, axes = plt.subplots(
        n_cycles, 1, figsize=(11.0, 4.4 * n_cycles),
        squeeze=False, sharex=True, sharey=True,
    )
    axes = axes.ravel()

    cycle_uavs: list[set[int]] = []

    for idx, (ax, cyc) in enumerate(zip(axes, cycles)):
        # 本周期被执行的任务 → 承运 UAV（用来给任务着色，体现分组）
        owner: dict[int, int] = {}
        this_uavs: set[int] = set()
        cycle_uavs.append(this_uavs)
        for tour in cyc.get("tours") or []:
            uav_id = int(tour.get("uav_id", -1))
            this_uavs.add(uav_id)
            for tid in tour.get("task_ids") or []:
                owner[int(tid)] = uav_id

        # ── 背景任务（本周期未执行） ──────────────────────────
        other = [t for t in tasks if int(t["id"]) not in owner]
        if other:
            ax.scatter(
                [float(t["x"]) for t in other],
                [float(t["y"]) for t in other],
                s=[_task_size(t["reward"]) * 0.6 for t in other],
                facecolors="none", edgecolors="#c3cbd4", linewidths=1.1,
                zorder=2,
            )
            for t in other:
                ax.annotate(
                    f"T{t['id']}", (float(t["x"]), float(t["y"])),
                    textcoords="offset points", xytext=_label_offset(t["id"]),
                    ha="center", fontsize=6.6, color="#7b8794", zorder=2,
                    bbox=_HALO,
                )

        # ── 航次：起点 ▲ → 任务 1 → … → 终点机巢 ─────────────
        for tour in cyc.get("tours") or []:
            uav_id = int(tour.get("uav_id", -1))
            color = uav_color(uav_id)
            start = tour.get("start") or [0.0, 0.0, 0.0]

            pts: list[tuple[float, float]] = [(float(start[0]), float(start[1]))]
            for tid in tour.get("task_ids") or []:
                t = task_by_id.get(int(tid))
                if t is not None:
                    pts.append((float(t["x"]), float(t["y"])))
            end = (
                nest_by_id.get(int(tour["end_nest_id"]))
                if tour.get("end_nest_id") is not None
                else None
            )
            if end is not None:
                pts.append((float(end["x"]), float(end["y"])))

            for p_from, p_to in zip(pts, pts[1:]):
                _arrow(ax, p_from, p_to, color)

            # UAV 起点：三角 + 编号（深色字 + 白底，避免浅色 UAV 色看不清）
            ax.scatter(
                [pts[0][0]], [pts[0][1]], marker="^", s=76,
                color=color, edgecolors="white", linewidths=1.1, zorder=5,
            )
            ax.annotate(
                f"UAV {uav_id}", pts[0], textcoords="offset points", xytext=(9, 9),
                ha="left", fontsize=7.4, color="#1f2933", fontweight="bold",
                zorder=8, bbox=_HALO,
            )
            # 终点机巢：同色圆环，标出异巢终止
            if end is not None:
                ax.scatter(
                    [float(end["x"])], [float(end["y"])], s=210,
                    facecolors="none", edgecolors=color, linewidths=1.9, zorder=4,
                )

        # ── 本周期执行的任务（按承运 UAV 着色） ───────────────
        for tid, uav_id in owner.items():
            t = task_by_id.get(tid)
            if t is None:
                continue
            ax.scatter(
                [float(t["x"])], [float(t["y"])],
                s=_task_size(t["reward"]), color=uav_color(uav_id),
                edgecolors="white", linewidths=1.0, zorder=6,
            )
            ax.annotate(
                f"T{tid}", (float(t["x"]), float(t["y"])),
                textcoords="offset points", xytext=_label_offset(tid),
                ha="center", fontsize=7.6, color="#1f2933",
                fontweight="bold", zorder=8, bbox=_HALO,
            )

        # ── 机巢 ────────────────────────────────────────────
        ax.scatter(
            [float(n["x"]) for n in nests],
            [float(n["y"]) for n in nests],
            s=[30.0 + 18.0 * float(n.get("capacity", 1)) for n in nests],
            marker="s", color=PALETTE["nest"], edgecolors="white",
            linewidths=1.1, zorder=7,
        )
        for n in nests:
            ax.annotate(
                f"巢 {n['id']}", (float(n["x"]), float(n["y"])),
                textcoords="offset points", xytext=(0, 11),
                ha="center", fontsize=8, color="#0b2545",
                fontweight="bold", zorder=8, bbox=_HALO,
            )

        # ── 本周期图例（只列本周期用到的 UAV，避免跨周期堆成大图例）──
        handles = [
            Line2D([0], [0], color=uav_color(u), lw=2.4, label=f"UAV {u}")
            for u in sorted(cycle_uavs[idx])
        ]
        handles += [
            Line2D([0], [0], marker="^", color="none", markerfacecolor="#7b8794",
                   markersize=7, label="UAV 起点"),
            Line2D([0], [0], marker="s", color="none", markerfacecolor=PALETTE["nest"],
                   markersize=8, label="机巢"),
            Line2D([0], [0], marker="o", color="none", markerfacecolor="none",
                   markeredgecolor="#2d3748", markersize=10, label="终点机巢"),
        ]
        ax.legend(
            handles=handles, loc="upper left", bbox_to_anchor=(1.008, 1.0),
            fontsize=7.4, title=f"周期 {cyc.get('cycle')} 航次", title_fontsize=7.8,
        )

        muas = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("muas", {})
        sel = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("selection", {})
        ax.set_title(
            f"周期 {cyc.get('cycle')}   @ t={float(cyc.get('current_time', 0.0)):.0f}s"
            f"      T_t={sel.get('n_tasks_in', 0)}"
            f" → T_sel={sel.get('n_selected', 0)}"
            f" → T_exec={len(owner)}"
            f"      F2={float(muas.get('f2_cost', 0.0)) / 1000.0:.1f} km"
            f"      航次 {len(cyc.get('tours') or [])}",
            fontsize=10.5, loc="left",
        )
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("东向 x (m)")
        ax.set_ylabel("北向 y (m)")

    # ── 全局图例（跨周期共享的语义元素） ──────────────────────────
    common = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#c3cbd4",
               markeredgecolor="#c3cbd4", markersize=8, label="任务（本周期未执行）"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#7b8794",
               markeredgecolor="white", markersize=9,
               label="任务（本周期执行，按承运 UAV 着色）"),
    ]
    fig.legend(
        handles=common, loc="lower center", bbox_to_anchor=(0.5, -0.005),
        ncol=2, fontsize=8,
    )

    fig.suptitle("exp01_smoke：机巢—任务—航次空间分配图", fontsize=13)
    fig.tight_layout(rect=(0, 0, 0.90, 0.96))

    stem = Path(out_dir) / "assignment_map"
    return save_figure(fig, stem, formats, dpi=dpi)
