# -*- coding: utf-8 -*-
"""ab_mechanism_3steps 实验自持公共模块（不跨实验共享）。

回答「DE 是否真正控制最终解 / 进化×精修交互 / 各模块解决什么问题」三步。

设计要点（零侵入生产代码）：
  - monkeypatch ``DMDESolver._evaluate_result``（staticmethod）：它是**每代每个
    trial 子代**的唯一必经点，入参 ``individual`` 携带基因（连续 cost_vector +
    离散 assignment），返回值 ``_EvalResult`` 携带修复链后的最终解 solution 与
    fitness。一次 hook 同时抓「解码前基因表达」与「解码后最终方案」两端，且不
    受初始化种群 / GMR 灭绝（走 ``_evaluate``）/ 交付后 polish 干扰。
  - ``fitness_evaluator`` 参数即 MUASAssignmentEvaluator 实例，顺带捕获，供
    第三步手工构造方案（problem 评价器 / _assemble / _best_order / 2-opt）复用。
  - ``population_callback(gen, ...)`` 作代边界：初始 gen=1，每代末回调把
    gen 置为 gen+1，故第 g 代 trials 恰好标 g。

场景与评价口径与 ab_optimization_attribution 完全一致（纯几何随机 SRP、
delivered_fitness = path_cost + w_viol*late + 1e6*n_hard），保证对话性。
"""
from __future__ import annotations

import copy
import csv
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
    Nest, Task, UAV, UAVStatus,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig, SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.stage_runner import (  # noqa: E402
    DMDEConfig, MUASStageConfig,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDESolver  # noqa: E402
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    HorizonConfig, RollingHorizon,
)
from task_allocation.methods.rolling.event_trigger import EventTriggerConfig  # noqa: E402
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402
from task_allocation.methods.selection.pipeline import SelectionConfig  # noqa: E402
from task_allocation.methods.selection.marginal_gain import MarginalConfig  # noqa: E402
from task_allocation.methods.selection.capability import CapabilityScaleConfig  # noqa: E402


# ---------------------------------------------------------------------------
# 场景（与 ab_optimization_attribution.make_instance 逐位一致）
# ---------------------------------------------------------------------------

def make_instance(seed: int, n_uavs: int, n_targets: int, area: float,
                  deadline_s: float = 0.0):
    """纯几何随机 SRP 实例（无 DEM => 三维直线代价，无需数据文件）。"""
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


def parse_scenarios(s: str):
    out = []
    for tok in str(s).split(","):
        tok = tok.strip()
        if not tok:
            continue
        a, _, b = tok.partition(":")
        out.append((int(a), int(b) if b else 1))
    return out


def parse_csv_ints(s: str):
    return [int(x) for x in str(s).split(",") if str(x).strip()]


# ---------------------------------------------------------------------------
# 配置构造
# ---------------------------------------------------------------------------

def build_configs(*, deadline_s, n_tasks, refine, gens_override,
                  pop_size, max_generations, seed,
                  population_callback=None, lamarckian=True,
                  order_mode="gene", relocate=None,
                  projection_only=False, compare_mode="penalty",
                  epsilon0=0.0, epsilon_tc=1):
    """构造本实验一次 run 的四个 config。

    refine           : 三个精修总闸（cost_polish / relocate / polish）
    gens_override    : 0 = 无进化（repair_only 地板）；None = 正常 --gens
    relocate         : 单独覆盖 cost_polish_relocate_enabled（第三步冻结归属用）
    projection_only  : P1 臂（剥解码期目标改写，定稿 §6 要素 1；默认关）
    compare_mode     : P1 臂（CompareSpec，定稿 §6 要素 2；默认 penalty 逐位不变）
    """
    prob_cfg = (MUASProblemConfig() if deadline_s <= 0 else
                MUASProblemConfig(w_violation=0.01,
                                  late_reward_decay_seconds=300.0))
    sel_cfg = SelectionConfig(
        marginal=MarginalConfig(max_selected=n_tasks),
        capability=CapabilityScaleConfig(enabled=False),
    )
    reloc = refine if relocate is None else relocate
    stage_cfg = MUASStageConfig(
        lamarckian=lamarckian,
        cost_polish_enabled=refine,
        cost_polish_relocate_enabled=reloc,
        polish_enabled=refine,
        polish_in_loop=False,
        order_candidate_mode=order_mode,
        projection_only=bool(projection_only),
        compare_mode=str(compare_mode),
        epsilon0=float(epsilon0),
        epsilon_tc=int(epsilon_tc),
    )
    solver_cfg = DMDEConfig(
        pop_size=pop_size,
        max_generations=(max_generations if gens_override is None
                         else gens_override),
        seed=seed,
        verbose=False,
        population_callback=population_callback,
    )
    return prob_cfg, sel_cfg, stage_cfg, solver_cfg


