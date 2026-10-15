# -*- coding: utf-8 -*-
"""图 ``allocation``——单周期分配结构：甘特 + 逐周期空间演化。

【process 层 · 求解过程 ③】

回答「分配结果长什么样」：谁做了哪些任务、按什么顺序、降哪个巢，
以及这一切发生在哪里。由原 ``allocation_gantt`` + ``assignment_map``
+ ``geo_evolution`` 三者合并而来。

版面（2026-10-12 依「少文字、大地图」重排）：

    (a) 航迹条：行 = 周期×UAV 航次，一条 UAV 色航线（长度 ∝ 真实航程），
        任务按真实位置落点画方框（上标号、下收益），两端标起飞/返航巢；
        替代原「收益着色顺序条」，直接读出谁飞多远、任务沿航线怎么分布。
    (b) 逐周期 DEM 演化：横排 1×3（不再是窄列 3 行），每图只留
        「周期 c · 里程」短标题；未执行任务只画空心点、不标号。

数据来源：``solution.json`` 的 ``cycles[].tours``（含 ``est_cost``）+ ``tasks`` /
``nests`` / ``dem``。``metrics`` 入参仅为统一签名保留——本图全部读数均取
solution（甘特条按 est_cost 标定、周期里程按 est_cost 求和），避免与 metrics 的
``f2_cost``（含惩罚项）混口径。
"""

from __future__ import annotations

# ── 独立运行自举：支持直接 `python plot/.../xxx.py`（脚本方式）──
# 脚本方式运行时 __package__ 为空，模块内相对导入不可用；此处转以 -m 语义
# 重跑自身（runpy 会正确设置包上下文），再触发文件末尾的 __main__ 守卫出图。
if __name__ == "__main__" and __package__ in (None, ""):
    import sys, runpy
    from pathlib import Path as _P
    _here = _P(__file__).resolve()
    _root = _here.parents[2]   # 实验根 = plot 的上两级（使 `plot` 包可导入）
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    # 同时把项目根（含 task_allocation 包）加入路径：否则 plotting 内
    # `from task_allocation.methods.utils import GeoTIFFDEM` 会因找不到包而
    # 静默回退到 solution.json 的降采样 DEM（115x101px），地图被放大 10~24 倍变糊。
    _proj = _here
    while not (_proj / "task_allocation" / "__init__.py").is_file() and _proj.parent != _proj:
        _proj = _proj.parent
    if (_proj / "task_allocation" / "__init__.py").is_file() and str(_proj) not in sys.path:
        sys.path.insert(0, str(_proj))
    _rel = _here.relative_to(_root / "plot")
    _mod = "plot." + ".".join(_rel.with_suffix("").parts)
    runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    raise SystemExit(0)


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
                    "est_cost": float(tour.get("est_cost") or 0.0),
                }
            )
    return rows


def _arrival_cycle_map(solution: dict[str, Any]) -> dict[int, int]:
    """任务 id -> 到达周期号（供图面标注 ``T#·Ck``，Ck=到达周期）。

    exp01 任务分批动态到达：``arrival_time`` 等于 ``(c-1)·Δt``（Δt=周期时长）。
    据此把到达时刻映射到周期序号，直观区分「执行周期」与「到达周期」——
    解释在线滚动下为何不能跨周期合并任务（如 C2 的 T15 与 C3 的 T19）。
    """
    cycles = list(solution.get("cycles") or [])
    if len(cycles) >= 2:
        dt = float(cycles[1].get("current_time", 0.0)) - float(cycles[0].get("current_time", 0.0))
    else:
        dt = 600.0
    if dt <= 0:
        dt = 600.0
    out: dict[int, int] = {}
    for t in (solution.get("tasks") or []):
        at = float(t.get("arrival_time", 0.0))
        out[int(t["id"])] = int(round(at / dt)) + 1
    return out


