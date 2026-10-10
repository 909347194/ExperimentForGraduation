# -*- coding: utf-8 -*-
"""exp01_smoke 图 2：动态任务选择漏斗 ``selection_funnel``。

左：各周期逐级筛选的任务数（T_t → 优先级 → 预筛选 → 边际收益 → T_sel → T_exec）；
右：各级相对 T_t 的保留率。

用于验证论文 §3 的两阶段选择链路：候选规模被 α·K_avail 截断、
边际收益阶段继续收敛、且 T_t^exec ⊆ T_t^sel ⊆ T_t。

数据来源：``results/metrics.json`` 的 ``cycles[].selection`` 与 ``cycles[].muas``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .theme import PALETTE, cycle_colors, plt, save_figure, style_axes

__all__ = ["plot_selection_funnel"]

#: (键, 显示名, 所属 dict)
STAGES = (
    ("n_tasks_in", "T_t 到达", "selection"),
    ("n_after_priority", "优先级排序", "selection"),
    ("n_after_prefilter", "预筛选 α·K", "selection"),
    ("n_after_marginal", "边际收益", "selection"),
    ("n_selected", "T_t^sel 选中", "selection"),
    ("n_executed", "T_t^exec 执行", "muas"),
)


def _stage_value(cycle: dict[str, Any], key: str, section: str) -> float:
    src = cycle.get(section) or {}
    v = src.get(key)
    return float(v) if v is not None else 0.0


def plot_selection_funnel(
    metrics: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png", "pdf"),
    *,
    dpi: int = 200,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    if not cycles:
        return []

    stage_names = [name for _, name, _ in STAGES]
    colors = cycle_colors(len(cycles))
    labels = [f"周期 {c.get('cycle')}" for c in cycles]

    fig, (ax_funnel, ax_rate) = plt.subplots(1, 2, figsize=(11.5, 4.4))

    # ── 左：逐级筛选任务数 ──────────────────────────────────────
    xs = np.arange(len(STAGES))
    width = 0.8 / max(len(cycles), 1)
    for i, c in enumerate(cycles):
        values = [_stage_value(c, key, sec) for key, _, sec in STAGES]
        offset = (i - (len(cycles) - 1) / 2.0) * width
        ax_funnel.bar(
            xs + offset, values, width * 0.92,
            label=labels[i], color=colors[i],
        )

    style_axes(ax_funnel, title="(a) 选择漏斗：逐级筛选任务数", ylabel="任务数")
    ax_funnel.set_xticks(xs, stage_names, rotation=18, ha="right")
    ax_funnel.set_ylim(bottom=0)
    ax_funnel.legend(loc="upper right")
    ax_funnel.axvspan(-0.5, 3.5, color=PALETTE["grid"], alpha=0.18, zorder=0)
    ax_funnel.text(
        1.5, ax_funnel.get_ylim()[1] * 0.96, "第一阶段：动态任务选择",
        ha="center", va="top", fontsize=8.5, color="#52606d",
    )

    # ── 右：各级保留率 ─────────────────────────────────────────
    for i, c in enumerate(cycles):
        base = _stage_value(c, "n_tasks_in", "selection")
        if base <= 0:
            continue
        rates = [_stage_value(c, key, sec) / base * 100.0 for key, _, sec in STAGES]
        ax_rate.plot(xs, rates, "o-", color=colors[i], label=labels[i])

    style_axes(ax_rate, title="(b) 各级保留率（相对 T_t）", ylabel="保留率 (%)")
    ax_rate.set_xticks(xs, stage_names, rotation=18, ha="right")
    ax_rate.set_ylim(0, 105)
    ax_rate.legend(loc="lower left")

    fig.suptitle("exp01_smoke：动态任务选择漏斗", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))

    stem = Path(out_dir) / "selection_funnel"
    return save_figure(fig, stem, formats, dpi=dpi)
