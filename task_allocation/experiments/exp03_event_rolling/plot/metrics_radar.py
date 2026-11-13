# -*- coding: utf-8 -*-
"""R05_metrics_radar — 多指标雷达图。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .theme import FACTOR_COLORS, FACTOR_LABELS


def plot_metrics_radar(all_summaries: dict, fig_dir: Path) -> str:
    metrics = ["completion_rate", "on_time_rate", "n_replan_cycles",
               "n_forced_in", "n_end_nest_changed", "n_released_by_fault"]
    labels = ["完成率", "按时率", "重规划", "强制纳入", "终点变更", "故障释放"]
    factors = sorted(all_summaries.keys())
    n = len(metrics)

    raw = {f: [all_summaries[f].get(m, {}).get("mean", 0) for m in metrics] for f in factors}
    max_vals = [max(raw[f][i] for f in factors) or 1 for i in range(n)]
    norm = {f: [raw[f][i] / max_vals[i] for i in range(n)] for f in factors}

    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    for f in factors:
        vals = norm[f] + norm[f][:1]
        color = FACTOR_COLORS.get(f, "#666")
        ax.plot(angles, vals, "o-", linewidth=2, label=FACTOR_LABELS.get(f, f), color=color)
        ax.fill(angles, vals, alpha=0.1, color=color)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_title("多指标雷达图", fontsize=14, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))

    plt.tight_layout()
    name = "R05_metrics_radar.png"
    fig.savefig(fig_dir / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return name
