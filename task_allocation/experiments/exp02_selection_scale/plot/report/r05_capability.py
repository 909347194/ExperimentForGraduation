# -*- coding: utf-8 -*-
"""图 ``r05_capability``——能力驱动标定：cap(t) 是天然平衡点。

【report 层 · 结论与论文插图】

回答「候选规模按机数名额还是机队能力标定？」——能力驱动口径
（``mode=primary``，选择数接到 cap(t)=⌊budget_scale·B/c̃⌋）把压缩损失
大部分买回来，且 n_cand 超过 cap(t) 后收益迅速衰减。

双联面板（x = 候选/选择规模 n_cand / n_sel，对数轴）：

    (a) 绝对口径   y = F1。能力族三点（n_cand=10/20/40）沿曲线爬升，
                      全量对照为同 N 红色虚线；cap_x1（n_cand≈cap(t)）
                      达到全量的高质量保持率。灰色带 = cap(t) 附近（±3）。
    (b) 相对口径（有全量对照的规模）   左轴 = 质量保持率 %（实心），
                      右轴 = 耗时比 %（空心，对数）。

读图要点：(a) 中 n_cand 超过 cap(t) 后（加到全量）收益速衰而耗时继续涨——
cap(t) 即「天然平衡点」。

数据来源：``metrics.json`` 的 ``scales[]``（cap_x* 阶梯 + quota_a2.5 +
full_baseline 参考）。
"""

from __future__ import annotations

# ── 独立运行自举：支持直接 `python plot/.../xxx.py`（脚本方式）──
if __name__ == "__main__" and __package__ in (None, ""):
    import sys, runpy
    from pathlib import Path as _P
    _here = _P(__file__).resolve()
    _root = _here.parents[2]   # 实验根 = plot 的上两级（使 `plot` 包可导入）
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    _rel = _here.relative_to(_root / "plot")
    _mod = "plot." + ".".join(_rel.with_suffix("").parts)
    runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    raise SystemExit(0)


from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import FIG_WIDE, FS, footnote, plt, save_figure, style_axes
from ._common import (
    cap_t,
    color,
    f1,
    grouped,
    label,
    marker,
    sel_n,
    t_muas_s,
    variants_present,
)

__all__ = ["plot_r05_capability"]

_FULL = "full_baseline"
_CAP_POINTS = ("cap_x0.25", "cap_x0.5", "cap_x1")
_CAP_T_FALLBACK = 40  # metrics 无 capability_cap 时的兜底值


