# -*- coding: utf-8 -*-
"""exp01_smoke 图 4：DMDE 收敛曲线 ``convergence``。

左：各滚动周期的每代最优适应度（DMDE 最小化 ``-F1 + λ·F2 + penalty``，越低越好）；
右：归一化收敛（相对初代的改进百分比），用于比较不同周期的收敛速度是否一致。

数据来源：``results/solution.json`` 的 ``cycles[].cost_history``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .theme import cycle_colors, plt, save_figure, style_axes

__all__ = ["plot_convergence"]


def plot_convergence(
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png", "pdf"),
    *,
    dpi: int = 200,
) -> list[Path]:
    cycles = [c for c in (solution.get("cycles") or []) if c.get("cost_history")]
    if not cycles:
        return []

    colors = cycle_colors(len(cycles))
    fig, (ax_raw, ax_norm) = plt.subplots(1, 2, figsize=(11.5, 4.4))

    for idx, cyc in enumerate(cycles):
        hist = [float(v) for v in cyc["cost_history"]]
        gens = np.arange(len(hist))
        label = f"周期 {cyc.get('cycle')}"
        color = colors[idx]

        ax_raw.plot(gens, hist, "-", color=color, linewidth=1.8, label=label)

        # 相对初代的改进百分比：(f0 - fg) / (f0 - f_end)
        f0, f_end = hist[0], hist[-1]
        span = f0 - f_end
        if abs(span) > 1e-12:
            norm = [(f0 - v) / span * 100.0 for v in hist]
        else:
            norm = [0.0 for _ in hist]
        ax_norm.plot(gens, norm, "-", color=color, linewidth=1.8, label=label)

    style_axes(ax_raw, title="(a) DMDE 收敛曲线", xlabel="代数", ylabel="最优适应度")
    ax_raw.legend(loc="best")

    style_axes(ax_norm, title="(b) 归一化收敛", xlabel="代数", ylabel="相对初代改进 (%)")
    ax_norm.set_ylim(-4, 104)
    ax_norm.legend(loc="lower right")

    fig.suptitle("exp01_smoke：DMDE 收敛性", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))

    stem = Path(out_dir) / "convergence"
    return save_figure(fig, stem, formats, dpi=dpi)
