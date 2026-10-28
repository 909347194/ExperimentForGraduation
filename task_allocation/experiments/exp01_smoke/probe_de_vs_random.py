# -*- coding: utf-8 -*-
"""probe_de_vs_random.py — 裁决「DE 是否真的在优化 任务归属+访问顺序」。

设计（2026-10-28，回应论文叙事质疑：λ 牵引小 ⇒ 搜索可能没在 f 上用力）：

    同一 config（seed 42）跑两条臂，各完整滚动链路：
      DE 臂      ：正常 DMDE 搜索，记录逐周期 cost_history（gen0 最优 → 收敛最优）
      随机对照臂 ：RandomSearchSolver 替换搜索器 —— 与 DMDE **同评估预算**
                   （pop_size×(1+max_generations) 次均匀采样同一初始分布），
                   经 **同一条** run_selective_muas 解码+覆盖门禁+polish 管线。

    三类证据：
      E1  DE 逐周期收敛曲线：f(gen0) → f(final) 改进量（DE 自身是否在优化）
      E2  DE vs 随机：同预算下搜索期最优 f 对比 + 交付结构（归属）重合度
          ——若 DE ≈ 随机，则 f 上的选择压没有净贡献，叙事必须改写
      E3  DE vs 随机：交付解路径/迟到对比（交付口径）

用法：
    cd task_allocation/experiments/exp01_smoke
    python probe_de_vs_random.py            # 两臂顺序各跑一遍 seed 42
    python probe_de_vs_random.py --arms de  # 只跑 DE 臂
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run as run_mod  # noqa: E402
import task_allocation.methods.rolling.horizon as H  # noqa: E402
from task_allocation.methods.muas.base.base_optimizer import (  # noqa: E402
    BaseOptimizer,
    SolverResult,
)
from task_allocation.methods.muas.representation.encoder import (  # noqa: E402
    PopulationEncoder,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDESolver  # noqa: E402


# ── 随机搜索对照求解器（同预算均匀采样） ─────────────────────────────


class RandomSearchSolver(BaseOptimizer):
    """与 DMDE 同评估预算的随机搜索：均匀采样编码器初始分布。

    评估预算 = pop_size × (1 + max_generations)，与 DMDESolver 的
    初始种群 + 逐代试验向量总数对齐；个体评价走**同一个**
    fitness_evaluator（含修复链），交付走同一条 stage 管线。
    """

    @property
    def name(self) -> str:
        return "random_search"

    def __init__(self, pop_size: int, n_gens: int, seed: int) -> None:
        self.pop_size = int(pop_size)
        self.n_gens = int(n_gens)
        self.seed = int(seed)

    def solve(self, cost_matrix, n_uavs, n_targets, **kwargs) -> SolverResult:
        evaluator = kwargs["fitness_evaluator"]
        rng = np.random.default_rng(self.seed)
        budget = self.pop_size * (self.n_gens + 1)
        enc = PopulationEncoder(cost_matrix, n_uavs, n_targets)
        t0 = time.time()
        best, best_f = None, float("inf")
        n_evals = 0
        while n_evals < budget:
            n = min(self.pop_size, budget - n_evals)
            pop = enc.generate(n, seed=int(rng.integers(0, 2**31 - 1)))
            for ind in pop:
                ind.fitness = DMDESolver._evaluate(
                    ind, evaluator, cost_matrix, n_uavs=n_uavs
                )
                if ind.fitness < best_f:
                    best_f, best = float(ind.fitness), ind.copy()
            n_evals += n
        return SolverResult(
            best_assignment=best.assignment,
            best_fitness=best_f,
            cost_history=[best_f],
            total_generations=0,
            elapsed_seconds=time.time() - t0,
            solver_name=self.name,
            extra={"model_type": "srp", "pop_size": self.pop_size,
                   "n_evals": n_evals},
        )


# ── 采集层：逐周期 stage 数据 + DE 逐代结构轨迹 ──────────────────────

CAPTURE: list[dict] = []   # 每次 run_selective_muas 调用（= 每周期）一条

OUT_TAG: dict = {"dir": None}  # 当前臂+seed 的输出目录（增量落盘用）


def _dump_trace(path: str) -> None:
    dump = {
        "arm": ARM["current"], "dir": OUT_TAG["dir"],
        "capture": [dict(c) for c in CAPTURE],
        "de_trace_gens": [
            {"gen": rec[0], "f": rec[1]} for rec in DE_TRACE
        ],
    }
    Path(path).write_text(json.dumps(dump, ensure_ascii=False),
                          encoding="utf-8")


_orig_rs = H.run_selective_muas


def _capture_rs(*a, **kw):
    stage = _orig_rs(*a, **kw)
    sol = stage.solution
    feas = sol.meta.get("feasibility") or {}
    tours = [t for t in sol.tours if t.task_ids]
    CAPTURE.append({
        "cost_history": [float(x) for x in stage.cost_history],
        "n_evals": int(stage.n_evals),
        "solver_name": str((stage.solver_extra or {}).get("solver_name", "")),
        # 交付口径：路径 / 迟到 / 归属结构
        "path_m": float(sum(t.est_cost or 0.0 for t in tours)),
        "late_s": float(feas.get("soft_violation", 0.0) or 0.0),
        "assign": tuple(sorted(
            (t.uav_id, tid) for t in tours for tid in t.task_ids
        )),
    })
    # 增量落盘：每周期写一次，进程被回收也能保住已完成周期
    if OUT_TAG["dir"]:
        try:
            _dump_trace(f"{OUT_TAG['dir']}_trace_partial.json")
        except OSError:
            pass
    return stage


H.run_selective_muas = _capture_rs

DE_TRACE: list[list] = []  # 每次 DMDESolver.solve 的逐代 (gen, f, assign)

_orig_solve = DMDESolver.solve


def _solve_trace(self, cost_matrix, n_uavs, n_targets, **kw):
    cfg = self._cfg
    rec: list = []
    outer_cb = cfg.gen_callback

    def cb(gen, ind):
        rec.append((int(gen), float(ind.fitness),
                    tuple(sorted((int(u), int(t))
                                 for (u, t) in ind.assignment))))
        if outer_cb is not None:
            outer_cb(gen, ind)

    cfg.gen_callback = cb
    try:
        return _orig_solve(self, cost_matrix, n_uavs, n_targets, **kw)
    finally:
        cfg.gen_callback = outer_cb
        DE_TRACE.append(rec)


DMDESolver.solve = _solve_trace


# ── 结构比较工具 ─────────────────────────────────────────────────────


def assign_stats(a: tuple, b: tuple) -> dict:
    sa, sb = set(a), set(b)
    inter = sa & sb
    union = sa | sb
    # 归属重合度：两臂都分了任务的对里，同机比例（按 task 对齐）
    da = {t: u for u, t in a}
    db = {t: u for u, t in b}
    common_tasks = set(da) & set(db)
    same_uav = sum(1 for t in common_tasks if da[t] == db[t])
    return {
        "jaccard": (len(inter) / len(union)) if union else 1.0,
        "same_uav_frac": (same_uav / len(common_tasks)) if common_tasks else 1.0,
        "n_common": len(common_tasks),
    }


# ── 主流程：两臂各跑一遍 ─────────────────────────────────────────────

RealHorizon = H.RollingHorizon
ARM = {"current": "de"}


def _patched_horizon(*a, **kw):
    if ARM["current"] == "random":
        scfg = kw.get("solver_config")
        pop = int(getattr(scfg, "pop_size", 50))
        gens = int(getattr(scfg, "max_generations", 1000))
        seed = int(getattr(scfg, "seed", 42) or 42)
        kw["solver_factory"] = (
            lambda: RandomSearchSolver(pop, gens, seed)
        )
    return RealHorizon(*a, **kw)


run_mod.RollingHorizon = _patched_horizon


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    arms = ["de", "random"]
    if "--arms" in argv:
        i = argv.index("--arms")
        arms = argv[i + 1].split(",")
        del argv[i:i + 2]
    seeds = [42]
    if "--seeds" in argv:
        i = argv.index("--seeds")
        for tok in argv[i + 1].split(","):
            if "-" in tok[1:]:
                lo, hi = tok.split("-", 1)
                seeds.extend(range(int(lo), int(hi) + 1))
            else:
                seeds.append(int(tok))
        del argv[i:i + 2]
    seeds = sorted(set(seeds))

    # results[(arm, seed)] = per-cycle capture list
    results: dict[tuple[str, int], list[dict]] = {}
    for arm in arms:
        for seed in seeds:
            ARM["current"] = arm
            CAPTURE.clear()
            DE_TRACE.clear()
            out = f"results_probe_{arm}_s{seed}"
            OUT_TAG["dir"] = out
            print(f"\n{'=' * 26} 臂 = {arm}  seed = {seed} → {out} {'=' * 26}")
            run_mod.main([
                "--seeds", str(seed), "--no-plots", "--no-audit",
                "--set", f"experiment.output_dir={out}",
                *argv,
            ])
            results[(arm, seed)] = [dict(c) for c in CAPTURE]
            # 完整落盘（含逐代追踪）：与增量 partial 文件区分
            _dump_trace(f"{out}_trace.json")
            print(f"[probe] 追踪已落盘 → {out}_trace.json")

    # ── 分析 ──
    print("\n" + "=" * 72)
    print("分析：DE 是否真的在优化「归属 + 顺序」？")
    print("=" * 72)

    de_seeds = sorted(s for a, s in results if a == "de")
    rnd_seeds = sorted(s for a, s in results if a == "random")

    print("\n[E1] DE 逐周期收敛曲线（gen0 最优 → 收敛最优）")
    for s in de_seeds:
        caps = results[("de", s)]
        tot0 = sum(c["cost_history"][0] for c in caps)
        totT = sum(c["cost_history"][-1] for c in caps)
        parts = "  ".join(
            f"c{i + 1}:{c['cost_history'][0]:.0f}→{c['cost_history'][-1]:.0f}"
            for i, c in enumerate(caps)
        )
        imp = (tot0 - totT) / abs(tot0) * 100 if tot0 else 0.0
        print(f"  seed {s}: {parts}  | 合计改进 {imp:.2f}%")

    common_seeds = sorted(set(de_seeds) & set(rnd_seeds))
    if common_seeds:
        print("\n[E2] 周期 1 同题对比（唯一两臂问题状态相同的周期；同预算）")
        wins = 0
        for s in common_seeds:
            d = results[("de", s)][0]["cost_history"][-1]
            r = results[("random", s)][0]["cost_history"][-1]
            gap = (r - d) / abs(r) * 100 if r else 0.0
            wins += 1 if d < r else 0
            st = assign_stats(results[("de", s)][0]["assign"],
                              results[("random", s)][0]["assign"])
            print(f"  seed {s}: DE={d:.1f}  随机={r:.1f}  DE 优 {gap:+.2f}%  "
                  f"| 结构重合: Jaccard={st['jaccard']:.2f}, "
                  f"同机率={st['same_uav_frac']:.2f} (共 {st['n_common']} 任务)")
        print(f"  DE 周期1 胜出: {wins}/{len(common_seeds)}")

        print("\n[E3] 交付口径对比（读各臂 metrics.json；权威口径）")
        keys = ("n_completed", "n_unreachable_geo", "n_late",
                "total_path_m", "on_time_rate_final", "correct_exec_rate")
        agg = {a: {k: [] for k in keys} for a in arms}
        for arm in arms:
            for s in sorted(x for a2, x in results if a2 == arm):
                mpath = Path(f"results_probe_{arm}_s{s}") / "metrics.json"
                try:
                    m = json.loads(mpath.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue
                sm = m["per_seed"][str(s)]["summary"]
                for k in keys:
                    if sm.get(k) is not None:
                        agg[arm][k].append(float(sm[k]))
        for arm in arms:
            row = "  ".join(
                f"{k}={np.mean(v):.3g}(±{np.std(v):.2g})" if v else f"{k}=NA"
                for k, v in agg[arm].items()
            )
            print(f"  {arm.upper().ljust(7)}: {row}")
        d, r = agg.get("de"), agg.get("random")
        if d and r and d["total_path_m"] and r["total_path_m"]:
            dp, rp = np.mean(d["total_path_m"]), np.mean(r["total_path_m"])
            do, ro = np.mean(d["on_time_rate_final"]), np.mean(r["on_time_rate_final"])
            print(f"  汇总  : path DE {(dp - rp) / rp * 100:+.2f}% vs 随机 | "
                  f"按时率差 {do - ro:+.3f}")
    if "random" not in arms:
        print("\n（只跑了 DE 臂；加 --arms de,random 补随机对照）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
