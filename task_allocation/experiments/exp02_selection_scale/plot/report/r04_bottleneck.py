# -*- coding: utf-8 -*-
"""图 ``r04_bottleneck``——结构观察：选择名额由 max_ratio·K 封顶。

【report 层 · 描述性观测】

登记「候选预筛的截断规模」在 intersect 口径下的结构：选择数被
⌈max_ratio·K⌉ 封顶，候选名额 ⌈α·K⌉ 增大未必传导到选择数。

双联面板：

    (a) 候选 n_cand vs 选择 n_sel（名额族）   每组 N 下两档的候选名额
                      与最终选择数；红色虚线为封顶值（从 metrics 现算）。
    (b) 同 n_sel 下的任务去向分账   两档各自的交付 / 未选中 /
                      选中未交付（堆叠柱），仅登记结构。

本图只描述结构，不判断 α 是否是「有效旋钮」、不给档位选型结论
（选型归 exp04 / exp06）。能力驱动口径的结构见图 r05。

数据来源：``metrics.json`` 的
``scales[].selection.{capability_n_cand,n_selected,capability_n_sel}``
与 ``accounting``。
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
    acc_of,
    color,
    grouped,
    label,
    n_candidate,
    n_sel_cap,
    sel_n,
)

__all__ = ["plot_r04_bottleneck"]

_QUOTA = ("quota_a2.5", "quota_a5")


def plot_r04_bottleneck(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    g = grouped(metrics)
    if not g or not all(v in g[sorted(g)[0]] for v in _QUOTA):
        return []
    ns = sorted(g)
    xs = np.arange(len(ns))

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(FIG_WIDE, 4.3))

    # ── (a) 候选 n_cand vs 选择 n_sel ─────────────────────────────
    width = 0.34
    for i, v in enumerate(_QUOTA):
        ks = [n_candidate(g[n].get(v, {})) or 0 for n in ns]
        ss = [sel_n(g[n].get(v, {})) for n in ns]
        pos = xs + (i - 0.5) * width
        ax_a.bar(pos, ks, width * 0.9, color=color(v), alpha=0.40,
                 edgecolor="white", linewidth=0.6, zorder=2,
                 label=f"{label(v)}｜候选 n_cand")
        ax_a.bar(pos, ss, width * 0.45, color=color(v),
                 edgecolor="white", linewidth=0.6, zorder=3,
                 label=f"{label(v)}｜选择 n_sel")
        for p, k, s in zip(pos, ks, ss):
            ax_a.annotate(f"{k}", xy=(p, k), xytext=(0, 2),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 1.0, color=color(v))
            ax_a.annotate(f"{s}", xy=(p, s), xytext=(0, 2),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 1.0,
                          color="white", fontweight="bold")
    # 封顶线
    lim_v = None
    for n in ns:
        mv = n_sel_cap(g[n].get(_QUOTA[0], {}))
        if mv is not None:
            lim_v = mv
            break
    if lim_v is not None:
        ax_a.axhline(lim_v, color="#e45756", linestyle="--", linewidth=1.3,
                     zorder=4)
        ax_a.text(xs[-1] + 0.42, lim_v, f" max_ratio·K 上取整={lim_v} 封顶",
                  color="#e45756", fontsize=FS["annotation"], va="center")
    style_axes(ax_a, title="(a) 名额族：候选 n_cand 与选择 n_sel 对照",
               xlabel="任务规模 N", ylabel="任务数")
    ax_a.set_xticks(xs, [str(n) for n in ns])
    ax_a.set_ylim(bottom=0, top=48)
    ax_a.legend(loc="upper right", fontsize=FS["legend"] - 1.0, ncol=1)

    # ── (b) 同 n_sel 下的任务去向分账（堆叠柱） ───────────────────
    width = 0.3
    palette = (
        ("n_done", "#54a24b", "交付 done"),
        ("n_deferred_in_selected", "#f58518", "选中未交付"),
        ("n_not_selected", "#b279a2", "未选中"),
    )
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    for i, v in enumerate(_QUOTA):
        for x, n in zip(xs, ns):
            a = acc_of(g[n].get(v, {}))
            pos = x + (i - 0.5) * width
            bottom = 0
            for key, c, name in palette:
                val = int(a.get(key, 0) or 0)
                if val <= 0:
                    continue
                ax_b.bar([pos], [val], width * 0.88, bottom=bottom, color=c,
                         edgecolor="white", linewidth=0.6, zorder=3,
                         label=name if x == xs[0] and i == 0 else None)
                bottom += val
            # 柱顶只标 N（每组一次），避免在两柱上写长档位名造成叠字
            if i == 0:
                ax_b.text(x, max(bottom, 0), f"N={n}", ha="center", va="bottom",
                          fontsize=FS["annotation"], color="#3c4148")
    style_axes(ax_b, title="(b) 同 n_sel 下的任务去向分账",
               xlabel="任务规模 N", ylabel="任务数（三项之和 = 到达 N）")
    ax_b.set_xticks(xs, [str(n) for n in ns])
    ax_b.set_ylim(bottom=0, top=max(ns) * 1.12)
    # 去向类别（实色方块）与左右柱对应档位（细边菱形）分两行图例
    handles = [
        Patch(facecolor=c, edgecolor="white", label=name)
        for _, c, name in palette
    ]
    handles += [
        Line2D([], [], marker="D", linestyle="", markersize=5,
                   color="#6b7280", label=f"{label(v)}（{'左' if i == 0 else '右'}柱）")
        for i, v in enumerate(_QUOTA)
    ]
    # 单列：两列排布会把「未选中」与「左柱」并排，容易被读成类别与柱位
    # 存在对应关系；实际是两类互相独立的图例项。
    ax_b.legend(handles=handles, loc="upper left",
                fontsize=FS["legend"] - 1.0, ncol=1)

    fig.suptitle("结构观察：名额族候选规模、选择数封顶与任务去向",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.06, 1, 0.93))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]（quota_a2.5 / quota_a5 两档）。"
             "intersect 口径下 capability 只能收紧不能放宽；候选名额（α·K 上取整）与"
             f"选择封顶（本次 capability_n_sel={lim_v}）均以本次 metrics 为准。"
             "分账：单周期快照无后续周期，未选中与选中未交付一律记 remain（未完成），"
             "本实验不评价完成约束。两档只作结构登记，不作档位选型结论；"
             "能力驱动口径的结构见图 r05。单 seed（42）。")

    stem = Path(out_dir) / "R04_bottleneck"
    return save_figure(fig, stem, formats, dpi=dpi)


if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
