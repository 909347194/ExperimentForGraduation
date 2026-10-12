# -*- coding: utf-8 -*-
"""图 ``convergence``——DMDE 收敛曲线：求解器在每个周期里怎么收敛。

【process 层 · 运行过程观测】

左：各滚动周期的每代最优适应度（DMDE 最小化 ``-F1 + λ·F2 + penalty``，越低越好）；
右：归一化收敛（相对初代的改进百分比），用于比较不同周期的收敛速度是否一致。

数据来源：``results/solution.json`` 的 ``cycles[].cost_history``。

统一签名（见 ``plot/registry.py``）：即使本图用不到 ``metrics``，也按
``(metrics, solution, out_dir, formats, *, dpi)`` 收参，好让调度端一处通用。

呈现约定（针对小规模滚动周期的真实收敛形态）：

    DMDE 的 ``cost_history`` 是 best-so-far 序列（单调不增的阶梯函数）。
    滚动周期候选任务通常只有 3~9 个，离散分配空间小，种群往往在最初
    若干代（甚至初代）即达最优；此后几百代均为无改进的平尾。若把
    500 代全部画进 x 轴，有效信息只占画面最左侧几个像素，曲线观感
    如同"坏掉的平线"。因此本图：

    1. x 轴截断到**有效改进窗口**（各曲线最后一次改进代 × 1.3 + 2，
       至少 10 代），平尾以图内注释说明"其后无进一步改进"；
    2. 曲线按 best-so-far 语义用 ``steps-post`` 阶梯绘制，改进点加标记；
    3. 初代即最优（全程无改进）的周期在归一化面板不画 0% 平线，
       改为图内注释说明，避免"归一化恒 0"被误读为绘图错误。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import cycle_colors, plt, save_figure, style_axes

__all__ = ["plot_convergence"]


def _last_change_index(hist: list[float]) -> int:
    """最后一次发生改进（值变化）的代下标；全程无改进返回 0。"""
    last = 0
    for i in range(1, len(hist)):
        if hist[i] != hist[i - 1]:
            last = i
    return last


def plot_convergence(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    cycles = [c for c in (solution.get("cycles") or []) if c.get("cost_history")]
    if not cycles:
        return []

    # ── 数据准备：曲线 + 各自最后一次改进代 ──────────────────────────
    series: list[tuple[str, list[float], int]] = []
    for cyc in cycles:
        hist = [float(v) for v in cyc["cost_history"]]
        series.append((f"周期 {cyc.get('cycle')}", hist, _last_change_index(hist)))

    n_total = max(len(hist) for _, hist, _ in series)
    g_last = max(change for _, _, change in series)
    has_flat_tail = g_last + 1 < n_total
    # 有效窗口：最后一次改进代 × 1.3 + 2（至少 10 代，至多全长）
    x_max = n_total - 1
    if has_flat_tail:
        x_max = min(n_total - 1, max(10, int(np.ceil(g_last * 1.3)) + 2))

    colors = cycle_colors(len(series))
    fig, (ax_raw, ax_norm) = plt.subplots(1, 2, figsize=(11.5, 4.4))

    # ── (a) 最优适应度：阶梯 + 改进点标记 ────────────────────────────
    for idx, (label, hist, change) in enumerate(series):
        gens = np.arange(len(hist))
        color = colors[idx]
        ax_raw.plot(
            gens, hist, "-", drawstyle="steps-post",
            color=color, linewidth=1.8, label=label,
        )
        if change > 0:
            ax_raw.scatter(
                [change], [hist[change]],
                s=22, color=color, zorder=3, edgecolors="white", linewidths=0.6,
            )

    style_axes(ax_raw, title="(a) DMDE 收敛曲线", xlabel="代数", ylabel="最优适应度")
    ax_raw.legend(loc="best")

    # ── (b) 归一化收敛：有改进的画线，初代即最优的注释说明 ──────────
    flat_labels: list[str] = []
    for idx, (label, hist, _change) in enumerate(series):
        gens = np.arange(len(hist))
        color = colors[idx]
        f0, f_end = hist[0], hist[-1]
        span = f0 - f_end
        if abs(span) > 1e-12:
            norm = [(f0 - v) / span * 100.0 for v in hist]
            ax_norm.plot(
                gens, norm, "-", drawstyle="steps-post",
                color=color, linewidth=1.8, label=label,
            )
        else:
            flat_labels.append(label)

    style_axes(ax_norm, title="(b) 归一化收敛", xlabel="代数", ylabel="相对初代改进 (%)")
    ax_norm.set_ylim(-4, 104)
    # 图例省略：与 (a) 共用同一套颜色-周期对应关系，避免挤占注释空间

    # 初代即最优的周期：注释替代 0% 平线（小规模候选集，初代种群已覆盖最优解）
    if flat_labels:
        ax_norm.text(
            0.97, 0.06,
            f"{('、'.join(flat_labels))} 初代即达最优\n（候选规模小，此后无改进）",
            transform=ax_norm.transAxes, ha="right", va="bottom",
            fontsize=8.5, color="0.35",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                      edgecolor="0.8", alpha=0.9),
        )

    # ── 平尾说明 + x 轴截断 ─────────────────────────────────────────
    for ax in (ax_raw, ax_norm):
        ax.set_xlim(-0.5, x_max)
    if has_flat_tail:
        ax_raw.text(
            0.98, 0.96,
            f"展示至第 {x_max} 代；\n其后至第 {n_total - 1} 代各周期均无改进",
            transform=ax_raw.transAxes, ha="right", va="top",
            fontsize=8.5, color="0.35",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                      edgecolor="0.8", alpha=0.9),
        )

    fig.suptitle("exp01_smoke：DMDE 收敛性", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))

    stem = Path(out_dir) / "convergence"
    return save_figure(fig, stem, formats, dpi=dpi)
