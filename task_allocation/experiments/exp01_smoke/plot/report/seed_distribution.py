# -*- coding: utf-8 -*-
"""图 ``seed_distribution``——跨 seed 分布：每个 seed 各自长什么样。

【report 层 · 结论与论文插图】

report 层回答「实验说明什么」。本图与 ``seed_summary`` 的分工：

    ``seed_summary``      3 个核心指标的 mean±σ 快速总览（轻量，一屏读完）
    本图                  **全部有均值的指标**逐一看分布，并保留每个 seed 的
                          个体值——5 个 seed 时 mean±σ 会藏信息（例如某指标
                          4 个 seed 相同、1 个离群，σ 看起来很小），散点让
                          离群 seed 直接可见

两代图并用：mean±σ 回答「平均表现」，个体散点回答「这个平均稳不稳」。

数据来源：``metrics.json`` 的 ``per_seed``（每个 seed 的 ``summary`` 与
``cycles[]``）、``seed_summary``（mean/std 参照）、``seeds``。
仅多 seed 运行产出；单 seed 运行时本图跳过。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import MONO_ZH, PALETTE, plt, save_figure, style_axes

__all__ = ["plot_seed_distribution"]

#: 小 multiples 指标表：(seed_summary 键, 显示名, 单位换算, 轴标签)
_PANELS: tuple[tuple[str, str, float, str], ...] = (
    ("completion_rate", "完成率", 100.0, "%"),
    ("cycle_feasible_rate", "周期解可行率", 100.0, "%"),
    ("avg_wait", "平均等待", 1.0, "s"),
    ("n_infeasible_delivery", "违约交付数", 1.0, "次"),
    ("total_reward", "总收益", 1.0, ""),
    ("solve_time_ms", "单周期求解耗时", 0.001, "s"),
)

#: 底部表格列：(summary 键, 显示名, 换算, 格式)
_TABLE_COLS: tuple[tuple[str, str, float, str], ...] = (
    ("completion_rate", "完成率%", 100.0, ".1f"),
    ("avg_wait", "等待s", 1.0, ".0f"),
    ("total_reward", "总收益", 1.0, ".1f"),
    ("cycle_feasible_rate", "可行率%", 100.0, ".1f"),
    ("n_infeasible_delivery", "违约", 1.0, ".1f"),
)


def _seed_values(per_seed: dict[str, Any], key: str, scale: float) -> list[float]:
    out = []
    for payload in per_seed.values():
        val = (payload.get("summary") or {}).get(key)
        if val is None:
            continue
        out.append(float(val) * scale)
    return out


def plot_seed_distribution(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """画「分布小 multiples + 逐 seed 演化 + 明细表」；非多 seed 返回 []。"""
    per_seed = metrics.get("per_seed") or {}
    if not per_seed:
        return []
    seeds = metrics.get("seeds") or sorted(int(s) for s in per_seed)
    seeds = [int(s) for s in seeds]
    seed_summary = metrics.get("seed_summary") or {}

    fig = plt.figure(figsize=(11.5, 9.8), layout="constrained")
    gs = fig.add_gridspec(3, 3, height_ratios=[1.0, 1.0, 1.15])
    ax_panels = [fig.add_subplot(gs[i // 3, i % 3]) for i in range(6)]
    ax_evolve = fig.add_subplot(gs[2, :2])
    ax_table = fig.add_subplot(gs[2, 2])

    # ── 上部：6 指标小 multiples（box + 个体散点 + mean±σ）─────────
    rng = np.random.default_rng(7)
    for ax, (key, name, scale, unit) in zip(ax_panels, _PANELS):
        vals = _seed_values(per_seed, key, scale)
        if not vals:
            ax.text(0.5, 0.5, "本批运行未产出该指标", transform=ax.transAxes,
                    ha="center", va="center", fontsize=9, color="0.5")
            style_axes(ax, title=f"{name}（无数据）")
            continue
        arr = np.asarray(vals, dtype=float)
        bp = ax.boxplot([arr], widths=0.42, patch_artist=True,
                        showmeans=True, meanline=False,
                        medianprops=dict(color="0.25", linewidth=1.2),
                        meanprops=dict(marker="D", markerfacecolor="white",
                                       markeredgecolor="0.25", markersize=5),
                        flierprops=dict(marker="o", markersize=4,
                                        markerfacecolor=PALETTE["release"],
                                        markeredgecolor="none"))
        for box in bp["boxes"]:
            box.set(facecolor=PALETTE["selected"], alpha=0.35,
                    edgecolor=PALETTE["selected"], linewidth=1.2)
        jitter = rng.uniform(-0.13, 0.13, len(arr))
        ax.scatter(np.ones(len(arr)) + jitter, arr, s=34, zorder=3,
                   color=PALETTE["new"], edgecolor="white", linewidth=0.7,
                   alpha=0.9)
        # 离群 seed 点名（与均值差 > 1.5σ）
        mean, std = float(arr.mean()), float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
        ref = (seed_summary.get(key) or {})
        if "mean" in ref:
            mean = float(ref["mean"]) * scale
            std = float(ref.get("std") or 0.0) * scale
        for xi, (seed, v) in enumerate(zip(seeds, arr)):
            if std and abs(v - mean) > 1.5 * std:
                ax.annotate(f"seed {seed}", (1 + jitter[xi], v),
                            textcoords="offset points", xytext=(8, 0),
                            fontsize=7.5, color=PALETTE["release"])
        ax.set_xticks([])
        lo, hi = float(arr.min()), float(arr.max())
        pad = max((hi - lo) * 0.35, abs(hi) * 0.06 + 1e-9)
        ax.set_ylim(lo - pad, hi + pad * 1.6)
        ax.text(0.03, 0.05, f"mean ± σ = {mean:.2g} ± {std:.2g} {unit}".strip(),
                transform=ax.transAxes, ha="left", va="bottom", fontsize=8,
                color="0.35")
        style_axes(ax, title=name, ylabel=unit)

    # ── 左下：逐 seed 累计完成演化 ─────────────────────────────────
    for seed, payload in zip(seeds, per_seed.values()):
        cyc = payload.get("cycles") or []
        cum = np.cumsum([int(c.get("n_completed") or 0) for c in cyc])
        n_arr = int((payload.get("summary") or {}).get("n_arrived")
                    or (cum[-1] if len(cum) else 0))
        rate = cum[-1] / n_arr if n_arr else float("nan")
        ax_evolve.plot(range(1, len(cum) + 1), cum, "o-", markersize=5,
                       linewidth=1.5, alpha=0.85,
                       label=f"seed {seed}（终值 {rate:.0%}）")
    # 均值线
    all_cum = [np.cumsum([int(c.get("n_completed") or 0)
                          for c in (p.get("cycles") or [])])
               for p in per_seed.values()]
    max_len = max((len(a) for a in all_cum), default=0)
    if max_len:
        pad = np.full((len(all_cum), max_len), np.nan)
        for i, a in enumerate(all_cum):
            pad[i, : len(a)] = a
        with np.errstate(invalid="ignore"):
            mean_curve = np.nanmean(pad, axis=0)
        ax_evolve.plot(range(1, max_len + 1), mean_curve, "k--", linewidth=1.8,
                       markersize=4, marker="D", markerfacecolor="white",
                       label="均值")
    ax_evolve.set_xticks(range(1, max_len + 1))
    ax_evolve.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax_evolve.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_evolve, title="逐 seed 累计完成任务数演化（括号内为各自完成率）",
               xlabel="滚动周期", ylabel="累计完成任务数")
    ax_evolve.legend(fontsize=8, ncol=2, loc="upper left", framealpha=0.92)

    # ── 右下：明细表 ───────────────────────────────────────────────
    ax_table.axis("off")
    header = f"{'seed':>6s}" + "".join(f"{name:>10s}" for _, name, _, _ in _TABLE_COLS)
    lines = [header, "─" * len(header)]
    for seed in seeds:
        payload = per_seed.get(seed) or per_seed.get(str(seed)) or {}
        summ = payload.get("summary") or {}
        row = f"{seed:>6d}"
        for key, _, scale, fmt in _TABLE_COLS:
            v = summ.get(key)
            row += f"{format(v * scale, fmt):>10s}" if v is not None else f"{'—':>10s}"
        lines.append(row)
    lines.append("─" * len(header))
    row = f"{'mean':>6s}"
    for key, _, scale, fmt in _TABLE_COLS:
        ref = seed_summary.get(key) or {}
        v = ref.get("mean")
        row += f"{format(v * scale, fmt):>10s}" if v is not None else f"{'—':>10s}"
    lines.append(row)
    row = f"{'σ':>6s}"
    for key, _, scale, fmt in _TABLE_COLS:
        ref = seed_summary.get(key) or {}
        v = ref.get("std")
        row += f"{format(v * scale, fmt):>10s}" if v is not None else f"{'—':>10s}"
    lines.append(row)
    ax_table.text(0.0, 0.96, "\n".join(lines), transform=ax_table.transAxes,
                  ha="left", va="top", fontsize=8.4, family=MONO_ZH,
                  color="0.2",
                  bbox=dict(boxstyle="round,pad=0.5", facecolor="#f4f6f8",
                            edgecolor="0.8", linewidth=0.8))
    style_axes(ax_table, title=f"各 seed 明细（n = {len(seeds)}）")

    fig.suptitle("跨 seed 分布：结论是否稳健、哪个 seed 在拖后腿",
                 fontsize=12.5)
    return save_figure(fig, Path(out_dir) / "seed_distribution", formats,
                       dpi=dpi)
