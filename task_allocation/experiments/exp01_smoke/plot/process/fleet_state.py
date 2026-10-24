# -*- coding: utf-8 -*-
"""图 ``fleet_state``——机队与机巢：三口径出动 / 占用热力 / 选巢归因。

【process 层 · 求解过程 ⑤】

盯**资源侧**：机队有多大、多少架真的在飞、机巢挤不挤，以及机巢选择到底是
**被成本拉**还是**被容量逼**。由原 ``fleet_utilization`` + ``nest_choice``
合并而来；``geo_fleet_transition`` 的机队迁移空间图由本图数值面板覆盖、
起降航线见 ``allocation`` 图。

六联面板::

    (a) 每周期 UAV 参与   可用 / 编码参与 / 实际出动（三口径，暴露"派了≠飞了"）
    (b) 各 UAV 出动频次   灰 = 全程待命，红 = 全程未出动
    (c) 周期末机巢占用     占用 / 容量 热力图（逐周期 × 机巢）
    (d) 容量背景           周期末占用 vs 容量：余量全程 ≥1，容量从未咬合
    (e) 距离排名           起飞 / 返航决策的选中巢距离排名 vs 随机期望
    (f) 容量反证           选中巢从不是"因为最空"；换巢均离开最空巢

数据来源：``metrics.json`` 的 ``cycles[].muas``（n_uav_avail / n_participating）/
``cycles[].nest_occupancy`` / ``summary``，``solution.json`` 的 ``nests`` /
``tasks`` / ``cycles[].tours``。
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


import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..base.geo import _metric_cycle
from ..theme import FS, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_fleet_state"]


def _xyz(node: dict[str, Any]) -> tuple[float, float, float]:
    """取节点三维坐标（缺 z 按 0）。"""
    return (float(node.get("x") or 0.0),
            float(node.get("y") or 0.0),
            float(node.get("z") or 0.0))


def _dispatched_per_cycle(solution: dict[str, Any],
                          cycles: Sequence[dict[str, Any]]) -> list[int]:
    """每周期「实际出动」的 UAV 数（tours 按 ``uav_id`` 去重）。"""
    tours_by_cycle: dict[int, set[int]] = {}
    for c in solution.get("cycles") or []:
        ids = {
            int(t["uav_id"])
            for t in (c.get("tours") or [])
            if t.get("uav_id") is not None
        }
        tours_by_cycle[int(c.get("cycle", -1))] = ids
    out = []
    for c in cycles:
        out.append(len(tours_by_cycle.get(int(c.get("cycle", -1)), set())))
    return out


def _tour_rows(metrics: dict[str, Any], solution: dict[str, Any]) -> list[dict[str, Any]]:
    """把每个航次摊平为一行，带齐选巢归因字段。无 nests/tasks/tours 返回 []。"""
    nests = list(solution.get("nests") or [])
    if not nests:
        return []
    tasks = {int(t["id"]): _xyz(t) for t in (solution.get("tasks") or [])
             if t.get("id") is not None}
    nest_xyz = {int(n["id"]): _xyz(n) for n in nests if n.get("id") is not None}
    capacity = {int(n["id"]): int(n.get("capacity") or 0) for n in nests}
    if not tasks or not nest_xyz:
        return []

    rows: list[dict[str, Any]] = []
    for cycle in solution.get("cycles") or []:
        c_no = int(cycle.get("cycle", -1))
        record = _metric_cycle(metrics, c_no)
        occupancy = {int(k): int(v) for k, v in (record.get("nest_occupancy") or {}).items()}
        free = {nid: capacity[nid] - occupancy.get(nid, 0) for nid in nest_xyz}
        for tour in cycle.get("tours") or []:
            task_ids = [int(t) for t in (tour.get("task_ids") or [])]
            if not task_ids or task_ids[0] not in tasks or task_ids[-1] not in tasks:
                continue
            start_nest = int(tour.get("start_nest_id", -1))
            end_nest = int(tour.get("end_nest_id", -1))
            if start_nest not in nest_xyz or end_nest not in nest_xyz:
                continue
            first_xy, last_xy = tasks[task_ids[0]], tasks[task_ids[-1]]
            d_start = {nid: math.dist(nest_xyz[nid], first_xy) for nid in nest_xyz}
            d_end = {nid: math.dist(nest_xyz[nid], last_xy) for nid in nest_xyz}
            rows.append({
                "cycle": c_no,
                "uav": int(tour.get("uav_id", -1)),
                "start_nest": start_nest,
                "end_nest": end_nest,
                "switched": start_nest != end_nest,
                "d_start": d_start,
                "d_end": d_end,
                "rank_start": 1 + sum(1 for v in d_start.values()
                                      if v < d_start[start_nest]),
                "rank_end": 1 + sum(1 for v in d_end.values()
                                    if v < d_end[end_nest]),
                "free_start": free.get(start_nest, 0),
                "free_end": free.get(end_nest, 0),
                "free_max": max(free.values()) if free else 0,
            })
    return rows


def plot_fleet_state(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    if not cycles:
        return []

    n = len(cycles)
    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    x = np.arange(n)

    avail = [int((c.get("muas") or {}).get("n_uav_avail") or 0) for c in cycles]
    coded = [int((c.get("muas") or {}).get("n_participating") or 0) for c in cycles]
    dispatched = _dispatched_per_cycle(solution, cycles)
    _summary = metrics.get("summary", {})
    avail_mean = float(_summary.get("K_mean", _summary.get("K_avail_mean")) or 0.0)

    # 机巢占用热力图数据
    nests = list(solution.get("nests") or [])
    nest_ids: list[int] = []
    capacity = {int(nest.get("id")): int(nest.get("capacity") or 0) for nest in nests}
    for c in cycles:
        for key in (c.get("nest_occupancy") or {}):
            nid = int(key)
            if nid not in nest_ids:
                nest_ids.append(nid)
    nest_ids.sort()
    if not capacity:
        capacity = {nid: 0 for nid in nest_ids}
    occ = np.zeros((n, len(nest_ids)))
    for i, c in enumerate(cycles):
        for key, val in (c.get("nest_occupancy") or {}).items():
            nid = int(key)
            if nid in nest_ids:
                occ[i, nest_ids.index(nid)] = float(val)
    cap_max = max(capacity.values()) if capacity else 1

    # 选巢归因数据
    rows = _tour_rows(metrics, solution)
    occ_series: dict[int, list[int]] = {nid: [] for nid in nest_ids}
    for record in cycles:
        occ_d = {int(k): int(v) for k, v in (record.get("nest_occupancy") or {}).items()}
        for nid in nest_ids:
            occ_series[nid].append(occ_d.get(nid, 0))
    min_free = min((capacity[nid] - max(occ_series[nid]) for nid in nest_ids
                    if capacity[nid]), default=0)
    n_end_best = sum(1 for r in rows if r["rank_end"] == 1)
    n_start_best = sum(1 for r in rows if r["rank_start"] == 1)
    n_not_emptiest = sum(1 for r in rows if r["free_end"] < r["free_max"])
    switches = [r for r in rows if r["switched"]]
    n_nests = len(nest_ids)

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 7.4))
    ax_part, ax_freq, ax_nest, ax_cap, ax_rank, ax_free = axes.ravel()

    # ── (a) 每周期参与：可用 / 编码参与 / 实际出动 ─────────────────
    w = 0.26
    series = [
        ("可用 UAV", avail, PALETTE["remain"]),
        ("编码参与", coded, PALETTE["selected"]),
        ("实际出动", dispatched, PALETTE["executed"]),
    ]
    for i, (name, vals, color) in enumerate(series):
        bars = ax_part.bar(x + (i - 1) * w, vals, w, label=name, color=color,
                           edgecolor="white", linewidth=0.6)
        for b, v in zip(bars, vals):
            ax_part.text(b.get_x() + b.get_width() / 2, v + 0.12, f"{v}",
                         ha="center", va="bottom", fontsize=FS["tick"], color="0.25")
    ax_part.set_xticks(x, labels)
    ax_part.set_ylim(0, max(max(avail), max(coded), 1) * 1.32)
    ax_part.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_part, title="(a) 每周期 UAV 参与（三口径）", ylabel="UAV 数")
    ax_part.legend(loc="upper right", ncol=3, fontsize=FS["legend"],
                   framealpha=0.92, columnspacing=0.9, handlelength=1.2)

    # ── (b) UAV 出动频次分布 ───────────────────────────────────────
    freq: dict[int, int] = {}
    for c in solution.get("cycles") or []:
        for t in c.get("tours") or []:
            uid = t.get("uav_id")
            if uid is not None:
                freq[int(uid)] = freq.get(int(uid), 0) + 1
    all_uavs = [int(u.get("id")) for u in solution.get("uavs") or []]
    if not all_uavs:
        all_uavs = sorted(set(range(max(avail))) | set(freq))
    all_uavs.sort()
    counts = [freq.get(u, 0) for u in all_uavs]
    colors = [PALETTE["executed"] if c else PALETTE["remain"] for c in counts]
    ypos = np.arange(len(all_uavs))
    ax_freq.barh(ypos, counts, 0.62, color=colors, edgecolor="white", linewidth=0.6)
    for yi, c in zip(ypos, counts):
        ax_freq.text(c + 0.04, yi, f"{c}", va="center", fontsize=FS["tick"], color="0.25")
    ax_freq.set_yticks(ypos, [f"UAV {u}" for u in all_uavs])
    ax_freq.invert_yaxis()
    ax_freq.set_xlim(0, max(counts + [1]) + 0.6)
    ax_freq.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    n_idle_fleet = sum(1 for c in counts if c == 0)
    style_axes(ax_freq, title=f"(b) 各 UAV 出动频次（{n_idle_fleet}/{len(all_uavs)} "
                              f"架全程未出动）", xlabel="出动周期数")
    ax_freq.text(0.98, 0.03, "灰 = 全程待命", transform=ax_freq.transAxes,
                 ha="right", va="bottom", fontsize=FS["footnote"], color="0.45")

    # ── (c) 机巢占用热力图 ─────────────────────────────────────────
    if n_nests:
        im = ax_nest.imshow(occ, aspect="auto", cmap="YlOrRd",
                            vmin=0, vmax=max(cap_max, 1))
        ax_nest.set_xticks(range(len(nest_ids)), [str(i) for i in nest_ids])
        ax_nest.set_yticks(range(n), labels)
        for i in range(n):
            for j in range(len(nest_ids)):
                cap = capacity.get(nest_ids[j], 0)
                over = cap and occ[i, j] >= cap
                ax_nest.text(j, i, f"{occ[i, j]:.0f}/{cap}",
                             ha="center", va="center", fontsize=FS["tick"],
                             color="white" if over else "0.25",
                             fontweight="bold" if over else "normal")
        ax_nest.set_xticks(np.arange(-0.5, len(nest_ids), 1), minor=True)
        ax_nest.set_yticks(np.arange(-0.5, n, 1), minor=True)
        ax_nest.grid(which="minor", color="white", linewidth=1.4)
        ax_nest.tick_params(which="minor", length=0)
        style_axes(ax_nest, title="(c) 周期末机巢占用 / 容量（泊位）",
                   xlabel="机巢 id")
        fig.colorbar(im, ax=ax_nest, shrink=0.82, pad=0.02)
    else:
        ax_nest.text(0.5, 0.5, "无机巢数据", ha="center", va="center",
                     fontsize=FS["base"], color="0.45", transform=ax_nest.transAxes)

    # ── (d) 容量背景：占用 vs 容量 ────────────────────────────────
    if n_nests:
        for j, nid in enumerate(nest_ids):
            vals = occ_series[nid] or [0]
            ax_cap.vlines(j, min(vals), max(vals), color=PALETTE["nest"],
                          linewidth=7, alpha=0.30, zorder=2)
            ax_cap.scatter([j] * len(vals), vals, s=26, color=PALETTE["nest"],
                           zorder=3, edgecolors="white", linewidths=0.6)
        if cap_max > 0:
            ax_cap.axhline(cap_max, color=PALETTE["release"], linewidth=1.2,
                           linestyle="--", zorder=4)
            ax_cap.text(n_nests - 0.4, cap_max + 0.06, f"容量上限 {cap_max}",
                        ha="right", va="bottom", fontsize=FS["footnote"],
                        color=PALETTE["release"])
        ax_cap.set_xticks(range(n_nests), [str(i) for i in nest_ids])
        ax_cap.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        ax_cap.set_ylim(0, max(cap_max, 1) + 0.8)
        style_axes(ax_cap, title="(d) 容量背景：周期末占用 vs 容量",
                   xlabel="机巢 id", ylabel="占用泊位")
    else:
        ax_cap.text(0.5, 0.5, "无周期/机巢数据", ha="center", va="center",
                    fontsize=FS["base"], color="0.45", transform=ax_cap.transAxes)

    # ── (e) 距离排名 vs 随机期望 ─────────────────────────────────
    if rows:
        ranks = np.arange(1, n_nests + 1)
        start_counts = [sum(1 for r in rows if r["rank_start"] == k) for k in ranks]
        end_counts = [sum(1 for r in rows if r["rank_end"] == k) for k in ranks]
        ww = 0.38
        ax_rank.bar(ranks - ww / 2, start_counts, ww, color=PALETTE["selected"],
                    label="起飞巢（按到首任务距离）")
        ax_rank.bar(ranks + ww / 2, end_counts, ww, color=PALETTE["executed"],
                    label="返航巢（按到末任务距离）")
        expect = len(rows) / n_nests if n_nests else 0
        ax_rank.axhline(expect, color="0.45", linewidth=1.0, linestyle="--", zorder=0)
        ax_rank.text(n_nests - 0.4, expect + 0.08, f"随机期望 {expect:.2f}",
                     ha="right", va="bottom", fontsize=FS["footnote"], color="0.4")
        for k, c in zip(ranks, start_counts):
            if c:
                ax_rank.text(k - ww / 2, c + 0.08, f"{c}", ha="center",
                             va="bottom", fontsize=FS["tick"], color="0.25")
        for k, c in zip(ranks, end_counts):
            if c:
                ax_rank.text(k + ww / 2, c + 0.08, f"{c}", ha="center",
                             va="bottom", fontsize=FS["tick"], color="0.25")
        ax_rank.set_xticks(ranks)
        ax_rank.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        style_axes(ax_rank, title="(e) 选中巢的距离排名（1 = 最近）",
                   xlabel="距离排名", ylabel="航次数")
        ax_rank.legend(loc="upper right", fontsize=FS["legend"], framealpha=0.92)
    else:
        ax_rank.text(0.5, 0.5, "无航次数据", ha="center", va="center",
                     fontsize=FS["base"], color="0.45", transform=ax_rank.transAxes)

    # ── (f) 容量反证 ─────────────────────────────────────────────
    if rows:
        ypos2 = np.arange(len(rows))
        row_labels = [f"周期{r['cycle']}·U{r['uav']}" for r in rows]
        ax_free.barh(ypos2, [r["free_max"] for r in rows], 0.58, color="0.93",
                     zorder=1, label="当周期最空巢空闲（容量假设预测）")
        for i, r in enumerate(rows):
            if r["switched"]:
                ax_free.annotate(
                    "", xy=(r["free_end"], i), xytext=(r["free_start"], i),
                    arrowprops=dict(arrowstyle="->", color=PALETTE["deferred"],
                                    lw=1.3, shrinkA=2, shrinkB=2), zorder=2)
            ax_free.scatter([r["free_start"]], [i], s=40, color=PALETTE["remain"],
                            zorder=3, edgecolors="white", linewidths=0.6,
                            label="起飞巢空闲" if i == 0 else None)
            ax_free.scatter([r["free_end"]], [i], s=58, color=PALETTE["executed"],
                            zorder=4, edgecolors="white", linewidths=0.8,
                            label="选中返航巢空闲" if i == 0 else None)
        ax_free.set_yticks(ypos2, row_labels)
        ax_free.invert_yaxis()
        ax_free.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
        ax_free.set_xlim(0, max(r["free_max"] for r in rows) + 2.0)
        style_axes(ax_free,
                   title=f"(f) 容量反证：{n_not_emptiest}/{len(rows)} 次未选最空巢"
                         f"；{len(switches)} 次换巢均离开最空巢",
                   xlabel="空闲泊位")
        leg = ax_free.legend(loc="lower right", fontsize=FS["legend"],
                             framealpha=0.95, labelspacing=0.35,
                             handlelength=1.2)
        leg.get_frame().set_edgecolor("none")
    else:
        ax_free.text(0.5, 0.5, "无航次数据", ha="center", va="center",
                     fontsize=FS["base"], color="0.45", transform=ax_free.transAxes)

    fig.suptitle("exp01_smoke：机队与机巢（三口径出动 · 占用 · 选巢归因）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    cap_note = (f"全程最高占用 {max(max(v) for v in occ_series.values())}/{cap_max}"
                f"（最低余量 {min_free} 泊位，容量从未咬合）"
                if n_nests and occ_series else "")
    rank_note = (f"起飞巢 {n_start_best}/{len(rows)} 次取最近"
                 if rows else "")
    footnote(fig, "口径：实际出动 = tours 按 uav_id 去重；空闲泊位 = 容量 − 周期末占用；"
                  "距离为三维直线代理（真代价为地形剖面代价，计入 F2）；"
                  f"{cap_note}{rank_note}；换巢明细见 allocation 图返航巢列。")
    return save_figure(fig, Path(out_dir) / "P10_fleet_state", formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
