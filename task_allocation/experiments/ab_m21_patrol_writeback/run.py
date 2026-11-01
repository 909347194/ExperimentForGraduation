# -*- coding: utf-8 -*-
"""M2-1 段内巡游序 Lamarckian 回写 · 多实例 3-seed A/B 跑批（设计稿 §4 验收）。

对照：M2-1 关 (OFF) vs 开 (ON)。每个随机实例上，ON/OFF 共用同一组求解器
随机种子（DE 初值一致），差异仅来自 M2-1——构成**配对比较**。跨 ``--n-instances``
个独立几何实例 × ``--seeds`` 个求解器种子，做配对的 ON−OFF 差值统计与胜/平/负计数。

记录设计稿要求的四类指标：

  1. 路径成本 (path cost, T3 = ``est_cost``，含返巢)
  2. 迟到     (late seconds, ``lateness_by_task`` 总和)
  3. 收敛曲线 (``cost_history``，搜索期每代最优适应度；滚动下跨周期拼接)
  4. 种群方差 (代际 ``cost_vector`` 种群方差，来自新增 ``population_callback``)

为干净归因到 M2-1 的「搜索期」效应，默认关闭交付后 polish / memetic
（与 OFF 同口径），使**交付解 == 搜索最优**——M2-1 是唯一变量。

周期 (n_cycles) 语义（见 methods/rolling/horizon.py）：
  - ``--n-cycles 1``（默认）：走原离线选择性 MUAS（所有任务单快照一次性求解，
    绕过 RollingHorizon 的 selection 限选，任务全量进第二阶段）。
  - ``--n-cycles > 1``：走 RollingHorizon 滚动时域，任务均匀分批到多个周期逐周
    期重规划（含 selection 阶段与 UAV 充电回巢）。M2-1 在每周期的 MUAS 内生效。

三档场景：``--scenarios "60:1,40:2,20:3"`` —— 任务数:周期数 逗号分隔。
每档独立落 ``results/t<任务数>/``，便于分档查看与跨档对比。

输出（每档落在各自的 ``results/t<任务数>/``）：
  - ``ab_m21_raw.csv``         逐 run 原始指标（含 instance 列）
  - ``ab_m21_summary.csv``     聚合（mean/std，ON−OFF 差值/比例）
  - ``ab_m21_paired.csv``      配对 ON−OFF 差值 + 胜/平/负计数
  - ``ab_m21_curves.png``      2×2 图（路径成本 / 迟到 / 收敛曲线均值 / 种群方差均值）
  - ``ab_m21_summary.json``    汇总结构化快照

用法：
  python run.py                                       # 默认三档 (60:1,40:2,20:3)
  python run.py --scenarios "60:1"                    # 单档离线 60 任务
  python run.py --scenarios "40:2" --deadline-s 150   # 40 任务 2 周期、注入时间窗
  python run.py --n-instances 10 --seeds 11,22,33
  python run.py --no-isolate                          # 走完整生产管线（含 polish/memetic）
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文图例/标题：优先用系统 Noto CJK 字体（matplotlib 注册为 *JP 子集，
# 含 CJK 统一表意文字，可覆盖简体常用字），避免方框乱码
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO = Path(__file__).resolve().parents[3]  # .../ExperimentForGraduation (仓库根)
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
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    HorizonConfig,
    RollingHorizon,
)
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig  # noqa: E402
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

DEFAULTS = dict(
    seeds=[11, 22],          # 求解器种子（同实例 ON/OFF 共用，构成配对）
    n_instances=3,
    inst_base=2000,          # 实例几何种子基准：第 i 个实例用 inst_base+i
    n_uavs=3,
    area=1000.0,
    pop_size=40,
    max_generations=120,
    deadline_s=150,          # >0 ⇒ 注入时间窗 latest=earliest+该秒数，使排序产生迟到压力
    isolate=True,            # 关闭交付后 polish/memetic，干净归因到 M2-1 搜索期
)

SCENARIOS = "60:1,40:2,20:3"  # 任务数:周期数


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
            # 最大飞行时间派生 = 航程/速度（此处 1e6s 近乎无界，约束不绑定）
            max_time=1e7 / 10.0,
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
# 单次试验（分发器）
# ---------------------------------------------------------------------------


def run_trial(tasks, uavs, nests, *, on: bool, solver_seed: int, instance: int, params: dict) -> dict:
    """单次试验：统一走滚动时域路径（n_cycles>=1；n_cycles==1 即单周期 RollingHorizon，
    等价于离线但三档口径一致，便于跨档对比）。M2-1 在每周期的 MUAS 内生效。"""
    return run_trial_rolling(
        tasks, uavs, nests, on=on, solver_seed=solver_seed,
        instance=instance, params=params,
    )




def run_trial_rolling(tasks, uavs, nests, *, on: bool, solver_seed: int, instance: int, params: dict) -> dict:
    """滚动时域路径（n_cycles>1）：任务均匀分批到多周期逐周期重规划。

    纯几何（dem=None, estimator=None），无需真实 DEM/坐标。ON/OFF 对 tasks/uavs
    做深拷贝隔离——RollingHorizon 的 apply_solution 会就地写 UAV 位姿与 Task.meta，
    不隔离会污染配对。配对仍由 ``inst_base+instance``（几何）与 ``solver_seed``
    （DE 初值）一致保证。
    """
    n_cycles = int(params["n_cycles"])
    isolate = params["isolate"]
    deadline_s = float(params.get("deadline_s", 0.0))
    prob_cfg = MUASProblemConfig()
    if deadline_s > 0:
        prob_cfg = MUASProblemConfig(w_violation=0.01, late_reward_decay_seconds=300.0)

    # 隔离 ON/OFF（rolling 会就地修改输入实体）
    tasks = copy.deepcopy(list(tasks))
    uavs = copy.deepcopy(list(uavs))
    nests = copy.deepcopy(list(nests))

    # 分批到达：把 n_targets 个任务均匀轮流分到 n_cycles 个周期
    rng = np.random.default_rng(int(params.get("inst_base", 1000)) + int(instance))
    order = list(range(len(tasks)))
    rng.shuffle(order)
    per = [order[c::n_cycles] for c in range(n_cycles)]
    arrivals = {c + 1: [tasks[i] for i in per[c]] for c in range(n_cycles)}

    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(list(uavs), current_time=0.0)
    max_range = max((float(u.remaining_range) for u in uavs), default=1e7) or 1e7
    # 放宽第一阶段选择：把 n_sel 上限抬到任务总数、并关闭 capability 能力收敛，
    # 使每周期到达的全部任务都进入第二阶段 MUAS——符合「任务都参与分配」的口径，
    # 让 M2-1 在充分参与的任务集上做 A/B（纯几何无 DEM 下可行性/几何可达性全过）。
    # 注意：horizon.run_cycle 每周期重建 SelectionConfig 时透传 self.sel_cfg 的
    # marginal 与 capability，但会用自己的 replan/故障 forced_ids 覆盖
    # self.sel_cfg.forced_task_ids，故这里必须走 marginal+capability 两条旋钮，
    # 单设 forced_task_ids 无效（实测仍被 n_sel 砍到 ~0.3 覆盖）。
    from task_allocation.methods.selection.pipeline import SelectionConfig
    from task_allocation.methods.selection.marginal_gain import MarginalConfig
    from task_allocation.methods.selection.capability import CapabilityScaleConfig
    sel_cfg = SelectionConfig(
        marginal=MarginalConfig(max_selected=len(tasks)),
        capability=CapabilityScaleConfig(enabled=False),
    )
    stage_cfg = MUASStageConfig(
        lamarckian_patrol_writeback=on,
        cost_polish_enabled=not isolate,
        cost_polish_relocate_enabled=not isolate,
        polish_enabled=not isolate,
        polish_in_loop=False,
    )
    rec = VarianceRecorder()
    solver_cfg = DMDEConfig(
        pop_size=params["pop_size"],
        max_generations=params["max_generations"],
        seed=solver_seed,
        verbose=False,
        population_callback=rec,
    )
    horizon = RollingHorizon(
        pool, fleet, list(nests), dem=None, estimator=None,
        horizon_config=HorizonConfig(
            n_cycles=n_cycles, cycle_length=600.0,
            complete_after_cycle=True, recharge_at_nest=True,
            uav_max_range=max_range,
        ),
        problem_config=prob_cfg,
        stage_config=stage_cfg,
        solver_config=solver_cfg,
        selection_config=sel_cfg,
        event_config=EventTriggerConfig(),
        nest_offset=1_000_000,
    )
    t0 = time.time()
    records = horizon.run(n_cycles=n_cycles, arrivals=arrivals)
    elapsed = time.time() - t0

    # 汇总长程指标（跨周期池化）
    completed: set[int] = set()
    late = 0.0
    n_hard = 0
    path_cost = 0.0
    conv: list[float] = []
    var = list(rec.variances)
    for rec_ in records:
        completed.update(int(t) for t in rec_.completed_ids)
        late += float(sum(rec_.lateness_by_task.values()))
        n_hard += len(rec_.infeasible_ids)
        path_cost += float(sum(t.get("est_cost", 0.0) for t in rec_.tours))
        conv.extend([float(x) for x in rec_.cost_history])
    total = len(tasks)
    collapse = (var[-1] / var[0]) if (var and var[0] > 0) else float("nan")
    # 交付质量代理（与离线 scalar_fitness 同量纲：λ·path + w_violation·late；
    # 这里 λ=1 简化，ON/OFF 同公式，配对差值口径一致）。
    delivered = path_cost + prob_cfg.w_violation * late + 1e6 * n_hard

    return dict(
        mode="ON" if on else "OFF",
        seed=solver_seed,
        instance=instance,
        search_best_fitness=float(conv[-1]) if conv else float("nan"),
        delivered_fitness=delivered,
        path_cost=path_cost,
        late=late,
        n_hard=int(n_hard),
        excess_m=0.0,
        coverage=len(completed) / max(1, total),
        n_evals=int(sum(int(rec_.muas.get("n_evals", 0)) for rec_ in records)),
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


# 配对比较：同一 (instance, solver_seed) 上 ON 相对 OFF 的差值。
# 质量指标（delivered_fitness / path_cost / late）越低越好 ⇒ delta<0 记为 ON「胜」；
# var_collapse_ratio 越低=越早坍缩（多样性损失，非质量收益）⇒ 单独给出 ON<OFF 计数。
_PAIRED_METRICS = ["delivered_fitness", "path_cost", "late", "var_collapse_ratio"]


def paired_summary(rows: list[dict]) -> dict:
    pairs: dict[tuple, dict] = {}
    for r in rows:
        pairs.setdefault((r["instance"], r["seed"]), {})[r["mode"]] = r
    deltas: list[dict] = []
    for m in pairs.values():
        if "OFF" in m and "ON" in m:
            deltas.append({k: m["ON"][k] - m["OFF"][k] for k in _PAIRED_METRICS})
    out: dict = {"n_pairs": len(deltas), "metrics": {}}
    for k in _PAIRED_METRICS:
        vals = [d[k] for d in deltas]
        if k == "var_collapse_ratio":
            # 多样性：ON<OFF=保留更多多样性（好）；ON>OFF=加速坍缩（坏）
            wins = sum(1 for v in vals if v < -1e-9)    # 保留多样性
            losses = sum(1 for v in vals if v > 1e-9)   # 加速坍缩
        else:
            wins = sum(1 for v in vals if v < -1e-9)    # ON 更优（更低）
            losses = sum(1 for v in vals if v > 1e-9)   # ON 更差
        ties = sum(1 for v in vals if abs(v) <= 1e-9)
        out["metrics"][k] = dict(
            mean=float(np.mean(vals)),
            std=float(np.std(vals)),
            wins=wins, losses=losses, ties=ties,
        )
    return out


def write_paired_csv(path: Path, psumm: dict) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "n_pairs", "mean_delta", "std_delta",
                    "wins_ON_better", "losses_ON_worse", "ties"])
        for k, d in psumm["metrics"].items():
            w.writerow([k, psumm["n_pairs"], d["mean"], d["std"],
                        d["wins"], d["losses"], d["ties"]])


def write_csv(path: Path, rows: list[dict]) -> None:
    scalar = ["mode", "seed", "instance", "search_best_fitness", "delivered_fitness",
              "path_cost", "late", "n_hard", "excess_m", "coverage", "n_evals",
              "elapsed_s", "var_initial", "var_final", "var_collapse_ratio"]
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
        f"cycles={params['n_cycles']}, pop={params['pop_size']}, "
        f"gen={params['max_generations']}, deadline={params['deadline_s']}s, "
        f"isolate={params['isolate']})",
        fontsize=12,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


def print_table(rows: list[dict], summ: dict, psumm: dict) -> None:
    print("\n=== 逐 run 指标（前 12 行）===")
    hdr = f"{'mode':<4}{'inst':>6}{'seed':>5}{'fit':>12}{'path':>12}{'late':>10}{'n_hard':>8}{'cov':>7}{'collapse':>10}"
    print(hdr)
    for r in rows[:12]:
        print(f"{r['mode']:<4}{r['instance']:>6}{r['seed']:>5}{r['delivered_fitness']:>12.2f}"
              f"{r['path_cost']:>12.2f}{r['late']:>10.2f}{r['n_hard']:>8}{r['coverage']:>7.2f}"
              f"{r['var_collapse_ratio']:>10.3f}")
    if len(rows) > 12:
        print(f"  … 其余 {len(rows) - 12} 行见 ab_m21_raw.csv")

    print("\n=== 聚合 (mean, std) + ON−OFF（池化）===")
    for m, d in summ["delta_ON_minus_OFF"].items():
        off_m, off_s = summ["OFF"][m]
        on_m, on_s = summ["ON"][m]
        print(f"  {m:<22} OFF={off_m:>10.4f}±{off_s:<8.4f} "
              f"ON={on_m:>10.4f}±{on_s:<8.4f}  Δ={d['diff']:>+.4f} "
              f"ratio={d['ratio']:.4f}")

    print(f"\n=== 配对比较（同实例同种子 ON−OFF，{psumm['n_pairs']} 对）===")
    label = {
        "delivered_fitness": "交付目标(越低越好)",
        "path_cost": "路径成本(越低越好)",
        "late": "迟到秒(越低越好)",
        "var_collapse_ratio": "方差坍缩比(低=更早同质化)",
    }
    for k, d in psumm["metrics"].items():
        print(f"  {k:<20}({label[k]})\n"
              f"      meanΔ={d['mean']:>+.4f}±{d['std']:<.4f}  "
              f"ON胜={d['wins']}  ON负={d['losses']}  平={d['ties']}")


# ---------------------------------------------------------------------------
# 场景解析 / 单场景执行
# ---------------------------------------------------------------------------


def parse_scenarios(s: str) -> list[tuple[int, int]]:
    """解析 ``"60:1,40:2,20:3"`` ⇒ [(60,1),(40,2),(20,3)]。"""
    out: list[tuple[int, int]] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            a, b = part.split(":", 1)
            out.append((int(a), int(b)))
        else:
            out.append((int(part), 1))
    return out


def run_scenario(params: dict, out_dir: Path) -> list[dict]:
    """执行单档场景（固定 n_targets / n_cycles），落结果到 out_dir。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    instance_seeds = [params["inst_base"] + i for i in range(params["n_instances"])]
    for inst_seed in instance_seeds:
        tasks, uavs, nests = make_instance(
            inst_seed, params["n_uavs"], params["n_targets"], params["area"],
            deadline_s=params["deadline_s"],
        )
        for solver_seed in params["seeds"]:
            for on in (False, True):
                tag = "ON " if on else "OFF"
                print(f"[run] T={params['n_targets']} cyc={params['n_cycles']} "
                      f"inst={inst_seed} seed={solver_seed} M2-1={tag} ...", flush=True)
                rows.append(run_trial(
                    tasks, uavs, nests, on=on, solver_seed=solver_seed,
                    instance=inst_seed, params=params,
                ))

    summ = summarize(rows)
    psumm = paired_summary(rows)
    write_csv(out_dir / "ab_m21_raw.csv", rows)
    write_summary_csv(out_dir / "ab_m21_summary.csv", summ)
    write_paired_csv(out_dir / "ab_m21_paired.csv", psumm)
    with open(out_dir / "ab_m21_summary.json", "w") as f:
        json.dump({"pooled": summ, "paired": psumm}, f, indent=2)
    plot_curves(out_dir / "ab_m21_curves.png", rows, params)
    print_table(rows, summ, psumm)
    print(f"\n输出目录：{out_dir}")
    return rows


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="M2-1 段内巡游序回写 多实例 A/B 跑批（含滚动时域周期）")
    ap.add_argument("--seeds", default=",".join(map(str, DEFAULTS["seeds"])),
                   help="逗号分隔的求解器随机种子，如 11,22")
    ap.add_argument("--n-instances", type=int, default=DEFAULTS["n_instances"],
                   help="每个场景的独立几何实例数（同实例上 ON/OFF 配对）")
    ap.add_argument("--inst-base", type=int, default=DEFAULTS["inst_base"],
                   help="实例几何种子基准：第 i 个实例用 inst_base+i")
    ap.add_argument("--n-uavs", type=int, default=DEFAULTS["n_uavs"])
    ap.add_argument("--area", type=float, default=DEFAULTS["area"])
    ap.add_argument("--pop", type=int, default=DEFAULTS["pop_size"])
    ap.add_argument("--gens", type=int, default=DEFAULTS["max_generations"])
    ap.add_argument("--deadline-s", type=float, default=DEFAULTS["deadline_s"],
                   help=">0 ⇒ 注入时间窗 latest=earliest+该秒数，使排序产生迟到压力（默认 150）")
    ap.add_argument("--no-isolate", action="store_true",
                   help="走完整生产管线（含交付后 polish / memetic），不清空")
    ap.add_argument("--scenarios", default=SCENARIOS,
                   help="多档场景：n_targets:n_cycles 逗号分隔，如 60:1,40:2,20:3")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "results"))
    args = ap.parse_args()

    params = dict(
        seeds=[int(s) for s in args.seeds.split(",") if s.strip()],
        n_instances=args.n_instances,
        inst_base=args.inst_base,
        n_uavs=args.n_uavs,
        area=args.area,
        pop_size=args.pop,
        max_generations=args.gens,
        deadline_s=args.deadline_s,
        isolate=not args.no_isolate,
        n_cycles=1,  # 每档在 run_scenario 内覆盖
    )
    out_base = Path(args.out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    scenarios = parse_scenarios(args.scenarios)
    print(f"[main] 场景：{scenarios}  out_dir={out_base}", flush=True)
    if len(scenarios) == 1:
        # 单档：直接写到 out_dir（便于多档并行时各占独立目录）
        n_targets, n_cycles = scenarios[0]
        sc_params = dict(params, n_targets=n_targets, n_cycles=n_cycles)
        print(f"\n################ 场景 T={n_targets} cyc={n_cycles} ################", flush=True)
        run_scenario(sc_params, out_base)
    else:
        for (n_targets, n_cycles) in scenarios:
            sc_params = dict(params, n_targets=n_targets, n_cycles=n_cycles)
            sc_dir = out_base / f"t{n_targets}"
            print(f"\n################ 场景 T={n_targets} cyc={n_cycles} ################", flush=True)
            run_scenario(sc_params, sc_dir)

    print("\n[main] 全部场景完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
