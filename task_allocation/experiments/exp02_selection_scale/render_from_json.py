# -*- coding: utf-8 -*-
"""render_from_json.py — 从已落盘的 metrics.json 补出 exp02 图表。

exp02 是单周期变体扫描，数值产物只有 ``results/metrics.json``
（无 solution.json）。求解全部完成后才调 make_plots；若绘图阶段被
工具超时打断（600s），用本脚本按格式/图 id 补出，不必重跑求解。

用法::

    python render_from_json.py png                        # 全部静态图
    python render_from_json.py png --figure r01_tradeoff  # 只出指定图 id
    python render_from_json.py pdf --results results      # 指定结果目录 + 矢量
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 json 补出 exp02 图表")
    ap.add_argument("formats", nargs="?", default="png",
                    help="逗号分隔：png / pdf")
    ap.add_argument("--figure", action="append", dest="figures", default=None,
                    help="只出指定图 id（可重复）")
    ap.add_argument("--group", action="append", dest="groups", default=None,
                    help="只出指定层（setup / report，可重复）")
    ap.add_argument("--dpi", type=int, default=None)
    ap.add_argument("--results", default="results",
                    help="结果目录名（相对实验目录；默认 results）")
    args = ap.parse_args(argv)

    results = EXP_DIR / args.results
    figures = results / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    metrics = json.loads((results / "metrics.json").read_text(encoding="utf-8"))

    fmts = tuple(
        f.strip().lower().lstrip(".")
        for f in args.formats.split(",") if f.strip()
    )

    try:
        import yaml

        raw_cfg = yaml.safe_load(
            (EXP_DIR / "config.yaml").read_text(encoding="utf-8")
        ) or {}
    except Exception:
        raw_cfg = {}
    dpi = args.dpi or int((raw_cfg.get("plot", {}) or {}).get("dpi", 200))

    from plot import make_plots

    make_plots(metrics, {}, figures, formats=fmts, dpi=dpi,
               names=args.figures, groups=args.groups)
    print(f"[render] 完成 formats={fmts} "
          f"figures={args.figures or args.groups or 'ALL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
