# -*- coding: utf-8 -*-
"""exp02_selection_scale 结果表生成器（实验自持、单源、禁手写数字）。

从已落盘的 ``results/metrics.json`` 读取 ``scales[]``，生成四份产物
（默认 ``results/tables/``）：

  * ``exp02_main_results.csv``   全格明细（N × 档位：任务去向分账 + 分段耗时）
  * ``exp02_main_results.tex``   LaTeX 三线表片段（booktabs，按 N 分组）
  * ``exp02_scalability.csv``    可扩展性长表（N × 档位的分段耗时）
  * ``exp02_scalability.tex``    LaTeX 三线表（N → 二阶段耗时）

口径纪律：

* 所有数字来自 json，脚本不内置任何实测值；单 seed 42；
  场景以 ``config.yaml`` 的 ``data.scenario`` 为准。
* **主判据是任务去向分账与墙钟量级，不是 F1**。F1 只作描述性观测，
  进 CSV、不进 LaTeX 主表、不产出任何优劣结论。
* 出表门禁：``metrics.scope.kind`` 必须是单周期可扩展性档，且全格
  ``accounting.identity_ok`` 为真；收窄前旧档（无 accounting）一律拒绝。
* 跨场景 / 跨实验禁直接比绝对值、禁配对 t 检验；表注不预设结论、
  不报告耗时倍数、不作机制归因。

用法::

    python build_tables.py
    python build_tables.py --results results_other --out results_other/tables
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

EXP_DIR = Path(__file__).resolve().parent
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))

from accounting import (  # noqa: E402
    assert_no_quality_claims,
    summarize,
)

MAIN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("N", "n_tasks"),
    ("variant", "variant"),
    ("n_cand", "_n_cand"),
    ("n_arrived", "_n_arrived"),
    ("n_sel", "n_selected"),
    ("n_done", "_n_done"),
    ("n_not_selected", "_n_not_selected"),
    ("n_deferred_in_selected", "_n_deferred"),
    ("n_remain", "_n_remain"),
    ("identity_ok", "_identity_ok"),
    ("selection_active", "_selection_active"),
    ("compression", "compression_ratio"),
    ("t_hint_ms", "t_hint_ms"),
    ("t_sel_ms", "t_selection_ms"),
    ("t_build_ms", "t_build_ms"),
    ("t_muas_ms", "t_muas_ms"),
    ("t_total_ms", "t_total_ms"),
    ("lazy_edges", "lazy_edges"),
    ("F1_descriptive_only", "_f1"),
)

_TIME_COLS = {"t_hint_ms", "t_sel_ms", "t_build_ms", "t_muas_ms", "t_total_ms"}


def validate_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """出表门禁：拒绝收窄前旧档、拒绝分账不闭合的档。"""
    scope = metrics.get("scope") or {}
    if scope.get("kind") != "single_cycle_scalability_observation":
        raise ValueError(
            "metrics 缺少收窄后的 scope.kind（单周期可扩展性档）；"
            "收窄前旧档已按 experiments/README.md §7 归档，不得据此出表。"
        )
    rows = list(metrics.get("scales") or [])
    if not rows:
        raise ValueError("metrics.scales 为空")
    missing = [i for i, r in enumerate(rows) if not r.get("accounting")]
    if missing:
        raise ValueError(f"第 {missing} 格缺任务去向分账 accounting，禁止出表")
    bad = [i for i, r in enumerate(rows)
           if not (r["accounting"].get("identity_ok") is True)]
    if bad:
        raise ValueError(f"第 {bad} 格分账恒等式未闭合，禁止出表")
    return summarize(rows)


def _tex_note(metrics: dict[str, Any], acc: dict[str, Any]) -> str:
    """表注口径说明：机队规模与航程从 metrics 现算，不写死场景数值。"""
    k = metrics.get("K", "—")
    rng = metrics.get("uav_max_range_m", "—")
    try:
        rng_km = f"{float(rng) / 1000.0:g}"
    except (TypeError, ValueError):
        rng_km = "—"
    note = (
        "% 由 exp02_selection_scale/build_tables.py 从 results/metrics.json 单源生成，勿手写数字。\n"
        f"% 口径：K={k}，满电航程 {rng_km} km，单 seed 42；λ=1e-1 全实验统一；\n"
        f"%   全格 arrived = done + remain 闭合（{acc['n_accounted']}/{acc['n_cells']} 格）；\n"
        "%   unreachable 在单周期静态快照中不可观测，记为 null 而非 0。\n"
        "% 本实验是单周期快照、无后续周期：未选中/选中未交付一律记 remain（未完成），\n"
        "%   不宣称完成约束被满足；完成约束由 exp01 滚动闭环检验。\n"
        "% selection_active 表示本格是否发生选择层截断；N 小于选择封顶时为 F，\n"
        "%   但 t_sel_ms 仍可能非零（选择层照常被调用，只是没有压缩任务数）。\n"
        "% F1 只作描述性观测（门禁口径下不进搜索标量 f），不进本表、不作质量判据。\n"
        "% 墙钟为单次观测、跨格共享 DEM 段长缓存，只描述量级、不作机制归因、不报告耗时倍数。\n"
        "% 跨场景/跨实验禁直接比绝对值、禁配对 t 检验；表注不预设结论。"
    )
    assert_no_quality_claims(note, where="table_note")
    return note


def _flat(row: dict) -> dict:
    """把嵌套的 selection / muas / accounting 摊平为单行列。"""
    sel = row.get("selection") or {}
    muas = row.get("muas") or {}
    acc = row.get("accounting") or {}
    return {
        "n_tasks": row.get("n_tasks"),
        "variant": row.get("variant"),
        "n_selected": row.get("n_selected"),
        "compression_ratio": row.get("compression_ratio"),
        "t_hint_ms": row.get("t_hint_ms"),
        "t_selection_ms": row.get("t_selection_ms"),
        "t_build_ms": row.get("t_build_ms"),
        "t_muas_ms": row.get("t_muas_ms"),
        "t_total_ms": row.get("t_total_ms"),
        "lazy_edges": row.get("lazy_edges"),
        "_n_cand": sel.get("capability_n_cand"),
        "_n_arrived": acc.get("n_arrived"),
        "_n_done": acc.get("n_done"),
        "_n_not_selected": acc.get("n_not_selected"),
        "_n_deferred": acc.get("n_deferred_in_selected"),
        "_n_remain": acc.get("n_remain"),
        "_identity_ok": acc.get("identity_ok"),
        "_selection_active": acc.get("selection_active"),
        "_f1": muas.get("f1_reward_descriptive", muas.get("f1_reward")),
    }


def _f(v: object, nd: int = 1) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "T" if v else "F"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def build(metrics: dict, out_dir: Path) -> list[Path]:
    acc_summary = validate_metrics(metrics)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [_flat(r) for r in (metrics.get("scales") or [])]
    ns = sorted({int(r["n_tasks"]) for r in rows if r.get("n_tasks") is not None})
    tex_note = _tex_note(metrics, acc_summary)

    # 1) 全格明细 CSV（计时列保留 2 位小数，其余保持 json 原值）
    p_csv = out_dir / "exp02_main_results.csv"
    with p_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in MAIN_COLUMNS])
        for r in rows:
            w.writerow([
                _f(r.get(key), 2) if col in _TIME_COLS else r.get(key, "")
                for col, key in MAIN_COLUMNS
            ])

    # 2) 主结果 LaTeX 三线表（每个 N 一段，行=档位）
    p_tex = out_dir / "exp02_main_results.tex"
    lines = [
        tex_note,
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{单周期任务规模可扩展性：任务去向分账与端到端耗时"
        r"（$K=" + str(metrics.get("K", "—")) + r"$，单 seed 42）}",
        r"  \label{tab:exp02-main}",
        r"  \begin{tabular}{llrrrrrr}",
        r"    \toprule",
        r"    $N$ & 档位 & 到达 & 入选 & 交付 & 未完成 & 压缩比 & 端到端 (s) \\",
    ]
    for n in ns:
        lines.append(r"    \midrule")
        for r in [x for x in rows if x.get("n_tasks") == n]:
            cells = [
                str(n),
                str(r.get("variant") or "—").replace("_", r"\_"),
                _f(r.get("_n_arrived"), 0),
                _f(r.get("n_selected"), 0),
                _f(r.get("_n_done"), 0),
                _f(r.get("_n_remain"), 0),
                _f(r.get("compression_ratio"), 2),
                _f((r.get("t_total_ms") or 0) / 1000.0, 2),
            ]
            lines.append("    " + " & ".join(cells) + r" \\")
    lines += [
        r"    \bottomrule",
        r"  \end{tabular}",
        r"\end{table}",
        "",
    ]
    assert_no_quality_claims("\n".join(lines), where="main_table")
    p_tex.write_text("\n".join(lines), encoding="utf-8")

    # 3) 可扩展性长表 CSV
    p_sc = out_dir / "exp02_scalability.csv"
    with p_sc.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["N", "variant", "t_muas_s", "t_total_s", "lazy_edges",
                    "n_selected", "n_done", "n_remain"])
        for r in rows:
            w.writerow([
                r.get("n_tasks"), r.get("variant"),
                _f((r.get("t_muas_ms") or 0) / 1000.0, 3),
                _f((r.get("t_total_ms") or 0) / 1000.0, 3),
                r.get("lazy_edges", ""),
                r.get("n_selected", ""),
                r.get("_n_done", ""),
                r.get("_n_remain", ""),
            ])

    # 4) 可扩展性 LaTeX 三线表（N → 各档位二阶段耗时）
    variants = list(dict.fromkeys(str(r.get("variant")) for r in rows))
    p_sc_tex = out_dir / "exp02_scalability.tex"
    lines = [
        tex_note,
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{任务规模与二阶段求解耗时（s，单 seed 42；单次墙钟观测）}",
        r"  \label{tab:exp02-scalability}",
        r"  \begin{tabular}{l" + "r" * len(variants) + "}",
        r"    \toprule",
        "    $N$ & " + " & ".join(v.replace("_", r"\_") for v in variants) + r" \\",
        r"    \midrule",
    ]
    for n in ns:
        cells = [str(n)]
        for v in variants:
            hit = next((x for x in rows
                        if x.get("n_tasks") == n and x.get("variant") == v), None)
            cells.append(_f((hit.get("t_muas_ms") or 0) / 1000.0, 2)
                         if hit else "—")
        lines.append("    " + " & ".join(cells) + r" \\")
    lines += [
        r"    \bottomrule",
        r"  \end{tabular}",
        r"\end{table}",
        "",
    ]
    assert_no_quality_claims("\n".join(lines), where="scalability_table")
    p_sc_tex.write_text("\n".join(lines), encoding="utf-8")
    print(f"[build_tables] K={metrics.get('K', '—')}, rows={len(rows)}, N={ns}, "
          f"identity_ok_all={acc_summary['identity_ok_all']}")
    return [p_csv, p_tex, p_sc, p_sc_tex]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 metrics.json 生成 exp02 结果表")
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
    try:
        for p in build(metrics, out_dir):
            print(f"[build_tables] -> {p}")
    except ValueError as exc:
        print(f"[build_tables][门禁拒绝] {exc}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
