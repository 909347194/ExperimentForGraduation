# -*- coding: utf-8 -*-
"""图 ``outcome_summary``——结果总览：正确执行吞吐与任务终局。

【report 层 · 结果分析】

回答 Q4「实验说明什么」的**核心图**：滚动闭环跑完，任务到底做得怎么样。

口径（2026-10-14 长程协议）：**完成率不是目标**，周期可无限延长；真实目标 =
持续到达下「尽可能多且正确地完成任务」。因此主面板是正确执行与吞吐，
完成率降为辅助对账。

2×2 四联面板：

    (a) 每周期执行构成   出动 = 按时交付 + 迟到交付 + 废飞（堆叠柱）；右轴 = 累计
                         吞吐（累计完成 / 周期数）
    (b) 任务终局构成     到达任务的最终去向：完成 / 滞留池 / 到期作废 / 曾被物理拦回
                         （拦回 = 解被判物理不可行、解绑回池——迟到交付已计入完成）
    (c) 双目标值         每周期 F1 收益（迟到折减后）/ F2 代价，不可行周期红字标记
    (d) 关键指标表       summary 全字段 + 单/多 seed 标注

数据来源：``metrics.json`` 的 cycles[] / summary / seeds。
本图对**调试期同样有用**：按时率塌陷、全周期不可行、物理拦回等回归问题，
在这张图上一眼可见（而不是被平均进某个数字里）。
"""

from __future__ import annotations

# ── 独立运行自举：支持直接 `python plot/.../xxx.py`（脚本方式）──
# 脚本方式运行时 __package__ 为空，模块内相对导入不可用；此处转以 -m 语义
# 重跑自身（runpy 会正确设置包上下文），再触发文件末尾的 __main__ 守卫出图。
if __name__ == "__main__" and __package__ in (None, ""):
    import sys, runpy
    from pathlib import Path as _P
    _here = _P(__file__).resolve()
    _root = _here.parents[2]   # 实验根 = plot 的上两级（使 `plot` 包可导入）
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    # 同时把项目根（含 task_allocation 包）加入路径：否则 plotting 内
    # `from task_allocation.methods.utils import GeoTIFFDEM` 会因找不到包而
    # 静默回退到 solution.json 的降采样 DEM（115x101px），地图被放大 10~24 倍变糊。
    _proj = _here
    while not (_proj / "task_allocation" / "__init__.py").is_file() and _proj.parent != _proj:
        _proj = _proj.parent
    if (_proj / "task_allocation" / "__init__.py").is_file() and str(_proj) not in sys.path:
        sys.path.insert(0, str(_proj))
    _rel = _here.relative_to(_root / "plot")
    _mod = "plot." + ".".join(_rel.with_suffix("").parts)
    runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    raise SystemExit(0)


from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import FIG_WIDE, FS, MONO_ZH, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_outcome_summary"]


