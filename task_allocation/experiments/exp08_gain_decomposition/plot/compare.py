# -*- coding: utf-8 -*-
"""exp08_gain_decomposition 事后绘图（本实验自持；跑批阶段**不调用**）。

读 ``results/<profile>/summary.json``，对四档 2×2 画「逐 seed」折线图。
数值结论一律以 summary.json / summary.csv 为准，图只作直观核对；
不画「独立收益」类误导性标注。

用法（跑批结束后单独执行）::

    python plot/compare.py --profile formal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import EXP_DIR  # noqa: E402

MODES = ("g0b0", "g0b1", "g1b0", "g1b1")
LABELS = {
    "g0b0": "几何关 / 归属关",
    "g0b1": "几何关 / 归属开",
    "g1b0": "几何开 / 归属关",
    "g1b1": "几何开 / 归属开",
}
PANELS = (
    ("quality_by_mode", "final_fitness_sum", "f 之和（越小越好）"),
    ("quality_by_mode", "total_path_m", "总航程 (m)"),
    ("ledger_by_mode", "l3a_geometric_actions", "L3a 几何动作数"),
    ("ledger_by_mode", "l3b_relocate_actions", "L3b 归属动作数"),
)


def main(argv: list[str] | None = None) -> int:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    p = argparse.ArgumentParser(description="2×2 四档逐 seed 对照图")
    p.add_argument("--profile", default="formal")
    args = p.parse_args(argv)

    summary_path = EXP_DIR / "results" / args.profile / "summary.json"
    report = json.loads(summary_path.read_text(encoding="utf-8"))
    seeds = report["paired_seeds"]
    xs = list(range(len(seeds)))

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), dpi=150)
    for ax, (section, key, title) in zip(axes.ravel(), PANELS):
        block = report.get(section, {}).get(key, {})
        if not block:
            ax.set_visible(False)
            continue
        for mode in MODES:
            ax.plot(xs, block.get(mode, []), "o-", label=LABELS[mode])
        ax.set_xticks(xs)
        ax.set_xticklabels([str(s) for s in seeds])
        ax.set_xlabel("seed")
        ax.set_title(title)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(
        f"exp08_gain_decomposition / profile={args.profile}（2×2 同 seed 配对；"
        "主效应与交互项见 summary.json，禁写独立收益）"
    )
    fig.tight_layout()

    out_dir = EXP_DIR / "results" / args.profile / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "gain_2x2.png"
    fig.savefig(out)
    print(f"[plot] 已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
