# -*- coding: utf-8 -*-
"""exp06_joint_regime 绘图：到达压力 × 成本权重的联合域形态。

读 ``results/summary.csv``（标量汇总）+ 每格 ``results/<tag>/metrics.json``
（逐周期序列，用于积压 / cap(t) 时序图）。

图内文字一律 ASCII（matplotlib 默认字体不含中文）。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FIGDIR = RESULTS / "figures"


def load_rows() -> list[dict]:
    p = RESULTS / "summary.csv"
    if not p.exists():
        print(f"[plot] missing {p}")
        return []
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _f(row: dict, key: str, default: float = float("nan")) -> float:
    try:
        v = row.get(key)
        return float("nan") if v in (None, "") else float(v)
    except ValueError:
        return default


def load_cell_cycles(tag: str, seed: str) -> list[dict]:
    p = RESULTS / tag / "metrics.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    m = d.get("per_seed", {}).get(str(seed), d)
    return m.get("cycles") or []


def fig_pressure_curves(rows: list[dict]) -> Path:
    """随到达强度变化的核心指标曲线。"""
    lams = sorted({_f(r, "arrival_lambda") for r in rows})
    lcs = sorted({_f(r, "lambda_cost") for r in rows})
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    specs = [
        ("completion_rate_final", "Completion rate (final)", None),
        ("on_time_rate_final", "On-time rate", None),
        ("throughput_per_cycle", "Throughput per cycle (tasks)", None),
        ("avg_lateness_s", "Average lateness (s)", None),
    ]
    for ax, (key, title, _scale) in zip(axes.ravel(), specs):
        for lc in lcs:
            ys = []
            for lam in lams:
                cand = [r for r in rows
                        if _f(r, "arrival_lambda") == lam and _f(r, "lambda_cost") == lc]
                ys.append(_f(cand[0], key) if cand else float("nan"))
            ax.plot(range(len(lams)), ys, "o-", label=f"lambda_cost={lc:g}",
                    linewidth=1.6, markersize=6)
        ax.set_xticks(range(len(lams)))
        ax.set_xticklabels([f"{lam:g}" for lam in lams])
        ax.set_xlabel("Arrival intensity lambda (tasks/cycle)")
        ax.set_title(title, fontsize=11)
        ax.grid(alpha=0.3, linestyle=":")
        ax.legend(fontsize=8)
        ax.set_axisbelow(True)
    fig.suptitle("exp06: joint regime behaviour vs arrival pressure", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = FIGDIR / "exp06_pressure_curves.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_backlog(rows: list[dict]) -> Path:
    """任务池积压 |T_t| 时序（每格一条曲线）。"""
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    ax = axes[0]
    for r in rows:
        cycles = load_cell_cycles(r["tag"], r["seed"])
        if not cycles:
            continue
        backlog = [int((c.get("pool_counts") or {}).get("n_total", 0)) for c in cycles]
        ax.plot(range(1, len(backlog) + 1), backlog, linewidth=1.5,
                label=f"arr={_f(r,'arrival_lambda'):g}")
    ax.set_xlabel("Rolling cycle t")
    ax.set_ylabel("Backlog |T_t| (tasks in pool)")
    ax.set_title("Task-pool backlog over cycles")
    ax.grid(alpha=0.3, linestyle=":")
    ax.legend(fontsize=8, title="arrival lambda")
    ax.set_axisbelow(True)

    ax = axes[1]
    for r in rows:
        cycles = load_cell_cycles(r["tag"], r["seed"])
        if not cycles:
            continue
        ratio = []
        for c in cycles:
            sel = c.get("selection") or {}
            ns, ct = sel.get("capability_n_sel"), sel.get("capability_cap")
            if ns is not None and ct:
                ratio.append(ns / ct)
        if ratio:
            ax.plot(range(1, len(ratio) + 1), ratio, linewidth=1.5,
                    label=f"arr={_f(r,'arrival_lambda'):g}")
    ax.set_xlabel("Rolling cycle t")
    ax.set_ylabel("n_sel / cap(t)")
    ax.set_title("Selection size vs capability bound")
    ax.grid(alpha=0.3, linestyle=":")
    ax.legend(fontsize=8, title="arrival lambda")
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = FIGDIR / "exp06_backlog.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_arrival_split(rows: list[dict]) -> Path:
    """到达 vs 完成 vs 周期末残留：产能饿死的可视证据。"""
    rows_sorted = sorted(rows, key=lambda r: _f(r, "arrival_lambda"))
    labels = [f"arr={_f(r,'arrival_lambda'):g}" for r in rows_sorted]
    x = range(len(rows_sorted))
    fig, ax = plt.subplots(figsize=(10, 5))
    arrived = [_f(r, "n_arrived") for r in rows_sorted]
    completed = [_f(r, "n_completed") for r in rows_sorted]
    remain = [_f(r, "n_remaining_pool") for r in rows_sorted]
    ax.bar(x, arrived, 0.6, label="Arrived", color="#bfbfbf", edgecolor="black",
           linewidth=0.5)
    ax.bar(x, completed, 0.6, label="Completed", color="#1f4e79", edgecolor="black",
           linewidth=0.5)
    ax.bar(list(x), remain, 0.6, bottom=completed, label="Remaining in pool",
           color="#c0504d", edgecolor="black", linewidth=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Tasks")
    ax.set_title("exp06: arrived vs completed vs carried over (K=10, lambda_cost=0.1)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)
    for xi, c, a in zip(x, completed, arrived):
        ax.annotate(f"{c:.0f}/{a:.0f}", (xi, c), ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    out = FIGDIR / "exp06_arrival_split.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_cap_decay(rows: list[dict]) -> Path:
    """cap(t) 时序：容量上限随 cycles 的衰减形态。"""
    fig, ax = plt.subplots(figsize=(9, 5))
    for r in rows:
        cycles = load_cell_cycles(r["tag"], r["seed"])
        if not cycles:
            continue
        cap = [(c.get("selection") or {}).get("capability_cap") for c in cycles]
        cap = [int(v) for v in cap if v is not None]
        if cap:
            ax.plot(range(1, len(cap) + 1), cap, "o-", linewidth=1.4, markersize=4,
                    label=f"arr={_f(r,'arrival_lambda'):g}")
    ax.set_xlabel("Rolling cycle t")
    ax.set_ylabel("cap(t): capability bound (tasks)")
    ax.set_title("exp06: capability bound decay over cycles")
    ax.grid(alpha=0.3, linestyle=":")
    ax.legend(fontsize=8, title="arrival lambda")
    ax.set_axisbelow(True)
    fig.tight_layout()
    out = FIGDIR / "exp06_cap_decay.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def main() -> None:
    FIGDIR.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    if not rows:
        print("[plot] nothing to plot")
        return
    for p in (
        fig_pressure_curves(rows),
        fig_backlog(rows),
        fig_arrival_split(rows),
        fig_cap_decay(rows),
    ):
        print(f"[plot] wrote {p}")


if __name__ == "__main__":
    main()
