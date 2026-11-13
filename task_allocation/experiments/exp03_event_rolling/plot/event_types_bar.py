# -*- coding: utf-8 -*-
"""R04_event_types_bar — 事件类型分布（堆叠柱状图）。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .theme import FACTOR_LABELS


def plot_event_types_bar(all_summaries: dict, fig_dir: Path) -> str:
    fig, ax = plt.subplots(figsize=(10, 5))
    factors = sorted(all_summaries.keys())
    types = ["uav_fault", "urgent_task", "nest_unavailable"]
    type_labels = ["UAV故障", "突发高优", "机巢闭塞"]
    type_colors = ["#FF5722", "#4CAF50", "#FF9800"]

    x = np.arange(len(factors))
    bottom = np.zeros(len(factors))

    for t, label, color in zip(types, type_labels, type_colors):
        vals = [all_summaries[f].get("event_types", {}).get(t, 0) for f in factors]
        ax.bar(x, vals, bottom=bottom, label=label, color=color, edgecolor="white")
        bottom += np.array(vals)

    ax.set_title("事件类型分布", fontsize=14, fontweight="bold")
    ax.set_ylabel("事件次数")
    ax.set_xticks(x)
    ax.set_xticklabels([FACTOR_LABELS.get(f, f) for f in factors], fontsize=10)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    name = "R04_event_types.png"
    fig.savefig(fig_dir / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return name
