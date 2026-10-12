# -*- coding: utf-8 -*-
"""图 ``dmde_search``——DMDE 搜索机制：交叉变异调度与收敛。

【process 层 · 求解过程 ②】

回答「DMDE 在一个周期里怎么搜」：探索→开发的调度（CR）、实际收敛轨迹、
以及这一切花多少评估代价。由原 ``convergence`` + ``cr_curve`` 合并而来，
面板信息无丢失。

六联面板::

    (a) 动态交叉率 CR(g)   公式 (3-9)：CR = 1 − (ln g / ln S)^ζ，1→S 代从 1 降到 0
    (b) 探索—开发平衡      基因位占比随世代转移（公式 3-10：CR 与 1−CR）
    (c) 收敛曲线           各周期每代最优适应度（best-so-far 阶梯，截断到有效改进窗口）
    (d) 归一化收敛         相对初代的改进百分比
    (e) 求解开销           每周期求解耗时 + 适应度评估次数
    (f) 公式与参数         3-9 / 3-10 / 3-11 与本实验的 (S, ζ, pop) 取值

数据来源：``solution.json`` 的 ``cycles[].cost_history``（长度 − 1 即 S）、
``config.yaml`` 的 ``solver`` 段（ζ / 种群 / 代数）、``metrics.json`` 的
``cycles[].muas.{solve_seconds, n_evals}``。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import FS, MONO_ZH, PALETTE, cycle_colors, footnote, plt, save_figure, style_axes

__all__ = ["plot_dmde_search"]


def _dynamic_cr(gen, total: int, zeta: float):
    """公式 (3-9)。与 methods/muas/operators/crossover.dynamic_crossover_rate 同式。"""
    gen = np.asarray(gen, dtype=float)
    if total <= 1:
        return np.ones_like(gen)
    out = 1.0 - (np.log(np.maximum(gen, 1.0)) / math.log(total)) ** zeta
    return np.clip(out, 0.0, 1.0)


def _read_zeta(out_dir: Path) -> float:
    cfg = Path(out_dir).resolve().parent.parent / "config.yaml"
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        return float((data.get("solver") or {}).get("zeta", 3))
    except Exception:
        return 3.0


def _last_change_index(hist: list[float]) -> int:
    """最后一次发生改进（值变化）的代下标；全程无改进返回 0。"""
    last = 0
    for i in range(1, len(hist)):
        if hist[i] != hist[i - 1]:
            last = i
    return last


def plot_dmde_search(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = [c for c in (solution.get("cycles") or []) if c.get("cost_history")]
    if not cycles:
        return []

    s_gen = max(len(c["cost_history"]) for c in cycles) - 1
    if s_gen < 2:
        return []
    zeta = _read_zeta(Path(out_dir))
    gens = np.arange(1, s_gen + 1)
    cr = _dynamic_cr(gens, s_gen, zeta)
    colors = cycle_colors(len(cycles))

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 7.4))
    ax_cr, ax_bal, ax_raw = axes[0]
    ax_norm, ax_cost, ax_formula = axes[1]

    # ── (a) CR 曲线 ──────────────────────────────────────────────
    ax_cr.plot(gens, cr, "-", color=PALETTE["new"], lw=1.8)
    ax_cr.scatter([1, s_gen], [1.0, 0.0], s=22, color=PALETTE["cost"],
                  zorder=3, edgecolors="white", linewidths=0.6)
    ax_cr.annotate("CR(1)=1 全探索", (1, 1.0), textcoords="offset points",
                   xytext=(8, -14), fontsize=FS["annotation"],
                   color=PALETTE["cost"])
    ax_cr.annotate(f"CR({s_gen})=0 全开发", (s_gen, 0.0),
                   textcoords="offset points", xytext=(-6, 10), ha="right",
                   fontsize=FS["annotation"], color=PALETTE["cost"])
    ax_cr.axhline(0.5, color="0.7", lw=0.8, ls="--")
    mid = int(gens[np.argmin(np.abs(cr - 0.5))])
    ax_cr.text(mid, 0.53, f"CR=0.5（第 {mid} 代）", fontsize=FS["footnote"],
               color="0.45", ha="center")
    style_axes(ax_cr, title="(a) 动态交叉率 CR(g)（公式 3-9）",
               xlabel="代数 g", ylabel="CR")
    ax_cr.set_ylim(-0.05, 1.08)
    ax_cr.text(0.98, 0.95, f"S={s_gen}, ζ={zeta:g}",
               transform=ax_cr.transAxes, ha="right", va="top",
               fontsize=FS["annotation"], color="0.35",
               bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                         edgecolor="0.8", alpha=0.9))

    # ── (b) 探索—开发平衡 ────────────────────────────────────────
    ax_bal.fill_between(gens, 0, cr, color=PALETTE["new"], alpha=0.75,
                        label="探索 DE/rand/1（占比 = CR）")
    ax_bal.fill_between(gens, cr, 1, color=PALETTE["deferred"], alpha=0.75,
                        label="开发 DE/best/2（占比 = 1−CR）")
    ax_bal.set_ylim(0, 1.0)
    style_axes(ax_bal, title="(b) 探索—开发平衡随世代转移（公式 3-10）",
               xlabel="代数 g", ylabel="基因位占比")
    ax_bal.legend(loc="center right", fontsize=FS["legend"], frameon=True,
                  framealpha=0.88, edgecolor="0.8", labelspacing=0.4)

    # ── (c)(d) 收敛曲线 + 归一化 ─────────────────────────────────
    series: list[tuple[str, list[float], int]] = []
    for cyc in cycles:
        hist = [float(v) for v in cyc["cost_history"]]
        series.append((f"周期 {cyc.get('cycle')}", hist,
                       _last_change_index(hist)))
    n_total = max(len(h) for _, h, _ in series)
    g_last = max(ch for _, _, ch in series)
    has_flat_tail = g_last + 1 < n_total
    x_max = n_total - 1
    if has_flat_tail:
        x_max = min(n_total - 1, max(10, int(np.ceil(g_last * 1.3)) + 2))

    for idx, (label, hist, change) in enumerate(series):
        g = np.arange(len(hist))
        color = colors[idx]
        ax_raw.plot(g, hist, "-", drawstyle="steps-post", color=color,
                    lw=1.4, label=label)
        if change > 0:
            ax_raw.scatter([change], [hist[change]], s=16, color=color,
                           zorder=3, edgecolors="white", linewidths=0.5)
    style_axes(ax_raw, title="(c) DMDE 收敛曲线（每代最优适应度）",
               xlabel="代数 g", ylabel="最优适应度（越低越好）")
    ax_raw.legend(loc="upper right", fontsize=FS["legend"])
    if has_flat_tail:
        ax_raw.text(0.02, 0.04,
                    f"展示至第 {x_max} 代（其后 {n_total - 1 - x_max} 代无改进）",
                    transform=ax_raw.transAxes, ha="left", va="bottom",
                    fontsize=FS["annotation"], color="0.35",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                              edgecolor="0.85", alpha=0.9))

    flat_labels: list[str] = []
    for idx, (label, hist, _ch) in enumerate(series):
        g = np.arange(len(hist))
        f0, f_end = hist[0], hist[-1]
        span = f0 - f_end
        if abs(span) > 1e-12:
            norm = [(f0 - v) / span * 100.0 for v in hist]
            ax_norm.plot(g, norm, "-", drawstyle="steps-post",
                         color=colors[idx], lw=1.4, label=label)
        else:
            flat_labels.append(label)
    style_axes(ax_norm, title="(d) 归一化收敛（相对初代改进）",
               xlabel="代数 g", ylabel="改进 (%)")
    ax_norm.set_ylim(-4, 104)
    if flat_labels:
        ax_norm.text(0.97, 0.06,
                     f"{('、'.join(flat_labels))} 初代即达最优\n"
                     "（候选规模小，此后无改进）",
                     transform=ax_norm.transAxes, ha="right", va="bottom",
                     fontsize=FS["annotation"], color="0.35",
                     bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                               edgecolor="0.85", alpha=0.9))
    for ax in (ax_raw, ax_norm):
        ax.set_xlim(-0.5, x_max)

    # ── (e) 求解开销 ─────────────────────────────────────────────
    mcycles = list(metrics.get("cycles") or [])
    if mcycles:
        x = np.arange(len(mcycles))
        solve_ms = [float((c.get("muas") or {}).get("solve_seconds") or 0.0) * 1000.0
                    for c in mcycles]
        evals = [float((c.get("muas") or {}).get("n_evals") or 0.0)
                 for c in mcycles]
        ax_cost.bar(x, solve_ms, 0.55, color=PALETTE["solve"], label="求解耗时")
        style_axes(ax_cost, title="(e) 求解开销（每周期）",
                   xlabel="滚动周期", ylabel="求解耗时 (ms)")
        ax_cost.set_xticks(x, [f"C{c.get('cycle')}" for c in mcycles])
        ax_cost.set_ylim(0, max(solve_ms) * 1.38 if solve_ms else 1.0)
        ax_cost2 = ax_cost.twinx()
        ax_cost2.plot(x, evals, "o-", color=PALETTE["evals"], ms=4,
                      label="适应度评估次数")
        ax_cost2.set_ylabel("评估次数", fontsize=FS["base"])
        ax_cost2.grid(False)
        ax_cost2.set_ylim(0, max(evals) * 1.38 if evals else 1.0)
        h1, l1 = ax_cost.get_legend_handles_labels()
        h2, l2 = ax_cost2.get_legend_handles_labels()
        ax_cost.legend(h1 + h2, l1 + l2, loc="upper right", ncol=1,
                       fontsize=FS["legend"], labelspacing=0.35,
                       handlelength=1.4)
    else:
        style_axes(ax_cost, title="(e) 求解开销")
        ax_cost.text(0.5, 0.5, "无 metrics 数据", transform=ax_cost.transAxes,
                     ha="center", va="center", fontsize=FS["base"],
                     color="0.5")

    # ── (f) 公式与参数（压缩版） ─────────────────────────────────
    ax_formula.axis("off")
    lines = [
        "公式 (3-9)  动态交叉率",
        "    CR(x) = 1 − (ln x / ln S)^ζ ,  x = 1..S",
        "",
        "公式 (3-10) 混合差分策略（逐基因位判定）",
        "    CR ≥ rand → DE/rand/1（探索）;  否则 DE/best/2（开发）",
        "",
        "公式 (3-11) 动态缩放因子",
        "    F = CR（rand ≤ CR）;  F = (1 − CR)/2（rand > CR）",
        "",
        f"本实验取值：S = {s_gen}（cost_history 长度−1），ζ = {zeta:g}（config.yaml）",
    ]
    ax_formula.text(0.02, 0.97, "\n".join(lines),
                    transform=ax_formula.transAxes, ha="left", va="top",
                    fontsize=FS["annotation"], family=MONO_ZH, color="0.2",
                    bbox=dict(boxstyle="round,pad=0.55", facecolor="#f4f6f8",
                              edgecolor="0.8", linewidth=0.8))
    style_axes(ax_formula, title="(f) 公式与参数（论文 §3.4.2）")

    fig.suptitle("exp01_smoke：DMDE 搜索机制（交叉变异调度 · 收敛 · 开销）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    footnote(fig, "口径：CR 为公式 (3-9) 解析值，与运行噪声无关；"
                  "cost_history 为 best-so-far 序列（单调不增阶梯）；"
                  "归一化收敛以各周期自身初代为基准；"
                  "求解耗时 = muas.solve_seconds × 1000。")
    return save_figure(fig, Path(out_dir) / "dmde_search", formats, dpi=dpi)
