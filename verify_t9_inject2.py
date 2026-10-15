# -*- coding: utf-8 -*-
"""注入实验 v2（horizon  harness，忠实复现真实周期 1 输入）。

回答：DMDE 没搜到 T9→UAV4 的更优解，是「从未生成它」还是「保不住它」？

  1) horizon 裸跑周期 1 → 基线（预期 T9→UAV0）；
  2) 捕获周期 1 评估器的局部下标（participating / task_ids 顺序）与代价矩阵；
  3) 构造「合并前形态」的更优解个体，经评估器验证其修复后形态；
  4) 把该个体注入初始种群（仅周期 1）重跑 → 看最终 best 是否保住它；
  5) 对照：不注入、代数 ×5 → 看纯加预算能否搜到。
"""
import copy
import sys

sys.path.insert(0, "task_allocation/experiments/exp01_smoke")

from dataclasses import replace

from paths import DATA_DIR, EXP_DIR
from build import (
    build_arrivals,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from config import build_config_bundle, load_config
from task_allocation.methods.muas.representation.encoder import (
    Gene,
    Individual,
    PopulationEncoder,
)
from task_allocation.methods.muas.solvers import muas_stage
from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

raw_cfg = load_config(EXP_DIR / "config.yaml")
scenario = build_scenario(raw_cfg, DATA_DIR)
nests = scenario.nests
terrain = build_terrain(scenario, raw_cfg)
estimator = build_cost_estimator(terrain, raw_cfg)

# ── 捕获周期 1 评估器：局部下标 + 代价矩阵 + 实例 ──────────────
cap: dict = {}
_orig_init = muas_stage.MUASAssignmentEvaluator.__init__
_orig_eval = muas_stage.MUASAssignmentEvaluator.evaluate


def _init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    if "evaluator" not in cap:
        cap["evaluator"] = self
        cap["participating"] = [u.id for u in self.participating]
        cap["task_ids"] = list(self._task_ids)


def _eval(self, assignment, cost_matrix=None, n_uavs=None):
    if "cm" not in cap and cost_matrix is not None:
        cap["cm"] = cost_matrix
    return _orig_eval(self, assignment, cost_matrix, n_uavs=n_uavs)


muas_stage.MUASAssignmentEvaluator.__init__ = _init
muas_stage.MUASAssignmentEvaluator.evaluate = _eval


def run_horizon(inject=None, max_iter=None):
    seed = 42
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(seed_cfg)
    uavs = build_uavs(nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, seed)
    batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(build_uavs(nests, raw_cfg), current_time=0.0)
    solver_cfg = bundle.solver
    if max_iter is not None:
        solver_cfg = replace(solver_cfg, max_iter=max_iter)
    horizon = RollingHorizon(
        pool, fleet, nests, dem=terrain, estimator=estimator,
        horizon_config=bundle.horizon, selection_config=bundle.selection,
        problem_config=bundle.problem_model, stage_config=bundle.stage_model,
        solver_config=solver_cfg, event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    if inject is not None:
        calls = {"n": 0}
        _orig_gen = PopulationEncoder.generate

        def _gen(self, pop_size, seed=None):
            pop = _orig_gen(self, pop_size, seed=seed)
            calls["n"] += 1
            if calls["n"] == 1:  # 只注入周期 1 的初始种群
                for k in range(10):
                    pop[-(k + 1)] = inject.copy()
            return pop

        PopulationEncoder.generate = _gen
    try:
        records = horizon.run(n_cycles=1, arrivals=batch.arrivals)
    finally:
        if inject is not None:
            PopulationEncoder.generate = _orig_gen
    return records[0]


def show(tag, rec):
    tours = {t["uav_id"]: t["task_ids"] for t in rec.tours}
    t9 = next((u for u, ts in tours.items() if 9 in ts), None)
    idle = [u for u in range(10) if u not in tours]
    print(f"[{tag}] T9→UAV{t9} | 空闲={idle} | F1={rec.muas['f1_reward']:.2f} "
          f"F2={rec.muas['f2_cost']:.0f} | fit={rec.muas['best_fitness']:.2f}")
    print(f"    tours={tours}")


# ── 1) 基线 + 捕获 ────────────────────────────────────────────
base = run_horizon()
show("基线(horizon)", base)
part_ids = cap["participating"]
task_ids = cap["task_ids"]
cm = cap["cm"]
print(f"    participating 顺序 = {part_ids}")
print(f"    task_ids 顺序     = {task_ids}")
lu = {uid: i for i, uid in enumerate(part_ids)}
lt = {tid: i for i, tid in enumerate(task_ids)}
K = len(part_ids)
print(f"    cm shape = {cm.shape}（行[0,K)=参与UAV，行[K,K+M)=任务→任务）")

# ── 2) 构造「合并前形态」更优解个体（全局任务 id 口径）────────
pre = {
    0: [0],        # 期望被合并腾空
    1: [6],        # 期望被合并腾空
    2: [2],
    3: [3, 5],
    4: [9],        # T9 归 UAV4
    5: [11],
    6: [7],
    7: [1],
    8: [10],
    9: [4, 8],
}
genes = []
for uid in part_ids:
    tl = pre[uid]
    genes.append(Gene(uav_id=lu[uid], target_id=lt[tl[0]],
                      cost=float(cm[lu[uid], lt[tl[0]]])))
    for a, b in zip(tl, tl[1:]):
        genes.append(Gene(uav_id=-1, target_id=lt[b],
                          cost=float(cm[K + lt[a], lt[b]])))
INJ = Individual(genes=genes, model_type="srp")
print(f"\n注入个体 assignment(局部) = {INJ.assignment}")

ev = cap["evaluator"]
res = ev.evaluate(INJ.assignment, cm, n_uavs=K)
sol = res.solution
t9_uav = next((t.uav_id for t in sol.tours if 9 in t.task_ids), None)
tours = {t.uav_id: t.task_ids for t in sol.tours if t.task_ids}
print(f"[注入个体·修复后形态] T9→UAV{t9_uav} | F1={sol.f1_reward:.2f} "
      f"path={ev.problem.path_cost(sol):.1f} | fit={res.fitness:.2f}")
print(f"    tours={tours}")
print(f"    (基线 fit={base.muas['best_fitness']:.2f})")

# ── 3) 注入 10 副本重跑 ───────────────────────────────────────
inj = run_horizon(inject=INJ)
show("注入10副本", inj)

# ── 4) 对照：5 倍代数、不注入 ────────────────────────────────
big = run_horizon(max_iter=500)
show("5x代数(500代)", big)