def _draw_gantt(ax, rows: list[dict[str, Any]],
                task_by_id: dict[int, dict[str, Any]],
                nest_by_id: dict[int, dict[str, Any]],
                arrival_cycle: dict[int, int] | None = None, *,
                title: str | None = None):
    """航程比例航迹条（替代原「收益着色顺序条」）。

    每行 = 一个航次（周期×UAV）；画一条 **UAV 色**航线，其**长度 ∝ 真实飞行
    距离**（起飞巢 → 各任务 → 返航巢，单位 km）。段间距按三维几何比例落点，
    **总量按该航次 est_cost（DEM 垂直切面航程，求解器真实代价）标定**——
    与 metrics 的 f2_cost、allocation_anim 的里程读数同口径。
    任务按真实位置落点画方框并标号，航线两端分别标「起飞巢 / 返航巢」。
    这样一眼能看出：哪架 UAV 飞了多远、任务沿航线怎么分布、是否异巢终止。
    """
    import math

    paths: list[dict[str, Any]] = []
    max_dist = 0.0
    for r in rows:
        pts: list[tuple[float, float, float]] = []
        s_nest = nest_by_id.get(int(r["start_nest_id"])) if r["start_nest_id"] is not None else None
        e_nest = nest_by_id.get(int(r["end_nest_id"])) if r["end_nest_id"] is not None else None
        if s_nest is not None:
            pts.append((float(s_nest["x"]), float(s_nest["y"]), float(s_nest["z"])))
        for tid in r["task_ids"]:
            t = task_by_id.get(int(tid))
            if t is not None:
                pts.append((float(t["x"]), float(t["y"]), float(t["z"])))
        if e_nest is not None:
            pts.append((float(e_nest["x"]), float(e_nest["y"]), float(e_nest["z"])))
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            d = math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)
            cum.append(cum[-1] + d)
        cum_km = [c / 1000.0 for c in cum]
        # 总量按 DEM 航程（est_cost，求解器真实代价 = 垂直切面三维航程）标定：
        # 段间距保留三维几何比例，整条缩放到 est_cost，使线长与 metrics 的
        # f2_cost、anim 的里程读数同口径（曾直接用三维直线，总量少报 ~2%，
        # 与同图 (b) 面板 / allocation_anim 里程对不上——2026-10-13 审查修复）。
        est_km = float(r.get("est_cost") or 0.0) / 1000.0
        if est_km > 0.0 and cum_km[-1] > 0.0:
            scale = est_km / cum_km[-1]
            cum_km = [c * scale for c in cum_km]
        max_dist = max(max_dist, cum_km[-1])
        paths.append({
            "uav": int(r["uav_id"]),
            "task_ids": [int(t) for t in r["task_ids"]],
            "rewards": [float(task_by_id.get(int(t), {}).get("reward", 0.0)) for t in r["task_ids"]],
            "s": r["start_nest_id"], "e": r["end_nest_id"],
            "cum": cum_km,
        })

    n = len(rows)
    ys = np.arange(n)[::-1]
    for y, r, p in zip(ys, rows, paths):
        color = uav_color(p["uav"])
        cum = p["cum"]
        if cum[-1] > 0:
            ax.plot([0.0, cum[-1]], [y, y], color=color, lw=2.4,
                    zorder=2, solid_capstyle="round")
        for i, tid in enumerate(p["task_ids"], start=1):
            x = cum[i]
            ax.scatter([x], [y], s=125, color=color, edgecolors="white",
                       linewidths=1.1, marker="s", zorder=5)
            label = f"T{tid}"
            if arrival_cycle and int(tid) in arrival_cycle:
                label += f"·C{arrival_cycle[int(tid)]}"
            ax.annotate(label, (x, y), textcoords="offset points",
                        xytext=(0, 9), ha="center", fontsize=FS["tick"],
                        color="#1f2933", zorder=7, bbox=_HALO)
            ax.annotate(f"{p['rewards'][i - 1]:.1f}", (x, y),
                        textcoords="offset points", xytext=(0, -13),
                        ha="center", fontsize=6.0, color="#8a95a1", zorder=7)
        # 起飞巢（左端，实心屋形）
        ax.scatter([0.0], [y], marker=_G_HOUSE, s=150, color=PALETTE["nest"],
                   edgecolors="white", linewidths=1.0, zorder=6)
        if p["s"] is not None:
            ax.annotate(f"巢{p['s']}", (0.0, y), textcoords="offset points",
                        xytext=(0, -15), ha="center", fontsize=6.4,
                        color="#5b6472", zorder=7)
        # 返航巢（右端，UAV 色空心圈）
        if cum[-1] > 0:
            ax.scatter([cum[-1]], [y], s=175, facecolors="none",
                       edgecolors=color, linewidths=1.6, marker="o", zorder=5)
        relocated = p["s"] is not None and p["e"] is not None and p["s"] != p["e"]
        if p["e"] is not None:
            ax.annotate(f"巢{p['e']}", (cum[-1], y), textcoords="offset points",
                        xytext=(0, -15), ha="center", fontsize=6.4,
                        color=_RELOCATED_COLOR if relocated else "#5b6472", zorder=7)

    for i in range(n - 1):
        if rows[i]["cycle"] != rows[i + 1]["cycle"]:
            ax.axhline((ys[i] + ys[i + 1]) / 2.0, color="#c3cbd4",
                       linewidth=0.9, linestyle=(0, (4, 3)), zorder=0)

    ax.set_yticks(ys, [f"C{r['cycle']}·U{r['uav_id']}" for r in rows],
                  fontsize=FS["tick"])
    ax.set_xlim(-max_dist * 0.03, max_dist * 1.06)
    ax.set_ylim(-0.7, n + 0.3)
    style_axes(ax, title=title or
               "(a) 分配航迹条：线长 ∝ 真实航程 · 方块=任务 · 两端=起/降巢（按 UAV 着色）")
    ax.grid(axis="x", alpha=0.15)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("累计航程 (km，起飞巢起算；段间距按三维几何比例、总量按 DEM 航程 est_cost 标定)")


