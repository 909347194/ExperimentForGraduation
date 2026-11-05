# -*- coding: utf-8 -*-
"""第一步：证明 DE 是否真正控制最终解（基因型→表型映射诊断）。

对每个 DE trial 子代，记录解码前（基因 assignment：归属/顺序/执行集/参与机）
与解码后（修复链产出的最终 AllocationSolution：同样四字段 + 适应度），统计：

  1. 表型碰撞率
     - gene_distinct / pheno_distinct：不同基因型 / 不同表型的个数
     - collision = 1 - pheno_distinct / gene_trials（同代内不同子代塌缩到同一方案）
     - cross_geno_pheno：全 run 里 distinct 基因型 → distinct 表型的压缩比
  2. 每代「真正改变最终方案」的子代数
     - new_pheno_in_gen：本代出现、此前所有代未见过的表型个数
     - changed_vs_prev：本代 trial 表型 != 上一代末代表型（best slot）的个数
  3. 解码链把基因改了什么：chg_assign / chg_order / chg_execset / chg_active 占比
  4. 判定分支
     - 碰撞率高（不同基因→同一表型）  ⇒ 编码 / 反映射 / 修复接口把搜索空间压平
     - 表型多样性高但适应度几乎不变  ⇒ 目标函数平坦 / 搜索策略问题

口径：单周期（T:1）以隔离跨周期 gen 混淆；生产默认 lamarckian=True。
输出：step1_raw.csv / step1_pergen.csv / step1_summary.csv /
      step1_collision.png / step1_report.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common as C  # noqa: E402


def attach(args, ap):
    ap.add_argument("--scenarios", default="40:1,60:1")
    ap.add_argument("--deadlines", default="0")
    ap.add_argument("--pop", type=int, default=10)
    ap.add_argument("--gens", type=int, default=25)
    ap.add_argument("--seeds", default="11,22,33")
    ap.add_argument("--n-instances", type=int, default=1)
    ap.add_argument("--inst-base", type=int, default=5000)
    ap.add_argument("--n-uavs", type=int, default=3)
    ap.add_argument("--area", type=float, default=1000.0)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--out-dir", default="results")
    return ap


def main():
    ap = argparse.ArgumentParser()
    ap = attach(None, ap)
    args = ap.parse_args()

    out = HERE / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    scenarios = C.parse_scenarios(args.scenarios)
    deadlines = C.parse_csv_ints(args.deadlines)
    seeds = C.parse_csv_ints(args.seeds)
    params = C.make_params(args)

    print(f"[step1] scenarios={scenarios} deadlines={deadlines} "
          f"pop={args.pop} gens={args.gens} seeds={seeds}", flush=True)

    all_raw = []
    all_pergen = []
    all_summary = []
    all_elite = []

    for (n_targets, n_cycles) in scenarios:
        for dl in deadlines:
            params = dict(params, n_cycles=n_cycles, deadline_s=float(dl))
            for inst in range(args.n_instances):
                tasks, uavs, nests = C.make_instance(
                    args.inst_base + inst, args.n_uavs, n_targets,
                    args.area, deadline_s=float(dl))
                for sd in seeds:
                    rec = C.TrialRecorder()
                    rec.install()
                    try:
                        row = C.run_rolling(
                            tasks, uavs, nests, params=params,
                            arm_cfg=dict(_label="evo", refine=False,
                                         gens_override=None, seed=sd,
                                         instance=inst),
                            recorder=rec,
                        )
                    finally:
                        rec.uninstall()

                    tag = f"t{n_targets}_c{n_cycles}/d{dl}/i{inst}/s{sd}"
                    rows = rec.rows
                    if not rows:
                        print(f"[step1][warn] {tag} 无 trial 记录", flush=True)
                        continue

                    # ---- DE 全局精英轨迹（cost_history = 每代 best_fitness）----
                    ch = [float(x) for x in row.get("cost_history", [])]
                    if len(ch) >= 2:
                        best0 = ch[0]
                        n_improve = sum(1 for a, b in zip(ch, ch[1:]) if b < a - 1e-9)
                        last_improve = max(
                            (k for k, (a, b) in enumerate(zip(ch, ch[1:]), start=1)
                             if b < a - 1e-9), default=0)
                        frozen_frac = round(1.0 - (last_improve + 1) / len(ch), 4)
                        elite_gain = round(best0 - min(ch), 4)
                    else:
                        n_improve, last_improve, frozen_frac, elite_gain = (
                            0, 0, float("nan"), float("nan"))
                    elite_changed_gens = sum(e["elite_cv_changed"] for e in rec.elite)

                    # ---- 逐代统计 ----
                    gens = sorted({r["gen"] for r in rows})
                    seen_pheno: set[str] = set()
                    prev_rep = None
                    for g in gens:
                        gr = [r for r in rows if r["gen"] == g]
                        phen = [r["pheno"] for r in gr]
                        geno = [r["geno"] for r in gr]
                        fit = [r["fitness"] for r in gr]
                        new_in_gen = len(set(phen) - seen_pheno)
                        distinct_pheno = len(set(phen))
                        distinct_geno = len(set(geno))
                        changed_vs_prev = sum(
                            1 for p in phen if prev_rep is not None and p != prev_rep)
                        all_pergen.append(dict(
                            scenario=f"t{n_targets}_c{n_cycles}", deadline_s=dl,
                            instance=inst, seed=sd, gen=g,
                            n_trials=len(gr),
                            distinct_geno=distinct_geno,
                            distinct_pheno=distinct_pheno,
                            collision=round(1.0 - distinct_pheno / max(1, len(gr)), 4),
                            geno_to_pheno=round(1.0 - distinct_pheno /
                                                max(1, distinct_geno), 4),
                            new_pheno=new_in_gen,
                            changed_vs_prev=changed_vs_prev,
                            fitness_best=round(min(fit), 4) if fit else float("nan"),
                            fitness_distinct=len(set(round(f, 6) for f in fit)),
                            fitness_std=round(float(C.mean_std(fit)[1]), 4),
                            chg_assign=round(sum(r["chg_assign"] for r in gr) /
                                             max(1, len(gr)), 4),
                            chg_order=round(sum(r["chg_order"] for r in gr) /
                                            max(1, len(gr)), 4),
                            chg_execset=round(sum(r["chg_execset"] for r in gr) /
                                              max(1, len(gr)), 4),
                            chg_active=round(sum(r["chg_active"] for r in gr) /
                                             max(1, len(gr)), 4),
                        ))
                        seen_pheno.update(set(phen))
                        prev_rep = phen[-1]

                    # ---- run 级汇总 ----
                    all_phen = [r["pheno"] for r in rows]
                    all_geno = [r["geno"] for r in rows]
                    all_fit = [r["fitness"] for r in rows]
                    n_phen = len(set(all_phen))
                    n_geno = len(set(all_geno))
                    n_trials = len(rows)
                    # 每代平均新方案数（"DE 每代真正改变最终方案的子代"）
                    per_gen_new = [p["new_pheno"] for p in all_pergen
                                   if p["scenario"] == f"t{n_targets}_c{n_cycles}"
                                   and p["deadline_s"] == dl
                                   and p["instance"] == inst and p["seed"] == sd]
                    all_summary.append(dict(
                        scenario=f"t{n_targets}_c{n_cycles}", deadline_s=dl,
                        instance=inst, seed=sd,
                        n_trials=n_trials,
                        distinct_geno=n_geno,
                        distinct_pheno=n_phen,
                        collision=round(1.0 - n_phen / max(1, n_trials), 4),
                        geno_to_pheno_compress=round(1.0 - n_phen /
                                                     max(1, n_geno), 4),
                        new_pheno_total=sum(per_gen_new),
                        new_pheno_per_gen=round(sum(per_gen_new) /
                                                max(1, len(per_gen_new)), 4),
                        fitness_distinct=len(set(round(f, 6) for f in all_fit)),
                        fitness_std=round(float(C.mean_std(all_fit)[1]), 4),
                        de_elite_improve_gens=n_improve,
                        de_elite_last_improve_gen=last_improve,
                        de_elite_frozen_frac=frozen_frac,
                        de_elite_gain=elite_gain,
                        de_elite_cv_changed_gens=elite_changed_gens,
                        delivered_fitness=row["delivered_fitness"],
                        path_cost=row["path_cost"],
                        late=row["late"],
                        coverage=row["coverage"],
                        n_hard=row["n_hard"],
                        elapsed_s=row["elapsed_s"],
                        chg_assign=round(sum(r["chg_assign"] for r in rows) /
                                         n_trials, 4),
                        chg_order=round(sum(r["chg_order"] for r in rows) /
                                        n_trials, 4),
                        chg_execset=round(sum(r["chg_execset"] for r in rows) /
                                          n_trials, 4),
                        chg_active=round(sum(r["chg_active"] for r in rows) /
                                         n_trials, 4),
                    ))
                    for r in rows:
                        all_raw.append(dict(
                            scenario=f"t{n_targets}_c{n_cycles}",
                            deadline_s=dl, instance=inst, seed=sd, **r))
                    for e in rec.elite:
                        all_elite.append(dict(
                            scenario=f"t{n_targets}_c{n_cycles}",
                            deadline_s=dl, instance=inst, seed=sd, **e))
                    print(f"[done] {tag}: trials={n_trials} "
                          f"distinct_geno={n_geno} distinct_pheno={n_phen} "
                          f"new/gen={sum(per_gen_new)/max(1,len(per_gen_new)):.2f}",
                          flush=True)

    C.write_csv(out / "step1_raw.csv", all_raw)
    C.write_csv(out / "step1_pergen.csv", all_pergen)
    C.write_csv(out / "step1_summary.csv", all_summary)
    C.write_csv(out / "step1_elite.csv", all_elite)

    # ---- 图：每代碰撞 / 新方案数 ----
    _plot(out, all_pergen)

    # ---- 报告 ----
    _report(out, all_summary, all_pergen)
    print(f"[step1] 写出 {out}", flush=True)


def _plot(out, pergen):
    if not pergen:
        return
    scen = sorted({p["scenario"] for p in pergen})[:1]
    for sc in scen:
        g = [p for p in pergen if p["scenario"] == sc and p["seed"] == min(
            q["seed"] for q in pergen if q["scenario"] == sc)]
        if not g:
            continue
        xs = [p["gen"] for p in g]
        fig, ax1 = plt.subplots(figsize=(8, 4.2))
        ax1.plot(xs, [p["collision"] for p in g], "o-", color="#c0392b",
                 label="表型碰撞率 (1-不同表型/子代数)")
        ax1.plot(xs, [p["geno_to_pheno"] for p in g], "s--", color="#e67e22",
                 label="基因型→表型压缩 (1-不同表型/不同基因)")
        ax1.set_xlabel("进化代数")
        ax1.set_ylabel("比例")
        ax1.set_ylim(-0.05, 1.05)
        ax2 = ax1.twinx()
        ax2.bar(xs, [p["new_pheno"] for p in g], alpha=0.25, color="#2980b9",
                label="本代新方案数")
        ax2.set_ylabel("新方案数（个）")
        ax1.set_title(f"第一步：{sc} 每代基因型→表型映射（seed={g[0]['seed']}）")
        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax1.legend(h1 + h2, l1 + l2, fontsize=8, loc="center right")
        fig.tight_layout()
        fig.savefig(out / "step1_collision.png", dpi=150)
        plt.close(fig)


def _report(out, summary, pergen):
    lines = ["# 第一步：基因型 → 表型映射诊断（DE 是否真正控制最终解）", "",
             "## 口径", "",
             "- 对每个 DE trial 子代，`_evaluate_result` 同时给出解码前（基因 assignment）"
             "与解码后（修复链产出的 AllocationSolution）；指纹覆盖任务归属、任务顺序、"
             "执行任务集、实际参与无人机四字段，适应度单列。",
             "- 单周期场景隔离跨周期混淆；生产默认 lamarckian=True、精修关（只看 DE+解码链）。",
             "- `collision = 1 - 本代不同表型数/本代子代数`；"
             "`geno_to_pheno = 1 - 不同表型数/不同基因型数`；"
             "`new_pheno` = 本代首次出现的新表型数（= 真正改变最终方案的子代上界）。",
             "- `de_elite_*` 来自 `cost_history`（每代 best_fitness）："
             "改进代数、最后一次改进代、冻结代占比、精英总增益、精英基因型改变代数。"
             "用于区分「表示坍缩」与「精英/选择停滞」两种病因。", ""]
    if summary:
        lines += ["## 各场景汇总（按 seed 配对）", "",
                  "| 场景 | d | seed | 子代数 | 不同基因 | 不同表型 | 碰撞率 | 基因→表型压缩 |"
                  " 新方案/代 | 适应度不同值 | 归属改动 | 顺序改动 | 执行集改动 | 参与机改动 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s in summary:
            lines.append(
                f"| {s['scenario']} | {s['deadline_s']} | {s['seed']} | "
                f"{s['n_trials']} | {s['distinct_geno']} | {s['distinct_pheno']} | "
                f"{s['collision']:.2f} | {s['geno_to_pheno_compress']:.2f} | "
                f"{s['new_pheno_per_gen']:.2f} | {s['fitness_distinct']} | "
                f"{s['chg_assign']:.2f} | {s['chg_order']:.2f} | "
                f"{s['chg_execset']:.2f} | {s['chg_active']:.2f} |")
        lines += ["", "### DE 全局精英轨迹", "",
                  "| 场景 | d | seed | 改进代数 | 最后改进代 | 冻结代占比 | 精英总增益 |"
                  " 精英基因型改变代数 |",
                  "|---|---|---|---|---|---|---|---|"]
        for s in summary:
            lines.append(
                f"| {s['scenario']} | {s['deadline_s']} | {s['seed']} | "
                f"{s['de_elite_improve_gens']} | {s['de_elite_last_improve_gen']} | "
                f"{s['de_elite_frozen_frac']} | {s['de_elite_gain']} | "
                f"{s['de_elite_cv_changed_gens']} |")
        lines.append("")
        # 判定（三条分支）
        avg_col = C.mean_std([s["collision"] for s in summary])[0]
        avg_comp = C.mean_std([s["geno_to_pheno_compress"] for s in summary])[0]
        avg_new = C.mean_std([s["new_pheno_per_gen"] for s in summary])[0]
        avg_fitd = C.mean_std([s["fitness_distinct"] for s in summary])[0]
        avg_gain = C.mean_std([s["de_elite_gain"] for s in summary])[0]
        avg_frozen = C.mean_std([s["de_elite_frozen_frac"] for s in summary])[0]
        lines += ["## 判定（三条分支）", "",
                  f"- 平均表型碰撞率 **{avg_col:.3f}**，基因型→表型压缩 **{avg_comp:.3f}**，"
                  f"每代新方案数 **{avg_new:.2f}**，全 run 适应度不同值 **{avg_fitd:.1f}**。",
                  f"- DE 全局精英平均总增益 **{avg_gain:.2f}**，收敛后冻结代占比 "
                  f"**{avg_frozen:.2f}**。", ""]
        if avg_comp > 0.5 or avg_col > 0.7:
            lines.append("→ **分支一（表示坍缩）**：大量不同基因被反映射/修复链压成同一"
                         "最终方案，DE 变异撬不动表型 ⇒ 改编码、反映射或修复接口。")
        elif avg_fitd <= 3:
            lines.append("→ **分支二（目标平坦）**：表型多样但适应度取值极少，"
                         "目标函数对方案几乎不敏感 ⇒ 检查目标函数与搜索策略。")
        else:
            lines.append("→ **分支三（非表示问题，选择/精英停滞）**：表型碰撞率低、"
                         "适应度随表型真实变化，说明编码与反映射把基因差异完整传导到了"
                         "最终解；若此时「进化占比」仍为 0，病因不在表示层，而在"
                         "**精英更新/选择压力/适应度与交付口径的对齐**（子代适应度在动，"
                         "但相对初始精英没有可采纳的改进）。")
        lines.append("")
    (out / "step1_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