def plot_outcome_summary(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    summary = metrics.get("summary") or {}
    if not cycles or not summary:
        return []

    n_cycles = len(cycles)
    cyc_labels = [f"C{c.get('cycle')}" for c in cycles]
    x = np.arange(n_cycles)
    w = 0.38

    # ── 数据准备 ─────────────────────────────────────────────────
    n_exec = [int(c.get("n_executed", 0)) for c in cycles]
    n_done = [int(c.get("n_completed", 0)) for c in cycles]
    # 迟到不是废飞（2026-10-15）：完成 = 按时交付 + 迟到交付；
    # 废飞只剩物理不可行的弹回（n_infeasible）
    n_late = [int(c.get("n_late", 0)) for c in cycles]
    n_on_time = [d - l for d, l in zip(n_done, n_late)]
    n_waste = [int(c.get("n_infeasible", 0)) for c in cycles]
    cum_done = np.cumsum(n_done)
    # 累计正确执行吞吐 = 累计完成（含迟到）/ 周期序号（长程协议主指标）
    thru_curve = [d / (i + 1) for i, d in enumerate(cum_done)]
    thru_final = thru_curve[-1] if thru_curve else 0.0

    done_ids: set[int] = set()
    expired_ids: set[int] = set()
    gated_ids: set[int] = set()
    for c in cycles:
        done_ids.update(int(i) for i in (c.get("completed_ids") or []))
        expired_ids.update(int(i) for i in (c.get("expired_ids") or []))
        gated_ids.update(int(i) for i in (c.get("infeasible_ids") or []))
    n_arrived = int(summary.get("n_arrived", 0))
    n_done_total = int(summary.get("n_completed", len(done_ids)))
    n_remain = int(summary.get("n_remaining_pool",
                               max(n_arrived - n_done_total - len(expired_ids), 0)))
    n_expired = len(expired_ids)
    n_gated = len(gated_ids - done_ids - expired_ids)
    n_late_total = int(summary.get("n_late", 0))
    on_time_rate = float(summary.get("on_time_rate", 0.0))
    avg_late = float(summary.get("avg_lateness_s", 0.0))

    f1 = [float((c.get("muas") or {}).get("f1_reward", 0.0)) for c in cycles]
    f2 = [float((c.get("muas") or {}).get("f2_cost", 0.0)) / 1000.0 for c in cycles]
    feas = [bool((c.get("muas") or {}).get("feasible", False)) for c in cycles]

    n_seeds = len(metrics.get("seeds") or [])
    feas_rate = float(summary.get("cycle_feasible_rate", 0.0))
    completion = float(summary.get("completion_rate", 0.0))
    correct_rate = float(summary.get("correct_exec_rate", 0.0))
    throughput = float(summary.get("throughput_per_cycle", 0.0))
    n_waste_total = int(summary.get("n_infeasible_delivery", 0))
    n_eliminated_total = int(summary.get("n_eliminated", 0))

    fig, ((ax_a, ax_b), (ax_c, ax_d)) = plt.subplots(
        2, 2, figsize=(FIG_WIDE, 7.3))

    # ── (a) 每周期执行构成 + 累计吞吐 ────────────────────────────
    ax_a.bar(x, n_on_time, 0.62, color=PALETTE["executed"], alpha=0.9,
             label="按时交付", edgecolor="white", linewidth=0.4)
    ax_a.bar(x, n_late, 0.62, bottom=n_on_time, color=PALETTE["deferred"],
             alpha=0.9, label="迟到交付（计完成）", edgecolor="white",
             linewidth=0.4)
    ax_a.bar(x, n_waste, 0.62, bottom=n_done, color=PALETTE["release"],
             alpha=0.85, label="废飞（物理不可行）", edgecolor="white",
             linewidth=0.4, hatch="//")
    style_axes(ax_a, title=f"(a) 每周期执行构成（柱高=出动；"
                           f"终点吞吐 {thru_final:.2f} 任务/周期）",
               xlabel="滚动周期", ylabel="任务数")
    ax_a.set_xticks(x, cyc_labels)
    ax_a2 = ax_a.twinx()
    ax_a2.plot(x, thru_curve, "-o", color=PALETTE["cost"],
               lw=1.6, ms=4, label="累计吞吐（右轴）")
    ax_a2.set_ylabel("累计吞吐（任务/周期）", color=PALETTE["cost"],
                     fontsize=FS["base"])
    ax_a2.set_ylim(bottom=0)
    ax_a2.tick_params(axis="y", labelcolor=PALETTE["cost"])
    ax_a2.spines["right"].set_visible(True)
    ax_a2.grid(False)
    h1, l1 = ax_a.get_legend_handles_labels()
    h2, l2 = ax_a2.get_legend_handles_labels()
    ax_a.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=FS["legend"])

    # ── (b) 任务终局构成 ─────────────────────────────────────────
    parts = [
        ("已完成", n_done_total, PALETTE["executed"]),
        ("滞留池中", n_remain, PALETTE["deferred"]),
        ("到期作废", n_expired, PALETTE["release"]),
        ("物理拦回", n_gated, "#b279a2"),
    ]
    parts = [(k, v, c) for k, v, c in parts if v > 0]
    if not parts:
        parts = [("无任务", 0, "#c9ced4")]
    bottom = 0
    for lab, v, col in parts:
        ax_b.bar([0], [v], 0.5, bottom=bottom, color=col, alpha=0.9,
                 edgecolor="white", linewidth=0.6, label=f"{lab}（{v}）")
        if v > 0:
            ax_b.text(0, bottom + v / 2, f"{lab}\n{v}", ha="center", va="center",
                      fontsize=FS["annotation"], color="white")
        bottom += v
    ax_b.set_xlim(-0.6, 0.6)
    ax_b.set_xticks([])
    style_axes(ax_b, title=f"(b) 任务终局构成（到达 {n_arrived} 个）",
               ylabel="任务数")
    ax_b.legend(loc="upper left", fontsize=FS["legend"],
                labelspacing=0.35, handlelength=1.2)

    # ── (c) 双目标值 ─────────────────────────────────────────────
    ax_c.bar(x - w / 2, f1, w, color=PALETTE["reward"], alpha=0.85,
             label="F1 收益", edgecolor="white", linewidth=0.4)
    ax_c.bar(x + w / 2, f2, w, color=PALETTE["cost"], alpha=0.85,
             label="F2 代价（km）", edgecolor="white", linewidth=0.4)
    for xi, ok in zip(x, feas):
        if not ok and f1:
            ax_c.annotate("不可行", (xi, max(f1) * 0.96),
                          textcoords="offset points", xytext=(0, 4),
                          ha="center", fontsize=FS["annotation"],
                          color=PALETTE["release"], fontweight=500)
    style_axes(ax_c, title="(c) 双目标值（每周期）", xlabel="滚动周期",
               ylabel="收益 / 代价")
    ax_c.set_xticks(x, cyc_labels)
    ax_c.legend(loc="upper right", fontsize=FS["legend"])

    # ── (d) 关键指标表 ───────────────────────────────────────────
    rows = [
        ("到达 / 完成任务", f"{n_arrived} / {n_done_total}"),
        ("正确执行吞吐（任务/周期）", f"{throughput:.3f}"),
        ("平均正确执行率（按时/出动）", f"{correct_rate:.1%}"),
        ("按时率 / 迟到交付", f"{on_time_rate:.1%} / {n_late_total}"),
        ("平均迟到", f"{avg_late / 60:.1f} min"),
        ("废飞（物理不可行）", str(n_waste_total)),
        ("超期淘汰", str(n_eliminated_total)),
        ("完成率 CR（辅助对账）", f"{completion:.1%}"),
        ("周期解可行率", f"{feas_rate:.0%}"),
        ("总收益 F1（折减后）", f"{summary.get('total_reward', 0.0):.1f}"),
        ("总路径代价", f"{summary.get('total_cost', 0.0) / 1000:.1f} km"),
        ("平均等待", f"{summary.get('avg_wait', 0.0):.0f} s"),
        ("平均可用机数 K", f"{summary.get('K_mean', summary.get('K_avail_mean', 0.0)):.1f}"),
        ("求解总耗时", f"{summary.get('solve_time_ms', 0.0) / 1000:.1f} s"),
        ("运行口径", "单 seed（调试）" if n_seeds <= 1 else f"{n_seeds} seeds 均值"),
    ]
    ax_d.set_axis_off()
    ax_d.set_xlim(0, 1)
    ax_d.set_ylim(0, len(rows) + 1.2)
    ax_d.text(0.0, len(rows) + 0.55, "(d) 关键指标（summary）",
              fontsize=FS["title"], fontweight=500, va="center", color="#1f2933")
    for i, (k, v) in enumerate(rows):
        y = len(rows) - i - 0.5
        if i % 2 == 0:
            ax_d.add_patch(plt.Rectangle((0, y - 0.40), 1, 0.80,
                                         facecolor="#f5f6f7", edgecolor="none",
                                         zorder=0))
        warn = (k == "周期解可行率" and feas_rate < 0.999) or \
               (k == "完成率 CR" and completion < 0.05)
        ax_d.text(0.02, y, k, fontsize=FS["tick"], va="center",
                  family=MONO_ZH, color="#3c4148", zorder=2)
        ax_d.text(0.98, y, v, fontsize=FS["tick"], va="center", ha="right",
                  family=MONO_ZH,
                  color=PALETTE["release"] if warn else "#0f4c81", zorder=2)

    fig.suptitle("exp01_smoke：结果总览（正确执行 · 终局构成 · 双目标 · 关键指标）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    footnote(fig, "口径（长程协议）：完成率不是目标，主指标 = 吞吐"
                  "（累计完成/周期）与正确执行率（按时出动/总出动）；迟到不是废飞"
                  "（2026-10-15）——迟到交付计入完成，有效奖励按迟到线性折减"
                  "（W=600s）；废飞 = 物理不可行（航程/续航/终点机巢违约）的弹回"
                  "交付，UAV 已飞航程但任务解绑回池不计完成；滞留 = 实验结束时仍在"
                  "任务池；到期作废 = deadline 已过被淘汰。"
                  "F1 = 任务收益和（折减后），F2 = 三维路径代价；红色数值为调试期回归信号。")
    return save_figure(fig, Path(out_dir) / "R01_outcome_summary", formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