def _draw_cycle_tours(ax, cyc: dict[str, Any],
                      task_by_id: dict[int, dict[str, Any]],
                      nest_by_id: dict[int, dict[str, Any]],
                      arrival_cycle: dict[int, int] | None = None) -> set[int]:
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
        label = f"T{tid}"
        if arrival_cycle and int(tid) in arrival_cycle:
            label += f"·C{arrival_cycle[int(tid)]}"
        ax.annotate(label, (float(t["x"]), float(t["y"])),
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
    arrival_cycle = _arrival_cycle_map(solution)

    n_rows = len(rows)
    gantt_h = max(2.8, 0.42 * n_rows + 1.5)

    fig = plt.figure(figsize=(11.8, gantt_h + 4.55))
    gs = fig.add_gridspec(
        2, 1, height_ratios=[gantt_h, 4.0], hspace=0.30,
        left=0.055, right=0.93, top=0.94, bottom=0.105,
    )
    ax_gantt = fig.add_subplot(gs[0])
    _draw_gantt(ax_gantt, rows, task_by_id, nest_by_id, arrival_cycle=arrival_cycle)

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
        uavs_used = _draw_cycle_tours(ax, cyc, task_by_id, nest_by_id,
                                       arrival_cycle=arrival_cycle)
        all_uavs_used |= uavs_used
        _draw_nests(ax, nests)
        # 周期里程 = 该周期各航次 est_cost 之和（纯 DEM 航程）。不用 metrics 的
        # f2_cost——它另含 w_penalty×惩罚（量纲不同），曾使 (b) 面板里程与
        # (a) 甘特条总长、anim 里程读数对不上（2026-10-13 审查统一）。
        tours_c = [t for t in (cyc.get("tours") or []) if t.get("task_ids")]
        km = sum(float(t.get("est_cost") or 0.0) for t in tours_c) / 1000.0
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
    footnote(fig, "口径：航迹条（a）行=航次，UAV 色线长 ∝ 真实航程（段间距按三维"
                  "几何比例、总量按 DEM 航程 est_cost 标定），方框=任务"
                  "（上标号 T#·Ck，Ck=到达周期；下收益 R_j），两端标起/降巢，"
                  "琥珀=异巢终止；地图（b）未执行任务仅画空心点，周期标题里程 = "
                  "该周期各航次 est_cost 之和（纯 DEM 航程，不含约束惩罚）。"
                  "Ck 标出任务到达周期，区分「到达」与「执行」——在线滚动下"
                  "晚到任务（如 T19·C3）无法并入早周期航次。")
    return save_figure(fig, Path(out_dir) / "P03_allocation", formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