def _cap_t_of(metrics: dict[str, Any]) -> int:
    """cap(t) 代表值：cap_x* 各行 capability_cap 的中位数（兜底 40）。"""
    vals = [
        cap_t(row)
        for row in (metrics.get("scales") or [])
        if str(row.get("variant")) in _CAP_POINTS and cap_t(row) is not None
    ]
    if not vals:
        return _CAP_T_FALLBACK
    vals.sort()
    return int(vals[len(vals) // 2])


def plot_r05_capability(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    g = grouped(metrics)
    if not g:
        return []
    ns = sorted(g)
    variants = [v for v in variants_present(metrics) if v != _FULL]
    ns_with_full = [n for n in ns if _FULL in g[n]]
    cap_t_val = _cap_t_of(metrics)

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(FIG_WIDE, 4.4))

    # cap(t) 平衡带
    band = (max(cap_t_val - 3, 1), cap_t_val + 3)
    for ax in (ax_a, ax_b):
        ax.axvspan(band[0], band[1], color="#9aa5b1", alpha=0.18, zorder=1)

    # ── (a) 绝对口径：F1 vs k ────────────────────────────────────
    for n in ns_with_full:
        ax_a.axhline(f1(g[n][_FULL]), color="#e45756", linestyle="--",
                     linewidth=1.0, alpha=0.6, zorder=1)
        ax_a.text(9.2, f1(g[n][_FULL]), f" 全量 N={n}（{f1(g[n][_FULL]):.0f}）",
                  color="#e45756", fontsize=FS["annotation"], va="center")
    for v in variants:
        for n in ns:
            row = g[n].get(v)
            if row is None:
                continue
            k = float(sel_n(row))
            ax_a.scatter(k, f1(row), s=46, marker=marker(n), color=color(v),
                         edgecolor="white", linewidth=0.7, zorder=3)
    # cap_x1 标注
    for n in ns:
        row = g[n].get("cap_x1")
        if row is not None:
            ax_a.annotate(f"N={n}", xy=(sel_n(row), f1(row)),
                          xytext=(6, -11), textcoords="offset points",
                          fontsize=FS["annotation"], color="#2f6b3a")
    ax_a.set_xscale("log")
    ax_a.set_xlim(8, 210)
    style_axes(ax_a, title="(a) 绝对口径：交付收益 vs 规模 n_cand/n_sel",
               xlabel="候选/选择规模 n_cand / n_sel（对数轴）", ylabel="交付收益 F1")

    # ── (b) 相对口径：保持率（左轴）/ 耗时比（右轴）──────────────
    for v in variants:
        for n in ns_with_full:
            row = g[n].get(v)
            if row is None:
                continue
            k = float(sel_n(row))
            yr = 100.0 * f1(row) / max(f1(g[n][_FULL]), 1e-9)
            tr = 100.0 * t_muas_s(row) / max(t_muas_s(g[n][_FULL]), 1e-9)
            ax_b.scatter(k, yr, s=46, marker=marker(n), color=color(v),
                         edgecolor="white", linewidth=0.7, zorder=3)
            ax_b.scatter(k, tr, s=46, marker=marker(n), facecolor="none",
                         edgecolor=color(v), linewidth=1.3, zorder=3)
    # cap_x1 关键数标注
    for n in ns_with_full:
        row = g[n].get("cap_x1")
        if row is None:
            continue
        k = float(sel_n(row))
        yr = 100.0 * f1(row) / max(f1(g[n][_FULL]), 1e-9)
        tr = 100.0 * t_muas_s(row) / max(t_muas_s(g[n][_FULL]), 1e-9)
        speed = 100.0 / tr if tr > 0 else float("inf")
        ax_b.annotate(
            f"N={n}: {yr:.0f}% @ {tr:.0f}%（{speed:.1f}×）",
            xy=(k, yr), xytext=(8, -13), textcoords="offset points",
            fontsize=FS["annotation"], color="#2f6b3a",
        )
    ax_b.axhline(100, color="#e45756", linestyle="--", linewidth=1.0,
                 alpha=0.6, zorder=1)
    ax_b.text(9.2, 100, " 全量参考（100%）", color="#e45756",
              fontsize=FS["annotation"], va="center")
    ax_b.set_xscale("log")
    ax_b.set_xlim(8, 210)
    ax_b.set_ylim(0, 108)
    style_axes(ax_b, title="(b) 相对口径：保持率（实心）与耗时比（空心）同轴",
               xlabel="候选/选择规模 n_cand / n_sel（对数轴）",
               ylabel="保持率 / 耗时比（%，对同 N 全量）")

    # 图例
    from matplotlib.lines import Line2D

    v_handles = [
        Line2D([], [], marker="o", linestyle="", markersize=5,
               color=color(v), label=label(v))
        for v in variants
    ]
    n_handles = [
        Line2D([], [], marker=marker(n), linestyle="", markersize=5,
               color="0.35", label=f"N={n}")
        for n in ns
    ]
    leg = ax_a.legend(handles=v_handles, loc="lower right",
                      fontsize=FS["legend"] - 0.5, title="变体",
                      title_fontsize=FS["legend"] - 0.5)
    ax_a.add_artist(leg)
    ax_b.legend(handles=[
        Line2D([], [], marker="o", linestyle="", markersize=5, color="0.35",
               label="实心 = 质量保持率"),
        Line2D([], [], marker="o", linestyle="", markersize=5,
               markerfacecolor="none", markeredgecolor="0.35",
               label="空心 = 耗时比"),
    ], loc="lower right", fontsize=FS["legend"] - 0.5)
    fig.legend(handles=n_handles, loc="lower center", ncol=len(n_handles),
               fontsize=FS["legend"], frameon=False,
               bbox_to_anchor=(0.5, 0.005))

    # cap(t) 带标注（放在两轴顶部）
    for ax in (ax_a, ax_b):
        ax.text(cap_t_val, 1.0, f"cap(t)≈{cap_t_val}",
                transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", fontsize=FS["annotation"] - 0.5,
                color="#6b7280")

    fig.suptitle("能力驱动标定：k≈cap(t) 是「质量—耗时」的天然平衡点（成功标准 3/4）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.06, 1, 0.93))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]。k = n_selected；cap(t)=下取整"
             "(budget_scale·B/c)（B=机队总航程 640 km，c=最近可用机三维航程中位数），"
             "实测见 metrics 的 capability_cap 字段。全量对照三个规模全测。"
             "灰色带 = cap(t)±3。单 seed（42）。")

    stem = Path(out_dir) / "R05_capability"
    return save_figure(fig, stem, formats, dpi=dpi)


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
