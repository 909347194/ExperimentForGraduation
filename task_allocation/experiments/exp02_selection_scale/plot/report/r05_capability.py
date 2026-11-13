# -*- coding: utf-8 -*-
"""图 ``r05_capability``——档位观察：cap_x* 阶梯的规模、墙钟与任务去向。

【report 层 · 描述性观测】

登记能力驱动口径（``mode=primary``，选择数接到
cap(t)=⌊budget_scale·B/c̃⌋）三档在给定任务规模下的选择规模、二阶段墙钟
与任务去向，全部描述性：

    (a) 二阶段墙钟 vs 选择规模    x = n_sel（对数轴），y = t_muas
                      （s，对数轴）；红色虚线为同 N 参照臂的 t_muas 水平。
                      灰色带 = cap(t) 代表值附近（±3，从 metrics 现算）。
    (b) 任务去向 vs 选择规模    x = n_sel（对数轴），y = 交付数 /
                      未完成数（两项之和 = 到达 N）。

本图不判断哪一档更好、不给选型结论、不给相对快慢比值；cap(t) 灰带只是
结构参考位置，不是「拐点」或取值建议。

数据来源：``metrics.json`` 的 ``scales[]``（cap_x* 阶梯 + 全部档位 +
full_baseline 参照）。
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


from ..theme import FIG_WIDE, FS, footnote, plt, save_figure, style_axes
from ._common import (
    acc_of,
    cap_t,
    color,
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


def _cap_t_of(metrics: dict[str, Any]) -> int:
    """cap(t) 代表值：cap_x* 各行 capability_cap 的中位数。

    无 capability_cap 字段时按机队规模推导（⌈max_ratio·K⌉ 量级），
    不写死旧场景数值。
    """
    vals = [
        cap_t(row)
        for row in (metrics.get("scales") or [])
        if str(row.get("variant")) in _CAP_POINTS and cap_t(row) is not None
    ]
    if not vals:
        return max(1, 2 * int(metrics.get("K", 0) or 0))
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

    # cap(t) 结构参考带（不是取值建议）
    band = (max(cap_t_val - 3, 1), cap_t_val + 3)
    for ax in (ax_a, ax_b):
        ax.axvspan(band[0], band[1], color="#9aa5b1", alpha=0.18, zorder=1)

    # ── (a) 二阶段墙钟 vs 选择规模 ────────────────────────────────
    for n in ns_with_full:
        y = t_muas_s(g[n][_FULL])
        ax_a.axhline(y, color="#e45756", linestyle="--",
                     linewidth=1.0, alpha=0.6, zorder=1)
        # 标注放右端右对齐，避免三条水平线的标签在左侧叠成一团
        ax_a.text(195.0, y, f"参照 N={n} ", color="#e45756",
                  fontsize=FS["annotation"], va="bottom", ha="right")
    for v in variants:
        for n in ns:
            row = g[n].get(v)
            if row is None:
                continue
            k = float(sel_n(row))
            ax_a.scatter(k, max(t_muas_s(row), 1e-3), s=46, marker=marker(n),
                         color=color(v), edgecolor="white", linewidth=0.7,
                         zorder=3)
    ax_a.set_xscale("log")
    ax_a.set_yscale("log")
    ax_a.set_xlim(8, 210)
    style_axes(ax_a, title="(a) 二阶段墙钟 vs 选择规模",
               xlabel="选择规模 n_sel（对数轴）",
               ylabel="二阶段墙钟 t_muas（s，对数轴）")

    # ── (b) 任务去向 vs 选择规模 ──────────────────────────────────
    for v in variants:
        for n in ns:
            row = g[n].get(v)
            if row is None:
                continue
            a = acc_of(row)
            if not a:
                continue  # 收窄前旧档无分账：跳过，不伪造 0
            k = float(sel_n(row))
            ax_b.scatter(k, int(a.get("n_done", 0)), s=46, marker=marker(n),
                         color=color(v), edgecolor="white", linewidth=0.7,
                         zorder=3)
            ax_b.scatter(k, int(a.get("n_remain", 0)), s=46, marker=marker(n),
                         facecolor="none", edgecolor=color(v), linewidth=1.3,
                         zorder=3)
    ax_b.set_xscale("log")
    ax_b.set_xlim(8, 210)
    ax_b.set_ylim(bottom=0)
    style_axes(ax_b, title="(b) 任务去向（实心=交付，空心=未完成）",
               xlabel="选择规模 n_sel（对数轴）",
               ylabel="任务数（两项之和 = 到达 N）")

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
    leg = ax_a.legend(handles=v_handles, loc="upper left",
                      fontsize=FS["legend"] - 0.5, title="档位",
                      title_fontsize=FS["legend"] - 0.5)
    ax_a.add_artist(leg)
    ax_b.legend(handles=[
        Line2D([], [], marker="o", linestyle="", markersize=5, color="0.35",
               label="实心 = 交付 done"),
        Line2D([], [], marker="o", linestyle="", markersize=5,
               markerfacecolor="none", markeredgecolor="0.35",
               label="空心 = 未完成 remain"),
    ], loc="upper right", fontsize=FS["legend"] - 0.5)
    fig.legend(handles=n_handles, loc="lower center", ncol=len(n_handles),
               fontsize=FS["legend"], frameon=False,
               bbox_to_anchor=(0.5, 0.085))

    budget_km = (float(metrics.get("K", 0) or 0)
                 * float(metrics.get("uav_max_range_m", 0.0) or 0.0) / 1000.0)
    fig.suptitle("档位观察：cap_x* 阶梯的选择规模、墙钟与任务去向",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.135, 1, 0.93))
    footnote(fig,
             "数据来源：results/metrics.json 的 scales[]。k = n_selected；cap(t)=下取整"
             f"(budget_scale·B/c)（B=机队总航程 {budget_km:g} km，c=最近可用机三维航程中位数），"
             "实测见 metrics 的 capability_cap 字段。灰色带 = cap(t)±3"
             f"（本次代表值 cap(t)≈{cap_t_val}），仅为结构参考位置、不是取值建议。"
             "分账：单周期快照无后续周期，未选中与选中未交付一律记 remain（未完成），"
             "本实验不评价完成约束。墙钟为单 seed 单次观测、跨格共享 DEM 段长缓存，"
             "只描述量级、不作机制归因或档位比较。参照臂三个规模全测。单 seed（42）。")

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
