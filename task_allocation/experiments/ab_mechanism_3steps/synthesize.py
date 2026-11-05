# -*- coding: utf-8 -*-
"""汇总三步实验 → SYNTHESIS.md + 总览图（实验自持、可复现）。

读取（存在则纳入，缺失则标注未跑）：
  results/step1_summary.csv, step1_pergen.csv, step1_elite.csv
  results/step2_cells.csv, step2_interaction.csv            (40:2 双截止)
  results_s2_60/step2_cells.csv, step2_interaction.csv     (60:1 d0)
  results/step3_offline.csv, step3_e2e.csv, step3_matrix.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import common as C  # noqa: E402

R1 = HERE / "results"
R2 = HERE / "results_s2_60"


def load(p):
    p = Path(p)
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fnum(r, k, default=float("nan")):
    try:
        return float(r[k])
    except (KeyError, TypeError, ValueError):
        return default


def avg(rows, k):
    return C.mean_std([fnum(r, k) for r in rows])[0]


def main():
    s1 = load(R1 / "step1_summary.csv")
    s1g = load(R1 / "step1_pergen.csv")
    s1e = load(R1 / "step1_elite.csv")
    s2a = load(R1 / "step2_cells.csv")
    s2ai = load(R1 / "step2_interaction.csv")
    s2b = load(R2 / "step2_cells.csv")
    s2bi = load(R2 / "step2_interaction.csv")
    s3a = load(R1 / "step3_offline.csv")
    s3b = load(R1 / "step3_e2e.csv")
    # 兼容旧版无 benchmark 列的离线 CSV（一律视作 prod 基准）
    for r in s3a:
        r.setdefault("benchmark", "prod")

    L = ["# 优化器机制三步诊断（ab_mechanism_3steps）", "",
         "承接 `ab_optimization_attribution` 的「进化占比体制依赖」结论，"
         "回答三个机制问题：DE 是否真正控制最终解、进化×精修是否可加、"
         "定序/路径 polish/归属搜索各解决什么问题。", "",
         "## 0. 速读", ""]

    # ---- 第一步 ----
    if s1:
        col = avg(s1, "collision")
        comp = avg(s1, "geno_to_pheno_compress")
        newg = avg(s1, "new_pheno_per_gen")
        fitd = avg(s1, "fitness_distinct")
        gain = avg(s1, "de_elite_gain")
        frozen = avg(s1, "de_elite_frozen_frac")
        imp = avg(s1, "de_elite_improve_gens")
        L += [f"1. **DE 并不控制最终解，但病因不是表示坍缩**：表型碰撞率 "
              f"**{col:.3f}**、基因型→表型压缩 **{comp:.3f}**，每代 "
              f"**{newg:.1f}** 个全新方案、全 run 适应度 **{fitd:.0f}** 个不同值"
              f"——编码/反映射/修复接口把基因差异完整传导到了最终解。"
              f"但 DE 精英平均总增益仅 **{gain:.2f}**、冻结代占比 **{frozen:.2f}**、"
              f"改进代数 **{imp:.1f}**：子代适应度在动，却几乎全部劣于初始精英，"
              f"贪婪选择从不采纳。", ""]
    # ---- 第二步 ----
    if s2ai:
        r0 = avg(s2ai, "refine_eff_evo0_delivered_fitness")
        e0 = avg(s2ai, "evo_eff_r0_delivered_fitness")
        e1 = avg(s2ai, "evo_eff_r1_delivered_fitness")
        it = avg(s2ai, "interaction_delivered_fitness")
        L += [f"2. **精修独立有效、进化与精修强交互**：40:2 体制下，无进化时精修"
              f"独立降低交付适应度 **{r0:.1f}**；进化效应在精修关闭时 **{e0:.1f}**、"
              f"开启后萎缩到 **{e1:.1f}**，交互项 **{it:.1f}**——DE 的价值被精修"
              f"大幅替代，两者非可加。", ""]
    # ---- 第三步 ----
    if s3a:
        # 离线 A：双基准分开归因
        #   prod     = 生产解码链已消费定序 → 测「再叠加定序」的边际
        #   reversed = 人为逆序劣化 → 测定序算子的能力上界
        def dd(bench, dl, name, metric):
            base = {(r["scenario"], r["instance"], r["seed"]): r
                    for r in s3a if r["variant"] == "base"
                    and r["benchmark"] == bench and r["deadline_s"] == dl}
            vals = []
            for r in s3a:
                if (r["variant"] != name or r["benchmark"] != bench
                        or r["deadline_s"] != dl):
                    continue
                b = base.get((r["scenario"], r["instance"], r["seed"]))
                if b is not None:
                    vals.append(fnum(b, metric) - fnum(r, metric))
            return C.mean_std(vals)[0]
        benches = sorted({r["benchmark"] for r in s3a})
        lines = []
        for bench in benches:
            for dl in sorted({r["deadline_s"] for r in s3a}):
                lines.append(
                    f"{bench}/d{dl}：定序 Δ航程={dd(bench,dl,'order','path'):.1f}/"
                    f"Δ迟到={dd(bench,dl,'order','late'):.1f}，"
                    f"路径 polish Δ航程={dd(bench,dl,'polish','path'):.1f}/"
                    f"Δ迟到={dd(bench,dl,'polish','late'):.1f}")
        L += ["3. **模块各解决一个维度（双基准分开读）**：" + "；".join(lines) +
              "。`prod` 基准下定序边际≈0 是因生产解码链已先行消费定序"
              "（`build_solution → _best_order`），`reversed` 逆序基准才暴露定序算子"
              "在时间窗维度上的作用：**d150 下定序显著降迟到却同时增加航程**"
              "（`_best_order` 判据 `(迟到数, 迟到量, 航程)` 字典序优先时间窗，"
              "即「以航程换迟到」）；路径 polish 相反，两基准两档截止下都稳定压航程、"
              "并因几何变短顺带降迟到，不改变执行集/参与机/覆盖。"
              "两者目标维度不同、有截止压力时甚至冲突，`order+polish` 叠加后同时"
              "取得最低迟到与压低的航程，证明互补而非冗余；归属搜索只在单周期高负载"
              "的组合分配难题上显出价值。（重点在确认各模块解决什么问题，不以"
              "「每个模块都必须取得收益」为判据。）", ""]

    L += ["", "## 0.1 复现性核查：进化效应高度依赖初始种群（诚实交代）", "",
          "上一轮 `ab_optimization_attribution` 在 60:1/d0（pop8/gen25、3 seed）"
          "报告进化占比 68.7%（C−A = 460.29）。本轮用同一套 `common.run_rolling`"
          " 做了交叉核查（`verify_pop_sensitivity.py`，结果 `step0_sensitivity.csv`）：", ""]
    sens = load(R1 / "step0_sensitivity.csv")
    if sens:
        L += ["| 核查配置 | pop | seeds | 无进化 | 有进化 | 进化效应 |",
              "|---|---|---|---|---|---|"]
        label = {
            "A_replay_prev": "精确复现上一轮参数",
            "B_this_2x2": "本轮 2×2 配置",
            "C_single_s11_p8": "单 seed=11",
            "C_single_s11_p10": "单 seed=11",
            "C_single_s11_p12": "单 seed=11",
        }
        for r in sens:
            L.append(f"| {label.get(r['case'], r['case'])} | {r['pop']} | "
                     f"{r['seeds']} | {r['no_evo']} | {r['evo']} | "
                     f"**{r['evo_effect']}** |")
        L.append("")
    L += ["即：**上一轮的 460 未能稳定复现**；pop 从 8 改到 10、或只取单 seed，"
          "进化效应就在 0~73 之间。这并非测量噪声，而是第一步机制的直接后果——"
          "DE 精英从第 0 代冻结，交付解几乎完全由**初始种群里最好的那个个体**决定；"
          "初始种群一变（pop/seed），基线就变，「进化有没有贡献」随之跳变。"
          "因此上一轮「60:1 大批量场景进化占 68.7%」应降级读作"
          "「特定初始种群下 DE 偶尔找到优于初始精英的子代」，**不是稳定的体制规律**。"
          "本轮 2×2 在两种后处理条件下均测得 0，配合精修独立有效，"
          "才是更稳健的结论。", ""]

    L += ["## 1. 对上一轮 §4.8「空间坍缩」表述的**更正**（重要）", "",
          "上一轮归因报告把「进化占比 0%」解释为「修复链把大量不同基因映射到"
          "同一/相近可行解，搜索景观被压平」。本轮逐 trial 诊断**证伪了这一表述**："
          "表型碰撞率为 0，不同基因确实产出不同最终方案与不同适应度。"
          "真正的机制是：", "",
          "1. 修复链**改写**了 100% 的任务归属与访问顺序（解码前后归属/顺序改动率均 1.0），"
          "但改写后表型仍彼此不同（碰撞率 0）——是「改写」不是「坍缩成同一点」；",
          "2. 覆盖优先装载 + 终点巢最小费用流 + 可达即执行，使**执行任务集恒为全集**、"
          "归属常被并入单机航次（第三步 `n_active_uav` 常为 1），归属维度的自由度被消掉；",
          "3. 初始种群在修复链加持下已含近优解，DE 变异/交叉产出的子代在此表示下"
          "**系统性劣于初始精英**，贪婪选择（仅当 `child.fitness < population[i].fitness` "
          "才替换）从一开始就无改进可采纳 ⇒ 精英从第 0 代冻结。", "",
          "因此准确表述应为「**精英更新停滞 / 子代相对初始精英无优势**」，"
          "而非「表型坍缩」。改进方向相应从「改编码/反映射」转为"
          "「**选择压力、初始种群质量、以及让 DE 能产出优于修复链基线解的变异算子**」。", ""]

    # ---- 细表 ----
    if s2a or s2b:
        L += ["## 2. 进化 × 精修 2×2（五指标）", "",
              "| 来源 | 场景 | d | evo | refine | 交付适应度 | 航程 | 迟到 | 覆盖 | 硬违约 | 耗时(s) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for src, rows in (("40:2", s2a), ("60:1", s2b)):
            for c in rows:
                L.append(f"| {src} | {c['scenario']} | {c['deadline_s']} | "
                         f"{c['evo']} | {c['refine']} | "
                         f"{fnum(c,'delivered_fitness'):.1f} | "
                         f"{fnum(c,'path_cost'):.1f} | {fnum(c,'late'):.1f} | "
                         f"{fnum(c,'coverage'):.3f} | {fnum(c,'n_hard'):.1f} | "
                         f"{fnum(c,'elapsed_s'):.2f} |")
        L.append("")
        inter_all = s2ai + s2bi
        if inter_all:
            L += ["### 交互判定（按场景）", "",
                  "| 场景 | d | 精修效应@无进化 | 进化效应@无精修 | 进化效应@有精修 | 交互项 |",
                  "|---|---|---|---|---|---|"]
            for sc, dl in sorted({(r["scenario"], r["deadline_s"])
                                  for r in inter_all}):
                g = [r for r in inter_all if r["scenario"] == sc
                     and r["deadline_s"] == dl]
                m = "delivered_fitness"
                L.append(f"| {sc} | {dl} | "
                         f"{avg(g, f'refine_eff_evo0_{m}'):.1f} | "
                         f"{avg(g, f'evo_eff_r0_{m}'):.1f} | "
                         f"{avg(g, f'evo_eff_r1_{m}'):.1f} | "
                         f"{avg(g, f'interaction_{m}'):.1f} |")
            L.append("")

    if s3a:
        L += ["## 3. 模块拆分：定序 / 路径 polish / 归属搜索", "",
              "### (A) 固定归属的离线四变体（冻结成功=归属/执行集/参与机全同）", "",
              "`prod` = `build_solution` 输出（生产链已消费定序）；"
              "`reversed` = 人为逆序劣化基准（测定序能力上界）。两者分开统计。", "",
              "| 基准 | 场景 | d | 变体 | 航程 | 迟到 | 硬违约 | 覆盖 | 冻结成功 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for bench in sorted({r["benchmark"] for r in s3a}):
            for sc, dl in sorted({(r["scenario"], r["deadline_s"]) for r in s3a}):
                for n in ["base", "order", "polish", "order+polish"]:
                    g = [r for r in s3a if r["scenario"] == sc
                         and r["deadline_s"] == dl and r["variant"] == n
                         and r["benchmark"] == bench]
                    if not g:
                        continue
                    L.append(f"| {bench} | {sc} | {dl} | {n} | "
                             f"{avg(g,'path'):.1f} | {avg(g,'late'):.1f} | "
                             f"{avg(g,'n_hard'):.2f} | {avg(g,'coverage'):.3f} | "
                             f"{avg(g,'frozen_ok'):.2f} |")
        L += ["", "> **读法**：d150 的 `reversed` 行最说明问题——定序把迟到拉回，"
              "代价是航程上升（Δ迟到 为正、Δ航程 为负）；polish 把航程压下，"
              "迟到顺带下降（两个 Δ 均为正）。两者各自朝一个维度使劲，"
              "`order+polish` 叠加后兼顾两端，是互补而非冗余。", ""]
    if s3b:
        L += ["### (B) 端到端模块叠加", "",
              "| 场景 | d | 模块 | 交付适应度 | 航程 | 迟到 | 覆盖 | 硬违约 |",
              "|---|---|---|---|---|---|---|---|"]
        for c in C.agg_by(s3b, ["scenario", "deadline_s", "module"],
                          ["delivered_fitness", "path_cost", "late",
                           "coverage", "n_hard"]):
            L.append(f"| {c['scenario']} | {c['deadline_s']} | {c['module']} | "
                     f"{c['delivered_fitness']:.1f} | {c['path_cost']:.1f} | "
                     f"{c['late']:.1f} | {c['coverage']:.3f} | "
                     f"{c['n_hard']:.2f} |")
        L.append("")

    L += ["## 4. 诚实口径与上限", "",
          "- 预算 pop8~12 / gen20~30，低于生产 exp01（pop50×gen200）；"
          "更大预算下精英可能突破初始解，但第一步 gen25 已显示冻结代占比 ~0.9，"
          "且探针（上一轮 gen80/pop20、lamarckian=False）同样零增益，方向稳健。",
          "- 纯几何随机 SRP 实例（无 DEM/建筑），与真实城区口径不同；"
          "结论针对「修复链强、覆盖优先」这一结构性质，不外推到弱修复链求解器。",
          "- 每格 n = 1 几何实例 × 3 求解器种子，绝对量级有噪声，方向与占比稳健。",
          "- d150 为不可行截止（全量迟到），是压力/不可行体制而非「紧可行」；"
          "其结论不可外推为「任何时间压力都让进化失效」。",
          "- 第一步 `_evaluate_result` hook 只覆盖 DE 主循环 trial，"
          "不含初始化种群/GMR 灭绝（走 `_evaluate`）与交付后 polish；"
          "精英轨迹另用 `cost_history` 与 `population_callback` 独立核验。",
          "- 第三步 (A) 采双基准：`prod` 已在生产解码链中消费定序，故其「再叠加定序」"
          "边际≈0 是口径使然、不是算子失效；`reversed` 逆序基准才用于测定序算子的"
          "能力上界，二者不可混在一起平均。",
          "- 三步插桩全部为实验侧 monkeypatch，生产代码零改动、429 测试不受影响。", ""]

    (HERE / "SYNTHESIS.md").write_text("\n".join(L), encoding="utf-8")
    print(f"[synthesize] 写 SYNTHESIS.md（{len(L)} 行）")
    _plot(s1, s1g, s2a, s2b, s3a)


def _plot(s1, s1g, s2a, s2b, s3a):
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.6))
    # (0,0) 第一步：每代碰撞 vs 新方案
    ax = axes[0][0]
    if s1g:
        sc = s1g[0]["scenario"]
        sd = min(r["seed"] for r in s1g if r["scenario"] == sc)
        g = [r for r in s1g if r["scenario"] == sc and r["seed"] == sd]
        xs = [fnum(r, "gen") for r in g]
        ax.plot(xs, [fnum(r, "collision") for r in g], "o-", color="#c0392b",
                label="表型碰撞率")
        ax.plot(xs, [fnum(r, "geno_to_pheno") for r in g], "s--",
                color="#e67e22", label="基因→表型压缩")
        ax.set_ylim(-0.05, 1.05)
        ax.set_ylabel("比例")
    ax.set_title("① 碰撞率≈0：表示层健康", fontsize=10)
    ax.set_xlabel("进化代数")
    ax.legend(fontsize=7)

    # (0,1) 第一步：精英冻结
    ax = axes[0][1]
    if s1:
        labels = [f"{r['scenario']}/s{r['seed']}" for r in s1]
        vals = [fnum(r, "de_elite_frozen_frac") for r in s1]
        ax.barh(range(len(vals)), vals, color="#8e44ad", alpha=0.8)
        ax.set_yticks(range(len(vals)))
        ax.set_yticklabels(labels, fontsize=7)
        ax.set_xlim(0, 1.05)
        ax.set_xlabel("精英冻结代占比")
    ax.set_title("② 精英从第 0 代冻结：真正的病因", fontsize=10)

    # (1,0) 第二步 2x2
    ax = axes[1][0]
    cells = s2a  # 40:2 d0
    if cells:
        sub = {(c["evo"], c["refine"]): fnum(c, "delivered_fitness")
               for c in cells if c["deadline_s"] in ("0", "0.0")}
        if sub:
            x = np.arange(2)
            ax.bar(x - 0.17, [sub.get((0, 0), 0), sub.get((1, 0), 0)], 0.34,
                   label="精修关", color="#95a5a6")
            ax.bar(x + 0.17, [sub.get((0, 1), 0), sub.get((1, 1), 0)], 0.34,
                   label="精修开", color="#2980b9")
            ax.set_xticks(x)
            ax.set_xticklabels(["无进化", "有进化"])
            ax.set_ylabel("交付适应度")
            ax.legend(fontsize=7)
    ax.set_title("③ 2×2：精修独立有效、与进化强交互", fontsize=10)

    # (1,1) 第三步 模块效应（双基准分组，避免 prod/reversed 的 base 互相覆盖）
    ax = axes[1][1]
    if s3a:
        names = ["order", "polish", "order+polish"]
        benches = ["prod", "reversed"]
        short = {"order": "ord", "polish": "pol", "order+polish": "o+p"}
        base = {(r["benchmark"], r["scenario"], r["instance"], r["seed"],
                 r["deadline_s"]): r
                for r in s3a if r["variant"] == "base"}
        dp, dl_, labels = [], [], []
        for bench in benches:
            for n in names:
                vp, vl = [], []
                for r in s3a:
                    if r["variant"] != n or r["benchmark"] != bench:
                        continue
                    b = base.get((r["benchmark"], r["scenario"],
                                  r["instance"], r["seed"], r["deadline_s"]))
                    if b:
                        vp.append(fnum(b, "path") - fnum(r, "path"))
                        vl.append(fnum(b, "late") - fnum(r, "late"))
                dp.append(C.mean_std(vp)[0])
                dl_.append(C.mean_std(vl)[0])
                labels.append(f"{'P' if bench == 'prod' else 'R'}.{short[n]}")
        x = np.arange(len(labels))
        ax.bar(x - 0.17, dp, 0.34, label="Δ航程", color="#2980b9")
        ax.bar(x + 0.17, dl_, 0.34, label="Δ迟到", color="#c0392b")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7)
        ax.axhline(0, color="gray", lw=0.8)
        ax.legend(fontsize=7)
    ax.set_title("④ 双基准模块效应（P=已定序 / R=逆序）", fontsize=9)
    fig.suptitle("优化器机制三步诊断总览")
    fig.tight_layout()
    fig.savefig(HERE / "synthesis_overview.png", dpi=150)
    plt.close(fig)
    print("[synthesize] 写 synthesis_overview.png")


if __name__ == "__main__":
    main()
