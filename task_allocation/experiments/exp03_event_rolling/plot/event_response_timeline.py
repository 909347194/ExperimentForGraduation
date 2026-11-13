# -*- coding: utf-8 -*-
"""R00_event_response_timeline — 事件响应全景时间线（论文核心图）。

E0 无事件基线 vs E1 UAV 故障同 seed 对比，一图讲完「检测→响应→恢复」：

    y 轴 = 任务池量 |remain| + |new| + |release|
    x 轴 = 滚动周期 1→N
    ── E0（蓝色虚线）平滑到达-消化曲线
    ━━ E1（红色实线）周期 6 注入 → 突起 → 回落 → 汇合

论文 §3.5.6 核心论点的视觉证明：事件冲击 → 快速恢复 → 任务不丢失。
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import numpy as np

from .theme import FACTOR_COLORS


def _load_cycles(results_root: Path, factor: str, seed: int) -> list[dict]:
    """从落盘 metrics.json 读逐周期数据。"""
    p = results_root / factor / f"seed_{seed}" / "metrics.json"
    if not p.exists():
        p = results_root / factor / "metrics.json"
    if not p.exists():
        return []
    m = json.loads(p.read_text(encoding="utf-8"))
    return m.get("cycles", [])


def _pool_sizes(cycles: list[dict]) -> tuple[list[int], list[int]]:
    """提取 (周期号, 池量) 序列。池量 = remain + new + release（周期初快照）。"""
    xs, ys = [], []
    for c in cycles:
        pc = c.get("pool_counts", {})
        # pool_counts 是 begin_cycle 切面；n_total 为权威字段
        n = int(pc.get("n_total", 0))
        if n == 0:
            n = (int(pc.get("n_remain", 0)) + int(pc.get("n_new", 0))
                 + int(pc.get("n_release", 0)))
        xs.append(int(c.get("cycle", len(xs) + 1)))
        ys.append(n)
    return xs, ys


def _event_annotations(cycles: list[dict]) -> list[tuple[int, str, str]]:
    """提取事件标注点：(周期号, 标签, 颜色)。"""
    anns = []
    for c in cycles:
        cy = int(c.get("cycle", 0))
        # 释放任务 = 故障注入的实际时刻（比事件检测早 1 周期）
        n_rel = len(c.get("released_by_fault", []) or [])
        if n_rel > 0:
            anns.append((cy, f"故障注入·释放{n_rel}任务", "#D32F2F"))
        for e in c.get("events", []):
            et = e.get("type", "")
            if et == "uav_fault" and not any(a[1].startswith("故障") for a in anns if a[0] == cy):
                anns.append((cy, "UAV故障检测", "#FF5722"))
            elif et == "nest_unavailable":
                anns.append((cy, "机巢闭塞", "#FF9800"))
            elif et == "urgent_task" and not any(a[0] == cy for a in anns):
                anns.append((cy, "突发高优", "#4CAF50"))
        if c.get("replan") and not any(a[0] == cy for a in anns):
            anns.append((cy, "重规划", "#7B1FA2"))
    return anns


def _completion_markers(cycles: list[dict]) -> tuple[list[int], list[int]]:
    """每周期完成量。"""
    xs, ys = [], []
    for c in cycles:
        xs.append(int(c.get("cycle", len(xs) + 1)))
        ys.append(int(c.get("n_completed", 0)))
    return xs, ys


def plot_event_response_timeline(all_summaries: dict, fig_dir: Path) -> str:
    """双 panel 时间线：上=池量对比，下=每周期完成量 + 事件标注。"""
    results_root = Path(__file__).resolve().parent.parent / "results"
    seed = 42  # 代表 seed

    e0_cycles = _load_cycles(results_root, "E0", seed)
    e1_cycles = _load_cycles(results_root, "E1", seed)

    if not e0_cycles or not e1_cycles:
        print("[plot] event_response_timeline: 缺 E0/E1 数据，跳过")
        return ""

    e0_x, e0_y = _pool_sizes(e0_cycles)
    e1_x, e1_y = _pool_sizes(e1_cycles)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13, 8),
        gridspec_kw={"height_ratios": [3, 2], "hspace": 0.25},
    )

    # ── 上 panel：任务池量对比 ─────────────────────────────
    ax1.plot(e0_x, e0_y, "--", color=FACTOR_COLORS["E0"], linewidth=2.2,
             label="E0 无事件（基线）", zorder=3, alpha=0.85)
    ax1.plot(e1_x, e1_y, "-", color=FACTOR_COLORS["E1"], linewidth=2.5,
             label="E1 UAV故障", zorder=4)

    # 事件竖线 + 注释
    anns = _event_annotations(e1_cycles)
    seen_labels: set[str] = set()
    for cy, label, color in anns:
        ax1.axvline(x=cy, color=color, linewidth=1.2, linestyle=":", alpha=0.7, zorder=2)
        if label not in seen_labels:
            ax1.annotate(
                label, xy=(cy, max(e1_y) * 0.95),
                fontsize=9, color=color, fontweight="bold",
                ha="center", va="top",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=color, alpha=0.9),
                arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
                xytext=(cy, max(e1_y) * 1.12),
            )
            seen_labels.add(label)

    ax1.set_ylabel("任务池量", fontsize=13)
    ax1.set_title("事件响应全景时间线 — E0 基线 vs E1 故障", fontsize=15, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=11, framealpha=0.9)
    ax1.set_xlim(0.5, max(max(e0_x), max(e1_x)) + 0.5)
    ax1.set_ylim(0, max(max(e0_y), max(e1_y)) * 1.25)
    ax1.grid(axis="y", alpha=0.25)

    # 恢复标注：E1 曲线与 E0 汇合的区间
    diff = np.abs(np.array(e1_y[:len(e0_y)]) - np.array(e0_y[:len(e1_y)]))
    if len(diff) > 8:
        # 找事件后的恢复区间（差值回到 2 以内）
        inject_cycle = anns[0][0] if anns else 6
        recover_idx = None
        for i in range(inject_cycle, len(diff)):
            if diff[i] <= 2:
                recover_idx = i
                break
        if recover_idx:
            rc = e0_x[recover_idx]
            ax1.annotate(
                "恢复汇合", xy=(rc, e1_y[recover_idx]),
                fontsize=9, color="#2E7D32", fontweight="bold",
                ha="left", va="bottom",
                bbox=dict(boxstyle="round,pad=0.3", fc="#E8F5E9", ec="#2E7D32", alpha=0.9),
                xytext=(rc + 0.5, e1_y[recover_idx] + max(e1_y) * 0.08),
                arrowprops=dict(arrowstyle="->", color="#2E7D32", lw=1.2),
            )

    # ── 下 panel：每周期完成量 + 事件脉冲 ───────────────────
    c0_x, c0_y = _completion_markers(e0_cycles)
    c1_x, c1_y = _completion_markers(e1_cycles)

    width = 0.38
    x0 = np.array(c0_x)
    x1 = np.array(c1_x)
    ax2.bar(x0 - width / 2, c0_y, width, color=FACTOR_COLORS["E0"],
            alpha=0.6, label="E0 完成量", edgecolor="white")
    ax2.bar(x1 + width / 2, c1_y, width, color=FACTOR_COLORS["E1"],
            alpha=0.8, label="E1 完成量", edgecolor="white")

    # 事件竖线同步到下 panel
    for cy, label, color in anns:
        ax2.axvline(x=cy, color=color, linewidth=1.2, linestyle=":", alpha=0.7)

    ax2.set_xlabel("滚动周期", fontsize=13)
    ax2.set_ylabel("每周期完成量", fontsize=13)
    ax2.legend(loc="upper right", fontsize=10)
    ax2.set_xlim(0.5, max(max(c0_x), max(c1_x)) + 0.5)
    ax2.grid(axis="y", alpha=0.25)

    plt.tight_layout()
    name = "R00_event_response_timeline.png"
    fig.savefig(fig_dir / name, dpi=250, bbox_inches="tight")
    plt.close(fig)
    return name


if __name__ == "__main__":
    print("通过 plot.__init__.make_plots 调用")
