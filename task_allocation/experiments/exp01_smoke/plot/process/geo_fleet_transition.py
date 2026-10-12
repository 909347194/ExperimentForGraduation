# -*- coding: utf-8 -*-
"""图 ``geo_fleet_transition``——机队迁移与周期末机巢占用

【process 层 · 运行过程观测】

process 层回答「谁飞了、从哪里出发、最后落在哪里、机巢是否拥挤」。
把**机队流转**与**容量约束**这两件通常分开讲的事放进同一张图：

    彩色四旋翼   本周期实际参与任务的 UAV（起→终，箭头按 UAV 着色）
    机巢外圆环   周期末占用率：< 75% 绿 / ≥ 75% 橙 / 满载 红
    巢旁数字     已占用泊位 / 容量

DEM 仅作背景（本图不占色条空间），高程色标见 scene/geo_overview。

数据来源：``results/solution.json 的 dem / nests / tasks / cycles[].tours，占用来自 metrics.json 的 cycles[].nest_occupancy``。
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
    _G_DRONE,
    _HALO,
    _IconHandler,
    _IconPatch,
    _arrow,
    _draw_nests,
    _metric_cycle,
    _panel_title,
    _row_figsize,
    _set_scene_lim,
    _strip_axes,
    uav_color,
)
from ..theme import plt, save_figure

__all__ = ["plot_geo_fleet_transition"]




def plot_geo_fleet_transition(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """展示活动 UAV 的起终点迁移以及每周期末机巢占用。

    圆环大小/颜色表示机巢占用率，彩色三角与路径表示实际参与本周期任务的 UAV。
    因而可把“谁飞了、从哪里出发、最后落在哪里、机巢是否拥挤”放在同一张图中。
    """
    nests = list(solution.get("nests") or [])
    tasks = {int(t["id"]): t for t in (solution.get("tasks") or [])}
    cycles = list(solution.get("cycles") or [])
    dem = solution.get("dem")
    if not nests or not cycles:
        return []
    nest_by_id = {int(n["id"]): n for n in nests}

    # 底图宽高比自适应画幅（本图无色条，预留更窄）；地图填满每行
    extent = _dem_view_extent(dem, solution)
    row_h = 5.0
    fig, axes = plt.subplots(
        len(cycles), 1,
        figsize=_row_figsize(extent, row_h, len(cycles), extra_w=1.1),
        squeeze=False, sharex=True, sharey=True, layout="constrained",
    )
    axes = axes.ravel()
    im0 = None
    for index, (ax, cycle) in enumerate(zip(axes, cycles)):
        im = _draw_dem(ax, dem, solution)
        if index == 0:
            im0 = im
        record = _metric_cycle(metrics, int(cycle.get("cycle", -1)))
        occupancy = {int(k): int(v) for k, v in (record.get("nest_occupancy") or {}).items()}

        # 机巢：底层方块固定，外圈编码周期末占用率。
        _draw_nests(ax, nests)
        for nest in nests:
            nid = int(nest["id"])
            used = occupancy.get(nid, 0)
            capacity = max(int(nest.get("capacity", 1)), 1)
            ratio = min(used / capacity, 1.0)
            ring_color = "#e45756" if ratio >= 1.0 else "#f58518" if ratio >= 0.75 else "#54a24b"
            ax.scatter(
                [float(nest["x"])], [float(nest["y"])],
                s=350 + 540 * ratio, facecolors="none", edgecolors=ring_color,
                linewidths=2.0, alpha=0.9, zorder=4,
            )
            ax.annotate(
                f"{used}/{capacity}", (float(nest["x"]), float(nest["y"])),
                textcoords="offset points", xytext=(0, -17), ha="center",
                fontsize=7.2, color=ring_color, fontweight="bold", zorder=9, bbox=_HALO,
            )

        active = list(cycle.get("tours") or [])
        for tour in active:
            uid = int(tour.get("uav_id", -1))
            color = uav_color(uid)
            start = tour.get("start") or [0.0, 0.0]
            start_xy = (float(start[0]), float(start[1]))
            path = [start_xy]
            for tid in tour.get("task_ids") or []:
                task = tasks.get(int(tid))
                if task is not None:
                    path.append((float(task["x"]), float(task["y"])))
            end = nest_by_id.get(int(tour["end_nest_id"])) if tour.get("end_nest_id") is not None else None
            if end is not None:
                path.append((float(end["x"]), float(end["y"])))
            for p0, p1 in zip(path, path[1:]):
                _arrow(ax, p0, p1, color, lw=2.5)
            ax.scatter([start_xy[0]], [start_xy[1]], marker=_G_DRONE, s=140, color=color, edgecolors="white", linewidths=1.3, zorder=7)
            ax.annotate(f"U{uid}", start_xy, textcoords="offset points", xytext=(8, 8), fontsize=7.3, fontweight="bold", zorder=9, bbox=_HALO)
            if end is not None:
                ax.scatter([float(end["x"])], [float(end["y"])], s=120, facecolors="none", edgecolors=color, linewidths=2.0, zorder=8)

        # 标题画进地图左上角（无人机已用四旋翼图标，不再写 ▲/▼ 符号说明）
        _panel_title(
            ax,
            f"周期 {cycle.get('cycle')}｜活动 UAV={len(active)}｜圆环 = 周期末机巢占用",
        )
        _strip_axes(ax)
        ax.set_aspect("equal", adjustable="box")

    _set_scene_lim(axes[0], nests, list(tasks.values()), [], dem, extent=_dem_view_extent(dem, solution))
    # 本图强调迁移与机巢容量，DEM 仅作背景；高程色标见 geo_overview / geo_evolution，
    # 此处不再挤占横向空间。
    handles = [
        _IconPatch(_G_DRONE, fc="#4c78a8", ec="white", lw=1.1, label="活动 UAV（起→终）"),
        Line2D([0], [0], marker="o", color="#54a24b", markerfacecolor="none", markersize=10, label="机巢占用率 < 75%"),
        Line2D([0], [0], marker="o", color="#f58518", markerfacecolor="none", markersize=10, label="机巢占用率 ≥ 75%"),
        Line2D([0], [0], marker="o", color="#e45756", markerfacecolor="none", markersize=10, label="机巢满载"),
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=4, fontsize=9,
               handler_map={_IconPatch: _IconHandler()}, frameon=False)
    fig.suptitle("无人机—机巢迁移与周期末容量状态", fontsize=13, fontweight="bold", color="#1f2933")
    return save_figure(fig, Path(out_dir) / "geo_fleet_transition", formats, dpi=dpi)
