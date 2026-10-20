# -*- coding: utf-8 -*-
"""图 ``r04_bottleneck``——瓶颈定位：选择名额 max_ratio·K，不是 α。

【report 层 · 结论与论文插图】

回答「Top-αK 的截断规模该按什么标定？」——原结论 2 说「α 需按机队总吞吐
标定」；本扫描给出更精确的答案：intersect 口径下候选名额 ⌈α·K⌉ 提到 40
毫无用处，因为**选择数被 ⌈max_ratio·K⌉=16 封顶**。

双联面板：

    (a) 候选 k vs 选择 sel（名额族）   每组 N 下两变体的候选名额
                      （20 / 40，浅柱）与最终选择数（16 / 16，深柱）；
                      红色虚线为 ⌈max_ratio·K⌉=16 的封顶线——sel 触线即停。
    (b) 名额族 F1 对照   同 sel 下两变体交付收益几乎相同
                      （133.2 vs 129.1 @ N=50，差异 < 4%），
                      说明候选名额在该区间是「死旋钮」。

读图要点：若瓶颈在 α，(a) 中 sel 应随 k 上升；(b) 中两柱应明显分化。
修正后的标定口径（选择数接到 cap(t)、候选名额只需 ≥ 选择数）见图 r05。

数据来源：``metrics.json`` 的
``scales[].selection.{capability_preselect_k,n_selected,capability_marginal_limit}``
与 ``muas.f1_reward``。
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
    f1,
    grouped,
    k_candidate,
    label,
    marginal_limit,
    sel_n,
    scales_present,
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

    # ── (a) 候选 k vs 选择 sel ───────────────────────────────────
    width = 0.34
    for i, v in enumerate(_QUOTA):
        ks = [k_candidate(g[n].get(v, {})) or 0 for n in ns]
        ss = [sel_n(g[n].get(v, {})) for n in ns]
        pos = xs + (i - 0.5) * width
        ax_a.bar(pos, ks, width * 0.9, color=color(v), alpha=0.40,
                 edgecolor="white", linewidth=0.6, zorder=2,
                 label=f"{label(v)}｜候选 k")
        ax_a.bar(pos, ss, width * 0.45, color=color(v),
                 edgecolor="white", linewidth=0.6, zorder=3,
                 label=f"{label(v)}｜选择 sel")
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
        mv = marginal_limit(g[n].get(_QUOTA[0], {}))
        if mv is not None:
            lim_v = mv
            break
    if lim_v is not None:
        ax_a.axhline(lim_v, color="#e45756", linestyle="--", linewidth=1.3,
                     zorder=4)
        ax_a.text(xs[-1] + 0.42, lim_v, f" max_ratio·K 上取整={lim_v} 封顶",
                  color="#e45756", fontsize=FS["annotation"], va="center")
    style_axes(ax_a, title="(a) 名额族：候选 k 翻倍、选择 sel 触顶不动",
               xlabel="任务规模 N", ylabel="任务数")
    ax_a.set_xticks(xs, [str(n) for n in ns])
    ax_a.set_ylim(bottom=0, top=48)
    ax_a.legend(loc="upper right", fontsize=FS["legend"] - 1.0, ncol=1)

    # ── (b) 名额族 F1 对照 ───────────────────────────────────────
    width = 0.3
    for i, v in enumerate(_QUOTA):
        ys = [f1(g[n].get(v, {})) for n in ns]
        pos = xs + (i - 0.5) * width
        ax_b.bar(pos, ys, width * 0.88, color=color(v),
                 edgecolor="white", linewidth=0.6, zorder=3, label=label(v))
        for p, y in zip(pos, ys):
            ax_b.annotate(f"{y:.1f}", xy=(p, y), xytext=(0, 2),
                          textcoords="offset points", ha="center",
                          fontsize=FS["annotation"] - 0.5, color=color(v))
        if i == 1:
            for p, y, n in zip(pos, ys, ns):
                base = f1(g[n].get(_QUOTA[0], {}))
                if base > 0:
                    d = 100.0 * (y - base) / base
                    ax_b.annotate(f"{d:+.1f}%", xy=(p, y / 2.0), ha="center",
                                  fontsize=FS["annotation"], color="white",
                                  fontweight="bold")
    style_axes(ax_b, title="(b) 名额族 F1：同 sel 下几无差异（标注为相对差）",
               xlabel="任务规模 N", ylabel="交付收益 F1")
    ax_b.set_xticks(xs, [str(n) for n in ns])
    ax_b.set_ylim(bottom=0)
    ax_b.legend(loc="upper left", fontsize=FS["legend"])

    fig.suptitle("瓶颈定位：选择数被 max_ratio·K 上取整封顶，α 在 intersect 口径下是死旋钮",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.06, 1, 0.93))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]（quota_a2.5 / quota_a5 两变体）。"
             "intersect 口径下 capability 只能收紧不能放宽：候选名额 α·K 上取整从 20 提到 40，"
             "选择数仍被 max_ratio·K 上取整=16 封顶（capability_marginal_limit），故 F1 差异 < 4%。"
             "修正口径（mode=primary，选择数接到 cap(t)）见图 r05。单 seed（42）。")

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
