# -*- coding: utf-8 -*-
"""exp07_polish_accept 事后绘图（本实验自持；跑批阶段**不调用**）。

读 ``results/<profile>/summary.json``，对若干主指标画「两档 × 逐 seed」
配对散点/连线图。数值结论一律以 summary.json / summary.csv 为准，图只作
直观核对；不画「独立收益」类误导性标注。

用法（跑批结束后单独执行）::

    python plot/compare.py --profile smoke
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import EXP_DIR  # noqa: E402

PLOT_KEYS = (
    "completion_rate_final",
    "on_time_rate_final",
    "total_path_m",
    "cost_polish_n_drift",
)


def main(argv: list[str] | None = None) -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    p = argparse.ArgumentParser(description="两档主指标配对图")
    p.add_argument("--profile", default="smoke")
    args = p.parse_args(argv)

    summary_path = EXP_DIR / "results" / args.profile / "summary.json"
    report = json.loads(summary_path.read_text(encoding="utf-8"))
    seeds = report["paired_seeds"]
    metrics = report["metrics"]

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), dpi=150)
    for ax, key in zip(axes.ravel(), PLOT_KEYS):
        rec = metrics.get(key)
        if not rec:
            ax.set_visible(False)
            continue
        xs = list(range(len(seeds)))
        ax.plot(xs, rec["per_seed_path"], "o-", label="按航程分量裁决 (path)")
        ax.plot(xs, rec["per_seed_f"], "s--", label="按完整目标 f 裁决 (f)")
        ax.set_xticks(xs)
        ax.set_xticklabels([str(s) for s in seeds])
        ax.set_xlabel("seed")
        ax.set_title(key)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(f"exp07_polish_accept / profile={args.profile}（同 seed 配对）")
    fig.tight_layout()

    out_dir = EXP_DIR / "results" / args.profile / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "pair_compare.png"
    fig.savefig(out)
    print(f"[plot] 已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
