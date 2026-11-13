# -*- coding: utf-8 -*-
"""生成 exp02 的论文主图 ``PAPER_scalability``（PNG + PDF）。

从 ``results/metrics.json`` 单源读数，不重跑求解、不改动既有 6 张登记图。

为什么单独做这张：论文 §4.5 要的是「任务规模 20→100+ 的单周期求解时间
曲线」，而既有 R01 的右面板是审计用的任务去向分账，信息重心不在论文叙事上。
本图把两个描述性观察放进同一幅：

    (a) 到达规模 N → 单周期端到端墙钟（论文占位符字面要求的曲线）
    (b) 同一批点按「进入二阶段的任务数」重排 → 与墙钟的对应关系

(b) 是本实验真正值得写进正文的一句：不同到达规模 N 的点，只要进入二阶段
的任务数相同，单周期墙钟就落在同一量级。这里只登记对应关系，不作因果或
机制说明，也不判断档位高低。

口径门禁与出表同源（scope.kind + 全格 identity_ok），旧档直接拒绝。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))

from accounting import assert_no_quality_claims  # noqa: E402
from plot.report._common import (  # noqa: E402
    VARIANT_ORDER,
    color,
    grouped,
    label,
    marker,
    scales_present,
    sel_n,
    t_total_s,
)
from plot.theme import (  # noqa: E402
    FIG_WIDE,
    FS,
    footnote,
    plt,
    save_figure,
    setup_style,
    style_axes,
)

OUT_STEM = EXP_DIR / "results" / "figures" / "PAPER_scalability"
_FULL = "full_baseline"


def load_metrics() -> dict:
    path = EXP_DIR / "results" / "metrics.json"
    if not path.exists():
        raise SystemExit("[paper] 缺少 results/metrics.json，请先跑正式档")
    m = json.loads(path.read_text(encoding="utf-8"))
    # 与 build_tables.validate_metrics 同口径：拒绝收窄前旧档与不闭合档
    if (m.get("scope") or {}).get("kind") != "single_cycle_scalability_observation":
        raise SystemExit("[paper] scope.kind 不符，拒绝旧档")
    rows = list(m.get("scales") or [])
    if not rows:
        raise SystemExit("[paper] scales 为空")
    bad = [i for i, r in enumerate(rows)
           if (r.get("accounting") or {}).get("identity_ok") is not True]
    if bad:
        raise SystemExit(f"[paper] 分账未闭合：{bad}")
    return m


def build() -> list[Path]:
    setup_style()
    metrics = load_metrics()
    g = grouped(metrics)
    ns = scales_present(metrics)
    variants = [v for v in VARIANT_ORDER if any(v in g[n] for n in ns)]
    xs = list(range(len(ns)))

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(FIG_WIDE, 4.6))

    # ── (a) 到达规模 N → 端到端墙钟 ─────────────────────────────────
    for v in variants:
        vx = [i for i, n in enumerate(ns) if v in g[n]]
        ys = [t_total_s(g[n][v]) for n in ns if v in g[n]]
        is_full = v == _FULL
        ax_a.plot(
            vx, ys, marker="D" if is_full else "o", markersize=4.2,
            linewidth=1.5, linestyle="--" if is_full else "-",
            color=color(v), label=label(v), zorder=3,
        )
    ax_a.set_yscale("log")
    ax_a.set_xticks(xs, [str(n) for n in ns])
    ax_a.set_xlim(-0.25, len(ns) - 0.75)
    style_axes(
        ax_a,
        title="(a) 到达任务规模与单周期端到端墙钟",
        xlabel="到达任务规模 N（单周期静态快照）",
        ylabel="端到端墙钟 t_total（s，对数轴）",
    )
    ax_a.legend(loc="upper left", fontsize=FS["legend"] - 0.5, ncol=2)

    # ── (b) 同一批点按进入二阶段的任务数重排 ────────────────────────
    for v in variants:
        pts = [(sel_n(g[n][v]), t_total_s(g[n][v]), n)
               for n in ns if v in g[n]]
        # 连线只表示同一档位随 N 的执行顺序，不代表连续函数或拟合
        ax_b.plot([p[0] for p in pts], [p[1] for p in pts],
                  color=color(v), alpha=0.35, linewidth=1.0, zorder=1)
        for xval, yval, n in pts:
            ax_b.scatter([xval], [yval], marker=marker(n), s=30,
                         color=color(v), edgecolor="white", linewidth=0.6,
                         zorder=3)
    ax_b.set_yscale("log")
    ax_b.set_xlim(0, 106)
    style_axes(
        ax_b,
        title="(b) 进入二阶段的任务数与单周期端到端墙钟",
        xlabel="进入二阶段的任务数 n_sel",
        ylabel="端到端墙钟 t_total（s，对数轴）",
    )
    from matplotlib.lines import Line2D

    ax_b.legend(
        handles=[
            Line2D([], [], marker=marker(n), linestyle="", color="#5F5E5A",
                   markersize=5, label=f"N={n}")
            for n in ns
        ],
        loc="upper left", fontsize=FS["legend"] - 0.5, ncol=1,
        title="点形状 = 到达规模；颜色同左图",
        title_fontsize=FS["legend"] - 0.5,
    )
    # 只标一个锚点，避免 18 个点全标造成叠字
    if _FULL in g[ns[-1]]:
        row = g[ns[-1]][_FULL]
        ax_b.annotate(
            f"N={ns[-1]} 全量对照\n{t_total_s(row):.0f} s",
            xy=(sel_n(row), t_total_s(row)), xytext=(-74, 14),
            textcoords="offset points", fontsize=FS["annotation"],
            color="#712B13", ha="left", va="bottom",
            arrowprops=dict(arrowstyle="-", color="#993C1D", linewidth=0.8),
        )

    fig.suptitle(
        "单周期任务规模可扩展性：到达规模、进入规模与端到端墙钟",
        fontsize=FS["suptitle"],
    )
    fig.tight_layout(rect=(0, 0.155, 1, 0.93))
    note = (
        "数据来源：results/metrics.json 的 scales[]（18 格，单 seed 42）。"
        "(a) 每格墙钟 = hint + selection + build + muas 四段之和；"
        "(b) 把同一批点按进入二阶段的任务数重排，细线只表示同一档位随 N 的"
        "执行顺序，不是拟合或连续函数。调用选择层的各档与不做选择的对照组"
        "共用同一惰性代价表、同一距离函数和同一求解预算，唯一差别为是否调用"
        "选择层。单周期静态快照无后续周期，未选中与选中未交付一律记 remain"
        "（未完成），本图不评价完成约束。墙钟为单次 perf_counter 观测、"
        "跨格共享 DEM 段长记忆化缓存，只描述量级、不作机制归因。"
        "F1 只作事后指标、不进搜索标量 f。"
    )
    assert_no_quality_claims(note, where="paper footnote")
    footnote(fig, note)
    return save_figure(fig, OUT_STEM, ("png", "pdf"), dpi=300)


if __name__ == "__main__":
    for p in build():
        print(f"[paper] wrote {p}")
