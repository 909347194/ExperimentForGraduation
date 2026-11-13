# -*- coding: utf-8 -*-
"""exp03_event_rolling 可视化：图 / 表 / GIF。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

__all__ = ["make_plots"]

# 中文显示
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "SimHei", "DejaVu Sans", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

# 因子配色
FACTOR_COLORS = {
    "E0": "#2196F3",
    "E1": "#FF5722",
    "E2": "#4CAF50",
    "E3": "#FF9800",
    "E4": "#9C27B0",
}
FACTOR_LABELS = {
    "E0": "E0 无事件",
    "E1": "E1 UAV故障",
    "E2": "E2 突发高优",
    "E3": "E3 机巢闭塞",
    "E4": "E4 组合",
}


def make_plots(all_summaries: dict[str, Any], figures_dir: Path) -> None:
    """生成全部图件。"""
    figures_dir = Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)

    _plot_event_overview(all_summaries, figures_dir)
    _plot_completion_comparison(all_summaries, figures_dir)
    _plot_replan_comparison(all_summaries, figures_dir)
    _plot_event_types_bar(all_summaries, figures_dir)
    _plot_metrics_radar(all_summaries, figures_dir)
    _plot_summary_table(all_summaries, figures_dir)
    _plot_pool_dynamics(all_summaries, figures_dir)
    _generate_gif(all_summaries, figures_dir)


def _plot_event_overview(all_summaries: dict, fig_dir: Path) -> None:
    """图 1：事件总览——各因子的事件数量与类型。"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    factors = sorted(all_summaries.keys())
    colors = [FACTOR_COLORS.get(f, "#666") for f in factors]

    # 左图：事件总数
    n_events = [all_summaries[f].get("n_event_occurrences", {}).get("mean", 0) for f in factors]
    bars = axes[0].bar(factors, n_events, color=colors, edgecolor="white", linewidth=1.5)
    axes[0].set_title("各因子事件触发次数", fontsize=14, fontweight="bold")
    axes[0].set_ylabel("事件次数")
    for bar, v in zip(bars, n_events):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                     f"{v:.1f}", ha="center", va="bottom", fontsize=11)

    # 右图：重规划次数
    n_replan = [all_summaries[f].get("n_replan_cycles", {}).get("mean", 0) for f in factors]
    bars = axes[1].bar(factors, n_replan, color=colors, edgecolor="white", linewidth=1.5)
    axes[1].set_title("各因子重规划周期数", fontsize=14, fontweight="bold")
    axes[1].set_ylabel("重规划次数")
    for bar, v in zip(bars, n_replan):
        axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                     f"{v:.1f}", ha="center", va="bottom", fontsize=11)

    plt.tight_layout()
    fig.savefig(fig_dir / "fig1_event_overview.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def _plot_completion_comparison(all_summaries: dict, fig_dir: Path) -> None:
    """图 2：完成率对比。"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    factors = sorted(all_summaries.keys())
    colors = [FACTOR_COLORS.get(f, "#666") for f in factors]

    # 完成率
    rates = [all_summaries[f].get("completion_rate", {}).get("mean", 0) for f in factors]
    stds = [all_summaries[f].get("completion_rate", {}).get("std", 0) for f in factors]
    bars = axes[0].bar(factors, rates, yerr=stds, color=colors, edgecolor="white",
                       linewidth=1.5, capsize=5, error_kw={"linewidth": 1.5})
    axes[0].set_title("任务完成率对比", fontsize=14, fontweight="bold")
    axes[0].set_ylabel("完成率")
    axes[0].set_ylim(0, 1.1)
    for bar, v in zip(bars, rates):
        axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                     f"{v:.1%}", ha="center", va="bottom", fontsize=11)

    # 按时率
    rates2 = [all_summaries[f].get("on_time_rate", {}).get("mean", 0) for f in factors]
    stds2 = [all_summaries[f].get("on_time_rate", {}).get("std", 0) for f in factors]
    bars = axes[1].bar(factors, rates2, yerr=stds2, color=colors, edgecolor="white",
                       linewidth=1.5, capsize=5, error_kw={"linewidth": 1.5})
    axes[1].set_title("按时交付率对比", fontsize=14, fontweight="bold")
    axes[1].set_ylabel("按时率")
    axes[1].set_ylim(0, 1.1)
    for bar, v in zip(bars, rates2):
        axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                     f"{v:.1%}", ha="center", va="bottom", fontsize=11)

    plt.tight_layout()
    fig.savefig(fig_dir / "fig2_completion_comparison.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def _plot_replan_comparison(all_summaries: dict, fig_dir: Path) -> None:
    """图 3：事件响应指标——故障释放 / 强制纳入 / 终点变更。"""
    fig, ax = plt.subplots(figsize=(10, 5))

    factors = sorted(all_summaries.keys())
    x = np.arange(len(factors))
    width = 0.25

    released = [all_summaries[f].get("n_released_by_fault", {}).get("mean", 0) for f in factors]
    forced = [all_summaries[f].get("n_forced_in", {}).get("mean", 0) for f in factors]
    end_nest = [all_summaries[f].get("n_end_nest_changed", {}).get("mean", 0) for f in factors]

    ax.bar(x - width, released, width, label="故障释放任务", color="#FF5722", edgecolor="white")
    ax.bar(x, forced, width, label="强制纳入任务", color="#4CAF50", edgecolor="white")
    ax.bar(x + width, end_nest, width, label="终点巢变更", color="#FF9800", edgecolor="white")

    ax.set_title("事件响应指标对比", fontsize=14, fontweight="bold")
    ax.set_ylabel("次数")
    ax.set_xticks(x)
    ax.set_xticklabels([FACTOR_LABELS.get(f, f) for f in factors], fontsize=10)
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    fig.savefig(fig_dir / "fig3_replan_comparison.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def _plot_event_types_bar(all_summaries: dict, fig_dir: Path) -> None:
    """图 4：事件类型分布（堆叠柱状图）。"""
    fig, ax = plt.subplots(figsize=(10, 5))

    factors = sorted(all_summaries.keys())
    types = ["uav_fault", "urgent_task", "nest_unavailable"]
    type_labels = ["UAV故障", "突发高优", "机巢闭塞"]
    type_colors = ["#FF5722", "#4CAF50", "#FF9800"]

    x = np.arange(len(factors))
    bottom = np.zeros(len(factors))

    for t, label, color in zip(types, type_labels, type_colors):
        vals = []
        for f in factors:
            et = all_summaries[f].get("event_types", {})
            vals.append(et.get(t, 0))
        ax.bar(x, vals, bottom=bottom, label=label, color=color, edgecolor="white")
        bottom += np.array(vals)

    ax.set_title("事件类型分布", fontsize=14, fontweight="bold")
    ax.set_ylabel("事件次数")
    ax.set_xticks(x)
    ax.set_xticklabels([FACTOR_LABELS.get(f, f) for f in factors], fontsize=10)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    fig.savefig(fig_dir / "fig4_event_types.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def _plot_metrics_radar(all_summaries: dict, fig_dir: Path) -> None:
    """图 5：多指标雷达图。"""
    metrics = ["completion_rate", "on_time_rate", "n_replan_cycles",
               "n_forced_in", "n_end_nest_changed", "n_released_by_fault"]
    labels = ["完成率", "按时率", "重规划", "强制纳入", "终点变更", "故障释放"]

    factors = sorted(all_summaries.keys())
    n_metrics = len(metrics)

    # 归一化
    raw = {}
    for f in factors:
        vals = []
        for m in metrics:
            vals.append(all_summaries[f].get(m, {}).get("mean", 0))
        raw[f] = vals

    max_vals = [max(raw[f][i] for f in factors) or 1 for i in range(n_metrics)]
    norm = {f: [raw[f][i] / max_vals[i] for i in range(n_metrics)] for f in factors}

    angles = np.linspace(0, 2 * np.pi, n_metrics, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

    for f in factors:
        vals = norm[f] + norm[f][:1]
        color = FACTOR_COLORS.get(f, "#666")
        ax.plot(angles, vals, "o-", linewidth=2, label=FACTOR_LABELS.get(f, f), color=color)
        ax.fill(angles, vals, alpha=0.1, color=color)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_title("多指标雷达图", fontsize=14, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))

    plt.tight_layout()
    fig.savefig(fig_dir / "fig5_metrics_radar.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def _plot_summary_table(all_summaries: dict, fig_dir: Path) -> None:
    """图 6：汇总表格。"""
    fig, ax = plt.subplots(figsize=(14, 4 + len(all_summaries) * 0.5))
    ax.axis("off")

    factors = sorted(all_summaries.keys())
    headers = ["因子", "完成率", "按时率", "事件数", "重规划", "故障释放", "强制纳入", "终点变更"]
    cell_data = []

    for f in factors:
        s = all_summaries[f]
        cell_data.append([
            FACTOR_LABELS.get(f, f),
            f"{s.get('completion_rate', {}).get('mean', 0):.1%}",
            f"{s.get('on_time_rate', {}).get('mean', 0):.1%}",
            f"{s.get('n_event_occurrences', {}).get('mean', 0):.1f}",
            f"{s.get('n_replan_cycles', {}).get('mean', 0):.1f}",
            f"{s.get('n_released_by_fault', {}).get('mean', 0):.1f}",
            f"{s.get('n_forced_in', {}).get('mean', 0):.1f}",
            f"{s.get('n_end_nest_changed', {}).get('mean', 0):.1f}",
        ])

    table = ax.table(
        cellText=cell_data,
        colLabels=headers,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.5)

    # 表头着色
    for j in range(len(headers)):
        table[0, j].set_facecolor("#3F51B5")
        table[0, j].set_text_props(color="white", fontweight="bold")

    # 行着色
    for i, f in enumerate(factors):
        color = FACTOR_COLORS.get(f, "#666") + "22"
        for j in range(len(headers)):
            table[i + 1, j].set_facecolor(color)

    ax.set_title("exp03_event_rolling 因子对比汇总表", fontsize=14, fontweight="bold", pad=20)

    plt.tight_layout()
    fig.savefig(fig_dir / "fig6_summary_table.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def _plot_pool_dynamics(all_summaries: dict, fig_dir: Path) -> None:
    """图 7：事件注入时间线示意。"""
    fig, ax = plt.subplots(figsize=(12, 4))

    # 事件注入时刻
    event_markers = {
        "E1": [(6, "UAV故障", "#FF5722")],
        "E2": [(6, "突发高优", "#4CAF50")],
        "E3": [(6, "机巢闭塞", "#FF9800")],
        "E4": [(6, "UAV故障", "#FF5722"), (10, "机巢闭塞", "#FF9800")],
    }

    factors = sorted(all_summaries.keys())
    y_positions = {f: i for i, f in enumerate(factors)}

    for f in factors:
        y = y_positions[f]
        ax.axhline(y=y, color="#E0E0E0", linewidth=30, alpha=0.5)
        for cycle, label, color in event_markers.get(f, []):
            ax.scatter(cycle, y, s=200, color=color, zorder=5, edgecolors="white", linewidth=2)
            ax.annotate(label, (cycle, y), textcoords="offset points",
                        xytext=(0, 15), ha="center", fontsize=9, color=color)

    ax.set_yticks(list(y_positions.values()))
    ax.set_yticklabels([FACTOR_LABELS.get(f, f) for f in factors])
    ax.set_xlabel("滚动周期", fontsize=12)
    ax.set_xlim(0, 21)
    ax.set_title("事件注入时间线", fontsize=14, fontweight="bold")
    ax.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    fig.savefig(fig_dir / "fig7_event_timeline.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def _generate_gif(all_summaries: dict, fig_dir: Path) -> None:
    """生成 GIF 动画：事件响应过程示意。"""
    try:
        from matplotlib.animation import FuncAnimation, PillowWriter
    except ImportError:
        print("[plot] matplotlib.animation 不可用，跳过 GIF")
        return

    factors = sorted(all_summaries.keys())
    n_frames = 20  # 20 个周期

    fig, ax = plt.subplots(figsize=(10, 6))

    def _update(frame):
        ax.clear()
        cycle = frame + 1

        # 绘制各因子的进度条
        for i, f in enumerate(factors):
            color = FACTOR_COLORS.get(f, "#666")
            y = len(factors) - i - 1

            # 进度条
            progress = cycle / n_frames
            ax.barh(y, progress, height=0.6, color=color, alpha=0.7,
                    edgecolor="white", linewidth=1.5)

            # 事件标记
            ev_markers = {"E1": [6], "E2": [6], "E3": [6], "E4": [6, 10]}
            for ev_cycle in ev_markers.get(f, []):
                if ev_cycle <= cycle:
                    ax.scatter(ev_cycle / n_frames, y, s=150, color="red",
                              zorder=5, edgecolors="white", linewidth=2, marker="*")

            ax.text(-0.02, y, FACTOR_LABELS.get(f, f), ha="right", va="center",
                    fontsize=11, transform=ax.get_yaxis_transform())

        ax.set_xlim(0, 1.15)
        ax.set_ylim(-0.5, len(factors) - 0.5)
        ax.set_xlabel("周期进度", fontsize=12)
        ax.set_title(f"事件响应动态 — 周期 {cycle}/{n_frames}", fontsize=14, fontweight="bold")
        ax.set_yticks([])
        ax.grid(axis="x", alpha=0.3)

    anim = FuncAnimation(fig, _update, frames=n_frames, interval=500, repeat=True)
    try:
        anim.save(fig_dir / "event_response_animation.gif", writer=PillowWriter(fps=2), dpi=100)
        print(f"[plot] GIF → {fig_dir / 'event_response_animation.gif'}")
    except Exception as exc:
        print(f"[plot] GIF 生成失败：{exc}")
    plt.close(fig)
