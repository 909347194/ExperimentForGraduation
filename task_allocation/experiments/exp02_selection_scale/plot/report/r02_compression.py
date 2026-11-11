# -*- coding: utf-8 -*-
"""图 ``r02_compression``——规模压缩与候选/选择/执行数。

【report 层 · 结论与论文插图】

回答「压缩真实发生了吗？压到多少？跟 N 走还是跟 K/能力走？」

三联面板（x = 任务规模 N，分类轴）：

    (a) 候选名额 n_cand   优先级预筛截断后的候选规模。机数名额族由 ⌈α·K⌉
                      给出、能力族由 cap(t) 给出；全量对照 = N 自身。
    (b) 选择数 sel / 执行数 exec   最终参与 MUAS 与真正交付的任务数
                      （漏斗 N → k → sel → exec 的末两级）。
    (c) 压缩比 N/sel   随 N 的变化。

读图要点：观察 (a) 中候选规模是否随 N 变化；若候选规模由名额或能力决定，
应呈平线。是否解耦以本次正式档实测为准，图注不预设结论。

数据来源：``metrics.json`` 的
``scales[].selection.{capability_n_cand,n_selected}`` +
``muas.n_executed`` + ``compression_ratio``。
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
    color,
    compression,
    exec_n,
    grouped,
    n_candidate,
    label,
    sel_n,
    variants_present,
)

__all__ = ["plot_r02_compression"]

_FULL = "full_baseline"


def _line(ax, xs, ys, v, *, annotate=True, seen=None):
    ax.plot(xs, ys, marker="o", markersize=4.5, linewidth=1.5,
            color=color(v), label=label(v), zorder=3)
    if annotate:
        for x, y in zip(xs, ys):
            if y is None:
                continue
            # 名额族两变体 sel 相同 ⇒ 压缩比曲线重合，标注去重防叠字
            key = (round(float(x), 3), round(float(y), 3))
            if seen is not None:
                if key in seen:
                    continue
                seen.add(key)
            ax.annotate(f"{y:g}", xy=(x, y), xytext=(0, 6),
                        textcoords="offset points", ha="center",
                        fontsize=FS["annotation"] - 0.5, color=color(v))


def plot_r02_compression(
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
    xs = np.arange(len(ns))

    fig, (ax_a, ax_b, ax_c) = plt.subplots(1, 3, figsize=(FIG_WIDE, 4.2))

    # ── (a) 候选任务数 n_cand ─────────────────────────────────────
    seen_a: set[tuple[float, float]] = set()
    for v in variants:
        ys = [n_candidate(g[n].get(v, {})) for n in ns]
        _line(ax_a, xs, ys, v, seen=seen_a)
    # 全量对照 = N 自身
    ax_a.plot(xs, ns, marker="s", markersize=4.5, linewidth=1.5,
              color=color(_FULL), linestyle="--", label=label(_FULL), zorder=3)
    for x, n in zip(xs, ns):
        ax_a.annotate(f"{n}", xy=(x, n), xytext=(0, 6),
                      textcoords="offset points", ha="center",
                      fontsize=FS["annotation"] - 0.5, color=color(_FULL))
    style_axes(ax_a, title="(a) 候选名额 n_cand 随 N 的变化",
               xlabel="任务规模 N", ylabel="候选任务数 n_cand")
    ax_a.set_yscale("log")
    ax_a.set_ylim(8, 260)

    # ── (b) sel / exec ───────────────────────────────────────────
    width = 0.36
    for i, v in enumerate(variants):
        sels = [sel_n(g[n].get(v, {})) for n in ns]
        execs = [exec_n(g[n].get(v, {})) for n in ns]
        pos = xs + (i - (len(variants) - 1) / 2.0) * width
        ax_b.bar(pos, sels, width * 0.92, color=color(v), alpha=0.45,
                 edgecolor="white", linewidth=0.6, zorder=2)
        ax_b.bar(pos, execs, width * 0.42, color=color(v),
                 edgecolor="white", linewidth=0.6, zorder=3,
                 label=label(v))
    for i, v in enumerate(variants):
        for x, n in zip(xs, ns):
            s = sel_n(g[n].get(v, {}))
            e = exec_n(g[n].get(v, {}))
            pos = x + (i - (len(variants) - 1) / 2.0) * width
            ax_b.annotate(f"{s}", xy=(pos, s), xytext=(0, 2),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 1.0, color=color(v))
            ax_b.annotate(f"{e}", xy=(pos, e), xytext=(0, 2),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 1.0,
                          color="white", fontweight="bold")
    # 全量对照执行数（参考点）
    for x, n in zip(xs, ns):
        full = g[n].get(_FULL)
        if full is not None:
            ax_b.scatter([x], [exec_n(full)], marker="D", s=34,
                         color=color(_FULL), zorder=4,
                         label=label(_FULL) + " exec" if x == xs[0] else None)
    style_axes(ax_b, title="(b) 选择数（浅）与执行数（深）",
               xlabel="任务规模 N", ylabel="任务数")

    # ── (c) 压缩比 ───────────────────────────────────────────────
    seen_c: set[tuple[float, float]] = set()
    for v in variants:
        ys = [compression(g[n].get(v, {})) for n in ns]
        _line(ax_c, xs, ys, v, annotate=True, seen=seen_c)
    style_axes(ax_c, title="(c) 压缩比 N/n_sel 随 N 的变化",
               xlabel="任务规模 N", ylabel="压缩比（×）")

    for ax in (ax_a, ax_b, ax_c):
        ax.set_xticks(xs, [str(n) for n in ns])
    ax_b.set_ylim(bottom=0)
    ax_c.set_ylim(bottom=0)

    ax_a.legend(loc="upper left", fontsize=FS["legend"] - 0.5, ncol=2)
    ax_b.legend(loc="upper left", fontsize=FS["legend"] - 0.5, ncol=2)

    fig.suptitle("规模压缩：候选/选择/执行与压缩比随 N 的演化",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.055, 1, 0.93))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]。n_cand = selection.capability_n_cand"
             "（Top-α 预筛后候选规模）；sel = n_selected（最终参与 MUAS）；exec = muas.n_executed"
             "（真正交付）。漏斗为 N → 候选 → 选择 → 交付；各档实测值以本次 metrics 为准。"
             "单 seed（42）。")

    stem = Path(out_dir) / "R02_compression"
    return save_figure(fig, stem, formats, dpi=dpi)


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
