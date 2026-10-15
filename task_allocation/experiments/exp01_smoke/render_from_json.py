# -*- coding: utf-8 -*-
"""render_from_json.py — 从已落盘的 metrics/solution json 补出图与表。

分块续跑驱动在求解全部完成后才调 make_plots；若绘图阶段被工具超时
打断（600s），用本脚本按格式/图 id 补出，不必重跑求解。

用法：:

    python render_from_json.py png                       # 全部静态图
    python render_from_json.py gif                       # 3 个动画图 GIF
    python render_from_json.py png,gif --figure outcome_summary
    python render_from_json.py tables                    # 只重出结果表 + PDF
    python render_from_json.py png --results results_phys  # 指定结果目录
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from paths import EXP_DIR


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 json 补出 exp01 图表")
    ap.add_argument("formats", nargs="?", default="png",
                    help="逗号分隔：png / gif / mp4 / pdf；或 tables")
    ap.add_argument("--figure", action="append", dest="figures", default=None,
                    help="只出指定图 id（可重复）")
    ap.add_argument("--dpi", type=int, default=None)
    ap.add_argument("--results", default="results",
                    help="结果目录名（相对实验目录；默认 results）")
    args = ap.parse_args(argv)

    results = EXP_DIR / args.results
    figures = results / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    import json

    metrics = json.loads((results / "metrics.json").read_text(encoding="utf-8"))
    solution = json.loads((results / "solution.json").read_text(encoding="utf-8"))

    if args.formats == "tables":
        from run import _compile_tables
        from tables import render as render_tables

        tables_dir = results / "tables"
        tex = render_tables(tables_dir / "exp01_tables.tex", results_dir=results)
        print(f"[render] 结果表：{tex}")
        _compile_tables(tables_dir, verbose=True)
        return 0

    fmts = tuple(f.strip().lower().lstrip(".")
                 for f in args.formats.split(",") if f.strip())
    from config import load_config
    from plot import make_plots

    raw_cfg = load_config(EXP_DIR / "config.yaml")
    dpi = args.dpi or int((raw_cfg.get("plot", {}) or {}).get("dpi", 200))
    make_plots(metrics, solution, figures, formats=fmts, dpi=dpi,
               names=args.figures, groups=None)
    print(f"[render] 完成 formats={fmts} figures={args.figures or 'ALL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
