# -*- coding: utf-8 -*-
"""R01_event_overview — 事件总览：各因子事件数与重规划数。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from .theme import FACTOR_COLORS, FACTOR_LABELS


def plot_event_overview(all_summaries: dict, fig_dir: Path) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    factors = sorted(all_summaries.keys())
    colors = [FACTOR_COLORS.get(f, "#666") for f in factors]

    # 左：事件总数
    n_events = [all_summaries[f].get("n_event_occurrences", {}).get("mean", 0) for f in factors]
    bars = axes[0].bar(factors, n_events, color=colors, edgecolor="white", linewidth=1.5)
    axes[0].set_title("各因子事件触发次数", fontsize=14, fontweight="bold")
    axes[0].set_ylabel("事件次数")
    for bar, v in zip(bars, n_events):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                     f"{v:.1f}", ha="center", va="bottom", fontsize=11)

    # 右：重规划次数
    n_replan = [all_summaries[f].get("n_replan_cycles", {}).get("mean", 0) for f in factors]
    bars = axes[1].bar(factors, n_replan, color=colors, edgecolor="white", linewidth=1.5)
    axes[1].set_title("各因子重规划周期数", fontsize=14, fontweight="bold")
    axes[1].set_ylabel("重规划次数")
    for bar, v in zip(bars, n_replan):
        axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                     f"{v:.1f}", ha="center", va="bottom", fontsize=11)

    plt.tight_layout()
    name = "R01_event_overview.png"
    fig.savefig(fig_dir / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return name


if __name__ == "__main__":
    print("通过 plot.__init__.make_plots 调用")
