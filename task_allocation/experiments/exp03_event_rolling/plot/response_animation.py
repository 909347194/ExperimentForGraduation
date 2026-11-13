# -*- coding: utf-8 -*-
"""R08_response_animation — 事件响应动态 GIF。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

from .theme import FACTOR_COLORS, FACTOR_LABELS


def plot_response_animation(all_summaries: dict, fig_dir: Path) -> str:
    factors = sorted(all_summaries.keys())
    n_frames = 20

    fig, ax = plt.subplots(figsize=(10, 6))

    def _update(frame):
        ax.clear()
        cycle = frame + 1

        for i, f in enumerate(factors):
            color = FACTOR_COLORS.get(f, "#666")
            y = len(factors) - i - 1
            progress = cycle / n_frames
            ax.barh(y, progress, height=0.6, color=color, alpha=0.7,
                    edgecolor="white", linewidth=1.5)

            ev_markers = {"E1": [6], "E2": [6], "E3": [6], "E4": [6, 10]}
            for ev_cycle in ev_markers.get(f, []):
                if ev_cycle <= cycle:
                    ax.scatter(ev_cycle / n_frames, y, s=150, color="red",
                               zorder=5, edgecolors="white", linewidth=2, marker="*")

            ax.text(-0.02, y, FACTOR_LABELS.get(f, f), ha="right", va="center",
                    fontsize=11, transform=ax.get_yaxis_transform())

        ax.set_xlim(0, 1.15)
        ax.set_ylim(-0.5, len(factors) - 0.5)
        ax.set_xlabel("周期进度", fontsize=12)
        ax.set_title(f"事件响应动态 — 周期 {cycle}/{n_frames}", fontsize=14, fontweight="bold")
        ax.set_yticks([])
        ax.grid(axis="x", alpha=0.3)

    anim = FuncAnimation(fig, _update, frames=n_frames, interval=500, repeat=True)
    name = "R08_response_animation.gif"
    anim.save(fig_dir / name, writer=PillowWriter(fps=2), dpi=100)
    plt.close(fig)
    return name
