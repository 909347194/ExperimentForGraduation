# -*- coding: utf-8 -*-
"""第二步：补齐 进化 × 精修 的 2×2 消融。

四格（固定场景、几何实例、求解器种子、评价口径，仅两个开关不同）：

                 refine=0            refine=1
  evo=0      no_evo_no_refine   no_evo_refine   ← 归因实验缺的格子
  evo=1      evo_no_refine      evo_refine(full / 生产)

分别报告：成本(path_cost / delivered_fitness)、迟到(late)、覆盖(coverage)、
硬约束违约(n_hard / final_n_hard)、运行时间(elapsed_s)。

交互分析（配对，同一 (场景,实例,seed) 四格齐全）：
  精修主效应@无进化 = f(0,0) - f(0,1)   → 精修是否独立有效
  精修主效应@有进化 = f(1,0) - f(1,1)
  进化主效应@无精修 = f(0,0) - f(1,0)   → DE 在无后处理时是否有价值
  进化主效应@有精修 = f(0,1) - f(1,1)   → DE 在有后处理时是否有价值
  交互项 = 精修效应@evo0 - 精修效应@evo1 = 进化效应@r0 - 进化效应@r1
  （≠0 ⇒ 两者非可加，DE 的价值依赖后处理条件）

输出：step2_raw.csv / step2_cells.csv / step2_interaction.csv /
      step2_2x2.png / step2_report.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common as C  # noqa: E402

# (label, evo(0/1), refine(0/1))
CELLS = [
    ("no_evo_no_refine", 0, 0),
    ("no_evo_refine", 0, 1),
    ("evo_no_refine", 1, 0),
    ("evo_refine", 1, 1),
]
METRICS = ["delivered_fitness", "path_cost", "late", "coverage",
           "n_hard", "final_n_hard", "n_executed", "elapsed_s"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="40:2,60:1")
    ap.add_argument("--deadlines", default="0,150")
    ap.add_argument("--pop", type=int, default=10)
    ap.add_argument("--gens", type=int, default=25)
    ap.add_argument("--seeds", default="11,22,33")
    ap.add_argument("--n-instances", type=int, default=1)
    ap.add_argument("--inst-base", type=int, default=5000)
    ap.add_argument("--n-uavs", type=int, default=3)
    ap.add_argument("--area", type=float, default=1000.0)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    out = HERE / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    scenarios = C.parse_scenarios(args.scenarios)
    deadlines = C.parse_csv_ints(args.deadlines)
    seeds = C.parse_csv_ints(args.seeds)
    print(f"[step2] scenarios={scenarios} deadlines={deadlines} "
          f"pop={args.pop} gens={args.gens} seeds={seeds}", flush=True)

    raw = []
    for (n_targets, n_cycles) in scenarios:
        for dl in deadlines:
            params = dict(pop_size=args.pop, max_generations=args.gens,
                          n_cycles=n_cycles, inst_base=args.inst_base,
                          n_uavs=args.n_uavs, area=args.area,
                          deadline_s=float(dl))
            for inst in range(args.n_instances):
                tasks, uavs, nests = C.make_instance(
                    args.inst_base + inst, args.n_uavs, n_targets,
                    args.area, deadline_s=float(dl))
                for sd in seeds:
                    for label, evo, refine in CELLS:
                        row = C.run_rolling(
                            tasks, uavs, nests, params=params,
                            arm_cfg=dict(
                                _label=label,
                                refine=bool(refine),
                                gens_override=(None if evo else 0),
                                seed=sd, instance=inst),
                        )
                        row.pop("cost_history", None)
                        row.update(scenario=f"t{n_targets}_c{n_cycles}",
                                   evo=evo, refine=refine)
                        raw.append(row)
                    print(f"[done] t{n_targets}_c{n_cycles} d{dl} i{inst} "
                          f"s{sd} 4/4 cells", flush=True)

    C.write_csv(out / "step2_raw.csv", raw)

    # ---- 每格聚合 ----
    key = ["scenario", "deadline_s", "evo", "refine"]
    cells = C.agg_by(raw, key, METRICS)
    C.write_csv(out / "step2_cells.csv", cells)

    # ---- 配对交互 ----
    idx = {(r["scenario"], r["deadline_s"], r["instance"], r["seed"],
            r["evo"], r["refine"]): r for r in raw}
    group_keys = sorted({(r["scenario"], r["deadline_s"], r["instance"],
                          r["seed"]) for r in raw})
    inter_rows = []
    for gk in group_keys:
        def f(e, r, m):
            d = idx.get((*gk, e, r))
            return float(d[m]) if d else float("nan")
        d = dict(scenario=gk[0], deadline_s=gk[1],
                 instance=gk[2], seed=gk[3])
        for m in METRICS:
            d[f"f00_{m}"] = f(0, 0, m)
            d[f"f01_{m}"] = f(0, 1, m)
            d[f"f10_{m}"] = f(1, 0, m)
            d[f"f11_{m}"] = f(1, 1, m)
            d[f"refine_eff_evo0_{m}"] = f(0, 0, m) - f(0, 1, m)
            d[f"refine_eff_evo1_{m}"] = f(1, 0, m) - f(1, 1, m)
            d[f"evo_eff_r0_{m}"] = f(0, 0, m) - f(1, 0, m)
            d[f"evo_eff_r1_{m}"] = f(0, 1, m) - f(1, 1, m)
            d[f"interaction_{m}"] = ((f(0, 0, m) - f(0, 1, m)) -
                                     (f(1, 0, m) - f(1, 1, m)))
        inter_rows.append(d)
    C.write_csv(out / "step2_interaction.csv", inter_rows)

    _plot(out, cells)
    _report(out, cells, inter_rows)
    print(f"[step2] 写出 {out}", flush=True)


def _plot(out, cells):
    if not cells:
        return
    combos = sorted({(c["scenario"], c["deadline_s"]) for c in cells})
    m = "delivered_fitness"
    fig, axes = plt.subplots(1, len(combos), figsize=(4.6 * len(combos), 4.0),
                             squeeze=False)
    for ax, (sc, dl) in zip(axes[0], combos):
        sub = {(c["evo"], c["refine"]): c[m] for c in cells
               if c["scenario"] == sc and c["deadline_s"] == dl}
        x = np.arange(2)
        w = 0.34
        b0 = [sub.get((0, 0), float("nan")), sub.get((1, 0), float("nan"))]
        b1 = [sub.get((0, 1), float("nan")), sub.get((1, 1), float("nan"))]
        ax.bar(x - w / 2, b0, w, label="精修关", color="#95a5a6")
        ax.bar(x + w / 2, b1, w, label="精修开", color="#2980b9")
        ax.set_xticks(x)
        ax.set_xticklabels(["无进化", "有进化"])
        ax.set_title(f"{sc} d{dl}")
        ax.set_ylabel("交付适应度（越低越好）")
        ax.legend(fontsize=8)
    fig.suptitle("第二步：进化 × 精修 2×2（交付适应度）")
    fig.tight_layout()
    fig.savefig(out / "step2_2x2.png", dpi=150)
    plt.close(fig)


def _report(out, cells, inter):
    L = ["# 第二步：进化 × 精修 2×2 消融", "",
         "四格固定同一场景、几何实例、求解器种子与评价口径；"
         "仅 `evo`(gens=0/正常) 与 `refine`(三精修总闸) 两个开关不同。", "",
         "## 各格五指标（均值，n = 几何实例 × 求解器种子）", "",
         "| 场景 | d | evo | refine | 交付适应度 | 航程 | 迟到 | 覆盖 | 硬违约 | 交付硬违约 | 耗时(s) |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        L.append(f"| {c['scenario']} | {c['deadline_s']} | {c['evo']} | "
                 f"{c['refine']} | {c['delivered_fitness']:.2f} | "
                 f"{c['path_cost']:.2f} | {c['late']:.2f} | "
                 f"{c['coverage']:.3f} | {c['n_hard']:.2f} | "
                 f"{c['final_n_hard']:.2f} | {c['elapsed_s']:.3f} |")
    L.append("")

    if inter:
        L += ["## 交互分析（按 (场景, 实例, seed) 配对，取均值）", "",
              "口径：效应 = 关 − 开（正数 = 该模块降低指标 = 有改进）。", "",
              "| 场景 | d | 精修效应@无进化 | 精修效应@有进化 | 进化效应@无精修 |"
              " 进化效应@有精修 | 交互项 |",
              "|---|---|---|---|---|---|---|"]
        for sc, dl in sorted({(r["scenario"], r["deadline_s"]) for r in inter}):
            g = [r for r in inter if r["scenario"] == sc
                 and r["deadline_s"] == dl]
            m = "delivered_fitness"
            def av(k):
                return C.mean_std([r[k] for r in g])[0]
            L.append(f"| {sc} | {dl} | "
                     f"{av(f'refine_eff_evo0_{m}'):.2f} | "
                     f"{av(f'refine_eff_evo1_{m}'):.2f} | "
                     f"{av(f'evo_eff_r0_{m}'):.2f} | "
                     f"{av(f'evo_eff_r1_{m}'):.2f} | "
                     f"{av(f'interaction_{m}'):.2f} |")
        L += ["", "### 判定", ""]
        m = "delivered_fitness"
        r0 = C.mean_std([r[f"refine_eff_evo0_{m}"] for r in inter])[0]
        e0 = C.mean_std([r[f"evo_eff_r0_{m}"] for r in inter])[0]
        e1 = C.mean_std([r[f"evo_eff_r1_{m}"] for r in inter])[0]
        it = C.mean_std([r[f"interaction_{m}"] for r in inter])[0]
        L.append(f"- **精修是否独立有效**：无进化时精修效应 = **{r0:.2f}** "
                 f"（>0 ⇒ 精修独立有效，不依赖 DE）。")
        L.append(f"- **DE 是否只在某种后处理条件下有价值**：进化效应 "
                 f"@无精修 = **{e0:.2f}** vs @有精修 = **{e1:.2f}**；"
                 f"交互项 = **{it:.2f}**。")
        if abs(e0) > 1e-6 and abs(e1) < 1e-6:
            L.append("  → DE 的价值**只在精修关闭时**显现（精修把 DE 的边际吃掉了）。")
        elif abs(e1) > abs(e0) + 1e-6:
            L.append("  → DE 的价值**在精修开启时更大**（两者互补）。")
        elif abs(it) < 1e-6:
            L.append("  → 交互项近零，进化与精修近似可加。")
        L.append("")
    (out / "step2_report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
