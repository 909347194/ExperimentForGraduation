# -*- coding: utf-8 -*-
"""基因顺序边际贡献消融（ab_order_marginal）。

问题：在现有修复强度下，基因里的「巡游顺序」究竟还有多少边际贡献？

机制事实（代码）：
- 统一基因同时编码「归属 + 段内巡游序」；DE 在代价空间优化两者。
- 解码后 build_solution 按基因出现顺序成航线，但随后 _reorder_for_deadlines
  把每段顺序作为「候选集锦标赛」重排：候选 = [原序(基因), EDD, earliest,
  松弛量, 最近邻]，按 (迟到数, 迟到量, 航程) 字典序选最优。
- 零迟到且 reorder_range_optimize=False（生产默认）时直接早退返回
  ⇒ 零迟到常态下基因顺序完整决定交付航线序。

因此基因顺序的贡献 = 「基因原序」作为候选是否频繁胜出锦标赛。本实验用
order_candidate_mode 控制 _best_order 是否把基因原序纳入候选：
- ``gene`` : 生产口径，候选含基因原序（DE 优化的顺序可进入交付）；
- ``neutral`` : 剔除基因原序候选（= 基因顺序对交付序零贡献的对照）。

各实例同几何种子 + 同求解器种子配对（差异仅来自 order_candidate_mode），
对比交付质量（path_cost / late / coverage / delivered_fitness），并统计
gene 臂的锦标赛胜率分布（gene/EDD/earliest/slack/NN 各自胜出占比），
直接给出「基因顺序在候选集中胜出率」。

输出（每档场景×每档截止落在 results/t<任务数>_c<周期>/d<deadline>/）：
- ab_order_raw.csv 逐 run 原始指标 + 胜率快照
- ab_order_summary.csv 各臂聚合（mean/std）
- ab_order_contrast.csv 配对对照（gene→neutral）差值 + 胜负
- ab_order_winner.csv gene 臂锦标赛胜率分布（合并）
- ab_order_armcmp.png 两臂交付质量对比
- ab_order_winner.png gene 臂锦标赛胜率分布柱
- ab_order_summary.json 结构化快照

顶层 results/ab_order_report.md：边际贡献结论 + 胜率解读 + 诚实上限。

用法：
  python run.py                 # 默认三档场景 + 双截止压力
  python run.py --scenarios "40:2" --deadlines "0"
  python run.py --pop 12 --gens 30 --n-instances 2
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

plt.rcParams["font.family"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

REPO = Path(__file__).resolve().parents[3]
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
from task_allocation.methods.muas.solvers.decode_repair import (  # noqa: E402
    enable_order_tracking,
    order_winner_counts,
    reset_order_tracking,
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
# 两臂定义
# ---------------------------------------------------------------------------

# (臂标签, order_candidate_mode)
ARMS = [
    ("gene", "gene"),        # 生产口径：候选含基因原序
    ("neutral", "neutral"),  # 对照：剔除基因原序候选
]

DEFAULTS = dict(
    seeds=[11, 22],          # 求解器种子（同实例各臂共用，构成配对）
    n_instances=2,
    inst_base=3000,          # 实例几何种子基准：第 i 个实例用 inst_base+i
    n_uavs=3,
    area=1000.0,
    pop_size=12,
    max_generations=30,
    isolate=True,            # 关闭交付后 polish/memetic，干净归因到搜索期定序
)

SCENARIOS = "60:1,40:2,20:3"  # 任务数:周期数
DEADLINES = "0,150"           # 截止压力分层：0=低压力，150=高压力


# ---------------------------------------------------------------------------
# 实例 / 记录器
# ---------------------------------------------------------------------------


def make_instance(seed: int, n_uavs: int, n_targets: int, area: float,
                  deadline_s: float = 0.0):
    """生成纯几何随机 SRP 实例（无 DEM ⇒ 代价退化为三维直线，无需数据文件）。"""
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
            max_time=1e7 / 10.0,
            nest_id=0,
            status=UAVStatus.IDLE,
        )
        for j in range(n_uavs)
    ]
    nests = [Nest(id=0, x=area / 2.0, y=area / 2.0, z=0.0,
                  capacity=10, available=True)]
    return tasks, uavs, nests


class VarianceRecorder:
    """代际 gene-space 种群方差记录器（多样性监控）。"""

    def __init__(self) -> None:
        self.variances: list[float] = []

    def __call__(self, gen: int, cost_vectors: np.ndarray,
                 best_idx: int) -> None:
        self.variances.append(
            float(np.var(cost_vectors, axis=0).mean()))


# ---------------------------------------------------------------------------
# 单次试验（滚动时域路径）
# ---------------------------------------------------------------------------


def run_trial_rolling(tasks, uavs, nests, *, arm: dict, solver_seed: int,
                      instance: int, params: dict) -> dict:
    """滚动时域路径。配对由 inst_base+instance（几何）与 solver_seed（DE 初值）
    一致保证；两臂对 tasks/uavs/nests 做深拷贝隔离。

    gene 臂开启顺序锦标赛胜率记录；neutral 臂关闭（避免污染）。
    """
    n_cycles = int(params["n_cycles"])
    isolate = params["isolate"]
    deadline_s = float(params.get("deadline_s", 0.0))
    prob_cfg = MUASProblemConfig()
    if deadline_s > 0:
        prob_cfg = MUASProblemConfig(
            w_violation=0.01, late_reward_decay_seconds=300.0)

    tasks = copy.deepcopy(list(tasks))
    uavs = copy.deepcopy(list(uavs))
    nests = copy.deepcopy(list(nests))

    from task_allocation.methods.selection.pipeline import SelectionConfig
    from task_allocation.methods.selection.marginal_gain import MarginalConfig
    from task_allocation.methods.selection.capability import CapabilityScaleConfig

    sel_cfg = SelectionConfig(
        marginal=MarginalConfig(max_selected=len(tasks)),
        capability=CapabilityScaleConfig(enabled=False),
    )
    stage_cfg = MUASStageConfig(
        lamarckian=True,
        order_candidate_mode=arm["mode"],
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

    # 胜率记录：仅 gene 臂
    if arm["mode"] == "gene":
        enable_order_tracking(True)
        reset_order_tracking()
    else:
        enable_order_tracking(False)

    max_range = max((float(u.remaining_range) for u in uavs),
                    default=1e7) or 1e7
    horizon = RollingHorizon(
        TaskPool(current_time=0.0), UAVFleet(list(uavs), current_time=0.0),
        list(nests), dem=None, estimator=None,
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
    # 分批到达
    rng = np.random.default_rng(
        int(params.get("inst_base", 3000)) + int(instance))
    order = list(range(len(tasks)))
    rng.shuffle(order)
    per = [order[c::n_cycles] for c in range(n_cycles)]
    arrivals = {c + 1: [tasks[i] for i in per[c]] for c in range(n_cycles)}

    t0 = time.time()
    records = horizon.run(n_cycles=n_cycles, arrivals=arrivals)
    elapsed = time.time() - t0

    winners = dict(order_winner_counts()) if arm["mode"] == "gene" else {}

    completed: set[int] = set()
    late = 0.0
    n_hard = 0
    path_cost = 0.0
    conv: list[float] = []
    genbest: list[float] = []
    var = list(rec.variances)
    for rec_ in records:
        completed.update(int(t) for t in rec_.completed_ids)
        late += float(sum(rec_.lateness_by_task.values()))
        n_hard += len(rec_.infeasible_ids)
        path_cost += float(sum(t.get("est_cost", 0.0) for t in rec_.tours))
        conv.extend([float(x) for x in rec_.cost_history])
        genbest.extend([float(x) for x in rec_.muas.get("gen_best_history", [])])
    total = len(tasks)
    collapse = (var[-1] / var[0]) if (var and var[0] > 0) else float("nan")
    w_viol = getattr(prob_cfg, "w_violation", None)
    if w_viol is None:
        w_viol = 0.01
    delivered = path_cost + w_viol * late + 1e6 * n_hard

    return dict(
        arm=arm["_label"],
        mode=arm["mode"],
        deadline_s=deadline_s,
        seed=solver_seed,
        instance=instance,
        search_best_fitness=float(conv[-1]) if conv else float("nan"),
        de_best_final=float(genbest[-1]) if genbest else float("nan"),
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
        gen_best=genbest,
        variance=var,
        winners=winners,
    )


# ---------------------------------------------------------------------------
# 聚合 / 对照
# ---------------------------------------------------------------------------


def _mean_std(xs):
    xs = [x for x in xs if math.isfinite(x)]
    if not xs:
        return float("nan"), float("nan")
    return float(np.mean(xs)), float(np.std(xs))


def summarize_arms(rows: list[dict]) -> dict:
    metrics = ["search_best_fitness", "de_best_final", "delivered_fitness",
               "path_cost", "late", "n_hard", "coverage", "var_initial",
               "var_final", "var_collapse_ratio"]
    arms = sorted({r["arm"] for r in rows})
    out: dict = {}
    for arm in arms:
        sub = [r for r in rows if r["arm"] == arm]
        out[arm] = {m: _mean_std([r[m] for r in sub]) for m in metrics}
    return out


_PAIRED_METRICS = ["delivered_fitness", "path_cost", "late",
                   "var_collapse_ratio"]


def contrast_pair(rows: list[dict], a: str, b: str) -> dict:
    """配对对照：b 相对 a 的差值（a→b = 加/减某项）。delta = b − a，越低越好。"""
    pairs: dict[tuple, dict] = {}
    for r in rows:
        pairs.setdefault((r["instance"], r["seed"], r["deadline_s"]),
                         {})[r["arm"]] = r
    deltas: list[dict] = []
    for m in pairs.values():
        if a in m and b in m:
            deltas.append({k: m[b][k] - m[a][k] for k in _PAIRED_METRICS})
    out: dict = {"pair": f"{a}→{b}", "n_pairs": len(deltas), "metrics": {}}
    for k in _PAIRED_METRICS:
        vals = [d[k] for d in deltas]
        wins = sum(1 for v in vals if v < -1e-9)
        losses = sum(1 for v in vals if v > 1e-9)
        ties = sum(1 for v in vals if abs(v) <= 1e-9)
        out["metrics"][k] = dict(
            mean=float(np.mean(vals)), std=float(np.std(vals)),
            wins=wins, losses=losses, ties=ties,
        )
    return out


def aggregate_winners(rows: list[dict]) -> dict:
    """合并 gene 臂所有 run 的锦标赛胜率计数。"""
    total: dict[str, int] = {}
    n_route_decisions = 0
    for r in rows:
        for name, c in (r.get("winners") or {}).items():
            total[name] = total.get(name, 0) + c
            n_route_decisions += c
    dist = {k: (v / n_route_decisions if n_route_decisions else 0.0)
            for k, v in total.items()}
    return dict(total=total, dist=dist, n_route_decisions=n_route_decisions)


# ---------------------------------------------------------------------------
# CSV / JSON 输出
# ---------------------------------------------------------------------------


def write_raw_csv(path: Path, rows: list[dict]) -> None:
    scalar = ["arm", "mode", "deadline_s", "seed", "instance",
              "search_best_fitness", "de_best_final", "delivered_fitness",
              "path_cost", "late", "n_hard", "coverage", "n_evals",
              "elapsed_s", "var_collapse_ratio"]
    winner_keys = ["gene", "EDD", "earliest", "slack", "NN"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=scalar + ["win_" + k for k in winner_keys])
        w.writeheader()
        for r in rows:
            row = {k: r.get(k) for k in scalar}
            for k in winner_keys:
                row["win_" + k] = (r.get("winners") or {}).get(k, 0)
            w.writerow(row)


def write_summary_csv(path: Path, summ: dict) -> None:
    metrics = ["search_best_fitness", "de_best_final", "delivered_fitness",
               "path_cost", "late", "n_hard", "coverage", "var_initial",
               "var_final", "var_collapse_ratio"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["arm", "metric", "mean", "std"])
        for arm, d in summ.items():
            for m in metrics:
                w.writerow([arm, m, d[m][0], d[m][1]])


def write_contrast_csv(path: Path, contrasts: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pair", "metric", "n_pairs", "mean_delta", "std_delta",
                    "wins_b_better", "losses_b_worse", "ties"])
        for c in contrasts:
            for k, d in c["metrics"].items():
                w.writerow([c["pair"], k, c["n_pairs"], d["mean"], d["std"],
                            d["wins"], d["losses"], d["ties"]])


def write_winner_csv(path: Path, agg: dict) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "count", "share"])
        for k in ["gene", "EDD", "earliest", "slack", "NN"]:
            c = agg["total"].get(k, 0)
            w.writerow([k, c, round(agg["dist"].get(k, 0.0), 4)])


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------


def plot_armcmp(out_png: Path, summ: dict, agg_win: dict,
                params: dict) -> None:
    """两臂交付质量对比（path_cost / late / coverage 归一化柱）。"""
    arms = [a[0] for a in ARMS]
    metrics = [("path_cost", "航程成本 (km)"), ("late", "迟到总量 (s)"),
               ("coverage", "完成覆盖率")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
    colors = {"gene": "#4c72b0", "neutral": "#dd8452"}
    for ax, (mk, label) in zip(axes, metrics):
        vals = [summ[a][mk][0] for a in arms]
        errs = [summ[a][mk][1] for a in arms]
        ax.bar(arms, vals, yerr=errs,
               color=[colors[a] for a in arms], alpha=0.85, capsize=4)
        ax.set_title(label, fontsize=10)
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=8)
    fig.suptitle(
        f"基因顺序边际贡献：gene(含基因序候选) vs neutral(剔除基因序) "
        f"T={params['n_targets']}, cyc={params['n_cycles']}, "
        f"pop={params['pop_size']}, gen={params['max_generations']}, "
        f"deadline={params['deadline_s']}s",
        fontsize=11,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(out_png, dpi=130)


def plot_winner(out_png: Path, agg_win: dict, params: dict) -> None:
    """gene 臂锦标赛胜率分布柱。"""
    labels = ["gene", "EDD", "earliest", "slack", "NN"]
    shares = [agg_win["dist"].get(k, 0.0) for k in labels]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    colors = ["#4c72b0" if k == "gene" else "#55a868" for k in labels]
    ax.bar(labels, [s * 100 for s in shares], color=colors, alpha=0.9)
    for i, s in enumerate(shares):
        ax.text(i, s * 100, f"{s*100:.1f}%",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("胜出占比 (%)")
    ax.set_title(
        f"gene 臂：交付航线序由谁胜出锦标赛"
        f"（n={agg_win['n_route_decisions']} 次定序决策）\n"
        f"T={params['n_targets']}, cyc={params['n_cycles']}, "
        f"deadline={params['deadline_s']}s",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def build_report(out_dir: Path, all_rows, contrasts, agg_win, params) -> None:
    lines = []
    lines.append("# 基因顺序边际贡献消融报告（ab_order_marginal）\n")
    lines.append("## 1. 问题与机制")
    lines.append(
        "基因同时编码「归属 + 段内巡游序」。解码后 `_reorder_for_deadlines` "
        "把每段顺序作为候选集锦标赛重排：候选 = [原序(基因), EDD, earliest, "
        "松弛量, 最近邻]，按 (迟到数, 迟到量, 航程) 字典序选最优。零迟到且 "
        "`reorder_range_optimize=False`(生产默认) 时直接早退返回基因原序 ⇒ "
        "零迟到常态下基因顺序完整决定交付航线序。\n"
    )
    lines.append(
        "本实验用 `order_candidate_mode` 控制是否把基因原序纳入候选：\n"
        "- **gene**：生产口径，候选含基因原序（DE 优化的顺序可进入交付）；\n"
        "- **neutral**：剔除基因原序候选（= 基因顺序对交付序零贡献的对照）。\n"
        "两臂同几何种子 + 同求解器种子配对，差异仅来自该开关。\n"
    )
    lines.append(
        "## 2. 配对对照（gene → neutral，delta = neutral − gene，越低越好）\n")
    lines.append("| 指标 | meanΔ | stdΔ | gene胜(更优) | neutral胜(更差) | 平 |")
    lines.append("|---|---|---|---|---|---|")
    for c in contrasts:
        for k, d in c["metrics"].items():
            lines.append(
                f"| {k} | {d['mean']:.4f} | {d['std']:.4f} | {d['wins']} | "
                f"{d['losses']} | {d['ties']} |"
            )
    lines.append("")
    lines.append("## 3. gene 臂锦标赛胜率分布（基因序在候选集中胜出率）\n")
    lines.append(f"- 总定序决策次数：{agg_win['n_route_decisions']}")
    lines.append("| 候选 | 胜出次数 | 占比 |")
    lines.append("|---|---|---|")
    for k in ["gene", "EDD", "earliest", "slack", "NN"]:
        lines.append(
            f"| {k} | {agg_win['total'].get(k, 0)} | "
            f"{agg_win['dist'].get(k, 0.0)*100:.1f}% |")
    lines.append("")
    lines.append("## 4. 结论（诚实口径）")
    c = next((x for x in contrasts if x["pair"] == "gene→neutral"), None)
    if c:
        pc = c["metrics"].get("path_cost", {})
        mean_delta = pc.get("mean", 0.0)
        if abs(mean_delta) < 1e-6 and pc.get("wins", 0) == 0 and pc.get("losses", 0) == 0:
            verdict = ("**基因顺序边际贡献≈0**：neutral 与 gene 在航程成本上逐位等价，"
                       "基因顺序在现有修复强度下对交付成本无可见贡献。")
        elif mean_delta > 1e-6:
            verdict = (f"**gene 臂在航程上平均更优 {mean_delta:.4f} km**"
                       "（neutral 更差），基因顺序有正向但有限的边际贡献。")
        else:
            verdict = (f"**neutral 臂在航程上平均更优 {-mean_delta:.4f} km**"
                       "（gene 更差）——说明生产早退返回的基因序并非最短，"
                       "朴素规范序反而更短。")
        lines.append(f"- 自动判定：{verdict}")
        gene_win_share = agg_win["dist"].get("gene", 0.0)
        lines.append(
            f"- gene 臂中，基因原序在锦标赛胜出占比 = {gene_win_share*100:.1f}%。"
            "该占比越高，说明 DE 优化的顺序越常优于规范序；越低，"
            "说明基因顺序多被规范序覆盖。"
        )
    lines.append(
        "\n**诚实上限说明**：① neutral 对照的代价是「多跑一次全候选评估」"
        "（零迟到早退被禁用），故 neutral 臂求解略慢；② 本开关只干预 "
        "`_best_order` 候选集，`_consolidate/_reflow` 等改派算子仍按原逻辑"
        "搬运任务（它们改变任务集、间接影响顺序），不在本开关范围内；"
        "③ 若 gene 胜出率与航程 Δ 都近 0，则「基因顺序」在当前修复强度下"
        "的边际贡献应如实写为讨论/展望，不当主贡献。"
    )
    (out_dir / "ab_order_report.md").write_text(
        "\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def parse_scenarios(s: str):
    out = []
    for part in s.split(","):
        part = part.strip()
        if ":" in part:
            t, c = part.split(":")
            out.append((int(t), int(c)))
        else:
            out.append((int(part), 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default=SCENARIOS)
    ap.add_argument("--deadlines", default=DEADLINES)
    ap.add_argument("--seeds",
                    default=",".join(str(x) for x in DEFAULTS["seeds"]))
    ap.add_argument("--n-instances", type=int,
                    default=DEFAULTS["n_instances"])
    ap.add_argument("--inst-base", type=int, default=DEFAULTS["inst_base"])
    ap.add_argument("--n-uavs", type=int, default=DEFAULTS["n_uavs"])
    ap.add_argument("--area", type=float, default=DEFAULTS["area"])
    ap.add_argument("--pop", type=int, default=DEFAULTS["pop_size"])
    ap.add_argument("--gens", type=int, default=DEFAULTS["max_generations"])
    ap.add_argument("--no-isolate", action="store_true")
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--combo-tag", default="")
    args = ap.parse_args()

    scenarios = parse_scenarios(args.scenarios)
    deadlines = [float(x) for x in args.deadlines.split(",")]
    seeds = [int(x) for x in args.seeds.split(",")]
    isolate = not args.no_isolate

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []

    # 单组合（场景×截止）内跑两臂
    for n_targets, n_cycles in scenarios:
        for deadline_s in deadlines:
            combo_dir = out_root / f"t{n_targets}_c{n_cycles}" / f"d{int(deadline_s)}"
            combo_dir.mkdir(parents=True, exist_ok=True)
            rows: list[dict] = []
            for instance in range(args.n_instances):
                base_seed = args.inst_base + instance
                tasks, uavs, nests = make_instance(
                    base_seed, args.n_uavs, n_targets, args.area, deadline_s)
                for arm_label, mode in ARMS:
                    arm = dict(_label=arm_label, mode=mode)
                    for solver_seed in seeds:
                        params = dict(
                            n_targets=n_targets, n_cycles=n_cycles,
                            isolate=isolate, deadline_s=deadline_s,
                            pop_size=args.pop, max_generations=args.gens,
                            inst_base=args.inst_base,
                        )
                        r = run_trial_rolling(
                            tasks, uavs, nests, arm=arm,
                            solver_seed=solver_seed,
                            instance=instance, params=params,
                        )
                        rows.append(r)
            # 聚合
            summ = summarize_arms(rows)
            contrast = contrast_pair(rows, "gene", "neutral")
            agg_win = aggregate_winners(rows)
            # 输出
            write_raw_csv(combo_dir / "ab_order_raw.csv", rows)
            write_summary_csv(combo_dir / "ab_order_summary.csv", summ)
            write_contrast_csv(combo_dir / "ab_order_contrast.csv", [contrast])
            write_winner_csv(combo_dir / "ab_order_winner.csv", agg_win)
            try:
                plot_armcmp(
                    combo_dir / "ab_order_armcmp.png", summ, agg_win, dict(
                        n_targets=n_targets, n_cycles=n_cycles,
                        pop_size=args.pop, max_generations=args.gens,
                        deadline_s=deadline_s))
                plot_winner(
                    combo_dir / "ab_order_winner.png", agg_win, dict(
                        n_targets=n_targets, n_cycles=n_cycles,
                        deadline_s=deadline_s))
            except Exception as e:  # noqa: BLE001
                print(f" [warn] plot failed: {e}")
            # 结构化快照
            (combo_dir / "ab_order_summary.json").write_text(
                json.dumps(dict(summary=summ, contrast=contrast,
                                winners=agg_win),
                           indent=2, ensure_ascii=False),
                encoding="utf-8")
            all_rows.extend(rows)
            print(
                f"[done] t{n_targets}_c{n_cycles}/d{int(deadline_s)}: "
                f"pathΔ(neutral−gene)="
                f"{contrast['metrics']['path_cost']['mean']:.3f}, "
                f"gene_win_share="
                f"{agg_win['dist'].get('gene', 0.0)*100:.1f}%")

    # 顶层报告（合并全部组合）
    full_summ = summarize_arms(all_rows)
    full_contrast = contrast_pair(all_rows, "gene", "neutral")
    full_win = aggregate_winners(all_rows)
    write_summary_csv(out_root / "ab_order_summary.csv", full_summ)
    write_contrast_csv(out_root / "ab_order_contrast.csv", [full_contrast])
    write_winner_csv(out_root / "ab_order_winner.csv", full_win)
    build_report(out_root, all_rows, [full_contrast], full_win, dict(
        n_targets="all", n_cycles="all", pop_size=args.pop,
        max_generations=args.gens, deadline_s="all"))
    print(f"[ok] report -> {out_root / 'ab_order_report.md'}")


if __name__ == "__main__":
    main()
