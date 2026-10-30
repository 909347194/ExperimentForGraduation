# -*- coding: utf-8 -*-
"""M2-1 段内巡游序 Lamarckian 回写 · 3-seed A/B 跑批（设计稿 §4 验收）。

对照：M2-1 关 (OFF) vs 开 (ON)，各 3 seed。记录设计稿要求的四类指标：

  1. 路径成本 (path cost, T3 = ``f2_cost/λ``，含返巢)
  2. 迟到     (late seconds, ``soft_violation``)
  3. 收敛曲线 (``cost_history``，搜索期每代最优适应度)
  4. 种群方差 (代际 ``cost_vector`` 种群方差，来自新增 ``population_callback``)

为干净归因到 M2-1 的「搜索期」效应，默认关闭交付后 polish / memetic
（与 OFF 同口径），使**交付解 == 搜索最优**——M2-1 是唯一变量。
种群方差、收敛曲线均为搜索期口径（设计稿 §3.4「不做运行时熔断、只作
A/B 否决指标」）。

输出（结果落在 ``results/``）：
  - ``ab_m21_raw.csv``         逐 run 原始指标
  - ``ab_m21_summary.csv``     聚合（mean/std，ON−OFF 差值/比例）
  - ``ab_m21_curves.png``      2×2 图（路径成本 / 迟到 / 收敛曲线均值 / 种群方差均值）
  - ``ab_m21_summary.json``    同上聚合的结构化快照

用法：
  python run.py                      # 默认 3 seed、关 polish/memetic
  python run.py --seeds 11,22,33 --n-uavs 3 --n-targets 12 --gens 120
  python run.py --no-isolate         # 走完整生产管线（含 polish/memetic）
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]  # .../ExperimentForGraduation
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    Nest,
    Task,
    UAV,
    UAVStatus,
)
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.stage_runner import (  # noqa: E402
    DMDEConfig,
    MUASStageConfig,
    run_selective_muas,
)


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

DEFAULTS = dict(
    seeds=[11, 22, 33],
    n_uavs=3,
    n_targets=12,
    area=1000.0,
    pop_size=40,
    max_generations=120,
    deadline_s=0,       # >0 ⇒ 注入时间窗（latest=earliest+deadline_s），使排序产生压力
    isolate=True,  # 关闭交付后 polish/memetic，干净归因到 M2-1 搜索期
)


# ---------------------------------------------------------------------------
# 实例 / 记录器
# ---------------------------------------------------------------------------


def make_instance(seed: int, n_uavs: int, n_targets: int, area: float, deadline_s: float = 0.0):
    """生成纯几何随机 SRP 实例（无 DEM ⇒ 代价退化为三维直线，无需数据文件）。

    ``deadline_s > 0`` 时给每个任务注入时间窗 ``latest = earliest + deadline_s``，
    使巡游排序产生迟到压力——M2-1 的段内序回写价值恰在修复链因截止而重排时显现。
    """
    rng = np.random.default_rng(seed)
    tasks = [
        Task(
            id=i,
            x=float(rng.uniform(0.0, area)),
            y=float(rng.uniform(0.0, area)),
            z=0.0,
            reward=5.0,
            earliest=0.0,
            latest=(1e6 if deadline_s <= 0 else deadline_s),
            arrival_time=0.0,
        )
        for i in range(n_targets)
    ]
    uavs = [
        UAV(
            id=j,
            x=float(rng.uniform(0.0, area)),
            y=float(rng.uniform(0.0, area)),
            z=0.0,
            remaining_range=1e7,
            speed=10.0,
            nest_id=0,
            status=UAVStatus.IDLE,
        )
        for j in range(n_uavs)
    ]
    nests = [Nest(id=0, x=area / 2.0, y=area / 2.0, z=0.0, capacity=10, available=True)]
    return tasks, uavs, nests


class VarianceRecorder:
    """代际 gene-space 种群方差记录器（设计稿 §3.4 多样性监控）。

    每代选择结束后回调，记录整代 ``cost_vector`` 矩阵在**各维度上**的方差的均值
    （标量多样性代理）。M2-1 若加速同质化，末代方差会较 OFF 更快坍缩。
    """

    def __init__(self) -> None:
        self.variances: list[float] = []

    def __call__(self, gen: int, cost_vectors: np.ndarray, best_idx: int) -> None:
        # 跨种群（axis=0）逐维度方差，再取维度均值 ⇒ 单代多样性标量。
        self.variances.append(float(np.var(cost_vectors, axis=0).mean()))


# ---------------------------------------------------------------------------
# 单次试验
# ---------------------------------------------------------------------------


def run_trial(tasks, uavs, nests, *, on: bool, seed: int, params: dict) -> dict:
    cb = build_pairwise_costs(tasks, uavs, nests=nests)
    isolate = params["isolate"]
    deadline_s = float(params.get("deadline_s", 0.0))
    # 时间窗口径：迟到计费 + 折减（与求解器内部同一组权重，保证指标口径一致）。
    prob_cfg = MUASProblemConfig()
    if deadline_s > 0:
        prob_cfg = MUASProblemConfig(w_violation=0.01, late_reward_decay_seconds=300.0)

    stage_cfg = MUASStageConfig(
        lamarckian_patrol_writeback=on,
        cost_polish_enabled=not isolate,
        cost_polish_relocate_enabled=not isolate,
        polish_enabled=not isolate,
        polish_in_loop=False,  # memetic 默认关（与 OFF 同口径）
    )
    rec = VarianceRecorder()
    solver_cfg = DMDEConfig(
        pop_size=params["pop_size"],
        max_generations=params["max_generations"],
        seed=seed,
        verbose=False,
        population_callback=rec,
    )

    t0 = time.time()
    res = run_selective_muas(
        tasks=list(tasks),
        uavs=list(uavs),
        nests=list(nests),
        cost_build=cb,
        stage_config=stage_cfg,
        solver_config=solver_cfg,
        problem_config=prob_cfg,
    )
    elapsed = time.time() - t0

    # 用同口径问题重算 path/late/hard（与 run_selective_muas 内部同一代价源/权重）。
    prob = SelectiveMUASProblem.from_entities(
        tasks,
        uavs,
        nests,
        pairwise_cost=cb.provider.pairwise,
        config=prob_cfg,
    )
    sol = res.solution
    path_cost = float(prob.path_cost(sol))
    late = float(prob.soft_violation(sol))
    n_hard, excess_m = prob.hard_stats(sol)
    delivered_fitness = float(prob.scalar_fitness(sol))  # 真实目标（λ·path + w_violation·late + 硬违约…）
    executed = len(sol.executed_task_ids)
    conv = [float(x) for x in res.cost_history]
    var = list(rec.variances)
    collapse = (var[-1] / var[0]) if (var and var[0] > 0) else float("nan")

    return dict(
        mode="ON" if on else "OFF",
        seed=seed,
        search_best_fitness=float(conv[-1]) if conv else float("nan"),
        delivered_fitness=delivered_fitness,
        path_cost=path_cost,
        late=late,
        n_hard=int(n_hard),
        excess_m=float(excess_m),
        coverage=executed / max(1, len(tasks)),
        n_evals=int(res.n_evals),
        elapsed_s=elapsed,
        var_initial=var[0] if var else float("nan"),
        var_final=var[-1] if var else float("nan"),
        var_collapse_ratio=collapse,
        convergence=conv,
        variance=var,
    )


# ---------------------------------------------------------------------------
# 聚合 / 输出
# ---------------------------------------------------------------------------


def _mean_std(xs):
    xs = [x for x in xs if math.isfinite(x)]
    if not xs:
        return float("nan"), float("nan")
    return float(np.mean(xs)), float(np.std(xs))


def summarize(rows: list[dict]) -> dict:
    metrics = ["search_best_fitness", "delivered_fitness", "path_cost", "late", "n_hard",
               "coverage", "var_initial", "var_final", "var_collapse_ratio"]
    out: dict = {}
    for mode in ("OFF", "ON"):
        sub = [r for r in rows if r["mode"] == mode]
        out[mode] = {m: _mean_std([r[m] for r in sub]) for m in metrics}
    # ON−OFF 差值 / 比例（以均值计）
    delta: dict = {}
    for m in metrics:
        off_m, _ = out["OFF"][m]
        on_m, _ = out["ON"][m]
        if math.isfinite(off_m) and math.isfinite(on_m) and off_m != 0:
            delta[m] = dict(diff=on_m - off_m, ratio=on_m / off_m)
        else:
            delta[m] = dict(diff=float("nan"), ratio=float("nan"))
    out["delta_ON_minus_OFF"] = delta
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    scalar = ["mode", "seed", "search_best_fitness", "delivered_fitness", "path_cost", "late",
              "n_hard", "excess_m", "coverage", "n_evals", "elapsed_s",
              "var_initial", "var_final", "var_collapse_ratio"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=scalar)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in scalar})


def write_summary_csv(path: Path, summ: dict) -> None:
    metrics = ["search_best_fitness", "delivered_fitness", "path_cost", "late", "n_hard",
               "coverage", "var_initial", "var_final", "var_collapse_ratio"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "OFF_mean", "OFF_std", "ON_mean", "ON_std",
                    "ON-OFF_diff", "ON/OFF_ratio"])
        for m in metrics:
            off_m, off_s = summ["OFF"][m]
            on_m, on_s = summ["ON"][m]
            d = summ["delta_ON_minus_OFF"][m]
            w.writerow([m, off_m, off_s, on_m, on_s, d["diff"], d["ratio"]])


def plot_curves(out_png: Path, rows: list[dict], params: dict) -> None:
    def _align(series: list[list[float]]) -> np.ndarray:
        n = min(len(s) for s in series) if series else 0
        return np.array([s[:n] for s in series])

    off_conv = _align([r["convergence"] for r in rows if r["mode"] == "OFF"])
    on_conv = _align([r["convergence"] for r in rows if r["mode"] == "ON"])
    off_var = _align([r["variance"] for r in rows if r["mode"] == "OFF"])
    on_var = _align([r["variance"] for r in rows if r["mode"] == "ON"])

    off_path = [r["path_cost"] for r in rows if r["mode"] == "OFF"]
    on_path = [r["path_cost"] for r in rows if r["mode"] == "ON"]
    off_late = [r["late"] for r in rows if r["mode"] == "OFF"]
    on_late = [r["late"] for r in rows if r["mode"] == "ON"]

    fig, ax = plt.subplots(2, 2, figsize=(11, 8))

    # (0,0) 路径成本：均值±标准差 柱状
    ax[0, 0].bar(["OFF", "ON"], [np.mean(off_path), np.mean(on_path)],
                 yerr=[np.std(off_path), np.std(on_path)], capsize=6,
                 color=["#4c72b0", "#dd8452"])
    ax[0, 0].set_title("路径成本 (path cost, T3)")
    ax[0, 0].set_ylabel("cost")

    # (0,1) 迟到：均值±标准差 柱状
    ax[0, 1].bar(["OFF", "ON"], [np.mean(off_late), np.mean(on_late)],
                 yerr=[np.std(off_late), np.std(on_late)], capsize=6,
                 color=["#4c72b0", "#dd8452"])
    ax[0, 1].set_title("迟到 (late seconds)")

    # (1,0) 收敛曲线：均值±标准差 折线
    if off_conv.size:
        g = np.arange(off_conv.shape[1])
        ax[1, 0].plot(g, off_conv.mean(0), label="OFF", color="#4c72b0")
        ax[1, 0].fill_between(g, off_conv.mean(0) - off_conv.std(0),
                              off_conv.mean(0) + off_conv.std(0), alpha=0.2,
                              color="#4c72b0")
    if on_conv.size:
        g = np.arange(on_conv.shape[1])
        ax[1, 0].plot(g, on_conv.mean(0), label="ON", color="#dd8452")
        ax[1, 0].fill_between(g, on_conv.mean(0) - on_conv.std(0),
                              on_conv.mean(0) + on_conv.std(0), alpha=0.2,
                              color="#dd8452")
    ax[1, 0].set_title("收敛曲线 (cost_history 均值)")
    ax[1, 0].set_xlabel("generation")
    ax[1, 0].legend()

    # (1,1) 种群方差：均值±标准差 折线
    if off_var.size:
        g = np.arange(off_var.shape[1])
        ax[1, 1].plot(g, off_var.mean(0), label="OFF", color="#4c72b0")
        ax[1, 1].fill_between(g, off_var.mean(0) - off_var.std(0),
                              off_var.mean(0) + off_var.std(0), alpha=0.2,
                              color="#4c72b0")
    if on_var.size:
        g = np.arange(on_var.shape[1])
        ax[1, 1].plot(g, on_var.mean(0), label="ON", color="#dd8452")
        ax[1, 1].fill_between(g, on_var.mean(0) - on_var.std(0),
                              on_var.mean(0) + on_var.std(0), alpha=0.2,
                              color="#dd8452")
    ax[1, 1].set_title("种群方差 (gene-space 均值)")
    ax[1, 1].set_xlabel("generation")
    ax[1, 1].legend()

    fig.suptitle(
        f"M2-1 A/B  (UAV={params['n_uavs']}, T={params['n_targets']}, "
        f"pop={params['pop_size']}, gen={params['max_generations']}, "
        f"deadline={params['deadline_s']}s, isolate={params['isolate']})",
        fontsize=12,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def print_table(rows: list[dict], summ: dict) -> None:
    print("\n=== 逐 run 指标 ===")
    hdr = f"{'mode':<4}{'seed':>5}{'fit':>12}{'path':>12}{'late':>10}{'n_hard':>8}{'cov':>7}{'var_fin':>12}{'collapse':>10}"
    print(hdr)
    for r in rows:
        print(f"{r['mode']:<4}{r['seed']:>5}{r['delivered_fitness']:>12.2f}"
              f"{r['path_cost']:>12.2f}{r['late']:>10.2f}{r['n_hard']:>8}{r['coverage']:>7.2f}"
              f"{r['var_final']:>12.4f}{r['var_collapse_ratio']:>10.3f}")
    print("\n=== 聚合 (mean, std) + ON−OFF ===")
    for m, d in summ["delta_ON_minus_OFF"].items():
        off_m, off_s = summ["OFF"][m]
        on_m, on_s = summ["ON"][m]
        print(f"  {m:<22} OFF={off_m:>10.4f}±{off_s:<8.4f} "
              f"ON={on_m:>10.4f}±{on_s:<8.4f}  Δ={d['diff']:>+.4f} "
              f"ratio={d['ratio']:.4f}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="M2-1 段内巡游序回写 3-seed A/B 跑批")
    ap.add_argument("--seeds", default=",".join(map(str, DEFAULTS["seeds"])),
                   help="逗号分隔的随机种子，如 11,22,33")
    ap.add_argument("--n-uavs", type=int, default=DEFAULTS["n_uavs"])
    ap.add_argument("--n-targets", type=int, default=DEFAULTS["n_targets"])
    ap.add_argument("--area", type=float, default=DEFAULTS["area"])
    ap.add_argument("--pop", type=int, default=DEFAULTS["pop_size"])
    ap.add_argument("--gens", type=int, default=DEFAULTS["max_generations"])
    ap.add_argument("--no-isolate", action="store_true",
                   help="走完整生产管线（含交付后 polish / memetic），不清空")
    ap.add_argument("--deadline-s", type=float, default=DEFAULTS["deadline_s"],
                   help=">0 ⇒ 注入时间窗 latest=earliest+该秒数，使排序产生迟到压力（默认 0=无窗）")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "results"))
    args = ap.parse_args()

    params = dict(
        seeds=[int(s) for s in args.seeds.split(",") if s.strip()],
        n_uavs=args.n_uavs,
        n_targets=args.n_targets,
        area=args.area,
        pop_size=args.pop,
        max_generations=args.gens,
        deadline_s=args.deadline_s,
        isolate=not args.no_isolate,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for seed in params["seeds"]:
        tasks, uavs, nests = make_instance(
            seed, params["n_uavs"], params["n_targets"], params["area"],
            deadline_s=params["deadline_s"],
        )
        for on in (False, True):
            tag = "ON " if on else "OFF"
            print(f"[run] seed={seed} M2-1={tag} ...", flush=True)
            rows.append(
                run_trial(tasks, uavs, nests, on=on, seed=seed, params=params)
            )

    summ = summarize(rows)
    write_csv(out_dir / "ab_m21_raw.csv", rows)
    write_summary_csv(out_dir / "ab_m21_summary.csv", summ)
    with open(out_dir / "ab_m21_summary.json", "w") as f:
        json.dump(summ, f, indent=2)
    plot_curves(out_dir / "ab_m21_curves.png", rows, params)
    print_table(rows, summ)
    print(f"\n输出目录：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
