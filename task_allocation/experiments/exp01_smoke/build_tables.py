# -*- coding: utf-8 -*-
"""exp01_smoke 结果表生成器（实验自持、单源、禁手写数字）。

从已落盘的 ``results/metrics.json`` 读取 ``seed_summary`` / ``per_seed``，
生成三份产物（默认 ``results/tables/``）：

  * ``exp01_main_results.csv``   主结果表（逐 seed + 均值±标准差）
  * ``exp01_main_results.tex``   LaTeX 三线表片段（booktabs，可直接 \\input）
  * ``exp01_per_seed.csv``       逐 seed 全指标明细

口径纪律：所有数字来自 json，脚本不内置任何实测值；完成率分母排除
UNREACHABLE；``total_f2`` 混量纲（米+秒）**不列入主表**；墙钟只作观测。

用法::

    python build_tables.py
    python build_tables.py --results results_other --out results_other/tables
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent

# (表内名称, metrics 键, 缩放系数, 小数位, 单位)
# 键同时存在于 seed_summary（{mean,std}）与 per_seed[*].summary（标量）。
ROWS: tuple[tuple[str, str, float, int, str], ...] = (
    ("到达任务数", "n_arrived", 1.0, 0, ""),
    ("完成任务数", "n_completed", 1.0, 0, ""),
    ("完成率", "completion_rate_final", 1.0, 4, ""),
    ("按时率", "on_time_rate_final", 1.0, 4, ""),
    ("迟到任务数", "n_late", 1.0, 0, ""),
    ("平均迟到", "avg_lateness_s", 1.0, 3, "s"),
    ("总三维航程", "total_path_m", 1.0, 2, "m"),
    ("单位任务航程", "path_per_completion_m", 1.0, 2, "m"),
    ("单周期吞吐", "throughput_per_cycle", 1.0, 2, ""),
    ("求解耗时", "solve_time_ms", 0.001, 2, "s"),
    ("硬违约", "n_hard_violations_total", 1.0, 0, ""),
    ("淘汰", "n_eliminated", 1.0, 0, ""),
    ("不可达", "n_unreachable", 1.0, 0, ""),
    ("几何不可达", "n_unreachable_geo", 1.0, 0, ""),
    ("服务不可达", "n_unreachable_service", 1.0, 0, ""),
)

TEX_NOTE = (
    "% 由 exp01_smoke/build_tables.py 从 results/metrics.json 单源生成，勿手写数字。\n"
    "% 口径：完成率分母排除 UNREACHABLE；恒等式 arrived = done + unreachable + remain；\n"
    "% λ=1e-1 全实验统一；f 与航程为线性缩放，不是两条独立证据；墙钟只作观测。\n"
    "% 表内「不可达」含真几何不可达与机队服务不可达两类，均不计入完成率分母。"
)


def _fmt(value: object, scale: float, nd: int) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * scale:.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _summary_value(blob: dict, key: str, field: str) -> object:
    """兼容 {mean,std} 聚合结构与标量结构。"""
    v = blob.get(key)
    if isinstance(v, dict):
        return v.get(field)
    return v if field == "mean" else None


def build(metrics: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    seeds = [str(s) for s in metrics.get("seeds", [])]
    agg = metrics.get("seed_summary", {}) or {}
    per = metrics.get("per_seed", {}) or {}

    # 1) 主结果 CSV
    p_csv = out_dir / "exp01_main_results.csv"
    with p_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "unit"] + [f"seed_{s}" for s in seeds]
                   + ["mean", "std"])
        for label, key, scale, nd, unit in ROWS:
            row = [label, unit]
            row += [_fmt((per.get(s, {}).get("summary") or {}).get(key), scale, nd)
                    for s in seeds]
            # 计数类指标的均值/标准差必须保留小数（如 0.67±0.47），
            # 否则四舍五入成整数会抹掉跨 seed 差异。
            agg_nd = max(nd, 2)
            row.append(_fmt(_summary_value(agg, key, "mean"), scale, agg_nd))
            row.append(_fmt(_summary_value(agg, key, "std"), scale, agg_nd))
            w.writerow(row)

    # 2) LaTeX 三线表
    p_tex = out_dir / "exp01_main_results.tex"
    cols = "l" + "r" * (len(seeds) + 1)
    lines = [
        TEX_NOTE,
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{主链路正式档结果（guangzhou，多种子重复）}",
        r"  \label{tab:exp01-main}",
        r"  \begin{tabular}{" + cols + "}",
        r"    \toprule",
        "    指标 & " + " & ".join(seeds) + r" & 均值$\pm$标准差 \\",
        r"    \midrule",
    ]
    for label, key, scale, nd, unit in ROWS:
        name = f"{label} ({unit})" if unit else label
        cells = [_fmt((per.get(s, {}).get("summary") or {}).get(key), scale, nd)
                 for s in seeds]
        agg_nd = max(nd, 2)
        mean = _fmt(_summary_value(agg, key, "mean"), scale, agg_nd)
        std = _fmt(_summary_value(agg, key, "std"), scale, agg_nd)
        cells.append(f"{mean}$\\pm${std}" if mean != "—" else "—")
        lines.append("    " + name + " & " + " & ".join(cells) + r" \\")
    lines += [
        r"    \bottomrule",
        r"  \end{tabular}",
        r"\end{table}",
        "",
    ]
    p_tex.write_text("\n".join(lines), encoding="utf-8")

    # 3) 逐 seed 全指标明细
    p_all = out_dir / "exp01_per_seed.csv"
    keys = sorted({k for s in seeds for k in (per.get(s, {}).get("summary") or {})})
    with p_all.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric"] + [f"seed_{s}" for s in seeds] + ["mean", "std"])
        for k in keys:
            row = [k]
            row += [str((per.get(s, {}).get("summary") or {}).get(k, ""))
                    for s in seeds]
            row.append(str(_summary_value(agg, k, "mean") or ""))
            row.append(str(_summary_value(agg, k, "std") or ""))
            w.writerow(row)

    return [p_csv, p_tex, p_all]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 metrics.json 生成 exp01 结果表")
    ap.add_argument("--results", default="results",
                    help="结果目录名（相对实验目录；默认 results）")
    ap.add_argument("--out", default=None,
                    help="表格输出目录（默认 <results>/tables）")
    args = ap.parse_args(argv)

    results = EXP_DIR / args.results
    src = results / "metrics.json"
    if not src.exists():
        print(f"[build_tables] 缺少 {src}", flush=True)
        return 1
    metrics = json.loads(src.read_text(encoding="utf-8"))
    out_dir = Path(args.out) if args.out else results / "tables"
    if not out_dir.is_absolute():
        out_dir = EXP_DIR / out_dir
    made = build(metrics, out_dir)
    print(f"[build_tables] seeds={metrics.get('seeds')}, status={metrics.get('status')}")
    for p in made:
        print(f"[build_tables] -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
