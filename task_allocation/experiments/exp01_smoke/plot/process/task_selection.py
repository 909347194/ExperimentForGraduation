# -*- coding: utf-8 -*-
"""图 ``task_selection``——动态任务选择：漏斗、保留率、预选咬合与池守恒。

【process 层 · 求解过程 ①】

回答「一个周期里，候选任务怎么被逐级收敛到 T_sel、机制参数起没起作用」。
验证论文 §3 两阶段选择链路：T_t^exec ⊆ T_t^sel ⊆ T_t。

六联面板（由原 ``selection_funnel`` + ``selection_detail`` 合并而来，
面板信息无丢失）::

    (a) 选择漏斗          逐级筛选任务数（T_t → 优先级 → 预筛 → 边际 → T_sel → T_exec）
    (b) 各级保留率        相对 T_t 的保留百分比
    (c) 预选上限咬合      capability_cap vs 池内任务数（上限远大于池内 → 该级从未咬合）
    (d) 池输入输出守恒    n_in = n_selected + n_rejected_prefilter
    (e) 强制入选与双口径  n_forced_in + selection/muas 两个统计口径交叉校验
    (f) 机制参数一览      首周期快照 + 读法

数据来源：``metrics.json`` 的 ``cycles[].selection`` 全字段与 ``cycles[].muas.n_selected``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..theme import FS, MONO_ZH, PALETTE, cycle_colors, footnote, plt, save_figure, style_axes

__all__ = ["plot_task_selection"]

#: (键, 显示名, 所属 dict)
STAGES = (
    ("n_tasks_in", "T_t 到达", "selection"),
    ("n_after_priority", "优先级排序", "selection"),
    ("n_after_prefilter", "预筛选 α·K", "selection"),
    ("n_after_marginal", "边际收益", "selection"),
    ("n_selected", "T_t^sel 选中", "selection"),
    ("n_executed", "T_t^exec 执行", "muas"),
)


def _stage_value(cycle: dict[str, Any], key: str, section: str) -> float:
    src = cycle.get(section) or {}
    v = src.get(key)
    return float(v) if v is not None else 0.0


def plot_task_selection(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = [c for c in (metrics.get("cycles") or []) if c.get("selection")]
    if not cycles:
        return []

    n = len(cycles)
    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    x = np.arange(n)
    sel = [c["selection"] for c in cycles]
    colors = cycle_colors(n)

    n_in = [int(s.get("n_tasks_in") or 0) for s in sel]
    n_sel = [int(s.get("n_selected") or 0) for s in sel]
    n_rej = [int(s.get("n_rejected_prefilter") or 0) for s in sel]
    n_forced = [int(s.get("n_forced_in") or 0) for s in sel]
    cap = [int(s.get("capability_cap") or 0) for s in sel]
    muas_sel = [int((c.get("muas") or {}).get("n_selected") or 0) for c in cycles]

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 7.4))
    ax_funnel, ax_rate, ax_cap = axes[0]
    ax_cons, ax_force, ax_param = axes[1]

    # ── (a) 选择漏斗 ─────────────────────────────────────────────
    stage_names = [name for _, name, _ in STAGES]
    xs = np.arange(len(STAGES))
    width = 0.8 / max(n, 1)
    for i, c in enumerate(cycles):
        values = [_stage_value(c, key, sec) for key, _, sec in STAGES]
        offset = (i - (n - 1) / 2.0) * width
        ax_funnel.bar(xs + offset, values, width * 0.92,
                      label=labels[i], color=colors[i])
    style_axes(ax_funnel, title="(a) 选择漏斗：逐级筛选任务数", ylabel="任务数")
    ax_funnel.set_xticks(xs, stage_names, rotation=20, ha="right",
                         fontsize=FS["tick"])
    ax_funnel.set_ylim(bottom=0)
    ax_funnel.legend(loc="upper right", fontsize=FS["legend"])

    # ── (b) 各级保留率 ───────────────────────────────────────────
    for i, c in enumerate(cycles):
        base = _stage_value(c, "n_tasks_in", "selection")
        if base <= 0:
            continue
        rates = [_stage_value(c, key, sec) / base * 100.0
                 for key, _, sec in STAGES]
        ax_rate.plot(xs, rates, "o-", color=colors[i], label=labels[i])
    style_axes(ax_rate, title="(b) 各级保留率（相对 T_t）", ylabel="保留率 (%)")
    ax_rate.set_xticks(xs, stage_names, rotation=20, ha="right",
                       fontsize=FS["tick"])
    ax_rate.set_ylim(0, 105)
    ax_rate.legend(loc="lower left", fontsize=FS["legend"])

    # ── (c) capability 预选上限 vs 池内规模 ───────────────────────
    ax_cap.bar(x, cap, 0.5, color=PALETTE["evals"], edgecolor="white",
               linewidth=0.6, label="capability_cap（预选上限）")
    for xi, v in zip(x, cap):
        ax_cap.text(xi, v + 1.2, f"{v}", ha="center", fontsize=FS["tick"],
                    color="0.25")
    ax_cap.plot(x, n_in, "o-", color=PALETTE["new"], linewidth=1.6,
                markersize=4, label="n_tasks_in（池内任务数）", zorder=3)
    for xi, v in zip(x, n_in):
        ax_cap.annotate(f"{v}", (xi, v), textcoords="offset points",
                        xytext=(0, -13), ha="center", fontsize=FS["tick"],
                        color=PALETTE["new"])
    k0 = sel[0].get("capability_preselect_k")
    if k0:
        ax_cap.axhline(int(k0), color="0.5", linewidth=0.9, linestyle=":")
        ax_cap.text(n - 0.42, int(k0) + 1.2, f"preselect_k = {int(k0)}",
                    ha="right", fontsize=FS["footnote"], color="0.45")
    ax_cap.set_xticks(x, labels)
    ax_cap.set_ylim(0, max(max(cap), max(n_in), 1) * 1.3)
    style_axes(ax_cap, title="(c) 能力预选上限 vs 池内规模", ylabel="任务数")
    ax_cap.legend(loc="upper right", fontsize=FS["legend"], framealpha=0.92)

    # ── (d) 池输入输出守恒 ───────────────────────────────────────
    ax_cons.bar(x, n_sel, 0.5, color=PALETTE["executed"], edgecolor="white",
                linewidth=0.6, label="进入 T_sel")
    ax_cons.bar(x, n_rej, 0.5, bottom=n_sel, color=PALETTE["release"],
                edgecolor="white", linewidth=0.6, label="预筛拦截")
    for xi, a, b in zip(x, n_sel, n_rej):
        ax_cons.text(xi, a / 2, f"{a}", ha="center", va="center",
                     fontsize=FS["tick"], color="white")
        if b:
            ax_cons.text(xi, a + b / 2, f"{b}", ha="center", va="center",
                         fontsize=FS["tick"], color="white")
        ax_cons.text(xi, a + b + 0.12, f"入 {a + b}", ha="center",
                     fontsize=FS["footnote"], color="0.35")
    ax_cons.set_xticks(x, labels)
    ax_cons.set_ylim(0, max(a + b for a, b in zip(n_sel, n_rej)) * 1.25 + 0.5)
    ax_cons.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_cons, title="(d) 池输入输出守恒：n_in = n_selected + n_rejected",
               ylabel="任务数")
    ax_cons.legend(loc="upper right", fontsize=FS["legend"], framealpha=0.92)

    # ── (e) 强制入选 + 双口径交叉校验 ────────────────────────────
    ax_force.bar(x - 0.18, n_forced, 0.34, color=PALETTE["deferred"],
                 edgecolor="white", linewidth=0.6,
                 label="n_forced_in（强制入选）")
    ax_force.plot(x + 0.18, muas_sel, "s--", color="0.35", markersize=4,
                  linewidth=1.2, label="muas.n_selected（求解口径）")
    ax_force.plot(x + 0.18, n_sel, "o-", color=PALETTE["executed"],
                  markersize=4, linewidth=1.4,
                  label="selection.n_selected（选择口径）")
    for xi, a, b in zip(x, n_sel, muas_sel):
        if a != b:
            ax_force.annotate(f"{a}≠{b}", (xi + 0.18, max(a, b)),
                              textcoords="offset points", xytext=(0, 6),
                              ha="center", fontsize=FS["footnote"],
                              color=PALETTE["release"])
    ax_force.set_xticks(x, labels)
    top = max(max(n_forced), max(n_sel), max(muas_sel), 1)
    ax_force.set_ylim(0, top * 1.3)
    ax_force.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    style_axes(ax_force, title="(e) 强制入选与双口径一致性", ylabel="任务数")
    ax_force.legend(loc="upper right", fontsize=FS["legend"], framealpha=0.92)

    # ── (f) 机制参数面板（压缩版：只留未在 setup_params 出现的运行值）──
    ax_param.axis("off")
    s0 = sel[0]
    rows = [
        ("preselect_k / marginal_limit",
         f"{s0.get('capability_preselect_k')} / {s0.get('capability_marginal_limit')}",
         "预选候选数 / 边际下限"),
        ("capability_budget",
         str(s0.get("capability_budget")),
         "机队能力预算（航程）"),
        ("capability_cap（实际）",
         f"{min(cap)}–{max(cap)}" if cap else "—",
         "逐周期递减，始终 >> 池内规模"),
        ("n_forced_in（全程）",
         str(sum(n_forced)),
         "紧急任务强制入选"),
    ]
    lines = [f"{'参数':26s}{'取值':>10s}   含义"]
    lines.append("─" * 58)
    for key, val, meaning in rows:
        lines.append(f"{key:26s}{val:>10s}   {meaning}")
    ax_param.text(0.02, 0.97, "\n".join(lines), transform=ax_param.transAxes,
                  ha="left", va="top", fontsize=FS["annotation"],
                  family=MONO_ZH, color="0.2",
                  bbox=dict(boxstyle="round,pad=0.5", facecolor="#f4f6f8",
                            edgecolor="0.8", linewidth=0.8))
    style_axes(ax_param, title="(f) 预选机制运行值（首周期快照）")

    fig.suptitle("exp01_smoke：动态任务选择（漏斗 · 保留率 · 预选咬合 · 池守恒）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    footnote(fig, "口径：n_tasks_in = 本周期入池任务（滞留+新到+释放），保留率以其为分母；"
                  "(c)(f) 预选三级（k → marginal → cap）全程未咬合，实际收敛由优先级"
                  "与时间窗驱动；(e) 图上未标注即双口径一致（本实验亦无强制入选）。")
    return save_figure(fig, Path(out_dir) / "task_selection", formats, dpi=dpi)