# ---------------------------------------------------------------------------
# 主链路：一次滚动求解 → 五指标
# ---------------------------------------------------------------------------

def run_rolling(tasks, uavs, nests, *, params, arm_cfg, recorder=None):
    """返回一行五指标 dict。recorder 若给则在其 install() 期间由调用方包裹。"""
    n_cycles = int(params["n_cycles"])
    deadline_s = float(params.get("deadline_s", 0.0))
    prob_cfg, sel_cfg, stage_cfg, solver_cfg = build_configs(
        deadline_s=deadline_s,
        n_tasks=len(tasks),
        refine=arm_cfg["refine"],
        gens_override=arm_cfg["gens_override"],
        pop_size=params["pop_size"],
        max_generations=params["max_generations"],
        seed=arm_cfg["seed"],
        population_callback=(recorder.on_population_callback
                             if recorder is not None else None),
        lamarckian=arm_cfg.get("lamarckian", True),
        order_mode=arm_cfg.get("order_mode", "gene"),
        relocate=arm_cfg.get("relocate", None),
        projection_only=arm_cfg.get("projection_only", False),
        compare_mode=arm_cfg.get("compare_mode", "penalty"),
        epsilon0=arm_cfg.get("epsilon0", 0.0),
        epsilon_tc=arm_cfg.get("epsilon_tc", 1),
    )
    tasks = copy.deepcopy(list(tasks))
    uavs = copy.deepcopy(list(uavs))
    nests = copy.deepcopy(list(nests))

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
    rng = np.random.default_rng(int(params.get("inst_base", 5000))
                                + int(arm_cfg["instance"]))
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
    final_n_hard = 0
    n_executed = 0
    cost_history: list[float] = []
    for r in records:
        completed.update(int(t) for t in r.completed_ids)
        late += float(sum(r.lateness_by_task.values()))
        n_hard += len(r.infeasible_ids)
        path_cost += float(sum(t.get("est_cost", 0.0) for t in r.tours))
        muas = r.muas or {}
        final_n_hard += int(muas.get("final_n_hard", 0) or 0)
        n_executed += int(muas.get("n_executed", 0) or 0)
        cost_history.extend(float(x) for x in (getattr(r, "cost_history", None) or []))
    total = max(1, len(tasks))
    w_viol = getattr(prob_cfg, "w_violation", None)
    if w_viol is None:
        w_viol = 0.01
    delivered = path_cost + w_viol * late + 1e6 * n_hard

    return dict(
        arm=arm_cfg["_label"],
        deadline_s=deadline_s,
        seed=arm_cfg["seed"],
        instance=arm_cfg["instance"],
        gens=solver_cfg.max_generations,
        refine=int(bool(arm_cfg["refine"])),
        evo=int(arm_cfg["gens_override"] is None),
        delivered_fitness=float(delivered),
        path_cost=float(path_cost),
        late=float(late),
        coverage=len(completed) / total,
        n_hard=int(n_hard),
        final_n_hard=int(final_n_hard),
        n_executed=int(n_executed),
        elapsed_s=float(elapsed),
        cost_history=cost_history,   # 非 CSV 字段，调用方按需消费
    )


# ---------------------------------------------------------------------------
# 表型 / 基因型指纹
# ---------------------------------------------------------------------------

