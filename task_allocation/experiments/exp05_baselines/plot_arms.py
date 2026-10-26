# -*- coding: utf-8 -*-
"""exp05_baselines 对照结果绘图。

读 ``results/<ARM>/summary.json``（``agg`` 为 mean/std，``per_seed`` 为逐 seed 值），
出一张多面板对照图 + 三张专题图到 ``results/figures/``。

图内文字一律 ASCII（matplotlib 默认字体不含中文，写中文会出方框）。
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FIGDIR = RESULTS / "figures"

# 臂顺序与配色（M0 本文方法高亮）
ARM_ORDER = ["M0", "D0", "B1", "B2", "B2e", "B3"]
ARM_COLOR = {
    "M0": "#1f4e79",
    "D0": "#7f7f7f",
    "B1": "#c0504d",
    "B2": "#e8a33d",
    "B2e": "#9bbb59",
    "B3": "#8064a2",
}
ARM_LABEL = {
    "M0": "M0 Proposed\n(DMDE+DEM)",
    "D0": "D0 DMDE\n+Euclidean",
    "B1": "B1 Random\n(lower bound)",
    "B2": "B2 Greedy\ninsertion",
    "B2e": "B2e Greedy\n+Euclidean",
    "B3": "B3 VRPTW\ninsertion",
}


def load_arms() -> dict[str, dict]:
    arms: dict[str, dict] = {}
    for arm in ARM_ORDER:
        p = RESULTS / arm / "summary.json"
        if not p.exists():
            print(f"[plot] skip missing arm summary: {p}")
            continue
        arms[arm] = json.loads(p.read_text(encoding="utf-8"))
    return arms


def _agg(arm_data: dict, key: str, what: str = "mean") -> float:
    node = arm_data.get("agg", {}).get(key)
    if node is None:
        return float("nan")
    if isinstance(node, dict):
        return float(node.get(what, float("nan")))
    return float(node)


def _err(arm_data: dict, key: str) -> float:
    node = arm_data.get("agg", {}).get(key)
    if isinstance(node, dict):
        return float(node.get("std", 0.0) or 0.0)
    return 0.0


def fig_main_panel(arms: dict[str, dict]) -> Path:
    """四合一面板：完成率 / 按时率 / 每完成航程 / 求解耗时。"""
    names = [a for a in ARM_ORDER if a in arms]
    x = np.arange(len(names))
    w = 0.55

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    specs = [
        ("completion_rate_final", "Completion rate (final)", 1.0),
        ("on_time_rate_final", "On-time rate", 1.0),
        ("reward_yield", "Reward yield", 1.0),
        ("path_per_completion_m", "Path per completion (km)", 1e-3),
    ]
    for ax, (key, title, scale) in zip(axes.ravel(), specs):
        vals = [_agg(arms[n], key) * scale for n in names]
        errs = [_err(arms[n], key) * scale for n in names]
        colors = [ARM_COLOR[n] for n in names]
        bars = ax.bar(x, vals, w, yerr=errs, capsize=4, color=colors,
                      edgecolor="black", linewidth=0.6, alpha=0.92)
        ax.set_xticks(x)
        ax.set_xticklabels([ARM_LABEL[n] for n in names], fontsize=8)
        ax.set_title(title, fontsize=11)
        ax.grid(axis="y", alpha=0.3, linestyle=":")
        ax.set_axisbelow(True)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:.3f}" if v < 100 else f"{v:.0f}",
                        (b.get_x() + b.get_width() / 2, b.get_height() + max(errs) * 0.15),
                        ha="center", va="bottom", fontsize=7.5)
    fig.suptitle("exp05: main outcomes by arm (5 seeds, mean +/- std)", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = FIGDIR / "exp05_main_panel.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_cost_tradeoff(arms: dict[str, dict]) -> Path:
    """Pareto 散点：每完成航程 vs 完成率（越靠左上越好）。"""
    names = [a for a in ARM_ORDER if a in arms]
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    for n in names:
        px = _agg(arms[n], "path_per_completion_m") / 1000.0
        py = _agg(arms[n], "completion_rate_final")
        ax.scatter(px, py, s=90, color=ARM_COLOR[n], edgecolor="black",
                   linewidth=0.8, zorder=3, label=n)
        ax.annotate(n, (px, py), textcoords="offset points", xytext=(8, 6), fontsize=9)
    ax.set_xlabel("Path per completion (km)")
    ax.set_ylabel("Completion rate (final)")
    ax.set_title("exp05: efficiency vs coverage trade-off")
    ax.grid(alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = FIGDIR / "exp05_tradeoff.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_lateness(arms: dict[str, dict]) -> Path:
    names = [a for a in ARM_ORDER if a in arms]
    x = np.arange(len(names))
    fig, ax1 = plt.subplots(figsize=(9, 5))
    late = [_agg(arms[n], "n_late") for n in names]
    late_e = [_err(arms[n], "n_late") for n in names]
    ax1.bar(x - 0.2, late, 0.4, yerr=late_e, capsize=3, color="#c0504d",
            label="Late deliveries (n)")
    ax1.set_ylabel("Late deliveries (n)", color="#c0504d")
    ax1.set_xticks(x)
    ax1.set_xticklabels(names)
    ax1.tick_params(axis="y", labelcolor="#c0504d")

    ax2 = ax1.twinx()
    avg_late = [_agg(arms[n], "avg_lateness_s") for n in names]
    ax2.plot(x + 0.2, avg_late, "o-", color="#1f4e79", linewidth=1.5,
             label="Average lateness (s)")
    ax2.set_ylabel("Average lateness (s)", color="#1f4e79")
    ax2.tick_params(axis="y", labelcolor="#1f4e79")
    plt.title("exp05: lateness by arm")
    ax1.grid(axis="y", alpha=0.3, linestyle=":")
    fig.tight_layout()
    out = FIGDIR / "exp05_lateness.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_gate_and_cost(arms: dict[str, dict]) -> Path:
    """门禁/名额诊断 + 求解耗时。"""
    names = [a for a in ARM_ORDER if a in arms]
    x = np.arange(len(names))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))

    keys = [
        ("s_coverage_rate", "S coverage rate", "#1f4e79"),
        ("quota_utilization", "Quota utilization", "#8064a2"),
        ("quota_full_cycle_ratio", "Quota-full cycle ratio", "#e8a33d"),
    ]
    ax = axes[0]
    w = 0.26
    for i, (key, label, color) in enumerate(keys):
        vals = [_agg(arms[n], key) for n in names]
        ax.bar(x + (i - 1) * w, vals, w, label=label, color=color, alpha=0.9,
               edgecolor="black", linewidth=0.4)
    shortfall = [_agg(arms[n], "n_capacity_shortfall") for n in names]
    skip = [_agg(arms[n], "n_voluntary_skip") for n in names]
    ax.bar(x + 2 * w, shortfall, w, label="Capacity shortfall (n)", color="#c0504d",
           alpha=0.9, edgecolor="black", linewidth=0.4)
    ax.bar(x + 3 * w, skip, w, label="Voluntary skip (n)", color="#7f7f7f",
           alpha=0.9, edgecolor="black", linewidth=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_title("exp05: coverage gate and quota diagnostics")
    ax.legend(fontsize=7.5, ncol=2)
    ax.grid(axis="y", alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)

    ax = axes[1]
    order = sorted(names, key=lambda n: _agg(arms[n], "n_arrived"), reverse=True)
    vals = [_agg(arms[n], "wall_seconds") for n in order]
    ax.barh([n for n in order], vals, color=[ARM_COLOR[n] for n in order],
            edgecolor="black", linewidth=0.5, alpha=0.92)
    for i, v in enumerate(vals):
        ax.annotate(f"{v:.0f}s", (v, i), xytext=(4, 0), textcoords="offset points",
                    va="center", fontsize=8)
    ax.set_xlabel("Solver wall time (s)")
    ax.set_title("exp05: compute cost by arm")
    ax.grid(axis="x", alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = FIGDIR / "exp05_gate_and_cost.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def main() -> None:
    FIGDIR.mkdir(parents=True, exist_ok=True)
    arms = load_arms()
    if not arms:
        print("[plot] no arm summaries found")
        return
    outs = [
        fig_main_panel(arms),
        fig_cost_tradeoff(arms),
        fig_lateness(arms),
        fig_gate_and_cost(arms),
    ]
    for p in outs:
        print(f"[plot] wrote {p}")
    # summary.csv 概览同步打印
    print("\n[plot] agg quick view")
    hdr = f"{'arm':<5}{'comp':>7}{'ontime':>8}{'km/comp':>9}{'late':>6}{'elim':>6}{'skip':>6}"
    print(hdr)
    for n in [a for a in ARM_ORDER if a in arms]:
        print(f"{n:<5}{_agg(arms[n],'completion_rate_final'):>7.3f}"
              f"{_agg(arms[n],'on_time_rate_final'):>8.3f}"
              f"{_agg(arms[n],'path_per_completion_m')/1000:>9.2f}"
              f"{_agg(arms[n],'n_late'):>6.1f}"
              f"{_agg(arms[n],'n_eliminated'):>6.1f}"
              f"{_agg(arms[n],'n_voluntary_skip'):>6.1f}")


if __name__ == "__main__":
    main()
