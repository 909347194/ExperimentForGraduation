"""探针：构造式贪心（完整方法级基线）vs 本文 DMDE+MCF。

同场景、同到达流、同 3 巢 / 每周期 10 任务；1 seed。
目的：把「终点规则消融」升级为「方法级对照」，看本文方法相对构造式贪心
在总航程 / 按时率 / 完成率上到底差多少。

用法（cwd = exp11 目录）：
    python probe_greedy_baseline.py --seeds 7 --tiers 6 --n-uavs 6
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys
import time

import paths  # noqa: F401  # sys.path 引导

from config import build_config_bundle, load_config
from build import (
    build_arrivals,
    build_building_layer,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from metrics import collect_run_metrics
from run import TIERS, _per_cycle, _tier_config

from task_allocation.baselines.registry import get_baseline
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

HERE = pathlib.Path(__file__).resolve().parent

#: (输出名, 求解器名, 终点规则)；solver=None ⇒ 默认 DMDE 主算法
#: 注意：构造式基线返回**预构建解**（``extra["solution"]``），不走解码修复链与
#: 终点重选（``stage_runner`` 预构建通道），其终点即"返回起飞巢"——所以基线
#: 只能与 ``dmde+home`` 对齐（同为返原巢），终点自由度单独由 home→nearest→mcf 读。
METHODS = (
    ("greedy(home)", "greedy", "home"),
    ("dmde+home", None, "home"),
    ("dmde+nearest", None, "nearest"),
    ("dmde+mcf", None, "mcf"),
)


def _run_one(raw_cfg, tier, seed, scenario, terrain, buildings, estimator):
    tier_cfg = _tier_config(raw_cfg, tier)
    tier_cfg.setdefault("experiment", {})["seed"] = seed
    tier_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(tier_cfg)

    uavs = build_uavs(scenario.nests, tier_cfg)
    initial_home = {u.id: u.nest_id for u in uavs}
    task_cfg = build_task_config(tier_cfg, seed)
    batch = build_arrivals(scenario.nests, scenario, task_cfg, tier_cfg)

    rows = []
    for label, solver_name, end_mode in METHODS:
        from dataclasses import replace

        stage_cfg = replace(
            bundle.stage_model,
            cost_polish_accept_mode="f",
            end_nest_l1_mode=end_mode,
            end_nest_l3_mode=end_mode,
        )
        solver_cfg = DMDEConfig(
            pop_size=int(bundle.solver.pop_size),
            max_generations=int(bundle.solver.max_generations),
            zeta=bundle.solver.zeta,
            delta=bundle.solver.delta,
            seed=seed,
            verbose=False,
            log_interval=bundle.solver.log_interval,
            lamarckian=bundle.solver.lamarckian,
        )
        factory = None
        if solver_name is not None:
            cls = get_baseline(solver_name)
            factory = (lambda cls=cls: cls())

        pool = TaskPool(current_time=0.0)
        fleet = UAVFleet(build_uavs(scenario.nests, tier_cfg), current_time=0.0)
        horizon = RollingHorizon(
            pool, fleet, scenario.nests, dem=terrain, estimator=estimator,
            horizon_config=bundle.horizon,
            selection_config=bundle.selection,
            problem_config=bundle.problem_model,
            stage_config=stage_cfg,
            solver_config=solver_cfg,
            event_config=bundle.event,
            nest_offset=bundle.nest_offset,
            solver_factory=factory,
        )
        t0 = time.time()
        records = horizon.run(
            n_cycles=bundle.n_cycles,
            arrivals=copy.deepcopy(batch.arrivals),
            progress_cb=None,
        )
        m = collect_run_metrics(
            records, pool=pool, batch=batch, seed=seed, mode=label,
            wall_seconds=round(time.time() - t0, 3),
            expected_accept_mode="f",
            initial_home=initial_home,
        )
        rows.append((label, m))
    return rows, batch.per_cycle_counts()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="7")
    ap.add_argument("--tiers", default="6")
    ap.add_argument("--n-uavs", type=int, default=None)
    ap.add_argument("--out", default="results/probes/probe_greedy.json")
    args = ap.parse_args()

    raw_cfg = load_config(HERE / "config.yaml")
    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
    tiers = [int(t) for t in str(args.tiers).split(",") if t.strip()]

    scenario = build_scenario(raw_cfg)
    terrain = build_terrain(scenario, raw_cfg)
    buildings = build_building_layer(raw_cfg, scenario)
    estimator = build_cost_estimator(terrain, raw_cfg, buildings=buildings)
    print(f"[probe] {scenario.count} nests, buildings={0 if buildings is None else buildings.count}",
          flush=True)

    all_rows = {}
    for tier in tiers:
        for seed in seeds:
            cfg = copy.deepcopy(raw_cfg)
            if args.n_uavs is not None:
                cfg.setdefault("problem", {})["n_uavs"] = int(args.n_uavs)
            rows, counts = _run_one(cfg, tier, seed, scenario, terrain, buildings, estimator)
            print(f"\n=== T{tier} seed={seed} 每周期={_per_cycle(cfg)} 到达={counts} ===")
            print("%-16s %10s %8s %7s %7s %7s %9s %6s %6s %6s" % (
                "method", "f", "路径km", "完成率", "按时率", "迟到h", "返航段km",
                "航次", "硬违", "超容"))
            for label, m in rows:
                print("%-16s %10.1f %8.2f %7.3f %7.3f %7.2f %9.2f %6d %6d %6d" % (
                    label, m["final_fitness_sum"], m["total_path_m"] / 1000.0,
                    m["completion_rate_final"], m["on_time_rate_final"],
                    m["total_lateness_s"] / 3600.0, m["total_return_leg_m"] / 1000.0,
                    m["n_sorties"],
                    int(m.get("n_hard_violations_total", 0)),
                    int(m.get("nest_overflow_total", 0)),
                ))
            all_rows[f"T{tier}_seed{seed}"] = {
                label: {k: m[k] for k in (
                    "final_fitness_sum", "total_path_m", "total_return_leg_m",
                    "completion_rate_final", "on_time_rate_final",
                    "total_lateness_s", "n_sorties",
                    "n_hard_violations_total", "n_infeasible_delivery",
                    "nest_overflow_total", "wall_seconds",
                ) if k in m}
                for label, m in rows
            }

    out = HERE / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[probe] 写入 {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
