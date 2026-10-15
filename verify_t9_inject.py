# -*- coding: utf-8 -*-
"""注入实验：把 T9→UAV4 的更优解编码后注入 DMDE 初始种群。

要回答的问题：DMDE 没搜到更优解，是因为「从未生成它」（初始化/变异缺口），
还是「保不住它」（解码/修复链/回写把它毁掉）？

做法：
  1) 先裸跑一次 run_selective_muas，捕获评估器的局部下标口径
     （participating 顺序 / task_ids 顺序）与基线结果；
  2) 用「合并前形态」构造更优解的个体（编码不变量：每架参与 UAV >=1 任务，
     闲置由解码端 _consolidate 产生），先经评估器验证其修复后形态确实是
     T9→UAV4 的 8 航次解；
  3) 把该个体注入初始种群（替换末尾若干个），重跑，看最终 best 是否保住它。
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
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs
from task_allocation.methods.muas.representation.encoder import (
    Gene,
    Individual,
    PopulationEncoder,
)
from task_allocation.methods.muas.solvers import muas_stage

raw_cfg = load_config(EXP_DIR / "config.yaml")
scenario = build_scenario(raw_cfg, DATA_DIR)
nests = scenario.nests
terrain = build_terrain(scenario, raw_cfg)
estimator = build_cost_estimator(terrain, raw_cfg)

seed = 42
seed_cfg = copy.deepcopy(dict(raw_cfg))
seed_cfg.setdefault("experiment", {})["seed"] = seed
seed_cfg.setdefault("solver", {})["seed"] = seed
bundle = build_config_bundle(seed_cfg)
uavs = build_uavs(nests, raw_cfg)
task_cfg = build_task_config(raw_cfg, seed)
batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
tasks = batch.arrivals[1]
cb = build_pairwise_costs(
    tasks, uavs, nests=nests, dem=terrain, estimator=estimator,
    nest_offset=bundle.nest_offset,
)

# ── 捕获评估器实例与局部下标口径 ──────────────────────────────
captured: dict = {}
_orig_init = muas_stage.MUASAssignmentEvaluator.__init__


def _patched_init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    captured["evaluator"] = self
    captured["participating"] = [u.id for u in self.participating]
    captured["task_ids"] = list(self._task_ids)


muas_stage.MUASAssignmentEvaluator.__init__ = _patched_init


def run_stage(solver_cfg=None, n_inject=0, inj_individual=None):
    kw = dict(
        tasks=tasks,
        uavs=uavs,
        nests=nests,
        cost_build=cb,
        fleet_uavs=uavs,
        problem_config=replace(bundle.problem_model, current_time=600.0),
        stage_config=bundle.stage_model,
        solver_config=solver_cfg or bundle.solver,
        nest_offset=bundle.nest_offset,
    )
    if n_inject and inj_individual is not None:
        _orig_gen = PopulationEncoder.generate

        def _patched_gen(self, pop_size, s=None):
            pop = _orig_gen(self, pop_size, seed=s)
            for k in range(min(n_inject, pop_size)):
                pop[-(k + 1)] = inj_individual.copy()
            return pop

        PopulationEncoder.generate = _patched_gen
    try:
        return muas_stage.run_selective_muas(**kw)
    finally:
        if n_inject and inj_individual is not None:
            PopulationEncoder.generate = _orig_gen


def show(tag, res):
    t9_uav = next((t.uav_id for t in res.solution.tours if 9 in t.task_ids), None)
    tours = {t.uav_id: t.task_ids for t in res.solution.tours if t.task_ids}
    idle = [u for u in captured["participating"] if u not in tours]
    print(
        f"[{tag}] T9→UAV{t9_uav} | 空闲={idle} | F1={res.diagnostics['f1_reward']:.2f} "
        f"F2={res.diagnostics['f2_cost']:.0f} | fit={res.diagnostics['best_fitness']:.2f} "
        f"| evals={res.n_evals}"
    )
    print(f"    tours={tours}")


# ── 1) 裸跑基线 + 捕获局部下标 ────────────────────────────────
base = run_stage()
show("基线(裸跑)", base)
part_ids = captured["participating"]
task_ids = captured["task_ids"]
print(f"    participating 顺序 = {part_ids}")
print(f"    task_ids 顺序     = {task_ids}")
lu = {uid: i for i, uid in enumerate(part_ids)}   # 真实 id -> 局部 UAV 下标
lt = {tid: i for i, tid in enumerate(task_ids)}   # 真实 id -> 局部任务下标
K = len(part_ids)
print(f"    T9 局部下标 = {lt[9]}, UAV4 局部下标 = {lu[4]}")

# ── 2) 构造「合并前形态」的更优解个体并验证 ────────────────────
# 合并前（10 航次，满足编码不变量）：UAV0/1 各持一个将被合并的任务，
# 其余 8 架与更优解终态一致；期望 _consolidate 把 UAV0/1 腾空。
pre = {
    0: [0],        # 将被并入 UAV5（终态 [0,11]）
    1: [6],        # 将被并入 UAV7（终态 [1,6]）
    2: [2],
    3: [3, 5],
    4: [9],
    5: [11],
    6: [7],
    7: [1],
    8: [10],
    9: [4, 8],
}
ev = captured["evaluator"]
# 评估器重建用的代价矩阵与 run_selective_muas 内一致：
# 行 [0,K) = 参与 UAV 的 uav_target 行，行 [K, K+M) = task_task
import numpy as np
part_rows = [next(k for k, u in enumerate(uavs) if u.id == pid) for pid in part_ids]
cm = np.vstack([cb.uav_target[part_rows, :], cb.task_task]).astype(float)

genes = []
for uid in part_ids:
    tl = pre[uid]
    genes.append(Gene(uav_id=lu[uid], target_id=lt[tl[0]],
                      cost=float(cm[lu[uid], lt[tl[0]]])))
    # 巡游基因：前驱是该航次上一个任务
    for a, b in zip(tl, tl[1:]):
        genes.append(Gene(uav_id=-1, target_id=lt[b],
                          cost=float(cm[K + lt[a], lt[b]])))
INJ = Individual(genes=genes, model_type="srp")
print(f"\n注入个体 assignment = {INJ.assignment}")

# 先用评估器验证：该个体的修复后形态 + 适应度
res = ev.evaluate(INJ.assignment, cm, n_uavs=K)
sol = res.solution
t9_uav = next((t.uav_id for t in sol.tours if 9 in t.task_ids), None)
tours = {t.uav_id: t.task_ids for t in sol.tours if t.task_ids}
print(f"[注入个体·评估器验证] T9→UAV{t9_uav} | F1={sol.f1_reward:.2f} "
      f"path={ev.problem.path_cost(sol):.1f} | fit={res.fitness:.2f}")
print(f"    tours={tours}")
print(f"    (基线 fit={base.diagnostics['best_fitness']:.2f})")

# ── 3) 注入 10 个副本，重跑 ───────────────────────────────────
inj = run_stage(n_inject=10, inj_individual=INJ)
show("注入10副本", inj)

# ── 4) 加预算对照：5 倍代数，不注入 ──────────────────────────
big = run_stage(solver_cfg=replace(bundle.solver, max_iter=500))
show("5x代数(500代)", big)
