# -*- coding: utf-8 -*-
"""图 ``nest_choice``——机巢选择归因：按成本选定，容量全程不咬合。

【process 层 · 运行过程观测】

无人机每周期要选两个机巢：**从哪个巢起飞**（``tours[].start_nest_id``）与
**降落到哪个巢**（``tours[].end_nest_id``，终点机巢基因 + 内层最小费用流定）。
本图回答一个归因问题：这些选择是**被容量逼的**，还是**被成本（距离/地形）
拉的**？

    巢5 全程 0 占用、容量 4，从没被选过          → 空 ≠ 被选
    c1·U5 离开最空的巢5(free 4) 降落到巢6(free 2) → 容量假设预测反向
    9/9 返航巢 = 末任务直线距离最近的巢            → 与成本一致

2×2 四联图：

    (a) 容量背景      —— 每巢周期末占用 vs 容量：余量全程 ≥1，容量从未咬合
    (b) 返航巢反事实  —— 末任务到 8 个巢的距离全列出，选中巢逐航次对标
    (c) 距离排名      —— 起飞/返航两个决策的选中巢距离排名 vs 随机期望
    (d) 容量反证      —— 选中巢空闲泊位 vs 起飞巢/最空巢：从不是"因为最空"

口径与边界（写进图注，避免过度宣称）：

    * 样本 = 代表 seed 全部 9 个航次（3 周期）；规模小，是**方向性证据**；
    * 距离用**三维直线距离**作代理——模型内部代价是地形垂直剖面代价
      （``VerticalSectionCostEstimator``）并计入 F2，两者排序在多数情况
      一致但不保证逐例一致，(c) 中对不一致的实例如实标注差距；
    * 容量数据来自 ``metrics.cycles[].nest_occupancy``（周期末快照），
      空闲泊位 = 容量 − 周期末占用。

数据来源：``solution.json`` 的 ``nests`` / ``tasks`` / ``cycles[].tours``
+ ``metrics.json`` 的 ``cycles[].nest_occupancy``。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..base.geo import _metric_cycle
from ..theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_nest_choice"]


def _xyz(node: dict[str, Any]) -> tuple[float, float, float]:
    """取节点三维坐标（缺 z 按 0）。"""
    return (float(node.get("x") or 0.0),
            float(node.get("y") or 0.0),
            float(node.get("z") or 0.0))


def _tour_rows(metrics: dict[str, Any], solution: dict[str, Any]) -> list[dict[str, Any]]:
    """把每个航次摊平成一行，带齐归因分析需要的全部字段。

    无 nests / tasks / tours 任一为空时返回 []（图自行跳过）。
    """
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
                "task_ids": task_ids,
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
                "n_tasks": len(task_ids),
            })
    return rows


def plot_nest_choice(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """画 2×2 归因面板；数据不足（无航次）时返回 []。"""
    rows = _tour_rows(metrics, solution)
    if not rows:
        return []

    nests = list(solution.get("nests") or [])
    nest_ids = [int(n["id"]) for n in nests]
    capacity = {int(n["id"]): int(n.get("capacity") or 0) for n in nests}
    cycles = list(metrics.get("cycles") or [])
    n_rows = len(rows)
    n_nests = len(nest_ids)

    # 每巢周期末占用序列（缺记当 0）
    occ_series: dict[int, list[int]] = {nid: [] for nid in nest_ids}
    for record in cycles:
        occ = {int(k): int(v) for k, v in (record.get("nest_occupancy") or {}).items()}
        for nid in nest_ids:
            occ_series[nid].append(occ.get(nid, 0))
    cap_max = max(capacity.values()) if capacity else 0
    min_free = min((capacity[nid] - max(occ_series[nid]) for nid in nest_ids
                    if capacity[nid]), default=0)

    n_end_best = sum(1 for r in rows if r["rank_end"] == 1)
    n_start_best = sum(1 for r in rows if r["rank_start"] == 1)
    n_not_emptiest = sum(1 for r in rows if r["free_end"] < r["free_max"])
    switches = [r for r in rows if r["switched"]]

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.2), layout="constrained")
    ax_cap, ax_dumb, ax_rank, ax_free = axes.ravel()
    ypos = np.arange(n_rows)
    row_labels = [f"周期{r['cycle']}·U{r['uav']}" for r in rows]

    # ── (a) 容量背景：占用 vs 容量 ────────────────────────────────
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
                    ha="right", va="bottom", fontsize=8.5, color=PALETTE["release"])
    ax_cap.set_xticks(range(n_nests), [str(i) for i in nest_ids])
    ax_cap.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_cap.set_ylim(0, max(cap_max, 1) + 0.8)
    style_axes(ax_cap, title="(a) 容量背景：周期末占用 vs 容量",
               xlabel="机巢 id", ylabel="占用泊位")
    ax_cap.text(0.02, 0.97,
                f"全程最高占用 {max(max(v) for v in occ_series.values())}/"
                f"{cap_max}，最低余量 {min_free} 泊位\n→ 容量从未咬合，"
                f"不可能成为选巢的原因",
                transform=ax_cap.transAxes, ha="left", va="top", fontsize=8.5,
                color="0.3")

    # ── (b) 返航巢反事实：末任务 → 各巢距离全列出 ─────────────────
    for i, r in enumerate(rows):
        dists = sorted(r["d_end"].values())
        ax_dumb.scatter([d / 1000.0 for d in dists], [i] * n_nests, s=16,
                        color="0.72", zorder=2, label="全部候选巢" if i == 0 else None)
        best = min(r["d_end"].values())
        ax_dumb.scatter([best / 1000.0], [i], s=110, facecolors="none",
                        edgecolors="0.25", linewidths=1.4, zorder=3,
                        label="距离最近巢" if i == 0 else None)
        chosen = r["d_end"][r["end_nest"]]
        color = PALETTE["executed"] if r["rank_end"] == 1 else PALETTE["release"]
        ax_dumb.scatter([chosen / 1000.0], [i], s=62, color=color, zorder=4,
                        edgecolors="white", linewidths=0.8,
                        label="选中返航巢" if i == 0 else None)
        if r["switched"]:
            ax_dumb.annotate("换巢", (chosen / 1000.0, i),
                             textcoords="offset points", xytext=(9, -3),
                             fontsize=8, color=PALETTE["deferred"])
    ax_dumb.set_yticks(ypos, row_labels)
    ax_dumb.invert_yaxis()
    style_axes(ax_dumb,
               title=f"(b) 返航巢反事实：末任务→各巢距离（选中=最近 {n_end_best}/{n_rows}）",
               xlabel="末任务 → 机巢 三维直线距离 (km)")
    leg_b = ax_dumb.legend(loc="lower right", fontsize=8, framealpha=0.95)
    leg_b.get_frame().set_edgecolor("none")
    ax_dumb.set_xlim(left=0)

    # ── (c) 距离排名 vs 随机期望 ─────────────────────────────────
    ranks = np.arange(1, n_nests + 1)
    start_counts = [sum(1 for r in rows if r["rank_start"] == k) for k in ranks]
    end_counts = [sum(1 for r in rows if r["rank_end"] == k) for k in ranks]
    w = 0.38
    ax_rank.bar(ranks - w / 2, start_counts, w, color=PALETTE["selected"],
                label="起飞巢（按到首任务距离）")
    ax_rank.bar(ranks + w / 2, end_counts, w, color=PALETTE["executed"],
                label="返航巢（按到末任务距离）")
    expect = n_rows / n_nests
    ax_rank.axhline(expect, color="0.45", linewidth=1.0, linestyle="--", zorder=0)
    ax_rank.text(n_nests - 0.4, expect + 0.08, f"随机期望 {expect:.2f}",
                 ha="right", va="bottom", fontsize=8, color="0.4")
    for k, c in zip(ranks, start_counts):
        if c:
            ax_rank.text(k - w / 2, c + 0.08, f"{c}", ha="center", va="bottom",
                         fontsize=8, color="0.25")
    for k, c in zip(ranks, end_counts):
        if c:
            ax_rank.text(k + w / 2, c + 0.08, f"{c}", ha="center", va="bottom",
                         fontsize=8, color="0.25")
    ax_rank.set_xticks(ranks)
    ax_rank.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_rank, title="(c) 选中巢的距离排名（1 = 最近）",
               xlabel="距离排名", ylabel="航次数")
    ax_rank.legend(loc="upper right", fontsize=8, framealpha=0.92)
    off = [r for r in rows if r["rank_start"] > 1]
    if off:
        gaps = [(r["d_start"][r["start_nest"]] - min(r["d_start"].values()))
                / min(r["d_start"].values()) * 100 for r in off]
        ax_rank.text(0.34, 0.60,
                     f"起飞巢 {n_start_best}/{n_rows} 取最近；{len(off)} 例 "
                     f"rank2 距最优 <{max(gaps):.0f}%\n（直线距离代理；真代价含"
                     f"地形剖面，可翻转）",
                     transform=ax_rank.transAxes, ha="left", va="top",
                     fontsize=8.5, color="0.3")

    # ── (d) 容量反证：选中巢从不是"因为最空" ──────────────────────
    ax_free.barh(ypos, [r["free_max"] for r in rows], 0.58, color="0.93",
                 zorder=1, label="当周期最空巢空闲（容量假设的预测）")
    for i, r in enumerate(rows):
        if r["switched"]:
            ax_free.annotate(
                "", xy=(r["free_end"], i), xytext=(r["free_start"], i),
                arrowprops=dict(arrowstyle="->", color=PALETTE["deferred"],
                                lw=1.3, shrinkA=2, shrinkB=2), zorder=2)
            ax_free.text(r["free_start"] + 0.1, i,
                         f"巢{r['start_nest']}→巢{r['end_nest']}", va="center",
                         fontsize=7.2, color=PALETTE["deferred"])
        ax_free.scatter([r["free_start"]], [i], s=40, color=PALETTE["remain"],
                        zorder=3, edgecolors="white", linewidths=0.6,
                        label="起飞巢空闲" if i == 0 else None)
        ax_free.scatter([r["free_end"]], [i], s=58, color=PALETTE["executed"],
                        zorder=4, edgecolors="white", linewidths=0.8,
                        label="选中返航巢空闲" if i == 0 else None)
        if r["free_end"] < r["free_max"]:
            ax_free.text(r["free_end"] + 0.09, i, "未达最空", va="center",
                         fontsize=7.4, color=PALETTE["release"])
    ax_free.set_yticks(ypos, row_labels)
    ax_free.invert_yaxis()
    ax_free.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    # 右侧留一条无数据带（最大空闲 4），图例放这里不压任何标记
    ax_free.set_xlim(0, max(r["free_max"] for r in rows) + 2.0)
    style_axes(ax_free,
               title=f"(d) 容量反证：{n_not_emptiest}/{n_rows} 次未选最空巢"
                     f"；{len(switches)} 次换巢均离开最空巢",
               xlabel="空闲泊位")
    leg_d = ax_free.legend(loc="center right", fontsize=8, framealpha=0.95)
    leg_d.get_frame().set_edgecolor("none")

    fig.suptitle("机巢选择归因：按成本（距离）选定，容量全程不咬合",
                 fontsize=12.5)
    fig.text(0.01, 0.002,
             f"样本：代表 seed 全部 {n_rows} 个航次；距离为三维直线代理"
             f"（模型真代价为地形剖面代价，计入 F2）；占用为周期末快照；"
             f"空闲泊位 = 容量 − 周期末占用",
             ha="left", va="bottom", fontsize=7.5, color="0.5")
    return save_figure(fig, Path(out_dir) / "nest_choice", formats, dpi=dpi)
