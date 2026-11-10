# -*- coding: utf-8 -*-
"""exp02_selection_scale 结果表生成器（实验自持、单源、禁手写数字）。

从已落盘的 ``results/metrics.json`` 读取 ``scales[]``，生成四份产物
（默认 ``results/tables/``）：

  * ``exp02_main_results.csv``   全格明细（N × variant 的规模/耗时/质量）
  * ``exp02_main_results.tex``   LaTeX 三线表片段（booktabs，按 N 分组）
  * ``exp02_scalability.csv``    可扩展性长表（N × variant 的分段耗时）
  * ``exp02_scalability.tex``    LaTeX 三线表（N → 二阶段耗时）

口径纪律：所有数字来自 json，脚本不内置任何实测值；单 seed 42；
当前场景 guangzhou（K=6 / 10 km）；**跨场景禁直接比绝对值、禁配对 t 检验**；
图注与表注不预设结论。

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

MAIN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("N", "n_tasks"),
    ("variant", "variant"),
    ("n_cand", "_n_cand"),
    ("cap_t", "_cap_t"),
    ("n_sel", "n_selected"),
    ("n_exec", "_n_exec"),
    ("compression", "compression_ratio"),
    ("F1", "_f1"),
    ("t_hint_ms", "t_hint_ms"),
    ("t_sel_ms", "t_selection_ms"),
    ("t_build_ms", "t_build_ms"),
    ("t_muas_ms", "t_muas_ms"),
    ("t_total_ms", "t_total_ms"),
    ("lazy_edges", "lazy_edges"),
)

def _tex_note(k: object, rng: object) -> str:
    """表注口径说明：机队规模与航程从 metrics 现算，不写死场景数值。"""
    try:
        rng_km = f"{float(rng) / 1000.0:g}"
    except (TypeError, ValueError):
        rng_km = "—"
    return (
        "% 由 exp02_selection_scale/build_tables.py 从 results/metrics.json 单源生成，勿手写数字。\n"
        f"% 口径：K={k}，满电航程 {rng_km} km，单 seed 42；λ=1e-1 全实验统一；\n"
        "%   场景以 config.yaml 的 data.scenario 为准。\n"
        "% 跨场景/跨实验禁直接比绝对值、禁配对 t 检验；结论以本次实测为准，表注不预设结论。"
    )


def _flat(row: dict) -> dict:
    """把嵌套的 selection / muas 摊平为单行列。"""
    sel = row.get("selection") or {}
    muas = row.get("muas") or {}
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
        "_cap_t": sel.get("capability_cap"),
        "_n_exec": muas.get("n_executed"),
        "_f1": muas.get("f1_reward"),
    }


def _f(v: object, nd: int = 1) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def build(metrics: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [_flat(r) for r in (metrics.get("scales") or [])]
    ns = sorted({int(r["n_tasks"]) for r in rows if r.get("n_tasks") is not None})
    k = metrics.get("K", "—")
    rng = metrics.get("uav_max_range_m", "—")
    tex_note = _tex_note(k, rng)

    # 1) 全格明细 CSV
    p_csv = out_dir / "exp02_main_results.csv"
    with p_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in MAIN_COLUMNS])
        for r in rows:
            w.writerow([r.get(key, "") for _, key in MAIN_COLUMNS])

    # 2) 主结果 LaTeX 三线表（每个 N 一段，行=variant）
    p_tex = out_dir / "exp02_main_results.tex"
    lines = [
        tex_note,
        r"\begin{table}[htbp]",
        r"  \centering",
        rf"  \caption{{选择层规模扫描结果（$K={k}$，单 seed 42）}}",
        r"  \label{tab:exp02-main}",
        r"  \begin{tabular}{lrrrrrr}",
        r"    \toprule",
        r"    $N$ & 变体 & 候选数 & 选择数 & 执行数 & $F_1$ & 端到端 (s) \\",
    ]
    for n in ns:
        lines.append(r"    \midrule")
        for r in [x for x in rows if x.get("n_tasks") == n]:
            cells = [
                str(n),
                str(r.get("variant") or "—"),
                _f(r.get("_n_cand"), 0),
                _f(r.get("n_selected"), 0),
                _f(r.get("_n_exec"), 0),
                _f(r.get("_f1"), 1),
                _f((r.get("t_total_ms") or 0) / 1000.0, 2),
            ]
            lines.append("    " + " & ".join(cells) + r" \\")
    lines += [
        r"    \bottomrule",
        r"  \end{tabular}",
        r"\end{table}",
        "",
    ]
    p_tex.write_text("\n".join(lines), encoding="utf-8")

    # 3) 可扩展性长表 CSV
    p_sc = out_dir / "exp02_scalability.csv"
    with p_sc.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["N", "variant", "t_muas_s", "t_total_s", "lazy_edges",
                    "n_selected", "n_executed"])
        for r in rows:
            w.writerow([
                r.get("n_tasks"), r.get("variant"),
                _f((r.get("t_muas_ms") or 0) / 1000.0, 3),
                _f((r.get("t_total_ms") or 0) / 1000.0, 3),
                r.get("lazy_edges", ""),
                r.get("n_selected", ""),
                r.get("_n_exec", ""),
            ])

    # 4) 可扩展性 LaTeX 三线表（N → 各变体二阶段耗时）
    variants = list(dict.fromkeys(str(r.get("variant")) for r in rows))
    p_sc_tex = out_dir / "exp02_scalability.tex"
    lines = [
        tex_note,
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{任务规模与二阶段求解耗时（s，单 seed 42）}",
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
    p_sc_tex.write_text("\n".join(lines), encoding="utf-8")
    print(f"[build_tables] K={k}, range={rng}m, rows={len(rows)}, N={ns}")
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
    for p in build(metrics, out_dir):
        print(f"[build_tables] -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
