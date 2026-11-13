"""完工时间（makespan）对照汇总：读逐 run metrics.json，不重跑求解。

用法（cwd = exp11 目录）：
    python analyze_makespan.py --profile makespan

输出：results/<profile>/makespan.md + makespan.csv，并打印同一张表。
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # 仓库根，供 import task_allocation
try:  # Windows 控制台默认 cp936，中文表头会静默丢输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

import run as exp11_run  # noqa: E402

ARMS = ("home", "nearest", "mcf")
LABEL = {"home": "强制返原巢", "nearest": "就近贪心", "mcf": "MCF（本文）"}
KEYS = (
    ("makespan_s", "完工时刻 s"),
    ("mean_completion_s", "平均交付时刻 s"),
    ("completion_rate_final", "完成率"),
    ("on_time_rate_final", "按时率"),
    ("n_cycles_with_delivery", "交付周期数"),
    ("total_path_m", "总航程 m"),
    ("return_leg_per_sortie_m", "返航段/航次 m"),
)


def _load(profile: str) -> dict[tuple[int, str], list[dict]]:
    root = HERE / "results" / profile
    cells: dict[tuple[int, str], list[dict]] = {}
    for mp in sorted(root.glob("T*/*/seed_*/metrics.json")):
        tier = int(mp.parts[-4].lstrip("T"))
        arm = mp.parts[-3]
        cells.setdefault((tier, arm), []).append(json.loads(mp.read_text(encoding="utf-8")))
    return cells


def _fmt(v, nd=1) -> str:
    if v is None:
        return "--"
    return f"{v:,.{nd}f}" if isinstance(v, float) else str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="makespan")
    args = ap.parse_args(argv)
    cells = _load(args.profile)
    if not cells:
        print(f"[makespan] 无产物：results/{args.profile}", flush=True)
        return 2
    tiers = sorted({t for t, _ in cells})
    lines: list[str] = []
    rows_csv: list[dict] = []
    for tier in tiers:
        rows = {a: sorted(cells.get((tier, a), []), key=lambda r: r["seed"]) for a in ARMS}
        n = min(len(v) for v in rows.values())
        seeds = [r["seed"] for r in rows["mcf"][:n]]
        lines.append(f"\n## T{tier}（{len(seeds)} seed，任务数 {rows['mcf'][0].get('n_arrived')}）\n")
        lines.append("| 臂 | 完工时刻 s | 平均交付 s | 完成率 | 按时率 | 交付周期数 | 总航程 km | 返航段/航次 m |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for a in ARMS:
            rr = rows[a]
            agg = {}
            for key, _ in KEYS:
                vals = [float(r[key]) for r in rr if r.get(key) is not None]
                agg[key] = (exp11_run._mean(vals), exp11_run._std(vals)) if vals else (None, None)
            lines.append(
                "| {} | {} ± {} | {:.0f} | {:.3f} | {:.3f} | {:.1f} | {:.1f} | {:.0f} |".format(
                    LABEL[a],
                    _fmt(agg["makespan_s"][0]), _fmt(agg["makespan_s"][1]),
                    agg["mean_completion_s"][0] or 0.0,
                    agg["completion_rate_final"][0] or 0.0,
                    agg["on_time_rate_final"][0] or 0.0,
                    agg["n_cycles_with_delivery"][0] or 0.0,
                    (agg["total_path_m"][0] or 0.0) / 1000.0,
                    agg["return_leg_per_sortie_m"][0] or 0.0,
                )
            )
            rows_csv.append({
                "tier": tier, "arm": a, "n_seeds": len(rr),
                **{k: (agg[k][0] if agg[k][0] is not None else "") for k, _ in KEYS},
            })
        # 配对差（home/nearest 相对 mcf）
        idx = {a: {r["seed"]: r for r in rows[a]} for a in ARMS}
        paired = [s for s in seeds if all(s in idx[a] for a in ARMS)]
        lines.append("")
        for key, lab in (("makespan_s", "完工时刻"), ("mean_completion_s", "平均交付时刻")):
            parts = []
            for base in ("home", "nearest"):
                d = [float(idx[base][s][key]) - float(idx["mcf"][s][key]) for s in paired]
                st = exp11_run._paired_stats(d)
                parts.append(f"{base}−mcf Δ{lab} = {st['mean']:+.1f} s（p={_fmt(st['p_two_sided'], 3)}）")
            lines.append("- " + "；".join(parts))
    out_md = HERE / "results" / args.profile / "makespan.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_csv = HERE / "results" / args.profile / "makespan.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_csv[0]))
        w.writeheader()
        w.writerows(rows_csv)
    print("\n".join(lines))
    print(f"\n[makespan] → {out_md}\n[makespan] → {out_csv}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
