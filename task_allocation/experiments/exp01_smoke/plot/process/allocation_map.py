# -*- coding: utf-8 -*-
"""图 ``allocation_map``——逐周期空间演化（从 ``allocation`` 拆出，独立成图）。

回答「分配发生在哪里」：每个周期一张 DEM 航次回放，横排 1×N。拆出后独占整张
画布，每个周期图更大、地形与航迹更清楚，适合论文正文单独引用。

数据来源：``solution.json`` 的 ``cycles[].tours`` + ``tasks`` / ``nests`` / ``dem``；
周期标题里程 = 该周期各航次 ``est_cost`` 之和（纯 DEM 航程，不含约束惩罚——
metrics 的 ``f2_cost`` 另含 ``w_penalty×惩罚``，不用作图面里程）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..base.dem import _dem_view_extent, _draw_dem
from ..base.geo import (
    _G_DOT,
    _G_DRONE,
    _G_HOUSE,
    _HALO,
    _IconHandler,
    _IconPatch,
    _draw_nests,
    _panel_title,
    _set_scene_lim,
    _strip_axes,
    uav_color,
)
from ..theme import FS, PALETTE, footnote, plt, save_figure

from .allocation import _draw_cycle_tours, _arrival_cycle_map

__all__ = ["plot_allocation_map"]


def plot_allocation_map(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    cycles = list(solution.get("cycles") or [])
    if not nests or not cycles:
        return []

    task_by_id = {int(t["id"]): t for t in tasks}
    nest_by_id = {int(n["id"]): n for n in nests}
    arrival_cycle = _arrival_cycle_map(solution)

    n_cyc = len(cycles)
    fig = plt.figure(figsize=(4.4 * n_cyc, 4.9))
    gs = fig.add_gridspec(
        1, n_cyc, wspace=0.05, left=0.03, right=0.965, top=0.90, bottom=0.135,
    )
    sm0 = None
    all_uavs_used: set[int] = set()
    evo_axes = []
    extent = _dem_view_extent(solution.get("dem"), solution)
    for idx, cyc in enumerate(cycles):
        ax = fig.add_subplot(gs[idx])
        evo_axes.append(ax)
        smx = _draw_dem(ax, solution.get("dem"), solution)
        if idx == 0:
            sm0 = smx
        uavs_used = _draw_cycle_tours(ax, cyc, task_by_id, nest_by_id,
                                       arrival_cycle=arrival_cycle)
        all_uavs_used |= uavs_used
        _draw_nests(ax, nests)
        # 周期里程 = 该周期各航次 est_cost 之和（纯 DEM 航程）。不用 metrics 的
        # f2_cost——它另含 w_penalty×惩罚（量纲不同），与甘特条/anim 里程口径
        # 不一致（2026-10-13 审查统一）。
        tours_c = [t for t in (cyc.get("tours") or []) if t.get("task_ids")]
        km = sum(float(t.get("est_cost") or 0.0) for t in tours_c) / 1000.0
        _panel_title(ax, f"周期 {cyc.get('cycle')} · {km:.0f} km")
        _strip_axes(ax)

    _set_scene_lim(evo_axes[0], nests, tasks, [], solution.get("dem"), extent=extent)
    for ax in evo_axes:
        ax.set_aspect("equal", adjustable="box")
    evo_axes[0].set_title("逐周期空间演化（DEM + 航次回放）",
                          fontsize=FS["title"], loc="left")

    if sm0 is not None:
        fig.colorbar(sm0, ax=evo_axes, location="right", shrink=0.78,
                     pad=0.02).set_label("高程 (m)", fontsize=FS["tick"])

    handles = [
        _IconPatch(_G_DRONE, fc=uav_color(u), ec="white", lw=1.0,
                   label=f"UAV {u}") for u in sorted(all_uavs_used)
    ]
    handles += [
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.0,
                   label="机巢（○=返航巢）"),
        _IconPatch(_G_DOT, fc="none", ec="#aab4bf", lw=1.1, label="未执行任务"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.028),
               handler_map={_IconPatch: _IconHandler()},
               ncol=min(7, len(handles)), fontsize=FS["legend"],
               columnspacing=1.1, frameon=False)

    fig.suptitle("exp01_smoke：逐周期空间分配演化", fontsize=FS["suptitle"])
    footnote(fig, "口径：起点=起飞巢；连线=航次顺序；终点空心圈=返航巢；"
                  "任务点按承运 UAV 着色，标号 T#·Ck（Ck=到达周期）；"
                  "未执行任务仅画空心点。周期标题里程 = 该周期各航次 est_cost "
                  "之和（纯 DEM 航程，不含约束惩罚；metrics 的 f2_cost 另含"
                  "惩罚项，不作为图面里程）。")
    return save_figure(fig, Path(out_dir) / "P05_allocation_map", formats, dpi=dpi)
