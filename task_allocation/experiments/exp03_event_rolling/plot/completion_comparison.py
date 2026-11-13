# -*- coding: utf-8 -*-
"""R02_completion_comparison — 完成率 / 按时率对比。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from .theme import FACTOR_COLORS


def plot_completion_comparison(all_summaries: dict, fig_dir: Path) -> str:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    factors = sorted(all_summaries.keys())
    colors = [FACTOR_COLORS.get(f, "#666") for f in factors]

    # 完成率
    rates = [all_summaries[f].get("completion_rate", {}).get("mean", 0) for f in factors]
    stds = [all_summaries[f].get("completion_rate", {}).get("std", 0) for f in factors]
    bars = axes[0].bar(factors, rates, yerr=stds, color=colors, edgecolor="white",
                       linewidth=1.5, capsize=5, error_kw={"linewidth": 1.5})
    axes[0].set_title("任务完成率对比", fontsize=14, fontweight="bold")
    axes[0].set_ylabel("完成率")
    axes[0].set_ylim(0, 1.1)
    for bar, v in zip(bars, rates):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                     f"{v:.1%}", ha="center", va="bottom", fontsize=11)

    # 按时率
    rates2 = [all_summaries[f].get("on_time_rate", {}).get("mean", 0) for f in factors]
    stds2 = [all_summaries[f].get("on_time_rate", {}).get("std", 0) for f in factors]
    bars = axes[1].bar(factors, rates2, yerr=stds2, color=colors, edgecolor="white",
                       linewidth=1.5, capsize=5, error_kw={"linewidth": 1.5})
    axes[1].set_title("按时交付率对比", fontsize=14, fontweight="bold")
    axes[1].set_ylabel("按时率")
    axes[1].set_ylim(0, 1.1)
    for bar, v in zip(bars, rates2):
        axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                     f"{v:.1%}", ha="center", va="bottom", fontsize=11)

    plt.tight_layout()
    name = "R02_completion_comparison.png"
    fig.savefig(fig_dir / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return name
