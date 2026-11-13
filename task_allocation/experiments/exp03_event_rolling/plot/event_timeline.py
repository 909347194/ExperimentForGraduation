# -*- coding: utf-8 -*-
"""R07_event_timeline — 事件注入时间线示意。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from .theme import FACTOR_LABELS


def plot_event_timeline(all_summaries: dict, fig_dir: Path) -> str:
    fig, ax = plt.subplots(figsize=(12, 4))

    event_markers = {
        "E1": [(6, "UAV故障", "#FF5722")],
        "E2": [(6, "突发高优", "#4CAF50")],
        "E3": [(6, "机巢闭塞", "#FF9800")],
        "E4": [(6, "UAV故障", "#FF5722"), (10, "机巢闭塞", "#FF9800")],
    }

    factors = sorted(all_summaries.keys())
    y_positions = {f: i for i, f in enumerate(factors)}

    for f in factors:
        y = y_positions[f]
        ax.axhline(y=y, color="#E0E0E0", linewidth=30, alpha=0.5)
        for cycle, label, color in event_markers.get(f, []):
            ax.scatter(cycle, y, s=200, color=color, zorder=5, edgecolors="white", linewidth=2)
            ax.annotate(label, (cycle, y), textcoords="offset points",
                        xytext=(0, 15), ha="center", fontsize=9, color=color)

    ax.set_yticks(list(y_positions.values()))
    ax.set_yticklabels([FACTOR_LABELS.get(f, f) for f in factors])
    ax.set_xlabel("滚动周期", fontsize=12)
    ax.set_xlim(0, 21)
    ax.set_title("事件注入时间线", fontsize=14, fontweight="bold")
    ax.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    name = "R07_event_timeline.png"
    fig.savefig(fig_dir / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return name
