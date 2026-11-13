# -*- coding: utf-8 -*-
"""图 ``r01_tradeoff``——单周期规模—耗时曲线与任务去向分账（核心图）。

【report 层 · 描述性观测】

对应论文 §4.5「任务规模 20→100+ 的单周期求解时间曲线，说明分层框架 +
选择层裁剪对搜索规模的压缩效果」。双联面板：

    (a) 规模—耗时曲线   x = 任务规模 N（分类轴），y = 单周期端到端墙钟
                        t_total（s，对数轴）；每条线一个给定档位，
                        红色虚线为同口径参照臂（不调用选择层）。
    (b) 任务去向分账    每个 N 的堆叠柱：交付 done / 未选中 / 选中未交付，
                        三者之和恒等于到达 N（恒等式门禁）。

两图都只描述量级与结构，**不作机制归因、不给相对快慢比值、不判断档位
高低**。墙钟为单 seed 单次观测且跨格共享 DEM 段长缓存，存在先后偏倚。

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
    acc_of,
    color,
    grouped,
    label,
    scales_present,
    t_total_s,
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
    xs = np.arange(len(ns))

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(FIG_WIDE, 4.4))

    # ── (a) 规模—耗时曲线（论文可扩展性主图） ──────────────────────
    for v in variants:
        ys = [t_total_s(g[n][v]) for n in ns if v in g[n]]
        vx = [x for x, n in enumerate(ns) if v in g[n]]
        ax_a.plot(vx, ys, marker="o", markersize=4.5, linewidth=1.5,
                  color=color(v), label=label(v), zorder=3)
    fx = [x for x, n in enumerate(ns) if _FULL in g[n]]
    if fx:
        ax_a.plot(fx, [t_total_s(g[n][_FULL]) for n in ns if _FULL in g[n]],
                  marker="D", markersize=4.5, linewidth=1.5,
                  color=color(_FULL), linestyle="--",
                  label=label(_FULL), zorder=3)
    ax_a.set_yscale("log")
    ax_a.set_xticks(xs, [str(n) for n in ns])
    ax_a.set_xlim(-0.25, len(ns) - 0.75)
    style_axes(ax_a,
               title="(a) 单周期端到端墙钟随任务规模的变化",
               xlabel="任务规模 N（到达任务数）",
               ylabel="端到端墙钟 t_total（s，对数轴）")
    ax_a.legend(loc="upper left", fontsize=FS["legend"] - 0.5, ncol=2)

    # ── (b) 任务去向分账（堆叠柱，恒等式 arrived = done + remain） ───
    width = 0.62
    for x, n in zip(xs, ns):
        row = g[n].get(_FULL) or next(iter(g[n].values()), {})
        a = acc_of(row)
        if not a:
            # 收窄前旧档没有分账：明确跳过该柱，不伪造 0。
            continue
        done = int(a.get("n_done", 0))
        not_sel = int(a.get("n_not_selected", 0))
        deferred = int(a.get("n_deferred_in_selected", 0))
        bottom = 0
        for val, c, name in (
            (done, "#54a24b", "交付 done"),
            (deferred, "#f58518", "选中未交付"),
            (not_sel, "#b279a2", "未选中"),
        ):
            if val <= 0:
                continue
            ax_b.bar([x], [val], width, bottom=bottom, color=c,
                     edgecolor="white", linewidth=0.6, zorder=3)
            bottom += val
        ax_b.text(x, bottom, f"N={n}", ha="center", va="bottom",
                  fontsize=FS["annotation"], color="#3c4148")
    ax_b.set_xticks(xs, [str(n) for n in ns])
    ax_b.set_ylim(bottom=0)
    style_axes(ax_b,
               title="(b) 参照臂的任务去向分账",
               xlabel="任务规模 N（到达任务数）",
               ylabel="任务数（三项之和 = 到达 N）")
    # 图例用显式句柄：N=10 时后两类为 0、不画柱，若靠 bar(label=...) 自动收集
    # 会只剩「交付」一项，而 N=50/100 的柱里明明还有选中未交付/未选中。
    from matplotlib.patches import Patch

    ax_b.legend(
        handles=[
            Patch(facecolor="#54a24b", edgecolor="white", label="交付 done"),
            Patch(facecolor="#f58518", edgecolor="white", label="选中未交付"),
            Patch(facecolor="#b279a2", edgecolor="white", label="未选中"),
        ],
        loc="upper left", fontsize=FS["legend"] - 0.5,
    )

    fig.suptitle("单周期任务规模可扩展性：墙钟曲线与任务去向分账",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.135, 1, 0.93))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]。t_total = hint + selection + "
             "build + muas 四段之和（参照臂前两段为 0）；两臂共用同一惰性代价表与"
             "同一距离函数，只差是否调用选择层。分账口径：单周期静态快照无后续周期，"
             "未选中与选中未交付一律记 remain（未完成），unreachable 本实验不可观测、"
             "记为 null 而非 0；本实验不评价完成约束，完成约束由 exp01 滚动闭环检验。"
             "墙钟为单 seed 单次 perf_counter 观测、跨格共享 DEM 段长记忆化缓存，"
             "只描述量级、不作机制归因。F1 只作事后指标、不进搜索标量 f。")

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
