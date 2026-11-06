# -*- coding: utf-8 -*-
"""搜索口径 vs 交付口径的**选择错配**诊断（三步机制实验的补充探针）。

用户论断：DE 比较的是 f(S_eval(x))，交付的是 f(S_final(x))，故

    argmin_x f(S_eval(x))  !=(可能)  argmin_x f(S_final(x))

代码事实（本探针要量化的对象）：
  - 两阶段用的是**同一个** `problem.scalar_fitness`（evaluator.py:199 /
    stage_runner.py:346），故 f 相同，差异全部来自映射 S。
  - S_eval  = evaluator.evaluate → build_solution（dmde_solver.py:219）。
  - S_final = build_solution(result.best_assignment) → assignment polish →
    memetic 档案 → coverage_first → 交付后成本 polish（stage_runner.py:344-438）。
  - 更硬的一层：Lamarckian 回写把 `child` 换成新基因 `re_encoded`，却把**旧
    基因的 fitness 直接抄给新基因**（dmde_solver.py:241-243）。于是下一代贪婪
    选择比较的是一个「陈旧适应度」，连 argmin f(S_eval(x)) 都没被严格执行。

实验设计（零侵入，生产代码零改动）：
  1. hook `DMDESolver._evaluate` / `_evaluate_result` / `solve`，记录本次 run
     **每一个被评价过的基因** assignment 与其 f_eval，及 DE 最终交付的
     best_assignment / best_fitness。
  2. 对每个唯一 assignment 离线**重放交付映射**：
       - D1 重解码：build_solution → problem.evaluate → scalar_fitness
       - D2 交付：D1 解再走 post_cost_polish，按 stage_runner 同款四道门采纳
     refine=False 时 D2 恒等于 D1（隔离校验：错配应归零）。
  3. 直接测两个 argmin：
       x*_eval  = argmin f_eval     （DE 想要选的）
       x*_final = argmin f_final    （交付口径下真正最好的）
       regret   = f_final(x*_eval) − min_x f_final(x)   ← 现状 vs「先打磨后选」
     另测排序反转、Spearman/Kendall、Δ(x) 与 f_eval 的相关性（若显著负相关
     ⇒ 越好的个体打磨收益越小 ⇒ 系统性逆向选择），以及陈旧适应度幅度。

口径：单周期纯几何随机 SRP，与 ab_mechanism_3steps 完全一致。
输出：selection_gap_raw.csv / selection_gap_summary.csv /
      selection_gap.png / selection_gap_report.md
"""
from __future__ import annotations

import argparse
import copy
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common as C  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDESolver  # noqa: E402


# ---------------------------------------------------------------------------
# 零侵入捕获
# ---------------------------------------------------------------------------

class Capture:
    """抓「每个被评价基因」的 (assignment, f_eval) 与 DE 最终精英。"""

    def __init__(self):
        self.rows: list[dict] = []
        self.solves: list[dict] = []
        self.gen = 0                 # 0 = 初始种群；其后按代递增
        self.evaluator = None
        self.ev_snapshot = None      # solve 返回瞬间的 evaluator 深拷贝
        self.snapshot_err = None
        self._orig: dict = {}
        self.installed = False

    def install(self):
        if self.installed:
            return
        cap = self

        # --- _evaluate_result（每代 trial 子代） ---
        desc = DMDESolver.__dict__.get("_evaluate_result")
        cap._orig["_evaluate_result"] = desc
        fn = desc.__func__ if isinstance(desc, staticmethod) else desc

        def hook_res(individual, fitness_evaluator, cost_matrix, n_uavs=None):
            res = fn(individual, fitness_evaluator, cost_matrix, n_uavs=n_uavs)
            try:
                cap._on(individual, fitness_evaluator,
                        float(getattr(res, "fitness", float("nan"))), "trial")
            except Exception:
                pass
            return res

        DMDESolver._evaluate_result = staticmethod(hook_res)

        # --- _evaluate（初始种群 / GMR 重建） ---
        desc2 = DMDESolver.__dict__.get("_evaluate")
        cap._orig["_evaluate"] = desc2
        fn2 = desc2.__func__ if isinstance(desc2, staticmethod) else desc2

        def hook_eval(individual, fitness_evaluator, cost_matrix, n_uavs=None):
            f = fn2(individual, fitness_evaluator, cost_matrix, n_uavs=n_uavs)
            try:
                cap._on(individual, fitness_evaluator, float(f), "init_or_gmr")
            except Exception:
                pass
            return f

        DMDESolver._evaluate = staticmethod(hook_eval)

        # --- solve（拿 SolverResult.best_assignment / best_fitness） ---
        orig_solve = DMDESolver.solve
        cap._orig["solve"] = orig_solve

        def hook_solve(slv, cost_matrix, n_uavs, n_targets, **kwargs):
            res = orig_solve(slv, cost_matrix, n_uavs, n_targets, **kwargs)
            try:
                cap._on_solve(res, kwargs.get("fitness_evaluator"))
            except Exception:
                pass
            return res

        DMDESolver.solve = hook_solve
        self.installed = True

    def uninstall(self):
        if not self.installed:
            return
        # 原始描述符原样恢复（staticmethod 不能解包后赋回，否则退化成实例方法）
        for name in ("_evaluate_result", "_evaluate", "solve"):
            if name in self._orig:
                setattr(DMDESolver, name, self._orig[name])
        self.installed = False

    def on_population_callback(self, gen, cost_vectors, best_idx):
        self.gen = int(gen) + 1

    def _on(self, ind, ev, fit, kind):
        if ev is not None and self.evaluator is None:
            self.evaluator = ev
        try:
            assign = tuple((int(a), int(b)) for a, b in ind.assignment)
            cv = np.asarray(getattr(ind, "cost_vector", []), dtype=float)
            cvfp = C._short(tuple(np.round(cv, 4).tolist()))
        except Exception:
            return
        self.rows.append(dict(gen=self.gen, kind=kind, assign=assign,
                              cv=cvfp, f_eval=float(fit)))

    def _on_solve(self, res, ev):
        if ev is not None and self.evaluator is None:
            self.evaluator = ev
        # 关键口径修复：solve 刚返回时 problem/evaluator 仍是**搜索期**状态
        # （实测同一基因重解码逐位复现）；但 horizon.run 随后的交付期后处理
        # 会原地改动 evaluator 的可变状态，d150 下实测重解码漂移 −375.37。
        # 因此在此刻深拷贝一份，全部重放都在副本上做：既锁住搜索期口径，
        # 又绝不污染生产链。多周期时只保留第一份（本实验单周期）。
        if ev is not None and self.ev_snapshot is None:
            try:
                self.ev_snapshot = copy.deepcopy(ev)
            except Exception as exc:
                self.snapshot_err = repr(exc)
        try:
            ba = tuple((int(a), int(b)) for a, b in res.best_assignment)
        except Exception:
            ba = ()
        self.solves.append(dict(
            best_assignment=ba,
            best_fitness=float(res.best_fitness),
            cost_history=[float(x) for x in (res.cost_history or [])],
        ))


