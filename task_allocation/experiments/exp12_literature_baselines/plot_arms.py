# -*- coding: utf-8 -*-
"""exp05 E1 对照结果绘图（描述性，不作方法优劣结论）。

读 ``results_e1/<ID>/summary.json``（``agg`` 为 mean/std，``per_seed`` 为逐 seed
值），出一张多面板对照图 + 三张专题图到 ``results_e1/figures/``。

E1 收窄（4 方法 M0/B1/B2/D0）后，所有航程一律取**唯一比较尺**
``path_m_audit``（垂直切面 DEM 有向三维航程的几何重算），自报列
``total_path_m`` / ``path_per_completion_m`` 与诊断列 ``path_m_euclid3d``
不进任何图。每完成航程由 ``per_seed`` 逐 seed 归一
（``path_m_audit / n_completed``）后再跨 seed 汇总，与
``claims_report.json`` 同口径；分母非正的 seed 显式跳过、不补 0。

图只描述量级与代价，不排名、不宣称优越；主判据（M0−B1 配对精确 p 与三态
结论）在 ``claims_report.json`` 单独给出，不由图产生。

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
RESULTS = HERE / "results_e1"
FIGDIR = RESULTS / "figures"

# E1 四方法顺序与配色（M0 本文完整方法高亮）
METHOD_ORDER = ["M0", "B1", "B2", "D0"]
METHOD_COLOR = {
    "M0": "#1f4e79",
    "B1": "#c0504d",
    "B2": "#e8a33d",
    "D0": "#7f7f7f",
}
METHOD_LABEL = {
    "M0": "M0 Proposed\n(DMDE+DEM)",
    "B1": "B1 Random\nfeasible assign",
    "B2": "B2 Greedy\ninsertion",
    "D0": "D0 DMDE\n+planar approx",
}

#: 唯一比较尺的逐 seed 归一列（不落 CSV，绘图期从 per_seed 现算）
UNIFIED_KEY = "path_m_audit_per_completion"


def load_methods() -> dict[str, dict]:
    methods: dict[str, dict] = {}
    for mid in METHOD_ORDER:
        p = RESULTS / mid / "summary.json"
        if not p.exists():
            print(f"[plot] skip missing method summary: {p}")
            continue
        methods[mid] = json.loads(p.read_text(encoding="utf-8"))
    return methods


def _per_completion_stats(data: dict) -> tuple[float, float]:
    """逐 seed 归一后跨 seed 汇总：mean, std(ddof=0)。

    与 claims._build_reference / _normalized_pair 完全同口径；分母 <= 0 的
    seed 显式跳过、绝不静默补 0。
    """
    vals: list[float] = []
    for row in data.get("per_seed") or []:
        n = float(row.get("n_completed", 0) or 0)
        p = float(row.get("path_m_audit", 0) or 0)
        if n > 0:
            vals.append(p / n)
    if not vals:
        return float("nan"), 0.0
    arr = np.asarray(vals, dtype=float)
    return float(arr.mean()), float(arr.std(ddof=0))


def _agg(data: dict, key: str, what: str = "mean") -> float:
    if key == UNIFIED_KEY:
        mean, std = _per_completion_stats(data)
        return std if what == "std" else mean
    node = data.get("agg", {}).get(key)
    if node is None:
        return float("nan")
    if isinstance(node, dict):
        return float(node.get(what, float("nan")))
    return float(node)


def _err(data: dict, key: str) -> float:
    if key == UNIFIED_KEY:
        return _per_completion_stats(data)[1]
    node = data.get("agg", {}).get(key)
    if isinstance(node, dict):
        return float(node.get("std", 0.0) or 0.0)
    return 0.0


def fig_main_panel(methods: dict[str, dict]) -> Path:
    """四合一面板：完成率 / 按时率 / 奖励产出 / 每完成 DEM 航程（唯一尺）。"""
    names = [m for m in METHOD_ORDER if m in methods]
    x = np.arange(len(names))
    w = 0.55

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    specs = [
        ("completion_rate_final", "Completion rate (final, hard constraint)", 1.0),
        ("on_time_rate_final", "On-time rate", 1.0),
        ("reward_yield", "Reward yield", 1.0),
        (UNIFIED_KEY, "DEM path per completion (m, single ruler)", 1.0),
    ]
    for ax, (key, title, scale) in zip(axes.ravel(), specs):
        vals = [_agg(methods[n], key) * scale for n in names]
        errs = [_err(methods[n], key) * scale for n in names]
        colors = [METHOD_COLOR[n] for n in names]
        bars = ax.bar(x, vals, w, yerr=errs, capsize=4, color=colors,
                      edgecolor="black", linewidth=0.6, alpha=0.92)
        ax.set_xticks(x)
        ax.set_xticklabels([METHOD_LABEL[n] for n in names], fontsize=8)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", alpha=0.3, linestyle=":")
        ax.set_axisbelow(True)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:.3f}" if v < 100 else f"{v:.0f}",
                        (b.get_x() + b.get_width() / 2, b.get_height() + max(errs) * 0.15),
                        ha="center", va="bottom", fontsize=7.5)
    fig.suptitle("exp05 E1: descriptive outcomes by method (mean +/- std, no ranking)",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = FIGDIR / "exp05_e1_main_panel.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_cost_tradeoff(methods: dict[str, dict]) -> Path:
    """描述性散点：每完成 DEM 航程 vs 完成率（不标注优劣方向）。"""
    names = [m for m in METHOD_ORDER if m in methods]
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    for n in names:
        px = _agg(methods[n], UNIFIED_KEY)
        py = _agg(methods[n], "completion_rate_final")
        ax.scatter(px, py, s=90, color=METHOD_COLOR[n], edgecolor="black",
                   linewidth=0.8, zorder=3, label=n)
        ax.annotate(n, (px, py), textcoords="offset points", xytext=(8, 6), fontsize=9)
    ax.set_xlabel("DEM path per completion (m, single ruler)")
    ax.set_ylabel("Completion rate (final, hard constraint)")
    ax.set_title("exp05 E1: descriptive magnitude (no superiority claim)")
    ax.grid(alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = FIGDIR / "exp05_e1_tradeoff.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_lateness(methods: dict[str, dict]) -> Path:
    names = [m for m in METHOD_ORDER if m in methods]
    x = np.arange(len(names))
    fig, ax1 = plt.subplots(figsize=(9, 5))
    late = [_agg(methods[n], "n_late") for n in names]
    late_e = [_err(methods[n], "n_late") for n in names]
    ax1.bar(x - 0.2, late, 0.4, yerr=late_e, capsize=3, color="#c0504d",
            label="Late deliveries (n)")
    ax1.set_ylabel("Late deliveries (n)", color="#c0504d")
    ax1.set_xticks(x)
    ax1.set_xticklabels(names)
    ax1.tick_params(axis="y", labelcolor="#c0504d")

    ax2 = ax1.twinx()
    avg_late = [_agg(methods[n], "avg_lateness_s") for n in names]
    ax2.plot(x + 0.2, avg_late, "o-", color="#1f4e79", linewidth=1.5,
             label="Average lateness (s)")
    ax2.set_ylabel("Average lateness (s)", color="#1f4e79")
    ax2.tick_params(axis="y", labelcolor="#1f4e79")
    plt.title("exp05 E1: lateness by method")
    ax1.grid(axis="y", alpha=0.3, linestyle=":")
    fig.tight_layout()
    out = FIGDIR / "exp05_e1_lateness.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_gate_and_cost(methods: dict[str, dict]) -> Path:
    """门禁/名额诊断 + 求解代价（墙钟只作观测，不跨实验横向比较）。"""
    names = [m for m in METHOD_ORDER if m in methods]
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
        vals = [_agg(methods[n], key) for n in names]
        ax.bar(x + (i - 1) * w, vals, w, label=label, color=color, alpha=0.9,
               edgecolor="black", linewidth=0.4)
    shortfall = [_agg(methods[n], "n_capacity_shortfall") for n in names]
    skip = [_agg(methods[n], "n_voluntary_skip") for n in names]
    ax.bar(x + 2 * w, shortfall, w, label="Capacity shortfall (n)", color="#c0504d",
           alpha=0.9, edgecolor="black", linewidth=0.4)
    ax.bar(x + 3 * w, skip, w, label="Voluntary skip (n)", color="#7f7f7f",
           alpha=0.9, edgecolor="black", linewidth=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_title("exp05 E1: coverage gate and quota diagnostics")
    ax.legend(fontsize=7.5, ncol=2)
    ax.grid(axis="y", alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)

    ax = axes[1]
    vals = [_agg(methods[n], "wall_seconds") for n in names]
    ax.barh(names, vals, color=[METHOD_COLOR[n] for n in names],
            edgecolor="black", linewidth=0.5, alpha=0.92)
    for i, v in enumerate(vals):
        ax.annotate(f"{v:.0f}s", (v, i), xytext=(4, 0), textcoords="offset points",
                    va="center", fontsize=8)
    ax.set_xlabel("Solver wall time (s, observation only)")
    ax.set_title("exp05 E1: compute cost by method")
    ax.grid(axis="x", alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = FIGDIR / "exp05_e1_gate_and_cost.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def main() -> None:
    FIGDIR.mkdir(parents=True, exist_ok=True)
    methods = load_methods()
    if not methods:
        print("[plot] no method summaries found under", RESULTS)
        return
    outs = [
        fig_main_panel(methods),
        fig_cost_tradeoff(methods),
        fig_lateness(methods),
        fig_gate_and_cost(methods),
    ]
    for p in outs:
        print(f"[plot] wrote {p}")
    # summary_primary.csv 概览同步打印（统一尺，逐 seed 归一）
    print("\n[plot] descriptive quick view (single DEM ruler)")
    hdr = (f"{'method':<7}{'comp':>7}{'ontime':>8}{'DEM m/comp':>12}"
           f"{'late':>6}{'elim':>6}{'skip':>6}")
    print(hdr)
    for n in [m for m in METHOD_ORDER if m in methods]:
        print(f"{n:<7}{_agg(methods[n],'completion_rate_final'):>7.3f}"
              f"{_agg(methods[n],'on_time_rate_final'):>8.3f}"
              f"{_agg(methods[n],UNIFIED_KEY):>12.1f}"
              f"{_agg(methods[n],'n_late'):>6.1f}"
              f"{_agg(methods[n],'n_eliminated'):>6.1f}"
              f"{_agg(methods[n],'n_voluntary_skip'):>6.1f}")


if __name__ == "__main__":
    main()
