# -*- coding: utf-8 -*-
"""图 ``r01_tradeoff``——吞吐—耗时—质量权衡（核心图）。

【report 层 · 结论与论文插图】

回答全实验的总问题：「把任务池压到多小，换来多快的求解，损失多少质量？」

双联面板：

    (a) 绝对口径   x = 二阶段求解耗时 t_muas（s，对数轴），y = 交付收益 F1。
                   颜色分变体、形状分任务规模 N；红色虚线为同 N 全量对照
                   （不经过第一阶段直接全量求解）的 F1 水平。
    (b) 相对口径   x = 耗时比（t_muas / 同 N 全量 × 100%，对数轴），
                   y = 质量保持率（F1 / 同 N 全量 × 100%）。
                   右下为优（更快且不掉质量）；虚线为 y=x 参考。

读图要点（对应结论 4/5）：能力族（绿/青/橙）沿「掉质量换耗时」的曲线分布，
k≈cap(t) 的 cap_x1 位于曲线拐点；名额族（蓝/紫）全部挤在左上角——
质量保持率低且耗时并不更低（被 max_ratio·K 封顶在 16）。

数据来源：``metrics.json`` 的 ``scales[]`` 全字段。
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
    VARIANT_ORDER,
    color,
    f1,
    grouped,
    label,
    marker,
    scales_present,
    sel_n,
    t_muas_s,
    variants_present,
)

__all__ = ["plot_r01_tradeoff"]

_FULL = "full_baseline"


def plot_r01_tradeoff(
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
    variants = [v for v in variants_present(metrics) if v != _FULL]
    ns = scales_present(metrics)

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(FIG_WIDE, 4.4))

    # ── (a) 绝对口径 ─────────────────────────────────────────────
    for n in ns:
        full = g[n].get(_FULL)
        if full is not None:
            ax_a.axhline(f1(full), color="#e45756", linestyle="--",
                         linewidth=1.0, alpha=0.55, zorder=1)
            ax_a.text(28.0, f1(full), f" 全量 N={n}（{f1(full):.0f}）",
                      color="#e45756", fontsize=FS["annotation"],
                      va="center", ha="left")
    for v in variants:
        for n in ns:
            row = g[n].get(v)
            if row is None:
                continue
            ax_a.scatter(t_muas_s(row), f1(row), s=46, marker=marker(n),
                         color=color(v), edgecolor="white", linewidth=0.7,
                         zorder=3)
    # 关键点标注：cap_x1（平衡点候选）
    for n in ns:
        row = g[n].get("cap_x1")
        if row is not None:
            ax_a.annotate(
                f"N={n}",
                xy=(t_muas_s(row), f1(row)),
                xytext=(6, -11), textcoords="offset points",
                fontsize=FS["annotation"], color="#2f6b3a",
            )
    ax_a.set_xscale("log")
    style_axes(ax_a,
               title="(a) 绝对口径：二阶段耗时 vs 交付收益",
               xlabel="二阶段求解耗时 t_muas（s，对数轴）",
               ylabel="交付收益 F1（奖励当量）")
    ax_a.set_xlim(left=18.0)

    # ── (b) 相对口径 ─────────────────────────────────────────────
    for v in variants:
        for n in ns:
            row = g[n].get(v)
            full = g[n].get(_FULL)
            if row is None or full is None:
                continue
            xr = 100.0 * t_muas_s(row) / max(t_muas_s(full), 1e-9)
            yr = 100.0 * f1(row) / max(f1(full), 1e-9)
            ax_b.scatter(xr, yr, s=46, marker=marker(n), color=color(v),
                         edgecolor="white", linewidth=0.7, zorder=3)
    # cap_x1 标注（结论 5 的三个数）
    for n in ns:
        row = g[n].get("cap_x1")
        full = g[n].get(_FULL)
        if row is None or full is None:
            continue
        xr = 100.0 * t_muas_s(row) / max(t_muas_s(full), 1e-9)
        yr = 100.0 * f1(row) / max(f1(full), 1e-9)
        ax_b.annotate(
            f"N={n}: {yr:.0f}% @ {xr:.0f}%",
            xy=(xr, yr), xytext=(7, 5), textcoords="offset points",
            fontsize=FS["annotation"], color="#2f6b3a",
        )
    lim = (6.0, 130.0)
    ax_b.plot(lim, lim, color="#9aa5b1", linestyle=":", linewidth=1.0,
              zorder=1, label="y = x（等比交换）")
    ax_b.set_xscale("log")
    ax_b.set_xlim(*lim)
    ax_b.set_ylim(0, 105)
    style_axes(ax_b,
               title="(b) 相对口径：耗时比 vs 质量保持率（对同 N 全量）",
               xlabel="耗时比（%，对数轴；越小越快）",
               ylabel="质量保持率（%）")

    # ── 图例：变体（色）+ N（形）两张合一 ────────────────────────
    from matplotlib.lines import Line2D

    v_handles = [
        Line2D([], [], marker="o", linestyle="", markersize=5,
               color=color(v), label=label(v))
        for v in [*variants, _FULL] if v in VARIANT_ORDER or v == _FULL
    ]
    n_handles = [
        Line2D([], [], marker=marker(n), linestyle="", markersize=5,
               color="0.35", label=f"N={n}")
        for n in ns
    ]
    leg1 = ax_a.legend(handles=v_handles, loc="lower right", fontsize=FS["legend"],
                       title="变体", title_fontsize=FS["legend"])
    ax_a.add_artist(leg1)
    ax_b.legend(loc="lower right", fontsize=FS["legend"])
    fig.legend(handles=n_handles, loc="lower center", ncol=len(n_handles),
               fontsize=FS["legend"], frameon=False,
               bbox_to_anchor=(0.5, 0.005))

    fig.suptitle("吞吐—耗时—质量权衡：变体扫描全景（颜色=变体，形状=N）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.06, 1, 0.94))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]。F1 = f1_reward（交付收益，"
             "按迟到折减后口径）；t_muas 仅计第二阶段求解（不含一阶段选择与建矩阵）。"
             "全量对照 = 不经过第一阶段、直接对全部 N 个任务求解。单 seed（42）。")

    stem = Path(out_dir) / "R01_tradeoff"
    return save_figure(fig, stem, formats, dpi=dpi)


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