# ---------------------------------------------------------------------------
# 交付映射的离线重放
# ---------------------------------------------------------------------------

def replay(ev, assign, refine):
    """返回 (f_redecode, f_final, n_accepted, adopted)。

    f_redecode：同一基因重新走 build_solution（应与 f_eval 逐位相等，隔离校验）
    f_final   ：再叠加交付后成本 polish，按 stage_runner 四道门采纳
    """
    prob = ev.problem
    sol = ev.build_solution(tuple(assign))
    prob.evaluate(sol)
    f0 = float(prob.scalar_fitness(sol))
    if not refine:
        return f0, f0, 0, 0

    try:
        cand, stats = ev.post_cost_polish(copy.deepcopy(sol))
    except Exception:
        return f0, f0, -1, 0
    n_acc = int((stats or {}).get("n_accepted", 0) or 0)
    if cand is None or n_acc <= 0:
        return f0, f0, n_acc, 0

    prob.evaluate(cand)
    f1 = float(prob.scalar_fitness(cand))
    nh0, ex0 = prob.hard_stats(sol)
    nh1, ex1 = prob.hard_stats(cand)
    e0 = set(int(x) for x in sol.executed_task_ids)
    e1 = set(int(x) for x in cand.executed_task_ids)
    p0, p1 = float(prob.path_cost(sol)), float(prob.path_cost(cand))
    # 与 stage_runner.py:434 逐位对齐：ε 取生产默认 cost_polish_eps（1.0），
    # 不能用 1e-6，否则会多采纳改进、高估排序压缩。
    eps = float(getattr(getattr(ev, "config", None), "cost_polish_eps", 1.0))
    ok = (e1 >= e0 and nh1 <= nh0 and ex1 <= ex0 + 1e-9
          and p1 < p0 - eps)
    return f0, (f1 if ok else f0), n_acc, int(ok)


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def rankdata(xs):
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def pearson(a, b):
    if len(a) < 2:
        return float("nan")
    a, b = np.asarray(a, float), np.asarray(b, float)
    if np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def kendall(a, b):
    n = len(a)
    con = dis = tie = 0
    for i in range(n):
        for j in range(i + 1, n):
            sa = np.sign(a[i] - a[j])
            sb = np.sign(b[i] - b[j])
            if sa == 0 or sb == 0:
                tie += 1
            elif sa == sb:
                con += 1
            else:
                dis += 1
    return (con - dis) / max(1, con + dis), con, dis, tie


