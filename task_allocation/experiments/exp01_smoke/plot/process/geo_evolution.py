# -*- coding: utf-8 -*-
"""图 ``geo_evolution``——逐周期空间演化：航次与分配的全过程回放

【process 层 · 运行过程观测】

process 层回答「滚动闭环到底怎么跑的」：每个滚动周期**一行**
（共享坐标），DEM 底图上叠加本周期航次，逐行看下去就是一次完整回放。

    背景任务   本周期未执行者灰显作底
    航次       有向箭头：UAV 起点 ▲ → 任务 1 → … → 终点机巢，按承运 UAV 着色
    机巢       房屋图标；标题行给出 任务→选中→执行 的逐级收敛与里程

    看其在哪 — geo_overview(scene) · 看怎么变 — 本图
    看停滞  — geo_task_lifecycle(process)

数据来源：``results/solution.json 的 dem / nests / tasks / cycles[].tours，叠加 metrics.json 的 selection / muas 计数``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from matplotlib.lines import Line2D

from ..base.dem import (
    _dem_view_extent,
    _draw_dem,
)
from ..base.geo import (
    _G_DOT,
    _G_DRONE,
    _G_HOUSE,
    _HALO,
    _IconHandler,
    _IconPatch,
    _arrow,
    _draw_nests,
    _label_offset,
    _panel_title,
    _row_figsize,
    _set_scene_lim,
    _strip_axes,
    _task_size,
    uav_color,
)
from ..theme import PALETTE, plt, save_figure

__all__ = ["plot_geo_evolution"]


# ---------------------------------------------------------------------------
# 本图专用绘制件（其它图不用，故不放 base/geo.py）
# ---------------------------------------------------------------------------

def _draw_cycle_tours(
    ax,
    cyc: dict[str, Any],
    task_by_id: dict[int, dict[str, Any]],
    nest_by_id: dict[int, dict[str, Any]],
) -> set[int]:
    """画单周期航次：起点▲ → 任务 → 终点机巢，执行的任务按承运 UAV 着色。

    返回本周期用到的 UAV id 集合（供图例）。
    """
    owner: dict[int, int] = {}
    uavs_used: set[int] = set()
    for tour in cyc.get("tours") or []:
        u = int(tour.get("uav_id", -1))
        uavs_used.add(u)
        for tid in tour.get("task_ids") or []:
            owner[int(tid)] = u

    # 背景任务（本周期未执行）
    others = [t for t in task_by_id.values() if int(t["id"]) not in owner]
    if others:
        ax.scatter(
            [float(t["x"]) for t in others],
            [float(t["y"]) for t in others],
            s=[_task_size(t["reward"]) * 0.6 for t in others],
            facecolors="none", edgecolors="#c3cbd4", linewidths=1.1, zorder=2,
        )
        for t in others:
            ax.annotate(
                f"T{t['id']}", (float(t["x"]), float(t["y"])),
                textcoords="offset points", xytext=_label_offset(t["id"]),
                ha="center", fontsize=6.4, color="#7b8794", zorder=2, bbox=_HALO,
            )

    # 航次
    for tour in cyc.get("tours") or []:
        u = int(tour.get("uav_id", -1))
        color = uav_color(u)
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
        for p0, p1 in zip(pts, pts[1:]):
            _arrow(ax, p0, p1, color)
        ax.scatter(
            [pts[0][0]], [pts[0][1]], marker=_G_DRONE, s=135, color=color,
            edgecolors="white", linewidths=1.3, zorder=5,
        )
        ax.annotate(
            f"UAV {u}", pts[0], textcoords="offset points", xytext=(9, 9),
            ha="left", fontsize=7.2, color="#1f2933", fontweight="bold",
            zorder=8, bbox=_HALO,
        )
        if end is not None:
            ax.scatter(
                [float(end["x"])], [float(end["y"])], s=210,
                facecolors="none", edgecolors=color, linewidths=1.9, zorder=4,
            )

    # 本周期执行的任务（按承运 UAV 着色）
    for tid, u in owner.items():
        t = task_by_id.get(tid)
        if t is None:
            continue
        ax.scatter(
            [float(t["x"])], [float(t["y"])],
            s=_task_size(t["reward"]), color=uav_color(u),
            edgecolors="white", linewidths=1.0, zorder=6,
        )
        ax.annotate(
            f"T{tid}", (float(t["x"]), float(t["y"])),
            textcoords="offset points", xytext=_label_offset(tid),
            ha="center", fontsize=7.4, color="#1f2933",
            fontweight="bold", zorder=8, bbox=_HALO,
        )
    return uavs_used



def plot_geo_evolution(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """逐周期演化：每个滚动周期一行，DEM 底图上叠加本周期航次与变化。"""
    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    cycles = list(solution.get("cycles") or [])
    dem = solution.get("dem")
    if not nests or not cycles:
        return []

    task_by_id = {int(t["id"]): t for t in tasks}
    nest_by_id = {int(n["id"]): n for n in nests}
    metric_by_cycle = {
        int(c.get("cycle", -1)): c for c in (metrics.get("cycles") or [])
    }
    n_cycles = len(cycles)

    # 底图宽高比自适应画幅：地图填满每行，避免两侧大片留白
    extent = _dem_view_extent(dem, solution)
    row_h = 5.0
    fig, axes = plt.subplots(
        n_cycles, 1, figsize=_row_figsize(extent, row_h, n_cycles),
        squeeze=False, sharex=True, sharey=True, layout="constrained",
    )
    axes = axes.ravel()
    sm0 = None
    all_uavs_used: set[int] = set()

    for idx, (ax, cyc) in enumerate(zip(axes, cycles)):
        sm = _draw_dem(ax, dem, solution)
        if idx == 0:
            sm0 = sm
        uavs_used = _draw_cycle_tours(ax, cyc, task_by_id, nest_by_id)
        all_uavs_used |= uavs_used
        _draw_nests(ax, nests)

        sel = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("selection", {})
        muas = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("muas", {})
        # 标题画进地图左上角，不再外置长标题
        _panel_title(
            ax,
            f"周期 {cyc.get('cycle')} @ {float(cyc.get('current_time', 0.0)):.0f}s"
            f"｜任务 {sel.get('n_tasks_in', 0)}→{sel.get('n_selected', 0)}→{len(uavs_used)}"
            f"｜里程 {float(muas.get('f2_cost', 0.0)) / 1000.0:.0f} km"
            f"｜航次 {len(cyc.get('tours') or [])}",
        )
        _strip_axes(ax)

    # 共享坐标范围
    _set_scene_lim(axes[0], nests, tasks, [], dem, extent=_dem_view_extent(dem, solution))
    for ax in axes:
        ax.set_aspect("equal", adjustable="box")

    # 图例合并到全图底部一行：所有周期用到的 UAV + 通用符号，
    # 不再每行挂一个图例框压在地图上。
    handles = [
        _IconPatch(_G_DRONE, fc=uav_color(u), ec="white", lw=1.1, label=f"UAV {u}")
        for u in sorted(all_uavs_used)
    ]
    handles += [
        _IconPatch(_G_DRONE, fc="#4b5563", ec="white", lw=1.1, label="UAV 起点"),
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.2, label="机巢"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="none",
               markeredgecolor="#2d3748", markersize=10, label="终点机巢"),
        _IconPatch(_G_DOT, fc="none", ec="#c3cbd4", lw=1.2, label="未执行任务"),
    ]
    fig.legend(
        handles=handles, loc="outside lower center",
        handler_map={_IconPatch: _IconHandler()},
        ncol=min(8, len(handles)), fontsize=8.5, columnspacing=1.2,
        frameon=False,
    )

    if sm0 is not None:
        fig.colorbar(
            sm0, ax=axes.tolist(), location="right",
            shrink=0.55, pad=0.015,
        ).set_label("高程 (m)", fontsize=9)

    fig.suptitle("逐周期空间演化 · DEM 晕渲底图", fontsize=13, fontweight="bold", color="#1f2933")
    stem = Path(out_dir) / "geo_evolution"
    return save_figure(fig, stem, formats, dpi=dpi)
