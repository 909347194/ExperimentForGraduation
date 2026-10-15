# -*- coding: utf-8 -*-
"""可达性检验：T9→UAV4 的 8 航次「更优解」在真实修复链下是否可达。

背景：verify_t9.py 的反事实用 problem.solution_from_assignment 直接构造解，
**绕过了评估器的修复链**（_consolidate 等）。本脚本把同一个解放进真实
evaluator.build_solution，看修复链是保留它、还是把它合并成别的形态。

同时做：
  - found 解过修复链（健全性对照，应复现基线）；
  - alt 8 航次解过修复链（不动点检验：修复链是否会进一步合并它）；
  - 5 倍代数对照（max_generations=500）。
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
from task_allocation.methods.muas.representation.encoder import PopulationEncoder
from task_allocation.methods.muas.solvers import muas_stage
from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

raw_cfg = load_config(EXP_DIR / "config.yaml")
scenario = build_scenario(raw_cfg, DATA_DIR)
nests = scenario.nests
terrain = build_terrain(scenario, raw_cfg)
estimator = build_cost_estimator(terrain, raw_cfg)

cap: dict = {}
_orig_init = muas_stage.MUASAssignmentEvaluator.__init__


def _init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    if "evaluator" not in cap:
        cap["evaluator"] = self
        cap["participating"] = [u.id for u in self.participating]
        cap["task_ids"] = list(self._task_ids)


muas_stage.MUASAssignmentEvaluator.__init__ = _init


def run_horizon(max_generations=None):
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
    if max_generations is not None:
        solver_cfg = replace(solver_cfg, max_generations=max_generations)
    horizon = RollingHorizon(
        pool, fleet, nests, dem=terrain, estimator=estimator,
        horizon_config=bundle.horizon, selection_config=bundle.selection,
        problem_config=bundle.problem_model, stage_config=bundle.stage_model,
        solver_config=solver_cfg, event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    return horizon.run(n_cycles=1, arrivals=batch.arrivals)[0]


def show(tag, rec):
    tours = {t["uav_id"]: t["task_ids"] for t in rec.tours}
    t9 = next((u for u, ts in tours.items() if 9 in ts), None)
    idle = [u for u in range(10) if u not in tours]
    print(f"[{tag}] T9→UAV{t9} | 空闲={idle} | F1={rec.muas['f1_reward']:.2f} "
          f"F2={rec.muas['f2_cost']:.0f} | fit={rec.muas['best_fitness']:.2f}")
    print(f"    tours={tours}")


# ── 1) 基线 + 捕获局部口径 ────────────────────────────────────
base = run_horizon()
show("基线(horizon)", base)
part_ids = cap["participating"]
task_ids = cap["task_ids"]
lu = {uid: i for i, uid in enumerate(part_ids)}
lt = {tid: i for i, tid in enumerate(task_ids)}
ev = cap["evaluator"]
print(f"    participating={part_ids}")
print(f"    task_ids     ={task_ids}")


def through_chain(tag, tours_global):
    """把全局 id 的航次表转局部 assignment，过真实修复链后报告。"""
    assign = []
    for uid, tl in tours_global.items():
        for t in tl:
            assign.append((lu[uid], lt[t]))
    sol = ev.build_solution(assign)
    ev.problem.evaluate(sol)
    fit = ev.problem.scalar_fitness(sol)
    out = {t.uav_id: t.task_ids for t in sol.tours if t.task_ids}
    t9 = next((u for u, ts in out.items() if 9 in ts), None)
    print(f"[{tag}] T9→UAV{t9} | F1={sol.f1_reward:.2f} "
          f"path={ev.problem.path_cost(sol):.1f} pen={sol.meta.get('penalty'):.1f} "
          f"fit={fit:.2f}")
    print(f"    tours={out}")
    return fit


# ── 2) found / alt 过真实修复链 ──────────────────────────────
found = {0: [9], 2: [2], 3: [3, 5], 5: [0, 11], 6: [7], 7: [1, 6], 8: [10], 9: [4, 8]}
alt = {2: [2], 3: [3, 5], 4: [9], 5: [0, 11], 6: [7], 7: [1, 6], 8: [10], 9: [4, 8]}
f_found = through_chain("found 过修复链", found)
f_alt = through_chain("alt 8航次 过修复链", alt)
print(f"    (基线 fit={base.muas['best_fitness']:.2f})")

# ── 3) 合并前形态试探：alt + 2 个单任务航次（不同剥离组合）──
for peel in [[(5, 0), (7, 6)], [(5, 11), (9, 8)], [(8, 10), (9, 4)]]:
    pre = {k: list(v) for k, v in alt.items()}
    for uid, t in peel:
        pre[uid] = [x for x in pre[uid] if x != t]
    # 把剥离出的任务挂到两架空闲能力机上（用 found 里空闲的 0/1 号机）
    free = [u for u in range(10) if u not in pre or not pre[u]]
    pre[free[0]] = [peel[0][1]]
    pre[free[1]] = [peel[1][1]]
    pre = {k: v for k, v in pre.items() if v}
    through_chain(f"合并前形态 peel={peel}", pre)

# ── 4) 5 倍代数对照 ──────────────────────────────────────────
big = run_horizon(max_generations=500)
show("5x代数(500代)", big)
