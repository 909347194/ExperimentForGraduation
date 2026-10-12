# -*- coding: utf-8 -*-
"""图 ``seed_summary``——多 seed 复现一致性：结论是否稳健。

【report 层 · 结论与论文插图】

当实验以多个随机种子重复（``experiment.seeds``）时，本图给出关键指标的
**逐种子柱状 + 均值线 + ±标准差带**，直观回答「结果是否稳健」：

    (a) 完成率         completion_rate（%）
    (b) 总代价         total_cost（km）
    (c) 总收益         total_reward

柱为各 seed 单次运行值，虚线为跨 seed 均值，阴影带为均值 ± 总体标准差
（与 ``results/metrics.json`` 的 ``seed_summary`` 一致）。单 seed 实验无此图。

数据来源：``results/metrics.json`` 的 ``per_seed`` / ``seed_summary`` / ``seeds``。

统一签名（见 ``plot/registry.py``）：即使本图用不到 ``solution``，也按
``(metrics, solution, out_dir, formats, *, dpi)`` 收参，好让调度端一处通用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import cycle_colors, plt, save_figure, style_axes

__all__ = ["plot_seed_summary"]

#: (指标键, 显示名, 单位换算, 轴标签)
_PANELS = (
    ("completion_rate", "完成率", 100.0, "完成率 (%)"),
    ("total_cost", "总代价", 1.0 / 1000.0, "总代价 (km)"),
    ("total_reward", "总收益", 1.0, "总收益"),
)


def plot_seed_summary(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    seeds = metrics.get("seeds") or []
    per_seed = metrics.get("per_seed") or {}
    seed_summary = metrics.get("seed_summary") or {}
    if len(seeds) < 2 or not per_seed:
        return []

    labels = [str(s) for s in seeds]
    colors = cycle_colors(len(seeds))
    x = np.arange(len(seeds))

    fig, axes = plt.subplots(1, len(_PANELS), figsize=(11.5, 4.2))

    for ax, (key, name, scale, ylabel) in zip(axes, _PANELS):
        vals = np.array(
            [float((per_seed.get(str(s), {}).get("summary", {}) or {}).get(key, 0.0))
             for s in seeds],
            dtype=float,
        ) * scale
        ss = seed_summary.get(key, {})
        mean = float(ss.get("mean", 0.0)) * scale
        std = float(ss.get("std", 0.0)) * scale

        ax.bar(x, vals, 0.6, color=colors, edgecolor="white", linewidth=1.0)
        ax.axhline(mean, color="#1f2933", linewidth=1.6, linestyle="--",
                   label=f"均值 {mean:.2f}")
        if std > 0:
            ax.axhspan(mean - std, mean + std, color="#9aa5b1", alpha=0.28,
                       label=f"±σ {std:.2f}")

        style_axes(ax, title=f"{name}", ylabel=ylabel)
        ax.set_xticks(x, labels)
        ax.set_ylim(bottom=0)
        ax.legend(loc="best", fontsize=8)

    fig.suptitle(
        f"exp01_smoke：多 seed 复现（{len(seeds)} 次，seed={seeds}）",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))

    stem = Path(out_dir) / "seed_summary"
    return save_figure(fig, stem, formats, dpi=dpi)
