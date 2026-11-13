# -*- coding: utf-8 -*-
"""R06_summary_table — 汇总表格。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from .theme import FACTOR_COLORS, FACTOR_LABELS


def plot_summary_table(all_summaries: dict, fig_dir: Path) -> str:
    factors = sorted(all_summaries.keys())
    headers = ["因子", "完成率", "按时率", "事件数", "重规划", "故障释放", "强制纳入", "终点变更"]

    cell_data = []
    for f in factors:
        s = all_summaries[f]
        cell_data.append([
            FACTOR_LABELS.get(f, f),
            f"{s.get('completion_rate', {}).get('mean', 0):.1%}",
            f"{s.get('on_time_rate', {}).get('mean', 0):.1%}",
            f"{s.get('n_event_occurrences', {}).get('mean', 0):.1f}",
            f"{s.get('n_replan_cycles', {}).get('mean', 0):.1f}",
            f"{s.get('n_released_by_fault', {}).get('mean', 0):.1f}",
            f"{s.get('n_forced_in', {}).get('mean', 0):.1f}",
            f"{s.get('n_end_nest_changed', {}).get('mean', 0):.1f}",
        ])

    fig, ax = plt.subplots(figsize=(14, 3 + len(factors) * 0.5))
    ax.axis("off")

    table = ax.table(cellText=cell_data, colLabels=headers, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.5)

    for j in range(len(headers)):
        table[0, j].set_facecolor("#3F51B5")
        table[0, j].set_text_props(color="white", fontweight="bold")

    for i, f in enumerate(factors):
        color = FACTOR_COLORS.get(f, "#666") + "22"
        for j in range(len(headers)):
            table[i + 1, j].set_facecolor(color)

    ax.set_title("exp03_event_rolling 因子对比汇总表", fontsize=14, fontweight="bold", pad=20)

    plt.tight_layout()
    name = "R06_summary_table.png"
    fig.savefig(fig_dir / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return name
