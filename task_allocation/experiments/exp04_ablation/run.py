# -*- coding: utf-8 -*-
"""exp04_ablation 入口：单周期机制消融台（B1/B2/B3）。

职责（唯一）：在固定问题口径与单周期快照下，对第二阶段 MUAS 求解器做单因素
   机制消融——A0 生产基线、B1 构造式解码、B2 三层选机、B3 小 N 枚举参照，
   量化各内部机制对交付解的边际贡献。产物为逐臂指标 + 描述性分析，**不排名、
   不宣称完成约束、不做显著性检验**。

口径继承：problem/tasks/data/solver 与 exp02_selection_scale 正式档逐位一致，
   故 A0 = exp02 同口径单周期解；四臂之间只差 arms[].overrides 里的机制开关。

不负责（边界，见 notes.md）：方法排名、完成约束验收（exp01）、选择层规模
   （exp02）、事件响应（exp03）、外部基线（exp05）、显著性。

计时：单进程、单 seed、perf_counter 观测；跨臂共享同一 DEM 距离缓存与同一
   任务快照，墙钟只作量级观测，不作机制归因。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import DATA_DIR, EXP_DIR, ROOT, RESULTS_DIR

for _p in (str(ROOT), str(EXP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import (  # noqa: E402
    enabled_arms,
    load_with_overrides,
    seed_list,
    validate,
)
from task_allocation.common import scenario_data  # noqa: E402
from task_allocation.common.scenario import build_nest_scenario  # noqa: E402
from task_allocation.common.task_scenario import (  # noqa: E402
    SyntheticTaskConfig,
    build_synthetic_tasks,
)
from task_allocation.common.types import UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.cost.vertical_section import (  # noqa: E402
    VerticalSectionCostEstimator,
    make_distance_fn,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    ConstraintConfig,
    MUASProblemConfig,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    enumerate_reference_participating,
    run_selective_muas,
)
from task_allocation.methods.muas.solvers.participating import (  # noqa: E402
    select_participating_uavs,
)
from task_allocation.methods.utils.building_layer import BuildingLayer  # noqa: E402


# ── 配置装配（从 cfg 里读，数值一律来自 yaml，不在本文件发明）──────────
def _build_scenario_and_estimator(cfg: dict):
    data_cfg = cfg["data"]
    problem_cfg = cfg["problem"]
    scenario = build_nest_scenario(
        scenario_data.resolve_file(data_cfg["path"], data_cfg.get("scenario")),
        count=problem_cfg["n_nests"],
        capacity=problem_cfg["nest_capacity"],
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=scenario_data.resolve_file(data_cfg["dem"], data_cfg.get("scenario")),
    )
    terrain = scenario.dem.to_metric_terrain(scenario.frame, step_m=200.0)
    buildings = None
    bname = data_cfg.get("buildings")
    if bname:
        bbase = scenario_data.scenario_dir(data_cfg.get("scenario")) / str(bname)
        bpaths = sorted(bbase.glob("*.shp")) if bbase.is_dir() else [bbase]
        if not bpaths:
            raise FileNotFoundError(f"建筑数据不存在：{bbase}")
        buildings = BuildingLayer.from_shapefiles(bpaths, scenario.frame)
    estimator = VerticalSectionCostEstimator(dem_terrain=terrain, buildings=buildings)
    distance_fn = make_distance_fn(estimator)
    return scenario, terrain, estimator, distance_fn, buildings


def _build_uavs(cfg: dict, scenario) -> list[UAV]:
    p = cfg["problem"]
    n = int(p["n_uavs"])
    max_range = float(p.get("uav_max_range", 10000.0))
    speed = float(p.get("uav_speed", 15.0))
    max_time = p.get("uav_max_time")
    if max_time is None and speed > 0:
        max_time = max_range / speed
    nests = list(scenario.nests)
    return [
        UAV(
            id=i,
            x=nests[i % len(nests)].x,
            y=nests[i % len(nests)].y,
            z=nests[i % len(nests)].z,
            nest_id=i % len(nests),
            remaining_range=max_range,
            speed=speed,
            max_time=max_time,
        )
        for i in range(n)
    ]


def _build_tasks(cfg: dict, scenario, seed: int):
    t = cfg["tasks"]
    data_cfg = cfg["data"]
    tcfg = SyntheticTaskConfig(
        n_tasks=int(t["n_tasks"]),
        seed=int(seed),
        domain=str(t.get("domain", "nest_anchored")).strip().lower(),
        spread_m=float(t.get("spread_m", 25000.0)),
        reward_min=float(t.get("reward_min", 1.0)),
        reward_max=float(t.get("reward_max", 10.0)),
        deadline_min=float(t.get("deadline_min", 3600.0)),
        deadline_max=float(t.get("deadline_max", 28800.0)),
        anchor_to_nests=bool(t.get("anchor_to_nests", True)),
        region_geojson=(
            str(scenario_data.resolve_file(data_cfg["region_geojson"], data_cfg.get("scenario")))
            if data_cfg.get("region_geojson") else None
        ),
    )
    return build_synthetic_tasks(scenario.nests, scenario.frame, scenario.dem, tcfg)


def _problem_config(cfg: dict) -> MUASProblemConfig:
    m = cfg.get("problem", {})
    return MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        lambda_cost=float(m.get("lambda_cost", 1.0e-2)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_violation=(None if m.get("w_violation") is None else float(m["w_violation"])),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        w_hard_violation=(None if m.get("w_hard_violation") is None else float(m["w_hard_violation"])),
        hard_violation_kappa=float(m.get("hard_violation_kappa", 10.0)),
        coverage_audit=bool(m.get("coverage_audit", m.get("lexicographic_coverage", False))),
        constraints=ConstraintConfig.from_dict(cfg.get("constraints") or m.get("constraints")),
    )


def _stage_config(cfg: dict) -> MUASStageConfig:
    s = cfg.get("stage", {}) or {}
    return MUASStageConfig(
        participating_policy=str(s.get("participating_policy", "demand")),
        participation_cost=float(s.get("participation_cost", 0.0)),
        reflow_rescue_scope=str(s.get("reflow_rescue_scope", "fleet")),
        prune_unprofitable=bool(s.get("prune_unprofitable", True)),
        prune_max_rounds=int(s.get("prune_max_rounds", 8)),
        prune_margin=float(s.get("prune_margin", 0.0)),
        prune_wait_bonus=float(s.get("prune_wait_bonus", 1.0)),
        wait_scale=float(s.get("wait_scale", 3600.0)),
        demand_coverage_radius=(None if s.get("demand_coverage_radius") is None
                                else float(s["demand_coverage_radius"])),
        demand_range_factor=float(s.get("demand_range_factor", 0.5)),
        demand_provision_serving=bool(s.get("demand_provision_serving", True)),
        demand_serve_margin=float(s.get("demand_serve_margin", 0.95)),
        decode_mode=str(s.get("decode_mode", "repair_chain")),
        repair_max_passes=int(s.get("repair_max_passes", 6)),
        constructive_max_passes=int(s.get("constructive_max_passes", 3)),
        polish_enabled=bool(s.get("polish_enabled", True)),
        polish_max_rounds=int(s.get("polish_max_rounds", 30)),
        polish_max_evals=int(s.get("polish_max_evals", 2000)),
        polish_eps=float(s.get("polish_eps", 1.0e-6)),
        coverage_first=bool(s.get("coverage_first", False)),
        memetic_max_rounds=int(s.get("memetic_max_rounds", 6)),
    )


def _apply_arm_overrides(base: dict, arm: dict) -> dict:
    """把 arm.overrides 的 dotted path 深合并进 base（唯一自变量）。"""
    cfg = copy.deepcopy(base)
    for path, val in (arm.get("overrides") or {}).items():
        parts = str(path).split(".")
        node = cfg
        for k in parts[:-1]:
            node = node.setdefault(k, {})
        node[parts[-1]] = val
    validate(cfg)
    return cfg


def _write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _cfg_sha256(cfg: dict) -> str:
    return hashlib.sha256(
        json.dumps(cfg, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _run_solve(cfg: dict, tasks, uavs, scenario, estimator, distance_fn, seed: int) -> dict:
    """对单臂单 seed 跑一次完整二阶段求解，返回指标行。"""
    t0 = time.perf_counter()
    problem_model = _problem_config(cfg)
    stage_cfg = _stage_config(cfg)
    solver_cfg = DMDEConfig(
        pop_size=int(cfg["solver"].get("pop_size", 20)),
        max_generations=int(cfg["solver"].get("max_generations", 40)),
        seed=int(seed),
    )
    cb = build_pairwise_costs(
        tasks, uavs, scenario.nests, estimator=estimator, distance_fn=distance_fn,
    )
    t_build = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    res = run_selective_muas(
        tasks, uavs, scenario.nests, cb,
        problem_config=problem_model, stage_config=stage_cfg,
        solver_config=solver_cfg,
    )
    t_solve = (time.perf_counter() - t1) * 1000.0

    diag = res.diagnostics or {}
    sol = res.solution
    n_done = len(sol.executed_task_ids)
    row = {
        "n_tasks": len(tasks),
        "n_uav_avail": len(uavs),
        "n_executed": n_done,
        "n_deferred": len(tasks) - n_done,
        "completion_ratio": round(n_done / max(len(tasks), 1), 4),
        "f1_reward_descriptive": round(float(sol.f1_reward), 4),
        "f2_cost": round(float(sol.f2_cost), 2),
        "final_fitness": (None if diag.get("final_fitness") is None
                          else round(float(diag["final_fitness"]), 4)),
        "n_evals": int(getattr(res, "n_evals", 0) or 0),
        "polish_evals": int(diag.get("polish_evals", 0) or 0),
        "polish_accepted": int(sum((diag.get("polish_accepted") or {}).values())),
        "model_type": (res.meta.get("model_type") if getattr(res, "meta", None) else None),
        "t_build_ms": round(t_build, 2),
        "t_solve_ms": round(t_solve, 2),
        "t_total_ms": round(t_build + t_solve, 2),
        "decode_mode": stage_cfg.decode_mode,
        "participating_policy": stage_cfg.participating_policy,
    }
    return row


def _run_enum_reference(cfg: dict, tasks, uavs, scenario, estimator, distance_fn, seed: int) -> dict:
    """B3：小 N 近精确枚举选机参照（固定解码器，量化选机 gap）。"""
    problem_model = _problem_config(cfg)
    stage_cfg = _stage_config(cfg)
    from task_allocation.methods.muas.solvers.evaluator import MUASAssignmentEvaluator
    from task_allocation.methods.muas.problem import SelectiveMUASProblem
    from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs as _bpc
    cb = _bpc(tasks, uavs, scenario.nests, estimator=estimator, distance_fn=distance_fn)
    # from_entities 需要 tuple->float 的代价映射；provider.pairwise 即 A0 同口径
    # 三维代价（含 DEM 段），distance_fn 一并注入保持评价端 ETA 口径一致。
    sel_problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, list(scenario.nests),
        pairwise_cost=cb.provider.pairwise,
        config=problem_model,
        distance_fn=distance_fn,
    )
    evaluator = MUASAssignmentEvaluator(
        problem=sel_problem,
        participating=list(uavs),
        tasks=list(tasks),
        nests=list(scenario.nests),
        fleet_uavs=list(uavs),
        config=stage_cfg,
        available_uavs=list(uavs),
    )
    # 启发式选机子集（用生产口径 demand 选机，与 B3 基线同口径）
    heur = select_participating_uavs(uavs, len(tasks), stage_cfg)
    heur_ids = [int(u.id) for u in heur]
    e = cfg.get("enum_reference", {}) or {}
    t0 = time.perf_counter()
    ref = enumerate_reference_participating(
        evaluator, heur_ids,
        max_avail=int(e.get("max_avail", 12)),
        max_tasks=int(e.get("max_tasks", 10)),
        max_subsets=int(e.get("max_subsets", 5000)),
    )
    t_enum = (time.perf_counter() - t0) * 1000.0
    out = {
        "n_tasks": len(tasks),
        "n_uav_avail": len(uavs),
        "executed": bool(ref.get("executed")),
        "n_subsets": int(ref.get("n_subsets", 0)),
        "sampled": bool(ref.get("sampled", False)),
        "heuristic": ref.get("heuristic"),
        "reference": ref.get("reference"),
        "gap": ref.get("gap"),
        "reason": ref.get("reason"),
        "heur_ids": heur_ids,
        "t_enum_ms": round(t_enum, 2),
    }
    return out


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="exp04_ablation 单周期机制消融台")
    ap.add_argument("--seeds", nargs="+", default=None,
                    help="覆盖 experiment.seeds（如 42 43 44）")
    ap.add_argument("--arms", nargs="+", default=None,
                    help="只跑指定臂名（默认全跑）")
    ap.add_argument("--no-plots", action="store_true", help="跳过出图")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印生效配置与臂定义，不求解不落盘")
    ap.add_argument("--set", action="append", default=[], metavar="dotted.path=value")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    cfg = load_with_overrides(set_items=args.set)
    if args.seeds:
        cfg.setdefault("experiment", {})["seeds"] = [int(s) for s in args.seeds]
    seeds = seed_list(cfg)

    if args.dry_run:
        print("=== exp04 dry-run 生效配置 ===")
        print("scenario =", cfg["data"].get("scenario"))
        print("problem  =", {k: cfg["problem"].get(k) for k in
                             ("n_nests", "nest_capacity", "n_uavs", "uav_max_range", "lambda_cost")})
        print("tasks    =", {k: cfg["tasks"].get(k) for k in ("n_tasks", "reward_min", "reward_max")})
        print("stage    =", {k: cfg["stage"].get(k) for k in
                             ("decode_mode", "participating_policy", "coverage_first")})
        print("solver   =", cfg["solver"])
        print("seeds    =", seeds)
        print("arms     =")
        for a in enabled_arms(cfg):
            print("  -", a["name"], "overrides=", a.get("overrides"), "enum=", a.get("enum", False))
        return 0

    all_arms = enabled_arms(cfg)
    if args.arms:
        all_arms = [a for a in all_arms if a["name"] in args.arms]
        if not all_arms:
            print(f"[exp04] 无匹配臂：{args.arms}", file=sys.stderr)
            return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    effective_base = copy.deepcopy(cfg)
    sha = _cfg_sha256(effective_base)

    print(f"[exp04] 场景构建：{cfg['data'].get('scenario')} ...", flush=True)
    scenario, terrain, estimator, distance_fn, buildings = _build_scenario_and_estimator(cfg)
    print(f"[exp04] 建筑层：{'挂载' if buildings else '无'}；{len(scenario.nests)} 机巢", flush=True)

    results: list[dict] = []
    enum_results: list[dict] = []
    t_start = time.perf_counter()
    for seed in seeds:
        uavs = _build_uavs(cfg, scenario)
        tasks = _build_tasks(cfg, scenario, seed)
        print(f"[exp04] seed={seed} 任务={len(tasks)} 机={len(uavs)}", flush=True)
        for arm in all_arms:
            aname = str(arm["name"])
            arm_cfg = _apply_arm_overrides(cfg, arm)
            if arm.get("enum", False):
                # B3 用小 N（tasks.n_tasks 覆盖）：按 arm_cfg 重建任务快照
                arm_tasks = tasks
                if int(arm_cfg["tasks"]["n_tasks"]) != int(cfg["tasks"]["n_tasks"]):
                    arm_tasks = _build_tasks(arm_cfg, scenario, seed)
                ref = _run_enum_reference(arm_cfg, arm_tasks, uavs, scenario, estimator, distance_fn, seed)
                ref["seed"] = seed
                ref["arm"] = aname
                enum_results.append(ref)
                print(f"[exp04] seed={seed} {aname:<26} | enum executed={ref['executed']} "
                      f"subsets={ref['n_subsets']} gap={ref['gap']} ({ref['t_enum_ms']:.0f}ms)",
                      flush=True)
                continue
            row = _run_solve(arm_cfg, tasks, uavs, scenario, estimator, distance_fn, seed)
            row["seed"] = seed
            row["arm"] = aname
            results.append(row)
            print(f"[exp04] seed={seed} {aname:<26} | done={row['n_executed']}/{row['n_tasks']} "
                  f"f1={row['f1_reward_descriptive']} f2={row['f2_cost']} "
                  f"fit={row['final_fitness']} t={row['t_total_ms']:.0f}ms "
                  f"[{row['decode_mode']}/{row['participating_policy']}]", flush=True)

    wall = round(time.perf_counter() - t_start, 3)

    # ── 汇总（描述性：逐臂跨 seed 均值/标准差）────────────────────────
    summary: dict[str, dict] = {}
    arm_names = [a["name"] for a in all_arms if not a.get("enum", False)]
    for aname in arm_names:
        rows = [r for r in results if r["arm"] == aname]
        if not rows:
            continue
        def agg(key):
            vals = [float(r[key]) for r in rows if r.get(key) is not None]
            if not vals:
                return {"mean": None, "std": None}
            mean = sum(vals) / len(vals)
            std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
            return {"mean": round(mean, 4), "std": round(std, 4)}
        summary[aname] = {
            "n_seeds": len(rows),
            "n_executed": agg("n_executed"),
            "completion_ratio": agg("completion_ratio"),
            "f1_reward_descriptive": agg("f1_reward_descriptive"),
            "f2_cost": agg("f2_cost"),
            "final_fitness": agg("final_fitness"),
            "t_total_ms": agg("t_total_ms"),
        }

    metrics = {
        "experiment": cfg["experiment"]["name"],
        "status": "ok",
        "scope_kind": "single_cycle_mechanism_ablation",
        "effective_config_sha256": sha,
        "arms": arm_names,
        "rows": results,
        "summary": summary,
        "enum_reference": enum_results,
        "disclaimer": [
            "描述性观测：不排名、不宣称完成约束、不做显著性检验",
            "F1 仅作事后指标（门禁口径下不进搜索标量 f），不得作优劣判据",
            "单周期快照：无后续周期，未执行任务记 deferred，完成约束由 exp01 检验",
            "B3 枚举参照固定贪心解码器，度量选机质量而非全流程质量",
        ],
    }
    run_meta = {
        "experiment": cfg["experiment"]["name"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "seeds": seeds,
        "arms": [a["name"] for a in all_arms],
        "n_rows": len(results),
        "n_enum": len(enum_results),
        "wall_clock_s": wall,
        "scenario": cfg["data"].get("scenario"),
        "effective_config_sha256": sha,
        "python": sys.version.split()[0],
    }

    _write_json_atomic(RESULTS_DIR / "effective_config.json", effective_base)
    _write_json_atomic(RESULTS_DIR / "metrics.json", metrics)
    _write_json_atomic(RESULTS_DIR / "run_meta.json", run_meta)
    print(f"[exp04] wrote {RESULTS_DIR / 'metrics.json'}", flush=True)
    print(f"[exp04] DONE wall={wall}s rc=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
