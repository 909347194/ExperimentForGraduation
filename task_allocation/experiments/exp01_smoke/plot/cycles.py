# -*- coding: utf-8 -*-
"""exp01_smoke 图 1：周期指标面板 ``cycle_metrics``。

2×2 四联图，回答「滚动闭环是否按预期推进」：

    (a) 任务池水位与流转  —— T_t 的 remain / new / release 构成，叠加 T_sel、T_exec
    (b) 双目标值          —— F1 收益（柱）与 F2 代价（折线，右轴）
    (c) 机巢泊位占用      —— 周期 × 机巢 热力图，验证始终不超容
    (d) 求解开销          —— 求解耗时（柱）与适应度评估次数（折线，右轴）

数据来源：``results/metrics.json`` 的 ``cycles[]`` / ``problem`` / ``nests``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_cycle_metrics"]


def _series(cycles: Sequence[dict[str, Any]], getter) -> list[float]:
    return [float(getter(c) or 0.0) for c in cycles]


def plot_cycle_metrics(
    metrics: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png", "pdf"),
    *,
    dpi: int = 200,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    if not cycles:
        return []

    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    x = np.arange(len(cycles))
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.2))
    ax_pool, ax_obj, ax_nest, ax_cost = axes.ravel()

    # ── (a) 任务池水位与流转 ─────────────────────────────────────
    remain = _series(cycles, lambda c: (c.get("pool_counts") or {}).get("n_remain"))
    new = _series(cycles, lambda c: (c.get("pool_counts") or {}).get("n_new"))
    release = _series(cycles, lambda c: (c.get("pool_counts") or {}).get("n_release"))

    ax_pool.bar(x, remain, 0.55, label="滞留 remain", color=PALETTE["remain"])
    ax_pool.bar(x, new, 0.55, bottom=remain, label="新到 new", color=PALETTE["new"])
    bottom = np.add(remain, new)
    ax_pool.bar(
        x, release, 0.55, bottom=bottom, label="释放 release", color=PALETTE["release"]
    )

    selected = _series(cycles, lambda c: (c.get("selection") or {}).get("n_selected"))
    executed = _series(cycles, lambda c: (c.get("muas") or {}).get("n_executed"))
    ax_pool.plot(x, selected, "o-", color=PALETTE["selected"], label="T_t^sel 选中")
    ax_pool.plot(x, executed, "s--", color=PALETTE["executed"], label="T_t^exec 执行")

    style_axes(ax_pool, title="(a) 任务池水位与流转", ylabel="任务数")
    ax_pool.set_xticks(x, labels)
    ax_pool.legend(ncol=2, loc="upper right")
    ax_pool.set_ylim(bottom=0)

    # ── (b) 双目标值 ────────────────────────────────────────────
    f1 = _series(cycles, lambda c: (c.get("muas") or {}).get("f1_reward"))
    f2_km = [
        v / 1000.0 for v in _series(cycles, lambda c: (c.get("muas") or {}).get("f2_cost"))
    ]
    ax_obj.bar(x, f1, 0.55, color=PALETTE["reward"], label="F1 收益")
    style_axes(ax_obj, title="(b) 双目标值", ylabel="F1 收益")
    ax_obj.set_xticks(x, labels)
    ax_obj.set_ylim(bottom=0)

    ax_obj2 = ax_obj.twinx()
    ax_obj2.plot(x, f2_km, "o-", color=PALETTE["cost"], label="F2 代价")
    ax_obj2.set_ylabel("F2 代价 (km)")
    ax_obj2.grid(False)
    ax_obj2.set_ylim(bottom=0)

    handles1, labels1 = ax_obj.get_legend_handles_labels()
    handles2, labels2 = ax_obj2.get_legend_handles_labels()
    ax_obj.legend(handles1 + handles2, labels1 + labels2, loc="upper left")

    # ── (c) 机巢泊位占用热力图 ──────────────────────────────────
    nests = metrics.get("nests") or []
    nest_ids = [str(n.get("id")) for n in nests]
    capacity = float((metrics.get("problem") or {}).get("nest_capacity") or 0.0)

    grid = np.zeros((len(cycles), max(len(nest_ids), 1)))
    for i, c in enumerate(cycles):
        occ = c.get("nest_occupancy") or {}
        for j, nid in enumerate(nest_ids):
            grid[i, j] = float(occ.get(nid, 0))

    vmax = max(grid.max(), capacity, 1.0)
    im = ax_nest.imshow(grid, cmap="Blues", vmin=0, vmax=vmax, aspect="auto")
    ax_nest.set_xticks(np.arange(len(nest_ids)), nest_ids)
    ax_nest.set_yticks(np.arange(len(cycles)), labels)
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            v = grid[i, j]
            ax_nest.text(
                j, i, f"{v:.0f}",
                ha="center", va="center", fontsize=8,
                color="white" if v > vmax * 0.6 else "#1f2933",
            )
    style_axes(ax_nest, title=f"(c) 机巢泊位占用（容量 {capacity:.0f}）", xlabel="机巢 id")
    ax_nest.grid(False)
    fig.colorbar(im, ax=ax_nest, fraction=0.046, pad=0.03, label="占用泊位")

    # ── (d) 求解开销 ────────────────────────────────────────────
    solve_ms = [
        v * 1000.0
        for v in _series(cycles, lambda c: (c.get("muas") or {}).get("solve_seconds"))
    ]
    evals = _series(cycles, lambda c: (c.get("muas") or {}).get("n_evals"))
    ax_cost.bar(x, solve_ms, 0.55, color=PALETTE["solve"], label="求解耗时")
    style_axes(ax_cost, title="(d) 求解开销", ylabel="求解耗时 (ms)")
    ax_cost.set_xticks(x, labels)
    ax_cost.set_ylim(bottom=0)

    ax_cost2 = ax_cost.twinx()
    ax_cost2.plot(x, evals, "o-", color=PALETTE["evals"], label="适应度评估次数")
    ax_cost2.set_ylabel("评估次数")
    ax_cost2.grid(False)
    ax_cost2.set_ylim(bottom=0)

    handles1, labels1 = ax_cost.get_legend_handles_labels()
    handles2, labels2 = ax_cost2.get_legend_handles_labels()
    ax_cost.legend(handles1 + handles2, labels1 + labels2, loc="upper left")

    fig.suptitle("exp01_smoke：动态滚动周期指标", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    stem = Path(out_dir) / "cycle_metrics"
    return save_figure(fig, stem, formats, dpi=dpi)
