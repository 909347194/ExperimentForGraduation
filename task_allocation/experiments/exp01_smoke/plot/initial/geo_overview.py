# -*- coding: utf-8 -*-
"""图 ``geo_overview``——初始态势：地形 · 机巢 · 初始机队 · 任务散布

【scene 层 · 环境初始配置】

scene 层回答「实验是怎么摆的」：**一次绘制、与算法无关**——
同一次实验无论怎么求解，这张图都一模一样，它是后面所有过程图的
坐标系与空间参照（看不懂过程图时，先回这张图认路）。

    DEM 高程底图   真实 ASTER 瓦片裁剪采样 + 山体阴影晕渲
    机巢           房屋图标，面积 ∝ 容量，标注「巢 {id}」及初始停驻机数
    任务           圆点，面积 ∝ 收益（本次实验的**全部**任务）
    初始无人机     本就停在各自机巢，故按巢计数直接标在巢上，不另撒点

数据来源：``results/solution.json 的 dem / nests / tasks / uavs``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..base.dem import (
    _dem_view_extent,
    _draw_dem,
)
from ..base.geo import (
    _G_DOT,
    _G_HOUSE,
    _IconHandler,
    _IconPatch,
    _draw_nests,
    _set_scene_lim,
    _strip_axes,
    _task_size,
)
from ..theme import PALETTE, plt, save_figure

__all__ = ["plot_geo_overview"]




def plot_geo_overview(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """初始态势：DEM 底图 + 机巢（标注初始无人机数）+ 全部任务。"""
    from collections import Counter

    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    uavs = list(solution.get("uavs") or [])
    dem = solution.get("dem")
    if not nests:
        return []

    # 初始无人机本就停在各自机巢：按 nest_id 计数，直接在巢上标数量
    count_by_nest = Counter(int(u.get("nest_id", -1)) for u in uavs)
    n_initial = len(uavs)

    fig, ax = plt.subplots(figsize=(10.5, 9.0), layout="constrained")
    sm = _draw_dem(ax, dem, solution)

    # 全部任务（灰显，作为背景）
    if tasks:
        ax.scatter(
            [float(t["x"]) for t in tasks],
            [float(t["y"]) for t in tasks],
            s=[_task_size(t["reward"]) for t in tasks],
            color="#9aa5b1", edgecolors="white", linewidths=0.8, zorder=6,
            label="任务（全部）",
        )

    _draw_nests(ax, nests, counts=count_by_nest)

    if sm is not None:
        cb = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02, shrink=0.75)
        cb.set_label("高程 (m)", fontsize=9)
        cb.ax.tick_params(labelsize=8)

    handles = [
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.2,
                   label=f"机巢（含初始无人机 {n_initial} 架）"),
        _IconPatch(_G_DOT, fc="#9aa5b1", ec="white", lw=1.0, label="任务（全部）"),
    ]
    ax.legend(
        handles=handles, loc="upper right", fontsize=9.5,
        handler_map={_IconPatch: _IconHandler()},
        frameon=True, fancybox=True, framealpha=0.96,
        edgecolor="#e3e7ec", facecolor="white", borderpad=0.7,
    )

    _set_scene_lim(ax, nests, tasks, uavs, dem, extent=_dem_view_extent(dem, solution))
    ax.set_aspect("equal", adjustable="box")
    _strip_axes(ax)
    ax.set_title("初始态势 · 机巢 / 任务 / DEM 地形", fontsize=13, fontweight="bold", color="#1f2933")

    stem = Path(out_dir) / "geo_overview"
    return save_figure(fig, stem, formats, dpi=dpi)
