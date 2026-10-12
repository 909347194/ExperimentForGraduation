# -*- coding: utf-8 -*-
"""图 ``assignment_map``——机巢—任务—航次空间分配图。

【process 层 · 运行过程观测】

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

from ..base.geo import (
    _G_DRONE,
    _HALO,
    _arrow,
    _label_offset,
    _task_size,
    uav_color,
)
from ..theme import MONO_ZH, PALETTE, plt, save_figure

__all__ = ["plot_assignment_map"]


def _cycle_summary(ax, cyc: dict[str, Any], mc: dict[str, Any],
                   owner: dict[int, int], task_by_id: dict[int, Any],
                   nest_by_id: dict[int, Any], uavs: list[int]) -> None:
    """在右侧面板写本周期的数值摘要：漏斗 / 机队 / 目标 / 求解 / 航次明细。"""
    muas = mc.get("muas") or {}
    sel = mc.get("selection") or {}
    n_avail = int(muas.get("n_uav_avail") or 0)
    n_coded = int(muas.get("n_participating") or 0)
    feasible = bool(muas.get("feasible", True))
    solve_s = float(muas.get("solve_seconds") or 0.0)
    evals = int(muas.get("n_evals") or 0)
    deferred = [int(i) for i in (mc.get("deferred_ids") or [])]

    lines = [
        f"周期 {cyc.get('cycle')}   @ t={float(cyc.get('current_time', 0.0)):.0f}s",
        "─" * 34,
        f"漏斗   T_t={sel.get('n_tasks_in', 0)}"
        f" → T_sel={sel.get('n_selected', 0)}"
        f" → T_exec={len(owner)}",
        f"机队   可用 {n_avail} · 编码参与 {n_coded} · 实际出动 {len(uavs)}",
        f"目标   F1={float(muas.get('f1_reward') or 0.0):.1f}"
        f"   F2={float(muas.get('f2_cost') or 0.0) / 1000.0:.1f} km",
        f"求解   {solve_s:.2f} s · {evals} evals · "
        f"{'可行' if feasible else '不可行'}",
        "─" * 34,
        "航次明细（UAV → 任务串 → 起→降巢）：",
    ]
    for tour in cyc.get("tours") or []:
        uid = int(tour.get("uav_id", -1))
        chain = " → ".join(f"T{int(t)}" for t in (tour.get("task_ids") or []))
        s_nest = tour.get("start_nest_id")
        e_nest = tour.get("end_nest_id")
        if not chain:
            continue
        lines.append(
            f"  UAV {uid:<3d} {chain}   巢{s_nest}→巢{e_nest}")
    if not any(cyc.get("tours") or []):
        lines.append("  （本周无航次）")
    lines.append("─" * 34)
    if deferred:
        shown = " ".join(f"T{i}" for i in deferred[:12])
        more = f" …(+{len(deferred) - 12})" if len(deferred) > 12 else ""
        lines.append(f"滞留 {len(deferred)}：{shown}{more}")
    else:
        lines.append("滞留 0：池内任务全部处理完毕")

    ax.text(0.0, 1.0, "\n".join(lines), transform=ax.transAxes,
            ha="left", va="top", fontsize=8.2, family=MONO_ZH, color="0.2",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#f4f6f8",
                      edgecolor="0.8", linewidth=0.8))


def plot_assignment_map(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
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

    fig = plt.figure(figsize=(11.5, 4.6 * n_cycles), layout="constrained")
    gs = fig.add_gridspec(n_cycles, 2, width_ratios=[1.85, 1.0])
    axes = [fig.add_subplot(gs[i, 0]) for i in range(n_cycles)]
    panels = [fig.add_subplot(gs[i, 1]) for i in range(n_cycles)]

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
            handles=handles, loc="upper left", fontsize=7.4,
            title=f"周期 {cyc.get('cycle')} 航次", title_fontsize=7.8,
            framealpha=0.94,
        )

        mc = metric_by_cycle.get(int(cyc.get("cycle", -1)), {})
        muas = mc.get("muas") or {}
        sel = mc.get("selection") or {}
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
        if idx == n_cycles - 1:
            ax.set_xlabel("东向 x (m)")
        if idx == 0:
            ax.set_ylabel("北向 y (m)")
        ax.tick_params(labelsize=7.5)

        # ── 右侧：本周期摘要面板（数值不看图也能读懂这一周期）──────
        panel = panels[idx]
        panel.axis("off")
        _cycle_summary(panel, cyc, mc, owner, task_by_id, nest_by_id,
                       sorted(cycle_uavs[idx]))

    # ── 全局图例（跨周期共享的语义元素） ──────────────────────────
    common = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#c3cbd4",
               markeredgecolor="#c3cbd4", markersize=8, label="任务（本周期未执行）"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#7b8794",
               markeredgecolor="white", markersize=9,
               label="任务（本周期执行，按承运 UAV 着色）"),
    ]
    fig.legend(
        handles=common, loc="outside lower center",
        ncol=2, fontsize=8,
    )

    fig.suptitle("机巢—任务—航次空间分配图：谁做什么、在哪发生", fontsize=12.5)

    stem = Path(out_dir) / "assignment_map"
    return save_figure(fig, stem, formats, dpi=dpi)
