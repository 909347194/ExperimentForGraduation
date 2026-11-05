# -*- coding: utf-8 -*-
"""优化工作归属消融（ab_optimization_attribution）。

问题：现有算法把多少优化工作交给了「进化（DE 搜索）」，多少交给了
「修复链 + 精修（polish / 基因序锦标赛）」？

方法：三级嵌套消融，固定下游、单独关上游来量边际贡献：
  - C repair_only : gens=0（无进化搜索）+ 精修全关（polish/cost_polish/
                    cost_polish_relocate 关闭）
                    => 修复链裸基线（不含任何搜索/后处理优化）
  - A evolution   : gens=正常 + 精修全关
                    => 进化边际 = A - C（在修复链之上，搜索额外榨出的改进）
  - B full        : gens=正常 + 精修全开（生产默认）
                    => 精修边际 = B - A（在 进化+修复 之上，后处理再榨出的改进）

则：
  总改进(相对修复链裸基线)   = C - B  （>0 表示最终比裸基线更优）
  进化占比  = (C - A) / (C - B)
  精修占比  = (A - B) / (C - B)   （两者相加 = 1）
修复链本身的贡献是「地板」（C），因它是可行性必需的，无法归零，单独报告。

各实例同几何种子 + 同求解器种子配对（差异仅来自臂配置），对比交付质量
（path_cost / late / coverage / delivered_fitness / n_hard / 种群方差塌缩）。

输出（每档场景x每档截止落在 results/t<任务数>_c<周期>/d<deadline>/）：
  - ab_attr_raw.csv            逐 run 原始指标
  - ab_attr_summary.csv        各臂聚合（mean/std）
  - ab_attr_contrast.csv       配对对照（C->A 进化、A->B 精修）
  - ab_attr_attribution.csv    归属分解（总改进 / 进化边际 / 精修边际 / 占比）
  - ab_attr_armcmp.png         三臂交付质量对比
  - ab_attr_attribution.png    归属饼/柱
  - ab_attr_summary.json       结构化快照
顶层 results/ab_attr_report.md：归属结论 + 诚实口径。

用法：
  python run.py                                       # 默认三档场景 + 双截止压力
  python run.py --scenarios "40:2" --deadlines "0,150"
  python run.py --pop 12 --gens 40 --n-instances 2 --seeds 11
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

REPO = Path(__file__).resolve().parents[3]  # .../ExperimentForGraduation (仓库根)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    Nest,
    Task,
    UAV,
    UAVStatus,
)
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
# 三臂定义（嵌套）
# ---------------------------------------------------------------------------

# (臂标签, gens_override(0=无进化,None=用 --gens), refine(bool))
ARMS = [
    ("repair_only", 0, False),   # 无进化 + 无精修：修复链裸基线
    ("evolution", None, False),  # 进化 + 修复链，无精修
    ("full", None, True),        # 进化 + 修复链 + 精修（生产）
]

DEFAULTS = dict(
    seeds=[11, 22],
    n_instances=2,
    inst_base=5000,
    n_uavs=3,
    area=1000.0,
    pop_size=12,
    max_generations=40,
)

SCENARIOS = "60:1,40:2,20:3"
DEADLINES = "0,150"


# ---------------------------------------------------------------------------
# 实例 / 记录器
# ---------------------------------------------------------------------------


def make_instance(seed: int, n_uavs: int, n_targets: int, area: float, deadline_s: float = 0.0):
    """纯几何随机 SRP 实例（无 DEM => 代价退化为三维直线，无需数据文件）。"""
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
    nests = [Nest(id=0, x=area / 2.0, y=area / 2.0, z=0.0, capacity=10, available=True)]
    return tasks, uavs, nests


class VarianceRecorder:
    """代际 gene-space 种群方差记录器（多样性监控）。"""

    def __init__(self) -> None:
        self.variances: list[float] = []

    def __call__(self, gen: int, cost_vectors: np.ndarray, best_idx: int) -> None:
        self.variances.append(float(np.var(cost_vectors, axis=0).mean()))


# ---------------------------------------------------------------------------
# 单次试验（滚动时域路径）
# ---------------------------------------------------------------------------


def run_trial_rolling(tasks, uavs, nests, *, arm: dict, solver_seed: int, instance: int, params: dict) -> dict:
    n_cycles = int(params["n_cycles"])
    deadline_s = float(params.get("deadline_s", 0.0))
    gens_override = arm["gens_override"]
    refine = arm["refine"]

    prob_cfg = MUASProblemConfig()
    if deadline_s > 0:
        prob_cfg = MUASProblemConfig(w_violation=0.01, late_reward_decay_seconds=300.0)

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
        cost_polish_enabled=refine,
        cost_polish_relocate_enabled=refine,
        polish_enabled=refine,
        polish_in_loop=False,
    )
    rec = VarianceRecorder()
    solver_cfg = DMDEConfig(
        pop_size=params["pop_size"],
        max_generations=(params["max_generations"] if gens_override is None
                         else gens_override),
        seed=solver_seed,
        verbose=False,
        population_callback=rec if gens_override else None,
    )

    max_range = max((float(u.remaining_range) for u in uavs), default=1e7) or 1e7
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
    rng = np.random.default_rng(int(params.get("inst_base", 5000)) + int(instance))
    order = list(range(len(tasks)))
    rng.shuffle(order)
    per = [order[c::n_cycles] for c in range(n_cycles)]
    arrivals = {c + 1: [tasks[i] for i in per[c]] for c in range(n_cycles)}

    t0 = time.time()
    records = horizon.run(n_cycles=n_cycles, arrivals=arrivals)
    elapsed = time.time() - t0

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
        deadline_s=deadline_s,
        seed=solver_seed,
        instance=instance,
        gens=solver_cfg.max_generations,
        refine=refine,
        search_best_fitness=float(conv[-1]) if conv else float("nan"),
        de_best_final=float(genbest[-1]) if genbest else float("nan"),
        delivered_fitness=delivered,
        path_cost=path_cost,
        late=late,
        n_hard=int(n_hard),
        coverage=len(completed) / max(1, total),
        n_evals=int(sum(int(rec_.muas.get("n_evals", 0)) for rec_ in records)),
        elapsed_s=elapsed,
        var_initial=var[0] if var else float("nan"),
        var_final=var[-1] if var else float("nan"),
        var_collapse_ratio=collapse,
    )


# ---------------------------------------------------------------------------
# 聚合 / 对照 / 归属
# ---------------------------------------------------------------------------


def _mean_std(xs):
    xs = [x for x in xs if math.isfinite(x)]
    if not xs:
        return float("nan"), float("nan")
    return float(np.mean(xs)), float(np.std(xs))


def summarize_arms(rows: list[dict]) -> dict:
    metrics = ["search_best_fitness", "de_best_final", "delivered_fitness", "path_cost",
               "late", "n_hard", "coverage", "var_initial", "var_final",
               "var_collapse_ratio"]
    arms = [a[0] for a in ARMS]
    out: dict = {}
    for arm in arms:
        sub = [r for r in rows if r["arm"] == arm]
        out[arm] = {m: _mean_std([r[m] for r in sub]) for m in metrics}
    return out


_PAIRED_METRICS = ["delivered_fitness", "path_cost", "late", "coverage", "n_hard"]


def contrast_pair(rows: list[dict], a: str, b: str) -> dict:
    """配对对照：b 相对 a 的差值（a->b），delta = b - a，越低越好。"""
    pairs: dict[tuple, dict] = {}
    for r in rows:
        pairs.setdefault((r["instance"], r["seed"], r["deadline_s"]), {})[r["arm"]] = r
    deltas: list[dict] = []
    for m in pairs.values():
        if a in m and b in m:
            deltas.append({k: m[b][k] - m[a][k] for k in _PAIRED_METRICS})
    out: dict = {"pair": f"{a}->{b}", "n_pairs": len(deltas), "metrics": {}}
    for k in _PAIRED_METRICS:
        vals = [d[k] for d in deltas]
        out["metrics"][k] = dict(
            mean=float(np.mean(vals)), std=float(np.std(vals)),
            wins=sum(1 for v in vals if v < -1e-9),
            losses=sum(1 for v in vals if v > 1e-9),
            ties=sum(1 for v in vals if abs(v) <= 1e-9),
        )
    return out


def attribute(rows: list[dict]) -> dict:
    """把总改进分解为进化边际 / 精修边际 / 占比。

    基线 C=repair_only，A=evolution，B=full。
    总改进(相对裸基线) = C - B（>0 即最终更优）。
    进化边际 = C - A；精修边际 = A - B。
    进化占比 = (C-A)/(C-B)；精修占比 = (A-B)/(C-B)。
    """
    summ = summarize_arms(rows)
    out: dict = {"metrics": {}, "baseline_repair_only": {}, "final_full": {}}
    for k in ["delivered_fitness", "path_cost", "late"]:
        c = summ["repair_only"][k][0]
        a = summ["evolution"][k][0]
        b = summ["full"][k][0]
        total_imp = c - b          # >0 表示最终比裸基线更优
        evo_margin = c - a         # 进化额外榨出的改进
        ref_margin = a - b         # 精修额外榨出的改进
        if abs(total_imp) > 1e-9:
            evo_share = evo_margin / total_imp
            ref_share = ref_margin / total_imp
        else:
            evo_share = float("nan")
            ref_share = float("nan")
        out["metrics"][k] = dict(
            repair_only=c, evolution=a, full=b,
            total_improvement=total_imp,
            evolution_margin=evo_margin, refinement_margin=ref_margin,
            evolution_share=evo_share, refinement_share=ref_share,
        )
    out["baseline_repair_only"] = {
        k: summ["repair_only"][k][0] for k in ["coverage", "n_hard"]}
    out["final_full"] = {k: summ["full"][k][0] for k in ["coverage", "n_hard"]}
    return out


# ---------------------------------------------------------------------------
# CSV / JSON 输出
# ---------------------------------------------------------------------------


def write_raw_csv(path: Path, rows: list[dict]) -> None:
    scalar = ["arm", "deadline_s", "seed", "instance", "gens", "refine",
              "search_best_fitness", "de_best_final", "delivered_fitness",
              "path_cost", "late", "n_hard", "coverage", "n_evals",
              "elapsed_s", "var_collapse_ratio"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=scalar)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in scalar})


def write_summary_csv(path: Path, summ: dict) -> None:
    metrics = ["search_best_fitness", "de_best_final", "delivered_fitness", "path_cost",
               "late", "n_hard", "coverage", "var_initial", "var_final",
               "var_collapse_ratio"]
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


def write_attribution_csv(path: Path, attr: dict) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "repair_only", "evolution", "full",
                    "total_improvement", "evolution_margin", "refinement_margin",
                    "evolution_share", "refinement_share"])
        for k, d in attr["metrics"].items():
            w.writerow([k, d["repair_only"], d["evolution"], d["full"],
                        d["total_improvement"], d["evolution_margin"],
                        d["refinement_margin"], d["evolution_share"],
                        d["refinement_share"]])


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------


def plot_armcmp(out_png: Path, summ: dict, params: dict) -> None:
    arms = [a[0] for a in ARMS]
    metrics = [("path_cost", "航程成本 (km)"), ("late", "迟到总量 (s)"),
               ("coverage", "完成覆盖率")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
    colors = {"repair_only": "#c44e52", "evolution": "#4c72b0", "full": "#55a868"}
    for ax, (mk, label) in zip(axes, metrics):
        vals = [summ[a][mk][0] for a in arms]
        errs = [summ[a][mk][1] for a in arms]
        ax.bar(arms, vals, yerr=errs, color=[colors[a] for a in arms], alpha=0.85, capsize=4)
        ax.set_title(label, fontsize=10)
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=8)
    fig.suptitle(
        f"优化工作归属：修复链裸基线 vs 进化 vs 全开  "
        f"T={params['n_targets']}, cyc={params['n_cycles']}, "
        f"pop={params['pop_size']}, gen={params['max_generations']}, "
        f"deadline={params['deadline_s']}s",
        fontsize=11,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(out_png, dpi=130)


def plot_attribution(out_png: Path, attr: dict, params: dict) -> None:
    """归属分解：进化 vs 精修 占总改进的比例（path_cost / late 双轴）。"""
    keys = ["path_cost", "late"]
    evo = [attr["metrics"][k]["evolution_share"] * 100 for k in keys]
    ref = [attr["metrics"][k]["refinement_share"] * 100 for k in keys]
    fig, ax = plt.subplots(figsize=(7, 3.8))
    x = np.arange(len(keys))
    ax.bar(x - 0.2, evo, width=0.4, label="进化 (DE 搜索)", color="#4c72b0")
    ax.bar(x + 0.2, ref, width=0.4, label="精修 (polish+定序)", color="#55a868")
    for i in range(len(keys)):
        ax.text(i - 0.2, evo[i], f"{evo[i]:.1f}%", ha="center", va="bottom", fontsize=9)
        ax.text(i + 0.2, ref[i], f"{ref[i]:.1f}%", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([k for k in keys])
    ax.set_ylabel("占总改进的比例 (%)")
    ax.set_title(
        f"优化工作归属：进化 vs 精修（相对修复链裸基线）\n"
        f"T={params['n_targets']}, cyc={params['n_cycles']}, deadline={params['deadline_s']}s",
        fontsize=10,
    )
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def build_report(out_dir: Path, all_rows, contrasts, attr, params) -> None:
    lines = []
    lines.append("# 优化工作归属消融报告（ab_optimization_attribution）\n")
    lines.append("## 1. 问题与三级嵌套设计\n")
    lines.append(
        "问题：现有算法把多少优化工作交给了「进化（DE 搜索）」，多少交给了"
        "「修复链 + 精修（polish / 基因序锦标赛）」？\n"
    )
    lines.append(
        "三级嵌套消融（固定下游、单独关上游量边际贡献）：\n"
        "- **repair_only**：`gens=0`（无进化搜索）+ 精修全关 => 修复链裸基线 C；\n"
        "- **evolution**：`gens=正常` + 精修全关 => 进化边际 = C - A；\n"
        "- **full**：`gens=正常` + 精修全开（生产默认）=> 精修边际 = A - B。\n"
        "总改进(相对裸基线) = C - B；**进化占比 = (C-A)/(C-B)，精修占比 = (A-B)/(C-B)**。"
        "修复链本身是可行性必需的地板 C，无法归零，单独报告。\n"
    )
    lines.append("## 2. 归属分解（各指标）\n")
    lines.append("| 指标 | 裸基线 C | 进化 A | 全开 B | 总改进 C-B | "
                 "进化边际 C-A | 精修边际 A-B | 进化占比 | 精修占比 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for k, d in attr["metrics"].items():
        lines.append(
            f"| {k} | {d['repair_only']:.2f} | {d['evolution']:.2f} | {d['full']:.2f} | "
            f"{d['total_improvement']:.2f} | {d['evolution_margin']:.2f} | "
            f"{d['refinement_margin']:.2f} | "
            f"{d['evolution_share']*100:.1f}% | {d['refinement_share']*100:.1f}% |"
        )
    lines.append("")
    lines.append("## 3. 配对对照\n")
    for c in contrasts:
        lines.append(f"### {c['pair']}（delta = 后者 - 前者，越低越好，n={c['n_pairs']}）")
        lines.append("| 指标 | meanΔ | stdΔ | 后者胜(更优) | 后者负(更差) | 平 |")
        lines.append("|---|---|---|---|---|---|")
        for k, d in c["metrics"].items():
            lines.append(
                f"| {k} | {d['mean']:.4f} | {d['std']:.4f} | {d['wins']} | "
                f"{d['losses']} | {d['ties']} |"
            )
        lines.append("")
    lines.append("## 4. 可行性保全（修复链地板）\n")
    lines.append(f"- 裸基线 repair_only：coverage={attr['baseline_repair_only'].get('coverage', float('nan')):.4f}, "
                 f"n_hard={attr['baseline_repair_only'].get('n_hard', float('nan')):.2f}")
    lines.append(f"- 全开 full：coverage={attr['final_full'].get('coverage', float('nan')):.4f}, "
                 f"n_hard={attr['final_full'].get('n_hard', float('nan')):.2f}")
    lines.append("")
    lines.append("## 5. 结论（诚实口径）\n")
    pc = attr["metrics"].get("path_cost", {})
    if pc:
        evo_s = pc.get("evolution_share", float("nan"))
        ref_s = pc.get("refinement_share", float("nan"))
        tot = pc.get("total_improvement", 0.0)
        if abs(tot) < 1e-6:
            lines.append(
                "- **总改进约0**：修复链裸基线已经和全开管线在航程上逐位等价 => "
                "进化与精修在这一体制下几乎没有额外可榨的航程（可能已完成度已近局部最优）。")
        else:
            lines.append(
                f"- 在航程指标上，相对修复链裸基线的总改进 = {tot:.2f} km；其中"
                f"**进化（DE 搜索）贡献 {evo_s*100:.1f}%，精修（polish+定序）贡献 {ref_s*100:.1f}%**。"
            )
        lines.append(
            "- 进化占比高 => 算法把主要优化工作交给了搜索；精修占比高 => 主要交给了后处理。"
            "若两者占比都低而裸基线已很优，则修复链的结构性构造（覆盖优先装载 + 可行性修复）"
            "本身就完成了大部分工作，搜索/精修是边际润色。"
        )
    lines.append(
        "\n**方法与诚实上限**：① 三级嵌套的边际贡献在「其余固定」下度量，"
        "进化与精修存在交互（搜索 landscape 受精修影响），故占比为边际口径、非严格可加；"
        "② 修复链地板 C 不可归零（可行性必需），故本实验报告的是「相对 C 的改进如何分配」，"
        "而非「三块占总工作的绝对份额」；③ 每格 n 偏小（受单次求解成本约束），"
        "绝对量级有噪声，方向与占比稳健。"
    )
    (out_dir / "ab_attr_report.md").write_text("\n".join(lines), encoding="utf-8")


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
    ap.add_argument("--seeds", default=",".join(str(x) for x in DEFAULTS["seeds"]))
    ap.add_argument("--n-instances", type=int, default=DEFAULTS["n_instances"])
    ap.add_argument("--inst-base", type=int, default=DEFAULTS["inst_base"])
    ap.add_argument("--n-uavs", type=int, default=DEFAULTS["n_uavs"])
    ap.add_argument("--area", type=float, default=DEFAULTS["area"])
    ap.add_argument("--pop", type=int, default=DEFAULTS["pop_size"])
    ap.add_argument("--gens", type=int, default=DEFAULTS["max_generations"])
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    scenarios = parse_scenarios(args.scenarios)
    deadlines = [float(x) for x in args.deadlines.split(",")]
    seeds = [int(x) for x in args.seeds.split(",")]

    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []

    for n_targets, n_cycles in scenarios:
        for deadline_s in deadlines:
            combo_dir = out_root / f"t{n_targets}_c{n_cycles}" / f"d{int(deadline_s)}"
            combo_dir.mkdir(parents=True, exist_ok=True)
            rows: list[dict] = []
            for instance in range(args.n_instances):
                base_seed = args.inst_base + instance
                tasks, uavs, nests = make_instance(
                    base_seed, args.n_uavs, n_targets, args.area, deadline_s
                )
                for arm_label, gens_override, refine in ARMS:
                    arm = dict(_label=arm_label, gens_override=gens_override, refine=refine)
                    for solver_seed in seeds:
                        params = dict(
                            n_targets=n_targets, n_cycles=n_cycles,
                            deadline_s=deadline_s, pop_size=args.pop,
                            max_generations=args.gens, inst_base=args.inst_base,
                        )
                        r = run_trial_rolling(
                            tasks, uavs, nests, arm=arm, solver_seed=solver_seed,
                            instance=instance, params=params,
                        )
                        rows.append(r)
            summ = summarize_arms(rows)
            c_evo = contrast_pair(rows, "repair_only", "evolution")
            c_ref = contrast_pair(rows, "evolution", "full")
            attr = attribute(rows)
            write_raw_csv(combo_dir / "ab_attr_raw.csv", rows)
            write_summary_csv(combo_dir / "ab_attr_summary.csv", summ)
            write_contrast_csv(combo_dir / "ab_attr_contrast.csv", [c_evo, c_ref])
            write_attribution_csv(combo_dir / "ab_attr_attribution.csv", attr)
            try:
                plot_armcmp(combo_dir / "ab_attr_armcmp.png", summ, dict(
                    n_targets=n_targets, n_cycles=n_cycles, pop_size=args.pop,
                    max_generations=args.gens, deadline_s=deadline_s))
                plot_attribution(combo_dir / "ab_attr_attribution.png", attr, dict(
                    n_targets=n_targets, n_cycles=n_cycles, deadline_s=deadline_s))
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] plot failed: {e}")
            (combo_dir / "ab_attr_summary.json").write_text(json.dumps(
                dict(summary=summ, contrasts=[c_evo, c_ref], attribution=attr),
                indent=2, ensure_ascii=False), encoding="utf-8")
            all_rows.extend(rows)
            a = attr["metrics"].get("path_cost", {})
            print(f"[done] t{n_targets}_c{n_cycles}/d{int(deadline_s)}: "
                  f"C={a.get('repair_only', float('nan')):.1f} A={a.get('evolution', float('nan')):.1f} "
                  f"B={a.get('full', float('nan')):.1f} | evo_share="
                  f"{a.get('evolution_share', float('nan'))*100:.1f}% "
                  f"ref_share={a.get('refinement_share', float('nan'))*100:.1f}%")

    full_summ = summarize_arms(all_rows)
    full_attr = attribute(all_rows)
    write_summary_csv(out_root / "ab_attr_summary.csv", full_summ)
    write_attribution_csv(out_root / "ab_attr_attribution.csv", full_attr)
    build_report(out_root, all_rows,
                 [contrast_pair(all_rows, "repair_only", "evolution"),
                  contrast_pair(all_rows, "evolution", "full")],
                 full_attr,
                 dict(n_targets="all", n_cycles="all", pop_size=args.pop,
                      max_generations=args.gens, deadline_s="all"))
    print(f"[ok] report -> {out_root / 'ab_attr_report.md'}")


if __name__ == "__main__":
    main()
