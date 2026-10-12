# -*- coding: utf-8 -*-
"""图 ``allocation``——单周期分配结构：甘特 + 逐周期空间演化。

【process 层 · 求解过程 ③】

回答「分配结果长什么样」：谁做了哪些任务、按什么顺序、降哪个巢，
以及这一切发生在哪里。由原 ``allocation_gantt`` + ``assignment_map``
+ ``geo_evolution`` 三者合并而来。

版面（2026-10-12 依「少文字、大地图」重排）：

    (a) 甘特：行 = 周期×UAV 航次，块 = 任务（颜色 ∝ 收益，块内只写任务号），
        行尾一列「返航巢」；删去原逐块的 R= 与航程/收益明细列（信息在
        颜色条与 fleet_state / outcome_summary 中都有）。
    (b) 逐周期 DEM 演化：横排 1×3（不再是窄列 3 行），每图只留
        「周期 c · 里程」短标题；未执行任务只画空心点、不标号。

数据来源：``solution.json`` 的 ``cycles[].tours`` + ``tasks`` / ``nests`` / ``dem``，
叠加 ``metrics.json`` 的 selection / muas 计数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np
from matplotlib.lines import Line2D

from ..base.dem import _dem_view_extent, _draw_dem
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
    _set_scene_lim,
    _strip_axes,
    _task_size,
    uav_color,
)
from ..theme import FS, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_allocation"]

_RELOCATED_COLOR = "#d97706"  # 琥珀——异巢终止提示（不再用大红）


def _collect_rows(solution: dict[str, Any]) -> list[dict[str, Any]]:
    """把各周期航次摊平成甘特图的行（保持周期顺序，组内按 UAV id 排）。"""
    rows: list[dict[str, Any]] = []
    for cyc in solution.get("cycles") or []:
        tours = [t for t in (cyc.get("tours") or []) if t.get("task_ids")]
        for tour in sorted(tours, key=lambda t: int(t.get("uav_id", -1))):
            rows.append(
                {
                    "cycle": int(cyc.get("cycle", 0)),
                    "uav_id": int(tour.get("uav_id", -1)),
                    "task_ids": [int(t) for t in tour["task_ids"]],
                    "start_nest_id": tour.get("start_nest_id"),
                    "end_nest_id": tour.get("end_nest_id"),
                }
            )
    return rows


def _draw_gantt(ax, rows: list[dict[str, Any]],
                tasks: dict[int, dict[str, Any]]):
    """甘特面板：行 = 航次，块 = 任务（色 ∝ 收益），行尾 = 返航巢。"""
    rewards = [float(tasks.get(tid, {}).get("reward", 0.0))
               for r in rows for tid in r["task_ids"]]
    vmin = min(rewards) if rewards else 0.0
    vmax = max(rewards) if rewards else 1.0
    if abs(vmax - vmin) < 1e-9:
        vmax = vmin + 1.0
    mid = (vmin + vmax) / 2.0

    cmap = plt.get_cmap("viridis")
    norm = plt.Normalize(vmin=vmin, vmax=vmax)

    max_len = max(len(r["task_ids"]) for r in rows)
    n = len(rows)
    x_nest = max_len + 1.15
    x_max = x_nest + 2.1

    ys = np.arange(n)[::-1]
    for y, row in zip(ys, rows):
        ax.barh(y, max_len + 0.5, left=0.5, height=0.72,
                color="#f3f5f8", edgecolor="none", zorder=1)
        for pos, tid in enumerate(row["task_ids"], start=1):
            reward = float(tasks.get(tid, {}).get("reward", 0.0))
            ax.barh(y, 0.86, left=pos - 0.43, height=0.64,
                    color=cmap(norm(reward)), edgecolor="white",
                    linewidth=0.9, zorder=2)
            ink = "white" if reward < mid else "#132238"
            ax.text(pos, y, f"T{tid}", ha="center", va="center",
                    fontsize=FS["tick"], color=ink, zorder=3)

        s, e = row["start_nest_id"], row["end_nest_id"]
        relocated = s is not None and e is not None and s != e
        label = f"巢{s} → 巢{e}" if relocated else f"巢{s}"
        ax.text(x_nest, y, label, ha="left", va="center",
                fontsize=FS["tick"],
                color=_RELOCATED_COLOR if relocated else "#5b6472",
                zorder=3)

    for i in range(n - 1):
        if rows[i]["cycle"] != rows[i + 1]["cycle"]:
            ax.axhline((ys[i] + ys[i + 1]) / 2.0, color="#c3cbd4",
                       linewidth=0.9, linestyle=(0, (4, 3)), zorder=0)

    ax.text(x_nest, n - 0.32, "返航巢", ha="left", va="bottom",
            fontsize=FS["tick"], color="#7b8794", zorder=3)
    ax.set_yticks(ys, [f"C{r['cycle']}·U{r['uav_id']}" for r in rows],
                  fontsize=FS["tick"])
    ax.set_xticks(np.arange(1, max_len + 1))
    ax.set_xlim(0.35, x_max)
    ax.set_ylim(-0.7, n + 0.3)
    style_axes(ax, title="(a) 分配甘特：谁 · 顺序 · 返航巢（块色 ∝ 任务收益）")
    ax.grid(axis="x", alpha=0.15)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("任务顺位（左 → 右）")

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    return sm


def _draw_cycle_tours(ax, cyc: dict[str, Any],
                      task_by_id: dict[int, dict[str, Any]],
                      nest_by_id: dict[int, dict[str, Any]]) -> set[int]:
    """画单周期航次：起点 → 任务 → 终点巢；任务点按承运 UAV 着色。

    未执行任务只画空心灰点（不再逐点标号，文字减半）。
    """
    owner: dict[int, int] = {}
    uavs_used: set[int] = set()
    for tour in cyc.get("tours") or []:
        u = int(tour.get("uav_id", -1))
        uavs_used.add(u)
        for tid in tour.get("task_ids") or []:
            owner[int(tid)] = u

    others = [t for t in task_by_id.values() if int(t["id"]) not in owner]
    if others:
        ax.scatter(
            [float(t["x"]) for t in others],
            [float(t["y"]) for t in others],
            s=[_task_size(t["reward"]) * 0.5 for t in others],
            facecolors="none", edgecolors="#aab4bf", linewidths=1.0, zorder=2,
        )

    for tour in cyc.get("tours") or []:
        u = int(tour.get("uav_id", -1))
        color = uav_color(u)
        start = tour.get("start") or [0.0, 0.0, 0.0]
        pts: list[tuple[float, float]] = [(float(start[0]), float(start[1]))]
        for tid in tour.get("task_ids") or []:
            t = task_by_id.get(int(tid))
            if t is not None:
                pts.append((float(t["x"]), float(t["y"])))
        end = (nest_by_id.get(int(tour["end_nest_id"]))
               if tour.get("end_nest_id") is not None else None)
        if end is not None:
            pts.append((float(end["x"]), float(end["y"])))
        for p0, p1 in zip(pts, pts[1:]):
            _arrow(ax, p0, p1, color, lw=1.8)
        ax.scatter([pts[0][0]], [pts[0][1]], marker=_G_DRONE, s=110,
                   color=color, edgecolors="white", linewidths=1.1, zorder=5)
        ax.annotate(f"U{u}", pts[0], textcoords="offset points",
                    xytext=(8, 8), ha="left", fontsize=7.0, color="#1f2933",
                    zorder=8, bbox=_HALO)
        if end is not None:
            ax.scatter([float(end["x"])], [float(end["y"])], s=170,
                       facecolors="none", edgecolors=color, linewidths=1.6,
                       zorder=4)

    for tid, u in owner.items():
        t = task_by_id.get(tid)
        if t is None:
            continue
        ax.scatter([float(t["x"])], [float(t["y"])], s=_task_size(t["reward"]),
                   color=uav_color(u), edgecolors="white", linewidths=0.9,
                   zorder=6)
        ax.annotate(f"T{tid}", (float(t["x"]), float(t["y"])),
                    textcoords="offset points", xytext=_label_offset(tid),
                    ha="center", fontsize=6.8, color="#1f2933",
                    zorder=8, bbox=_HALO)
    return uavs_used


def plot_allocation(
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
    rows = _collect_rows(solution)
    if not nests or not cycles or not rows:
        return []

    task_by_id = {int(t["id"]): t for t in tasks}
    nest_by_id = {int(n["id"]): n for n in nests}
    metric_by_cycle = {int(c.get("cycle", -1)): c
                       for c in (metrics.get("cycles") or [])}

    n_rows = len(rows)
    gantt_h = max(2.8, 0.42 * n_rows + 1.5)

    fig = plt.figure(figsize=(11.8, gantt_h + 4.55))
    gs = fig.add_gridspec(
        2, 1, height_ratios=[gantt_h, 4.0], hspace=0.30,
        left=0.055, right=0.90, top=0.94, bottom=0.105,
    )
    ax_gantt = fig.add_subplot(gs[0])
    sm = _draw_gantt(ax_gantt, rows, task_by_id)
    fig.colorbar(sm, ax=ax_gantt, fraction=0.024, pad=0.012,
                 label="任务收益 R_j")

    # ── (b) 逐周期 DEM 演化：横排 1×N，共享范围 ───────────────────
    n_cyc = len(cycles)
    inner = gs[1].subgridspec(1, n_cyc, wspace=0.06)
    sm0 = None
    all_uavs_used: set[int] = set()
    evo_axes = []
    extent = _dem_view_extent(solution.get("dem"), solution)
    for idx, cyc in enumerate(cycles):
        ax = fig.add_subplot(inner[idx])
        evo_axes.append(ax)
        smx = _draw_dem(ax, solution.get("dem"), solution)
        if idx == 0:
            sm0 = smx
        uavs_used = _draw_cycle_tours(ax, cyc, task_by_id, nest_by_id)
        all_uavs_used |= uavs_used
        _draw_nests(ax, nests)
        muas = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("muas", {})
        km = float(muas.get("f2_cost", 0.0)) / 1000.0
        _panel_title(ax, f"周期 {cyc.get('cycle')} · {km:.0f} km")
        _strip_axes(ax)

    _set_scene_lim(evo_axes[0], nests, tasks, [], solution.get("dem"),
                   extent=extent)
    for ax in evo_axes:
        ax.set_aspect("equal", adjustable="box")
    evo_axes[0].set_title("(b) 逐周期空间演化（DEM + 航次回放）",
                          fontsize=FS["title"], loc="left")

    if sm0 is not None:
        fig.colorbar(sm0, ax=evo_axes, location="right", shrink=0.75,
                     pad=0.015).set_label("高程 (m)", fontsize=FS["tick"])

    handles = [
        _IconPatch(_G_DRONE, fc=uav_color(u), ec="white", lw=1.0,
                   label=f"UAV {u}") for u in sorted(all_uavs_used)
    ]
    handles += [
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.0,
                   label="机巢（○=返航巢）"),
        _IconPatch(_G_DOT, fc="none", ec="#aab4bf", lw=1.1, label="未执行任务"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.475, 0.030),
               handler_map={_IconPatch: _IconHandler()},
               ncol=min(6, len(handles)), fontsize=FS["legend"],
               columnspacing=1.1, frameon=False)

    fig.suptitle("exp01_smoke：分配结构与逐周期空间演化",
                 fontsize=FS["suptitle"])
    footnote(fig, "口径：甘特只画真被分配任务的航次；块色 ∝ 收益 R_j（数值见颜色条）；"
                  "琥珀色 = 异巢终止（起飞巢 ≠ 返航巢）；地图未执行任务仅画空心点。")
    return save_figure(fig, Path(out_dir) / "allocation", formats, dpi=dpi)