def solution_fields(sol):
    """解码后最终方案的四个结构字段（用真实 id，可直接与解码前对比）。"""
    if sol is None:
        return None
    assign = tuple(sorted((int(t), int(u)) for t, u in sol.task_to_uav().items()))
    order = tuple(
        (int(t.uav_id), tuple(int(x) for x in t.task_ids))
        for t in sorted(sol.tours, key=lambda z: z.uav_id) if t.task_ids
    )
    execset = tuple(sorted(int(x) for x in sol.executed_task_ids))
    active = tuple(sorted(int(x) for x in sol.active_uav_ids))
    return dict(assign=assign, order=order, execset=execset, active=active)


def pheno_fp(sol):
    """表型指纹 = (归属, 顺序, 执行集, 参与UAV)。适应度单列不进指纹。"""
    f = solution_fields(sol)
    if f is None:
        return ("EMPTY",)
    return (f["assign"], f["order"], f["execset"], f["active"])


def gene_fields(individual, evaluator):
    """解码前基因表达：局部下标 → 真实 id 映射后，给出同样的四个结构字段。

    ``individual.assignment`` 是 [(uav_idx, task_idx)]，uav_idx 是 participating
    列表下标、task_idx 是 tasks 列表下标；build_solution 用
    ``participating[uav_idx].id`` / ``_task_ids[task_idx]`` 映射成真实 id。
    """
    if individual is None or evaluator is None:
        return None
    part = list(getattr(evaluator, "participating", []) or [])
    task_ids = list(getattr(evaluator, "_task_ids", []) or [])
    pairs = []
    for uav_idx, task_idx in individual.assignment:
        uav_idx, task_idx = int(uav_idx), int(task_idx)
        if 0 <= uav_idx < len(part) and 0 <= task_idx < len(task_ids):
            pairs.append((int(task_ids[task_idx]), int(part[uav_idx].id)))
    order_map: dict[int, list[int]] = {}
    assign: dict[int, int] = {}
    for tid, uid in pairs:
        if tid in assign:
            continue
        assign[tid] = uid
        order_map.setdefault(uid, []).append(tid)
    return dict(
        assign=tuple(sorted(assign.items())),
        order=tuple((u, tuple(ts)) for u, ts in sorted(order_map.items())),
        execset=tuple(sorted(assign.keys())),
        active=tuple(sorted(order_map.keys())),
    )


def geno_fp(individual, evaluator):
    f = gene_fields(individual, evaluator)
    if f is None:
        return ("EMPTY",)
    return (f["assign"], f["order"], f["execset"], f["active"])


# ---------------------------------------------------------------------------
# 零侵入 recorder：抓每个 DE trial 的解码前后
# ---------------------------------------------------------------------------

