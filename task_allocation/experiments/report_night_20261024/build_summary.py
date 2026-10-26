# -*- coding: utf-8 -*-
"""整夜重跑汇总：四实验 → 总表 + 跨实验总览图 + 数据快照 JSON。

输入（各自 results/）：
    exp01_smoke/results/metrics.json      seed_summary = {metric: {mean, std}}
    exp02_selection_scale/results/metrics.json   scales = [row, ...]
    exp05_baselines/results/summary.csv   每臂一行（mean/std 已展平）
    exp06_joint_regime/results/summary.csv 每格一行

输出：
    summary_all.csv        逐"观测单元"的长表（experiment / arm / seed）
    snapshot.json          供报告撰写引用（数值已算好）
    figures/*.png          跨实验总览图

图内文字一律 ASCII。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

EXPS = Path(__file__).resolve().parent.parent          # task_allocation/experiments
HERE = Path(__file__).resolve().parent                 # report_night_20261024
FIGDIR = HERE / "figures"


# ── 读取 ────────────────────────────────────────────────────────
def read_exp01() -> tuple[list[dict], dict]:
    p = EXPS / "exp01_smoke" / "results" / "metrics.json"
    if not p.exists():
        return [], {}
    d = json.loads(p.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for seed, m in (d.get("per_seed") or {}).items():
        s = m.get("summary", m)
        rows.append({
            "experiment": "exp01", "arm": "M0", "variant": f"seed_{seed}",
            "n_arrived": s.get("n_arrived"), "n_completed": s.get("n_completed"),
            "completion_rate_final": s.get("completion_rate_final"),
            "effective_completion_rate": s.get("effective_completion_rate"),
            "on_time_rate_final": s.get("on_time_rate_final"),
            "reward_yield": s.get("reward_yield"),
            "n_late": s.get("n_late"), "avg_lateness_s": s.get("avg_lateness_s"),
            "total_path_m": s.get("total_path_m", s.get("total_cost")),
            "path_per_completion_m": s.get("path_per_completion_m"),
            "n_eliminated": s.get("n_eliminated"),
            "n_unreachable": s.get("n_unreachable"),
            "n_remaining_pool": s.get("n_remaining_pool"),
            "n_cycles": s.get("n_cycles"),
            "wall_seconds": s.get("wall_seconds"),
        })
    return rows, {"seed_summary": d.get("seed_summary", {}),
                  "seeds": d.get("seeds", []),
                  "n_cycles": d.get("n_cycles")}


def read_exp02() -> tuple[list[dict], dict]:
    p = EXPS / "exp02_selection_scale" / "results" / "metrics.json"
    if not p.exists():
        return [], {}
    d = json.loads(p.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for r in d.get("scales", []):
        rows.append({
            "experiment": "exp02",
            "arm": r.get("variant", "?"),
            "variant": f"N={r.get('n_tasks')}",
            "n_tasks": r.get("n_tasks"),
            "n_selected": r.get("n_selected"),
            "n_executed": r.get("n_executed"),
            "f1": r.get("f1", r.get("F1")),
            "time_s": r.get("time_s", r.get("t_select_s")),
            "solve_s": r.get("solve_time_s", r.get("time_s")),
            "total_path_m": r.get("total_path_m"),
        })
    return rows, {"K": d.get("K"), "max_range_m": d.get("uav_max_range_m")}


def read_csv(exp: str) -> list[dict]:
    p = EXPS / exp / "results" / "summary.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["experiment"] = exp
    return rows


def _f(v, default=float("nan")) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── 图 ─────────────────────────────────────────────────────────
def fig_exp01_seeds(rows: list[dict]) -> Path | None:
    if not rows:
        return None
    seeds = [str(r["variant"]).replace("seed_", "") for r in rows]
    x = range(len(rows))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    specs = [("completion_rate_final", "Completion rate", 1.0),
             ("on_time_rate_final", "On-time rate", 1.0),
             ("path_per_completion_m", "Path per completion (km)", 1e-3)]
    for ax, (key, title, scale) in zip(axes, specs):
        vals = [_f(r.get(key)) * scale for r in rows]
        bars = ax.bar(list(x), vals, 0.55, color="#1f4e79", edgecolor="black",
                      linewidth=0.6, alpha=0.9)
        ax.set_xticks(list(x))
        ax.set_xticklabels(seeds)
        ax.set_xlabel("seed")
        ax.set_title(title, fontsize=11)
        ax.grid(axis="y", alpha=0.3, linestyle=":")
        ax.set_axisbelow(True)
        for b, v in zip(bars, vals):
            if v == v:  # not nan
                ax.annotate(f"{v:.3f}" if v < 100 else f"{v:.1f}",
                            (b.get_x() + b.get_width() / 2, b.get_height()),
                            ha="center", va="bottom", fontsize=8)
        if key.endswith("path_per_completion_m"):
            mv = mean([v for v in vals if v == v])
            ax.axhline(mv, ls="--", color="#c0504d", lw=1.2,
                       label=f"mean {mv:.2f} km")
            ax.legend(fontsize=8)
    fig.suptitle("exp01: per-seed outcomes (lambda_cost=0.1, coverage gate on)",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    out = FIGDIR / "cross_exp01_seeds.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_exp02_scaling(rows: list[dict]) -> Path | None:
    if not rows:
        return None
    variants: dict[str, list[tuple[float, float, float]]] = {}
    for r in rows:
        if r.get("variant") == "full_baseline" or r["arm"] == "full_baseline":
            continue
        n, t = _f(r.get("n_tasks")), _f(r.get("time_s"))
        sel = _f(r.get("n_selected"))
        if n == n and t == t:
            variants.setdefault(r["arm"], []).append((n, t, sel))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    for name, pts in sorted(variants.items()):
        pts.sort()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        axes[0].plot(xs, ys, "o-", label=name, linewidth=1.5, markersize=5)
        axes[1].plot(xs, [p[2] for p in pts], "o-", label=name, linewidth=1.5,
                     markersize=5)
    base = sorted([(n, t, s) for r in rows
                   if str(r.get("variant", "")).startswith("N=")
                   for n, t, s in [(r.get("n_tasks"), r.get("time_s"), r.get("n_selected"))]
                   if False])
    full = [(_f(r.get("n_tasks")), _f(r.get("time_s"))) for r in rows
            if r["arm"] == "full_baseline"]
    if full:
        full.sort()
        axes[0].plot([p[0] for p in full], [p[1] for p in full], "k^--",
                     label="full_baseline (no selection)", linewidth=1.4, markersize=6)
    axes[0].set_xlabel("|T| candidate task pool size")
    axes[0].set_ylabel("Stage-1 time (s)")
    axes[0].set_title("exp02: selection cost vs problem scale")
    axes[0].grid(alpha=0.3, linestyle=":")
    axes[0].legend(fontsize=7.5)
    axes[1].set_xlabel("|T| candidate task pool size")
    axes[1].set_ylabel("Selected tasks n_sel")
    axes[1].set_title("exp02: compression behaviour")
    axes[1].grid(alpha=0.3, linestyle=":")
    axes[1].legend(fontsize=7.5)
    fig.tight_layout()
    out = FIGDIR / "cross_exp02_scaling.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


def fig_overview(snap: dict) -> Path | None:
    """四个场景的核心 outcome 总览。"""
    entries: list[tuple[str, float, float, float]] = []  # label, comp, ontime, km
    e1 = snap.get("exp01", {}).get("seed_summary", {})
    if e1:
        entries.append((
            "exp01\nproposed (3 seeds)",
            _f(e1.get("completion_rate_final", {}).get("mean")),
            _f(e1.get("on_time_rate_final", {}).get("mean")),
            _f(e1.get("path_per_completion_m", {}).get("mean")) / 1000.0,
        ))
    e5 = snap.get("exp05_arms", {})
    for arm in ["M0", "B2", "B3", "D0"]:
        if arm in e5:
            entries.append((
                f"exp05 {arm}",
                _f(e5[arm].get("completion_rate_final")),
                _f(e5[arm].get("on_time_rate_final")),
                _f(e5[arm].get("path_per_completion_m")) / 1000.0,
            ))
    e6 = snap.get("exp06_cells", [])
    for cell in e6:
        entries.append((
            f"exp06 arr={cell.get('arrival_lambda'):g}",
            _f(cell.get("completion_rate_final")),
            _f(cell.get("on_time_rate_final")),
            _f(cell.get("path_per_completion_m")) / 1000.0,
        ))
    if not entries:
        return None
    labels = [e[0] for e in entries]
    x = range(len(entries))
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, idx, title, fmt in zip(
        axes, (1, 2, 3),
        ("Completion rate", "On-time rate", "Path per completion (km)"),
        ("{:.3f}", "{:.3f}", "{:.1f}"),
    ):
        vals = [e[idx] for e in entries]
        colors = ["#1f4e79" if e[0].startswith("exp01") or "M0" in e[0] else "#8faadc"
                  for e in entries]
        bars = ax.bar(list(x), vals, 0.6, color=colors, edgecolor="black",
                      linewidth=0.5, alpha=0.92)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=7, rotation=40, ha="right")
        ax.set_title(title, fontsize=11)
        ax.grid(axis="y", alpha=0.3, linestyle=":")
        ax.set_axisbelow(True)
        for b, v in zip(bars, vals):
            if v == v:
                ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, b.get_height()),
                            ha="center", va="bottom", fontsize=7)
    fig.suptitle("Cross-experiment overview: proposed method vs baselines vs regimes",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    out = FIGDIR / "cross_overview.png"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return out


# ── 主流程 ─────────────────────────────────────────────────────
def main() -> None:
    FIGDIR.mkdir(parents=True, exist_ok=True)
    e1_rows, e1_meta = read_exp01()
    e2_rows, e2_meta = read_exp02()
    e5_rows = read_csv("exp05_baselines")
    e6_rows = read_csv("exp06_joint_regime")

    all_rows: list[dict] = []
    all_rows += e1_rows
    all_rows += e2_rows
    all_rows += e5_rows
    all_rows += e6_rows

    # 长表（字段取并集）
    fields: list[str] = []
    for r in all_rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with (HERE / "summary_all.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    print(f"[summary] rows={len(all_rows)} -> summary_all.csv")

    # 快照
    snap: dict = {
        "exp01": e1_meta,
        "exp01_per_seed": e1_rows,
        "exp02": {"meta": e2_meta, "rows": e2_rows},
        "exp05_rows": e5_rows,
        "exp06_rows": e6_rows,
    }
    # exp05 按臂的 mean/std
    arms: dict[str, dict] = {}
    for arm in ["M0", "D0", "B1", "B2", "B2e", "B3"]:
        p = EXPS / "exp05_baselines" / "results" / arm / "summary.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            agg = d.get("agg", {})
            arms[arm] = {k: (v.get("mean") if isinstance(v, dict) else v)
                         for k, v in agg.items()}
            arms[arm].update({f"{k}_std": (v.get("std") if isinstance(v, dict) else 0)
                              for k, v in agg.items()})
    snap["exp05_arms"] = arms
    snap["exp06_cells"] = [
        {
            "tag": r.get("tag"),
            "arrival_lambda": _f(r.get("arrival_lambda")),
            "lambda_cost": _f(r.get("lambda_cost")),
            "n_arrived": _f(r.get("n_arrived")),
            "n_completed": _f(r.get("n_completed")),
            "completion_rate_final": _f(r.get("completion_rate_final")),
            "on_time_rate_final": _f(r.get("on_time_rate_final")),
            "n_eliminated": _f(r.get("n_eliminated")),
            "n_unreachable": _f(r.get("n_unreachable")),
            "n_remaining_pool": _f(r.get("n_remaining_pool")),
            "total_path_m": _f(r.get("total_path_m")),
            "path_per_completion_m": _f(r.get("path_per_completion_m")),
            "throughput_per_cycle": _f(r.get("throughput_per_cycle")),
            "obs_sel_total": _f(r.get("obs_sel_total")),
            "obs_backlog_last": _f(r.get("obs_backlog_last")),
            "obs_cap_t_first": _f(r.get("obs_cap_t_first")),
            "obs_cap_t_last": _f(r.get("obs_cap_t_last")),
            "wall_seconds": _f(r.get("wall_seconds")),
        }
        for r in e6_rows
    ]
    (HERE / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[summary] wrote snapshot.json")

    for p in (fig_exp01_seeds(e1_rows), fig_exp02_scaling(e2_rows), fig_overview(snap)):
        if p:
            print(f"[summary] wrote {p.name}")


if __name__ == "__main__":
    main()
