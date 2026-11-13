# -*- coding: utf-8 -*-
"""R03_replan_comparison — 事件响应指标：故障释放 / 强制纳入 / 终点变更。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .theme import FACTOR_LABELS


def plot_replan_comparison(all_summaries: dict, fig_dir: Path) -> str:
    fig, ax = plt.subplots(figsize=(10, 5))
    factors = sorted(all_summaries.keys())
    x = np.arange(len(factors))
    width = 0.25

    released = [all_summaries[f].get("n_released_by_fault", {}).get("mean", 0) for f in factors]
    forced = [all_summaries[f].get("n_forced_in", {}).get("mean", 0) for f in factors]
    end_nest = [all_summaries[f].get("n_end_nest_changed", {}).get("mean", 0) for f in factors]

    ax.bar(x - width, released, width, label="故障释放任务", color="#FF5722", edgecolor="white")
    ax.bar(x, forced, width, label="强制纳入任务", color="#4CAF50", edgecolor="white")
    ax.bar(x + width, end_nest, width, label="终点巢变更", color="#FF9800", edgecolor="white")

    ax.set_title("事件响应指标对比", fontsize=14, fontweight="bold")
    ax.set_ylabel("次数")
    ax.set_xticks(x)
    ax.set_xticklabels([FACTOR_LABELS.get(f, f) for f in factors], fontsize=10)
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    name = "R03_replan_comparison.png"
    fig.savefig(fig_dir / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return name