class TrialRecorder:
    """monkeypatch DMDESolver._evaluate_result，逐代记录子代的基因型→表型。

    额外在 population_callback 结算「全局精英是否更新」——这是区分
    「表示坍缩」与「搜索/选择停滞」的决定性证据：trial 适应度可以持续
    变化，但若 best_individual 从初始化后不再被替换，交付解就与 DE 无关。
    """

    def __init__(self):
        self.rows: list[dict] = []
        self.gen = 1
        self._orig = None
        self.installed = False
        self.evaluator = None          # 捕获（第三步复用）
        self.cv_bytes: dict[int, str] = {}
        # 精英轨迹（每代一条）
        self.elite: list[dict] = []
        self._gen_fit: list[float] = []
        self._global_best = float("inf")
        self._elite_updates = 0
        self._best_cv_fp = None

    def install(self):
        if self.installed:
            return
        # 取类字典里的原始描述符（staticmethod 对象），卸载时原样恢复；
        # 若直接取 DMDESolver._evaluate_result 得到的是解包后的函数，
        # 赋回类属性会退化成实例方法 → 后续 self._evaluate_result(...) 多传 self。
        desc = DMDESolver.__dict__.get("_evaluate_result")
        self._orig = desc
        orig_fn = desc.__func__ if isinstance(desc, staticmethod) else desc
        rec = self

        def hooked(individual, fitness_evaluator, cost_matrix, n_uavs=None):
            res = orig_fn(individual, fitness_evaluator, cost_matrix,
                          n_uavs=n_uavs)
            try:
                rec._on_trial(individual, fitness_evaluator, res)
            except Exception:
                pass  # 诊断钩子绝不干扰生产数值路径
            return res

        DMDESolver._evaluate_result = staticmethod(hooked)
        self.installed = True

    def uninstall(self):
        if self.installed and self._orig is not None:
            DMDESolver._evaluate_result = self._orig  # 原样恢复 staticmethod
            self.installed = False

    def on_population_callback(self, gen, cost_vectors, best_idx):
        # 回调在第 gen 代末触发（选择+Lamarckian 写回后、GMR 前），
        # cost_vectors 是当代存活种群；cv[best_idx] 即当代精英基因型。
        try:
            cv = np.asarray(cost_vectors[int(best_idx)], dtype=float)
            fp = _short(tuple(np.round(cv, 4).tolist()))
        except Exception:
            fp = None
        gf = list(self._gen_fit)
        gen_best = min(gf) if gf else float("nan")
        improved = bool(math.isfinite(gen_best) and gen_best < self._global_best - 1e-9)
        if improved:
            self._global_best = gen_best
            self._elite_updates += 1
        elite_changed = int(fp is not None and fp != self._best_cv_fp)
        self.elite.append(dict(
            gen=int(gen),
            gen_trial_best=round(gen_best, 4) if math.isfinite(gen_best) else float("nan"),
            global_best=round(self._global_best, 4) if math.isfinite(self._global_best) else float("nan"),
            improved=int(improved),
            elite_cv_changed=elite_changed,
            n_trials=len(gf),
        ))
        self._best_cv_fp = fp if fp is not None else self._best_cv_fp
        self._gen_fit = []
        # 此后产生的 trial 属于第 gen+1 代
        self.gen = int(gen) + 1

    def _on_trial(self, individual, evaluator, res):
        if evaluator is not None and self.evaluator is None:
            self.evaluator = evaluator
        sol = getattr(res, "solution", None)
        fit = float(getattr(res, "fitness", float("nan")))
        self._gen_fit.append(fit)
        g = gene_fields(individual, evaluator)
        p = solution_fields(sol)
        cv = np.asarray(getattr(individual, "cost_vector", []), dtype=float)
        row = dict(
            gen=self.gen,
            geno=_short(geno_fp(individual, evaluator)),
            pheno=_short(pheno_fp(sol)),
            cv=_short(tuple(np.round(cv, 4).tolist())),
            fitness=fit,
            n_gene_tasks=len(g["execset"]) if g else 0,
            n_pheno_tasks=len(p["execset"]) if p else 0,
            n_gene_uav=len(g["active"]) if g else 0,
            n_pheno_uav=len(p["active"]) if p else 0,
            chg_assign=int(bool(g and p and g["assign"] != p["assign"])),
            chg_order=int(bool(g and p and g["order"] != p["order"])),
            chg_execset=int(bool(g and p and g["execset"] != p["execset"])),
            chg_active=int(bool(g and p and g["active"] != p["active"])),
        )
        self.rows.append(row)


def _short(x) -> str:
    """稳定短指纹（md5），避免把大 tuple 写进 CSV。"""
    import hashlib
    return hashlib.md5(repr(x).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# CSV / 聚合小工具
# ---------------------------------------------------------------------------

def write_csv(path, rows, fieldnames=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def mean_std(xs):
    xs = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not xs:
        return float("nan"), float("nan")
    return float(np.mean(xs)), float(np.std(xs))


def agg_by(rows, key_fields, value_fields):
    """按 key_fields 分组，对 value_fields 求 mean/std。"""
    groups: dict[tuple, list] = {}
    for r in rows:
        k = tuple(r[f] for f in key_fields)
        groups.setdefault(k, []).append(r)
    out = []
    for k, g in sorted(groups.items(), key=lambda z: str(z[0])):
        d = dict(zip(key_fields, k))
        for vf in value_fields:
            m, s = mean_std([r.get(vf, float("nan")) for r in g])
            d[vf] = round(m, 4)
            d[vf + "_std"] = round(s, 4)
        d["n"] = len(g)
        out.append(d)
    return out


def make_params(args):
    return dict(
        pop_size=args.pop,
        max_generations=args.gens,
        n_cycles=args.cycles,
        inst_base=args.inst_base,
        n_uavs=args.n_uavs,
        area=args.area,
        deadline_s=0.0,
    )
