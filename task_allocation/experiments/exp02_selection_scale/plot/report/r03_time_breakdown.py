# -*- coding: utf-8 -*-
"""图 ``r03_time_breakdown``——耗时分解与惰性边（成功标准 2 + 优化 A 证据）。

【report 层 · 结论与论文插图】

回答「分层省下的搜索时间，是否被 O(n²) 代价构建吃掉？」——
代价矩阵延迟构建（惰性代价表）下的答案：没有，且二阶段耗时与 N 无关。

三联面板：

    (a) 二阶段耗时 t_muas vs N（对数轴）   固定 k 时三条平线
                      （极差 < 2%）——耗时只随 k 涨，不随 N 涨。
    (b) 一阶段三项耗时 vs N（对数轴）   hint（最近机 O(n·K)）/
                      selection / build（候选子集矩阵）全程在百毫秒~秒级，
                      占端到端比例随 N 下降（二阶段求解主导端到端）。
    (c) 惰性表现算边数 vs N（双对数）   实测惰性边沿 O(N) 增长（k 固定），
                      全量矩阵沿 O(N²)（斜率 2 参考线）。

数据来源：``metrics.json`` 的 ``scales[].t_{hint,selection,build,muas}_ms``
与 ``lazy_edges``。
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
    edges,
    grouped,
    label,
    t_build_s,
    t_hint_s,
    t_muas_s,
    t_selection_s,
    variants_present,
)

__all__ = ["plot_r03_time_breakdown"]

_FULL = "full_baseline"


def plot_r03_time_breakdown(
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

    # ── (a) 二阶段耗时 vs N（对数轴）─────────────────────────────
    for v in variants:
        ys = [t_muas_s(g[n].get(v, {})) for n in ns]
        ax_a.plot(xs, ys, marker="o", markersize=4.5, linewidth=1.5,
                  color=color(v), label=label(v), zorder=3)
        for x, y in zip(xs, ys):
            ax_a.annotate(f"{y:.0f}", xy=(x, y), xytext=(0, 6),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 1.0, color=color(v))
    # 全量对照（三个规模全测）
    fx = [i for i, n in enumerate(ns) if _FULL in g[n]]
    fy = [t_muas_s(g[n][_FULL]) for n in ns if _FULL in g[n]]
    ax_a.plot(fx, fy, marker="D", markersize=4.5, linewidth=1.5,
              color=color(_FULL), linestyle="--", label=label(_FULL), zorder=3)
    ax_a.set_yscale("log")
    style_axes(ax_a, title="(a) 二阶段耗时 t_muas：固定 k 与 N 无关",
               xlabel="任务规模 N", ylabel="t_muas（s，对数轴）")

    # ── (b) 一阶段三项耗时 vs N（对数轴）─────────────────────────
    series = (
        ("hint 最近机 O(n·K)", t_hint_s, "#4c78a8", "o"),
        ("selection 选择", t_selection_s, "#f58518", "s"),
        ("build 子集矩阵", t_build_s, "#54a24b", "^"),
    )
    for name, fn, c, mk in series:
        ys = [max(fn(g[n].get("quota_a2.5", {})), 1e-4) for n in ns]
        ax_b.plot(xs, ys, marker=mk, markersize=4.5, linewidth=1.5,
                  color=c, label=name, zorder=3)
        for x, y in zip(xs, ys):
            ax_b.annotate(f"{y * 1000:.0f}", xy=(x, y), xytext=(0, 6),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 1.0, color=c)
    ax_b.set_yscale("log")
    style_axes(ax_b, title="(b) 一阶段耗时：百毫秒~秒级（quota_a2.5 格）",
               xlabel="任务规模 N", ylabel="耗时（s，对数轴）")

    # ── (c) 惰性边数 vs N（双对数）───────────────────────────────
    for v in variants:
        ys = [edges(g[n].get(v, {})) for n in ns]
        ax_c.plot(ns, ys, marker="o", markersize=4.5, linewidth=1.5,
                  color=color(v), label=label(v), zorder=3)
        for x, y in zip(ns, ys):
            ax_c.annotate(f"{y}", xy=(x, y), xytext=(0, 6),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 1.0, color=color(v))
    # 全量矩阵：三个规模全测（实测点）+ O(N²) 参考线
    ax_c.plot(fx, [edges(g[n][_FULL]) for n in ns if _FULL in g[n]],
              marker="D", markersize=4.5, linewidth=1.5, color=color(_FULL),
              label=label(_FULL), zorder=3)
    # O(N²) 参考线：以实测最大规模的全量边数为锚（斜率 2）
    anchor_n = max(n for n in ns if _FULL in g[n])
    anchor = float(edges(g[anchor_n][_FULL]))
    guide = [anchor * (n / float(anchor_n)) ** 2 for n in ns]
    ax_c.plot(ns, guide, color="#9aa5b1", linestyle=":", linewidth=1.2,
              label="O(N²) 参考（斜率 2）", zorder=2)
    for x, y in zip(ns, guide):
        ax_c.annotate(f"{y:.0f}", xy=(x, y), xytext=(0, -12),
                      textcoords="offset points", ha="center",
                      fontsize=FS["annotation"] - 1.0, color="#6b7280")
    ax_c.set_xscale("log")
    ax_c.set_yscale("log")
    style_axes(ax_c, title="(c) 惰性表现算边数：O(N) vs 全量 O(N²)",
               xlabel="任务规模 N（对数轴）", ylabel="边数（对数轴）")

    for ax in (ax_a, ax_b):
        ax.set_xticks(xs, [str(n) for n in ns])

    ax_a.legend(loc="upper left", fontsize=FS["legend"] - 0.5)
    ax_b.legend(loc="upper left", fontsize=FS["legend"] - 0.5)
    ax_c.legend(loc="lower right", fontsize=FS["legend"] - 1.0)

    fig.suptitle("耗时分解：二阶段与 N 解耦、一阶段百毫秒级、惰性边 O(N)（成功标准 2）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.06, 1, 0.93))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]。t_hint/t_selection/t_build/t_muas "
             "为 perf_counter 分段计时；lazy_edges = LazyPairwiseCost 实际现算边数"
             "（按需估算 + 缓存，值与全量 eager 建表逐位一致）。全量对照三个规模"
             "全测。单 seed（42）。")

    stem = Path(out_dir) / "R03_time_breakdown"
    return save_figure(fig, stem, formats, dpi=dpi)


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