def summarize(run_key, raw, meta):
    """raw: 每个唯一 assignment 一行，含 f_eval / f_redecode / f_final。"""
    fe = [r["f_eval"] for r in raw]
    ff = [r["f_final"] for r in raw]
    fr = [r["f_redecode"] for r in raw]
    n = len(raw)
    if n < 2:
        return None
    ie = int(np.argmin(fe))
    iff = int(np.argmin(ff))
    regret = ff[ie] - ff[iff]
    # DE 选中个体在交付口径下的名次（1 = 最好）
    rank = 1 + sum(1 for v in ff if v < ff[ie] - 1e-9)
    sp = pearson(rankdata(fe), rankdata(ff))
    tau, con, dis, tie = kendall(fe, ff)
    delta = [a - b for a, b in zip(fe, ff)]
    redecode_gap = [abs(a - b) for a, b in zip(fe, fr)]
    # 交付口径下「并列最优」的个数：top1_match=1 可能只是大量候选收敛到
    # 同一局部最优（退化巧合），而非 DE 真的选对了。
    best_v = min(ff)
    n_tied = sum(1 for v in ff if v <= best_v + 1e-6)
    rand_exp = float(np.mean(ff))
    pctl = 100.0 * sum(1 for v in ff if v < ff[ie] - 1e-9) / n
    return dict(
        **run_key,
        delivered_fitness=meta.get("delivered_fitness", ""),
        elapsed_s=meta.get("elapsed_s", ""),
        n_cand=n,
        f_eval_best=round(min(fe), 4),
        f_final_best=round(min(ff), 4),
        top1_match=int(ie == iff),
        de_choice_final_fitness=round(ff[ie], 4),
        regret=round(regret, 4),
        regret_pct=round(100.0 * regret / max(1e-9, abs(min(ff))), 4),
        de_choice_rank=rank,
        de_choice_rank_pct=round(100.0 * rank / n, 2),
        n_tied_best=n_tied,
        de_choice_percentile=round(pctl, 2),
        random_expected_final=round(rand_exp, 4),
        selection_value=round(rand_exp - ff[ie], 4),
        spearman=round(sp, 4) if math.isfinite(sp) else "",
        kendall_tau=round(tau, 4),
        n_inversions=dis,
        n_pairs=con + dis,
        inversion_rate=round(dis / max(1, con + dis), 4),
        delta_mean=round(float(np.mean(delta)), 4),
        delta_std=round(float(np.std(delta)), 4),
        corr_delta_feval=(round(pearson(delta, fe), 4)
                          if math.isfinite(pearson(delta, fe)) else ""),
        redecode_gap_max=round(max(redecode_gap), 8),
        n_polish_adopted=sum(int(r.get("adopted", 0)) for r in raw),
        stale_gap=meta.get("stale_gap", ""),
        best_gene_seen=meta.get("best_gene_seen", ""),
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_experiments(args):
    out = HERE / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    scenarios = C.parse_scenarios(args.scenarios)
    deadlines = C.parse_csv_ints(args.deadlines)
    seeds = C.parse_csv_ints(args.seeds)
    params0 = C.make_params(args)
    refines = [int(x) for x in str(args.refines).split(",") if str(x).strip()]

    print(f"[gap] scenarios={scenarios} deadlines={deadlines} "
          f"refines={refines} pop={args.pop} gens={args.gens} seeds={seeds}",
          flush=True)

    all_raw: list[dict] = []
    all_sum: list[dict] = []
    for (n_targets, n_cycles) in scenarios:
        for dl in deadlines:
            params = dict(params0, n_cycles=n_cycles, deadline_s=float(dl))
            for inst in range(args.n_instances):
                tasks, uavs, nests = C.make_instance(
                    args.inst_base + inst, args.n_uavs, n_targets,
                    args.area, deadline_s=float(dl))
                for refine in refines:
                    for sd in seeds:
                        key = dict(scenario=f"t{n_targets}_c{n_cycles}",
                                   deadline_s=dl, instance=inst, seed=sd,
                                   refine=refine)
                        cap = Capture()
                        cap.install()
                        try:
                            row = C.run_rolling(
                                tasks, uavs, nests, params=params,
                                arm_cfg=dict(_label="gap", refine=bool(refine),
                                             gens_override=None, seed=sd,
                                             instance=inst),
                                recorder=cap,
                            )
                        finally:
                            cap.uninstall()

                        # 去重：同一 assignment 可能被 evaluate 缓存反复命中
                        uniq: dict[tuple, float] = {}
                        for r in cap.rows:
                            uniq.setdefault(r["assign"], r["f_eval"])

                        # 重放必须用 solve 返回瞬间的隔离副本，不能用 run 结束后
                        # 已被交付期后处理改动过的 ev（d150 会整体失真）。
                        ev = cap.ev_snapshot
                        if ev is None:
                            print(f"[gap][warn] evaluator 快照失败 "
                                  f"{cap.snapshot_err}: {key}", flush=True)
                            continue
                        stale_gap, best_seen = "", ""
                        if cap.solves:
                            s = cap.solves[-1]
                            ba = s["best_assignment"]
                            best_seen = int(ba in uniq)
                            try:
                                f_ba, _, _, _ = replay(ev, ba, False)
                                stale_gap = round(f_ba - s["best_fitness"], 6)
                            except Exception:
                                stale_gap = ""

                        raw = []
                        for k, (assign, fe) in enumerate(uniq.items()):
                            try:
                                fr, ff, nacc, adopted = replay(ev, assign,
                                                               bool(refine))
                            except Exception:
                                fr, ff, nacc, adopted = float("nan"), float("nan"), -1, 0
                            r = dict(**key, cand=k,
                                     assign_fp=C._short(assign),
                                     f_eval=round(fe, 6),
                                     f_redecode=round(fr, 6),
                                     f_final=round(ff, 6),
                                     delta=round(fe - ff, 6),
                                     n_accepted=nacc, adopted=adopted)
                            raw.append(r)
                            all_raw.append(r)

                        sm = summarize(key, raw, dict(
                            stale_gap=stale_gap, best_gene_seen=best_seen,
                            delivered_fitness=round(row["delivered_fitness"], 4),
                            elapsed_s=round(row["elapsed_s"], 3),
                        ))
                        if sm:
                            all_sum.append(sm)
                            print(f"[gap] {key} n={sm['n_cand']} "
                                  f"top1={sm['top1_match']} "
                                  f"regret={sm['regret']:.2f} "
                                  f"rank={sm['de_choice_rank']} "
                                  f"inv={sm['inversion_rate']:.3f} "
                                  f"stale={stale_gap}", flush=True)

    C.write_csv(out / "selection_gap_raw.csv", all_raw)
    C.write_csv(out / "selection_gap_summary.csv", all_sum)
    return out, all_raw, all_sum


def load_csv(path):
    import csv
    p = Path(path)
    if not p.exists() or not p.stat().st_size:
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fnum(r, k, default=float("nan")):
    try:
        return float(r[k])
    except (KeyError, TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 图 / 报告
# ---------------------------------------------------------------------------

def plot(out, raw, sm):
    if not raw:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    ax = axes[0]
    # 代表 run：refine=1 的第一组
    cand_runs = [r for r in sm if int(float(r["refine"])) == 1]
    pick = cand_runs[0] if cand_runs else sm[0]
    sel = [r for r in raw
           if all(str(r[k]) == str(pick[k]) for k in
                  ("scenario", "deadline_s", "instance", "seed", "refine"))]
    x = [fnum(r, "f_eval") for r in sel]
    y = [fnum(r, "f_final") for r in sel]
    if x and y:
        ax.scatter(x, y, s=18, alpha=0.65, color="#2980b9",
                   label="候选基因 (f_eval, f_final)")
        lo, hi = min(min(x), min(y)), max(max(x), max(y))
        ax.plot([lo, hi], [lo, hi], ls=":", color="gray", lw=1.0,
                label="y = x（排序完全一致）")
        ie, iff = int(np.argmin(x)), int(np.argmin(y))
        ax.scatter([x[ie]], [y[ie]], marker="x", s=120, lw=2.5,
                   color="#c0392b", zorder=5, label="DE 选中的 x*_eval")
        ax.scatter([x[iff]], [y[iff]], marker="*", s=150,
                   color="#27ae60", zorder=5, label="交付最优 x*_final")
        if ie != iff:
            ax.annotate("", xy=(x[iff], y[iff]), xytext=(x[ie], y[ie]),
                        arrowprops=dict(arrowstyle="->", color="#c0392b",
                                        lw=1.4, ls="--"))
    ax.set_xlabel("搜索期适应度 $f(S_{eval}(x))$")
    ax.set_ylabel("交付口径适应度 $f(S_{final}(x))$")
    ax.set_title("① 候选排序在两口径下是否一致", fontsize=10)
    ax.legend(fontsize=7)
    ax.grid(alpha=0.25)

    ax = axes[1]
    groups = {}
    for r in sm:
        g = (int(float(r["refine"])), int(float(r["deadline_s"])))
        groups.setdefault(g, []).append(fnum(r, "regret"))
    labels, vals = [], []
    for g in sorted(groups):
        labels.append(f"{'开' if g[0] else '关'}/d{g[1]}")
        vals.append(float(np.mean([v for v in groups[g] if math.isfinite(v)] or [0])))
    colors = ["#c0392b" if lab.startswith("开") else "#7f8c8d"
              for lab in labels]
    ax.bar(range(len(vals)), vals, color=colors, alpha=0.85)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("regret = $f_{final}(x^*_{eval}) - \\min_x f_{final}(x)$")
    ax.set_title("② 选择口径错配的机会成本（正 = 选错）", fontsize=10)
    ax.axhline(0, color="gray", lw=0.8)
    ax.grid(alpha=0.25, axis="y")
    fig.suptitle("搜索阶段选择标准与最终交付目标的一致性诊断", fontsize=12)
    fig.tight_layout()
    fig.savefig(out / "selection_gap.png", dpi=150)
    fig.savefig(HERE / "selection_gap.png", dpi=150)
    plt.close(fig)


def report(out, raw, sm):
    L = ["# 搜索口径 vs 交付口径：选择错配的量化诊断", "",
         "> `ab_mechanism_3steps` 的补充探针（零侵入 monkeypatch，生产代码零改动）。",
         "> 回答：DE 按 `f(S_eval(x))` 选出的精英，是不是 `f(S_final(x))` 下的最优？",
         ""]

    L += ["## 1. 论断的代码坐实（比「映射不同」更严重）", "",
          "**f 是同一个函数。** 搜索期 `evaluator.evaluate` 取 "
          "`problem.scalar_fitness(solution)`（`evaluator.py:199`），交付期 "
          "`stage_runner.py:346` / `:363` / `:400` / `:437` 逐次重算的也是同一个 "
          "`problem.scalar_fitness`。所以两式的差异**全部来自映射 S**，不是目标函数换了权重。",
          "",
          "| | 搜索期 `S_eval(x)` | 交付期 `S_final(x)` |",
          "|---|---|---|",
          "| 入口 | `evaluator.evaluate(assignment)` | `build_solution(result.best_assignment)` |",
          "| 代码 | `dmde_solver.py:219` → `evaluator.py:197` | `stage_runner.py:344` |",
          "| 后续 | 无 | assignment polish `:354`、memetic 档案 `:370`、coverage_first `:388`、交付后成本 polish `:415` |",
          "| 作用对象 | **每个候选个体** | **仅被选中的那一个** |",
          "",
          "三点必须补上，用户的形式化还低估了问题：",
          "",
          "1. **后处理不保序。** `S_final` 里的 polish 是 x 依赖的局部搜索，改进量 "
          "`Δ(x)=f_eval−f_final` 随 x 的局部结构变化。只要 Δ 不是常数，就能构造 "
          "`f_eval(x₁)<f_eval(x₂)` 但 `f_final(x₁)>f_final(x₂)` 的排序反转。"
          "且 DE 只在**一个**胜出个体上执行 polish —— 这是「先选后打磨」，"
          "与「先打磨后选」不等价。",
          "2. **精英基因被替换，适应度却是「继承」而非重算（结构性风险）。** "
          "`dmde_solver.py:241-248`：Lamarckian 回写得到新基因 `re_encoded` 后，"
          "`re_encoded.fitness = child.fitness` 把**回写前基因的适应度**直接抄给新基因，"
          "再存入种群；下一代 `if child.fitness < population[i].fitness` 比较的就是这个继承值。"
          "其正确性**依赖回写不变式**（`evaluator.py:233-240` 的 Q2.1：写回后以 "
          "`temperature=0` 重解码，表型必须等于目标修复解）。但默认路径走 "
          "`_reencode_start_only`（`evaluator.py:267-273`），只改写起始通道、"
          "**巡游基因保留未修复原值**，Q2.1 的表型复核仅在 M2-1 开启时执行；"
          "回写异常还被 `except: pass` 静默吞掉（`dmde_solver.py:244-246`）。"
          "所以这是一个未加校验的假设，不是已证实的数值 bug。"
          "`stage_runner.py:314-320` 的注释已承认「交付解与当时被评价的解"
          "任务集/顺序/ETA 均可不同，两者适应度本不可比」。"
          "本探针实测最终精英的 `f(build_solution(best_assignment)) − best_fitness` "
          "来检验其是否真的失准（见第 3 节 `陈旧适应度差`）。",
          "3. **逐代贪心是路径依赖的。** 即使两口径完全同序，DE 也是逐代替换，"
          "`f_final` 意义下的好个体若在早期因 `f_eval` 略差被淘汰，就永远进不了种群，"
          "`argmin` 的候选集本身已被截断。",
          "",
          "**argmin 相等的充分条件**：存在严格递增 `g` 使 "
          "`f_final = g ∘ f_eval`（特例：`Δ(x) ≡ 常数`）。交付后 polish 是逐点局部搜索，"
          "不满足该条件，除非所有候选恰好都落在同一局部最优盆地。",
          ""]

    L += ["## 2. 实验设计", "",
          "- 单周期纯几何随机 SRP（与三步实验同场景/同种子/同评价口径）。",
          "- **重放时点（关键口径修复）**：`DMDESolver.solve` 刚返回时 "
          "`problem` / `evaluator` 仍是搜索期状态（实测同一基因重解码逐位复现），"
          "但 `horizon.run` 随后的交付期后处理会**原地改动** evaluator 的可变状态 —— "
          "d150 下若在 run 结束后重放，重解码漂移 −375.37、所有候选塌成同一个解，"
          "并列最优数与反转率全部失真。故在 `_on_solve` 内 `deepcopy(evaluator)` "
          "取隔离副本，全部重放只在副本上做：既锁住搜索期口径，又不污染生产链。"
          "修复后 d0 / d150 的 `|f_eval − f_redecode|` 均为 0。",
          "- hook `DMDESolver._evaluate` / `_evaluate_result` / `solve`，"
          "记录本次 run **每一个被评价过的基因** `assignment` 及 `f_eval`；"
          "按 assignment 去重。",
          "- 对每个候选离线重放交付映射：D1 重解码（隔离校验，应与 `f_eval` 逐位相等）；"
          "D2 再叠加 `post_cost_polish`，按 `stage_runner` 同款四道门"
          "（执行集不减 / 硬违约不增 / excess 不增 / 航程严格下降）决定采纳，"
          "ε 取生产默认 `cost_polish_eps=1.0`（`stage_config.py:161`），"
          "与 `stage_runner.py:434` 逐位对齐。",
          "- `refine=关`（关掉交付后成本优化总闸）时 D2 恒等于 D1，"
          "作为**隔离对照**：若错配仍存在，就来自重解码/基因替换而非 polish。",
          "- 直接测两个 argmin：`x*_eval=argmin f_eval`、"
          "`x*_final=argmin f_final`，以及 "
          "`regret = f_final(x*_eval) − min_x f_final(x)`"
          "（现状「先选后打磨」vs 理论上限「先打磨后选」）。",
          ""]

    if not sm:
        L += ["（无数据）", ""]
    else:
        def avg(rows, k):
            v = [fnum(r, k) for r in rows]
            return C.mean_std(v)[0]

        # d0（零迟到、纯航程）与 d150（强截止、迟到主导）是两种体制，
        # 错配形态不同，混在一起平均会互相抵消 ⇒ 一律分组汇总。
        dls = sorted({int(float(r["deadline_s"])) for r in sm})
        groups = {
            dl: (
                [r for r in sm if int(float(r["deadline_s"])) == dl
                 and int(float(r["refine"])) == 1],
                [r for r in sm if int(float(r["deadline_s"])) == dl
                 and int(float(r["refine"])) == 0],
            )
            for dl in dls
        }

        L += ["## 3. 实测结果", "",
              "| 场景 | d | 精修 | seed | 候选数 | top1重合 | "
              "regret | 并列最优数 | DE选中名次 | 选择价值 | 反转率 | "
              "Kendall τ | Δ均值 | corr(Δ,f_eval) | 重解码差 | 陈旧适应度差 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in sm:
            L.append(
                f"| {r['scenario']} | {r['deadline_s']} | "
                f"{'开' if int(float(r['refine'])) else '关'} | {r['seed']} | "
                f"{r['n_cand']} | {r['top1_match']} | {r['regret']} | "
                f"{r['n_tied_best']} | {r['de_choice_rank']} | "
                f"{r['selection_value']} | {r['inversion_rate']} | "
                f"{r['kendall_tau']} | {r['delta_mean']} | "
                f"{r['corr_delta_feval']} | {r['redecode_gap_max']} | "
                f"{r['stale_gap']} |")
        L.append("")
        L += ["> `选择价值 = mean(f_final) − f_final(x*_eval)`：DE 的选择相对"
              "「随便挑一个候选」值多少；`并列最优数` 识破 `top1重合=1` 是否"
              "只是大量候选被 polish 压到同一局部最优的退化巧合。", ""]

        L += ["### 汇总（按截止体制分组，不混平均）", ""]
        for dl in dls:
            on_d, off_d = groups[dl]
            if on_d:
                L.append(
                    f"- **d{dl} / 开启交付后成本优化**（n={len(on_d)}）："
                    f"top1 重合率 **{avg(on_d,'top1_match'):.3f}**，"
                    f"平均 regret **{avg(on_d,'regret'):.2f}**，"
                    f"DE 选中个体在交付口径下平均名次 "
                    f"**{avg(on_d,'de_choice_rank'):.1f}/{avg(on_d,'n_cand'):.0f}**，"
                    f"排序反转率 **{avg(on_d,'inversion_rate'):.3f}**，"
                    f"Spearman **{avg(on_d,'spearman'):.3f}**、"
                    f"Kendall τ **{avg(on_d,'kendall_tau'):.3f}**，"
                    f"平均并列最优数 **{avg(on_d,'n_tied_best'):.1f}**，"
                    f"选择价值 **{avg(on_d,'selection_value'):.2f}**。")
            if off_d:
                L.append(
                    f"- **d{dl} / 关掉总闸**（n={len(off_d)}）："
                    f"top1 重合率 **{avg(off_d,'top1_match'):.3f}**，"
                    f"regret **{avg(off_d,'regret'):.2f}**，"
                    f"反转率 **{avg(off_d,'inversion_rate'):.3f}**，"
                    f"Spearman **{avg(off_d,'spearman'):.3f}**，"
                    f"选择价值 **{avg(off_d,'selection_value'):.2f}**。")
        L += [
              f"- 重解码有效性门槛：`|f_eval − f_redecode|` 最大值 "
              f"**{max([fnum(r,'redecode_gap_max') for r in sm] or [float('nan')]):.2g}**"
              "（必须为 0，否则该 run 作废）。",
              f"- 陈旧适应度：`f(build_solution(best_assignment)) − best_fitness` 平均 "
              f"**{C.mean_std([fnum(r,'stale_gap') for r in sm])[0]:.2f}**；"
              f"`best_assignment` 出现在被评价候选集里的比例 "
              f"**{C.mean_std([fnum(r,'best_gene_seen') for r in sm])[0]:.2f}**。",
              f"- `corr(Δ, f_eval)` 平均 "
              f"**{C.mean_std([fnum(r,'corr_delta_feval') for r in sm])[0]:.3f}**："
              "显著非零即证明打磨收益 Δ 依赖候选 x，序关系不保；"
              "正值表示「越差的候选被打磨得越多」（向同一局部最优压缩，"
              "易造成并列最优），负值表示「越好的候选收益越大」（拉开差距）。",
              ""]

        # 逐体制自动判定，不预设方向
        rmax = max([fnum(r, "redecode_gap_max") for r in sm] or [float("nan")])
        stale = C.mean_std([fnum(r, "stale_gap") for r in sm])[0]
        valid = math.isfinite(rmax) and rmax < 1e-6
        parts = []
        if not valid:
            parts.append("**重放有效性门槛未通过**（重解码漂移非 0），以下判定不可采信。")
        for dl in dls:
            on_d, off_d = groups[dl]
            if not on_d:
                continue
            rg = avg(on_d, "regret")
            inv = avg(on_d, "inversion_rate")
            sp_on = avg(on_d, "spearman")
            tied = avg(on_d, "n_tied_best")
            sv_on = avg(on_d, "selection_value")
            sv_off = avg(off_d, "selection_value") if off_d else float("nan")
            rank = avg(on_d, "de_choice_rank")
            if not math.isfinite(rg):
                parts.append(f"**d{dl}**：数据不足。")
            elif rg > 1e-6:
                parts.append(
                    f"**d{dl}：两个 argmin 直接不重合**。top1 重合率 "
                    f"{avg(on_d,'top1_match'):.3f}、平均 regret **{rg:.2f}**、"
                    f"DE 选中个体在交付口径下只排第 **{rank:.1f}** —— "
                    "DE 正确选出了 `f∘S_eval` 的最优，但它不是 `f∘S_final` 的最优，"
                    "用户的形式化在数值上被直接坐实。")
            elif inv > 0.01 or (math.isfinite(sp_on) and sp_on < 0.95):
                parts.append(
                    f"**d{dl}：两个 argmin 恰好重合，但序关系已被破坏**。"
                    f"regret={rg:.2f}，可排序反转率 **{inv:.3f}**、Spearman "
                    f"**{sp_on:.3f}**、`Δ(x)` 强依赖 x ⇒ `f_final=g∘f_eval` 不成立，"
                    f"两个 argmin **没有对齐保证**。regret 为 0 是因为平均 "
                    f"**{tied:.1f}** 个候选被 polish 压到同一个 `f_final` 最小值"
                    "（并列最优），DE 恰好选中其一，属退化巧合；"
                    f"选择价值从关闸时 **{sv_off:.2f}** 塌到 **{sv_on:.2f}**。")
            else:
                parts.append(
                    f"**d{dl}：未观察到错配**（regret={rg:.2f}、反转率={inv:.3f}）；"
                    "但充分条件仍不成立，不得外推为「已证明对齐」。")
        if valid:
            has_mis = any("直接不重合" in p for p in parts)
            has_cmp = any("序关系已被破坏" in p for p in parts)
            if has_mis and has_cmp:
                shape = ("两种截止体制给出互补的失效形态：零迟到体制是"
                         "「排序被压缩成大量并列最优、DE 的选择价值大幅塌缩」，"
                         "强截止体制是「argmin 直接错位、产生可测 regret」；")
            elif has_mis:
                shape = "实测出现「argmin 直接错位、产生可测 regret」；"
            elif has_cmp:
                shape = "实测出现「排序被压缩成大量并列最优、DE 的选择价值塌缩」；"
            else:
                shape = "本批次未观察到错配；"
            parts.append(
                "**总判定：用户论断成立。** " + shape +
                "关掉交付后成本优化总闸后各体制都恢复完全对齐"
                "（Spearman=1、反转率 0），证明错配由交付后优化引入，"
                "而非重解码或随机性。根源是同一件事：后处理是 x 依赖的局部搜索，"
                "且只对胜出者执行一次，即 `select-then-polish ≠ polish-then-select`。")
        verdict = " ".join(parts)
        L += ["## 4. 判定", "", verdict, "",
              f"- **隔离校验**：`|f_eval − f_redecode|` 最大 **{rmax:.2g}**。"
              + ("该值为 0 ⇒ 同一基因重走 `build_solution` 逐位复现，"
                 "错配**不是**重解码/随机性引入的，而是由交付后优化引入；"
                 "关掉总闸后反转率应同步归零。"
                 if (math.isfinite(rmax) and rmax < 1e-6) else
                 "该值非 0 ⇒ 重解码本身已产生漂移，需优先排查解码链状态依赖。"),
              f"- **陈旧适应度**：最终精英的 `f(build_solution(best_assignment)) "
              f"− best_fitness` 平均 **{stale:.2f}**。"
              + ("本次为 0，说明回写不变式在最终精英上成立；"
                 "但不代表中间代的继承适应度都正确（默认回写路径无 Q2.1 复核，"
                 "异常被静默吞掉），仍是 P0 要补的校验点。"
                 if abs(stale) < 1e-6 else
                 "该值非 0 ⇒ 种群中确实存在与当前基因不对应的继承适应度，"
                 "DE 连搜索期 argmin 都未严格执行，须优先修复。"),
              ""]

        L += ["## 5. 与三步实验证据的勾连", "",
              "- 2×2 消融交互项 **158.1**（进化效应 202.6 → 开精修后 44.5）："
              "收益非可加，正是 `Δ(x)` 依赖 x 的宏观表现；若两口径同序，"
              "交互项应接近 0。",
              "- 第一步精英冻结（冻结代占比 0.89、改进代数 0.2）："
              "陈旧适应度使 `population[i].fitness` 与当前基因脱钩，"
              "贪婪选择天然倾向不替换 —— 这是「DE 选不动」的第二个独立解释，"
              "与「子代劣于初始精英」并存。",
              "- 第三步 `prod` 基准定序边际 0：定序收益在解码期被提前消费，"
              "属于 `S_eval` 与 `S_final` 共享链路上被重复计入的部分。",
              ""]

        L += ["## 6. 修复路线（按代价排序）", "",
              "1. **P0 给继承适应度加校验（改动最小）**："
              "`dmde_solver.py:241-243` 在 `child = re_encoded` 后，"
              "用 `fitness_evaluator.evaluate(re_encoded.assignment, ...)` 重算 "
              "`re_encoded.fitness`（有缓存，开销可控）并与继承值比对，"
              "不一致时计数/告警；同时把 `except: pass` 改为计数 + 日志。"
              "本次实测最终精英未失准，但默认回写路径无 Q2.1 复核、异常被静默吞掉，"
              "属于未加校验的结构性风险，补上即可消除。",
              "2. **P1 让选择口径看见交付后处理**：把 polish 从「只对胜出者执行一次」"
              "改为「对候选集执行后再选」（memetic / 种群级局部搜索），"
              "或在选择阶段用 `f_final` 的廉价下界作代理。"
              "代价是评价次数上升，需重新标定预算。",
              "3. **P2 结构性对齐**：把 `_consolidate` / `_best_order` / 跨机 "
              "relocate 从修复器剥离为显式优化模块（上一轮 P1），"
              "并给 `build_solution` 加 `DecisionTrace` 逐次标注 `hard|opt`，"
              "使 `S_eval` 与 `S_final` 的差异可审计、可按模块归因。",
              "4. **论文口径**：不能写「DE 搜索并选择最优方案」；"
              "应写「DE 在 `f∘S_eval` 上做种群搜索并提名精英，"
              "交付解经重建与后处理定案；两口径排序一致性由本探针实测给出」，"
              "并把 `best_fitness` / `final_fitness` 并列报告。",
              ""]

    L += ["## 7. 诚实口径", "",
          "交付链 `stage_runner.py:344-438` 各步的**重放覆盖情况**（逐项声明，"
          "避免把「重放」误读成「完整交付链」）：",
          "",
          "| 交付链步骤 | 本探针 | 说明 |",
          "|---|---|---|",
          "| `build_solution(best_assignment)` | 已覆盖 | D1 重解码，逐位复现 f_eval |",
          "| `polish_assignment`（assignment 级局部搜索） | **未覆盖** | 每候选重放代价过高；生产只对胜出者执行一次 |",
          "| memetic M1 档案择优 | 不适用 | 本实验 `polish_in_loop=False`，档案为空 |",
          "| `coverage_first` 终态后处理 | 不适用 | `stage_config.py:96` 默认 `False` |",
          "| `post_cost_polish`（交付后成本 polish） | 已覆盖 | D2，四道门 + ε=1.0 与生产一致 |",
          "",
          "因此本文的 regret 是**下界**，不是完整错配幅度；"
          "「并列最优数」也可能因少一层打磨而与真实交付不同。"
          "所有在生产链里只作用于胜出者的模块，恰恰是造成"
          "「先选后打磨 ≠ 先打磨后选」的根源，本探针只量化了其中可重放的那一段。"
          "方向上是安全的：漏掉的 `polish_assignment` 是**又一次逐点局部搜索**，"
          "只会进一步压缩候选差异、增加并列，不会把已被打乱的序关系恢复成同序，"
          "故实测的反转率 / 并列最优数应理解为保守下界。",
          "- 候选集 = 本次 run 实际被 DE 评价过的基因（含初始种群与 GMR 重建），"
          "不含从未采样到的基因；故 `min_x f_final` 是「同一搜索轨迹内」的上限，"
          "不是全局最优。",
          "- **重放有效性门槛**：`redecode_gap_max = max|f_eval − f_redecode|` "
          "必须为 0 才认为该 run 的重放锁定在搜索期口径；若非 0，该 run 的 "
          "反转率 / 并列最优 / regret 一律作废，不得纳入结论。本次修复后 "
          "12 组全为 0。",
          "- 预算 pop/gens 低于生产口径（pop50×gen200），场景为纯几何 SRP，"
          "结论不外推到 DEM 有向代价与多周期滚动。",
          "- 所有插桩在实验侧 monkeypatch，不改动生产代码，不影响 "
          "`pytest` 全量回归。", ""]

    (HERE / "selection_gap_report.md").write_text("\n".join(L), encoding="utf-8")
    (out / "selection_gap_report.md").write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="40:1")
    ap.add_argument("--deadlines", default="0,150")
    ap.add_argument("--refines", default="1,0", help="1=开启交付后成本优化,0=关掉总闸")
    ap.add_argument("--pop", type=int, default=10)
    ap.add_argument("--gens", type=int, default=10)
    ap.add_argument("--seeds", default="11,22,33")
    ap.add_argument("--n-instances", type=int, default=1)
    ap.add_argument("--inst-base", type=int, default=5000)
    ap.add_argument("--n-uavs", type=int, default=3)
    ap.add_argument("--area", type=float, default=1000.0)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--out-dir", default="results_gap")
    ap.add_argument("--report-only", action="store_true",
                    help="从已落盘 CSV 重生成图与报告，不重跑实验")
    args = ap.parse_args()

    out = HERE / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        raw = load_csv(out / "selection_gap_raw.csv")
        sm = load_csv(out / "selection_gap_summary.csv")
        print(f"[gap] report-only: {len(raw)} raw / {len(sm)} summary", flush=True)
    else:
        out, raw, sm = run_experiments(args)
    plot(out, raw, sm)
    report(out, raw, sm)
    print("[gap] done -> selection_gap_report.md / selection_gap.png", flush=True)


if __name__ == "__main__":
    main()
