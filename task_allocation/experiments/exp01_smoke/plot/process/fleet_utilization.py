# -*- coding: utf-8 -*-
"""图 ``fleet_utilization``——机队利用率与机巢压力。

【process 层 · 运行过程观测】

process 层回答「滚动闭环到底怎么跑的」。本图盯**资源侧**：机队有多大、
多少架真的在飞、机巢挤不挤——与 ``task_flow``（任务侧）互为镜像。

    可用 UAV     每周期可用数（含故障/充电扣除）
    编码参与     DMDE 解里出现的 UAV 数（分配决策涉及）
    实际出动     真正执行了航次的 UAV 数（tours 去重）
    机巢占用     周期末各巢已占泊位，对照容量看余量

「编码参与」与「实际出动」的差是本图要暴露的第一个诊断量：模型里
分配了很多 UAV，不等于很多 UAV 真的离巢——只看前者会高估机队压力。

数据来源：``metrics.json`` 的 ``cycles[].muas``（n_uav_avail /
n_participating）、``cycles[].nest_occupancy``、``fleet.stats``、``summary``，
以及 ``solution.json`` 的 ``cycles[].tours[].uav_id`` 与 ``nests[].capacity``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_fleet_utilization"]


def _dispatched_per_cycle(solution: dict[str, Any],
                          cycles: Sequence[dict[str, Any]]) -> list[int]:
    """每周期「实际出动」的 UAV 数（tours 按 ``uav_id`` 去重）。

    没有 tours 数据时（仅 metrics 落盘）返回全 0，由调用方决定是否画。
    """
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


def plot_fleet_utilization(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """画 2×2 机队面板；无 cycles 时返回 []。"""
    cycles = list(metrics.get("cycles") or [])
    if not cycles:
        return []

    n = len(cycles)
    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    x = np.arange(n)

    avail = [int((c.get("muas") or {}).get("n_uav_avail") or 0) for c in cycles]
    coded = [int((c.get("muas") or {}).get("n_participating") or 0) for c in cycles]
    dispatched = _dispatched_per_cycle(solution, cycles)
    k_mean = float(metrics.get("summary", {}).get("K_avail_mean") or 0.0)

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0), layout="constrained")
    ax_part, ax_freq, ax_nest, ax_state = axes.ravel()

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
                         ha="center", va="bottom", fontsize=8, color="0.25")
    if k_mean:
        ax_part.axhline(k_mean, color="0.45", linewidth=0.9, linestyle="--",
                        zorder=0)
        ax_part.text(n - 0.45, k_mean + 0.12, f"K_avail 均值 {k_mean:.0f}",
                     ha="right", va="bottom", fontsize=8, color="0.4")
    ax_part.set_xticks(x, labels)
    ax_part.set_ylim(0, max(max(avail), max(coded), 1) * 1.22)
    ax_part.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_part, title="(a) 每周期 UAV 参与（三口径）", ylabel="UAV 数")
    ax_part.legend(loc="upper right", ncol=3, framealpha=0.92)

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
    ax_freq.barh(ypos, counts, 0.62, color=colors, edgecolor="white",
                 linewidth=0.6)
    for yi, c in zip(ypos, counts):
        ax_freq.text(c + 0.04, yi, f"{c}", va="center", fontsize=8,
                     color="0.25")
    ax_freq.set_yticks(ypos, [f"UAV {u}" for u in all_uavs])
    ax_freq.invert_yaxis()
    ax_freq.set_xlim(0, max(counts + [1]) + 0.6)
    ax_freq.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    n_idle_fleet = sum(1 for c in counts if c == 0)
    style_axes(ax_freq, title=f"(b) 各 UAV 出动频次（{n_idle_fleet}/{len(all_uavs)} "
                              f"架全程未出动）", xlabel="出动周期数")
    ax_freq.text(0.98, 0.03, "灰 = 全程待命", transform=ax_freq.transAxes,
                 ha="right", va="bottom", fontsize=8, color="0.45")

    # ── (c) 机巢占用热力图 ─────────────────────────────────────────
    nest_ids: list[int] = []
    capacity = {int(nest.get("id")): int(nest.get("capacity") or 0)
                for nest in solution.get("nests") or []}
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
    im = ax_nest.imshow(occ, aspect="auto", cmap="YlOrRd",
                        vmin=0, vmax=max(cap_max, 1))
    ax_nest.set_xticks(range(len(nest_ids)), [str(i) for i in nest_ids])
    ax_nest.set_yticks(range(n), labels)
    for i in range(n):
        for j in range(len(nest_ids)):
            cap = capacity.get(nest_ids[j], 0)
            over = cap and occ[i, j] >= cap
            ax_nest.text(j, i, f"{occ[i, j]:.0f}/{cap}",
                         ha="center", va="center", fontsize=8,
                         color="white" if over else "0.25",
                         fontweight="bold" if over else "normal")
    ax_nest.set_xticks(np.arange(-0.5, len(nest_ids), 1), minor=True)
    ax_nest.set_yticks(np.arange(-0.5, n, 1), minor=True)
    ax_nest.grid(which="minor", color="white", linewidth=1.4)
    ax_nest.tick_params(which="minor", length=0)
    style_axes(ax_nest, title="(c) 周期末机巢占用 / 容量（泊位）",
               xlabel="机巢 id")
    fig.colorbar(im, ax=ax_nest, shrink=0.82, pad=0.02)

    # ── (d) 机队状态构成（初始快照）+ 结论 ─────────────────────────
    stats = (metrics.get("fleet") or {}).get("stats") or {}
    parts = [("idle 待命", stats.get("n_idle"), PALETTE["remain"]),
             ("busy 任务中", stats.get("n_busy"), PALETTE["executed"]),
             ("charging 充电", stats.get("n_charging"), PALETTE["deferred"]),
             ("fault 故障", stats.get("n_fault"), PALETTE["release"])]
    parts = [(name, int(v or 0), color) for name, v, color in parts if v is not None]
    total = sum(v for _, v, _ in parts) or 1
    left = 0.0
    for name, val, color in parts:
        ax_state.barh(0, val, 0.5, left=left, color=color, edgecolor="white",
                      linewidth=0.8, label=f"{name}：{val}")
        if val:
            ax_state.text(left + val / 2, 0, f"{val}", ha="center",
                          va="center", fontsize=9,
                          color="white" if color != PALETTE["remain"] else "0.25")
        left += val
    ax_state.set_xlim(0, total)
    ax_state.set_ylim(-0.6, 0.6)
    ax_state.set_yticks([])
    ax_state.set_xlabel("UAV 数")
    style_axes(ax_state,
               title=f"(d) 初始机队状态构成（t={((metrics.get('fleet') or {}).get('current_time') or 0):.0f}s）")
    ax_state.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=2,
                    fontsize=8.5, framealpha=0.92)
    note = (
        f"全程可用数恒为 {avail[0] if avail else 0} 架、无故障/充电事件"
        f"——机队规模不是瓶颈，出动率才是。"
    )
    ax_state.text(0.5, 0.86, note, transform=ax_state.transAxes, ha="center",
                  va="top", fontsize=8.5, color="0.35")

    fig.suptitle("机队利用率与机巢压力：多少飞机在飞、巢挤不挤",
                 fontsize=12.5)
    return save_figure(fig, Path(out_dir) / "fleet_utilization", formats,
                       dpi=dpi)
