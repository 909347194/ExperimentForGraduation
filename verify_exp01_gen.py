"""决定性对照：同一实例（任务 seed=42、求解器 seed=42）下 max_iter=100 vs 500，
只跑周期 1，用求解器自己的记录（F1/F2/penalty/best_fitness/infeasible）回答：
「500 代到底好不好」。

注意：max_iter 同时改变温度退火 schedule（temperature=1-gen/Gen），
所以这不是「同一条搜索跑更久」，而是两条不同的搜索轨迹——必须整体比记录。
"""

import copy
import sys

sys.path.insert(0, "task_allocation/experiments/exp01_smoke")

from paths import DATA_DIR, EXP_DIR  # noqa: E402
from build import (  # noqa: E402
    build_arrivals,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from config import build_config_bundle, load_config  # noqa: E402
from task_allocation.methods.rolling.horizon import RollingHorizon  # noqa: E402
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402

SEED = 42


def run_cycle1(max_iter: int) -> dict:
    raw_cfg = load_config(EXP_DIR / "config.yaml")
    raw_cfg.setdefault("solver", {})["max_iter"] = max_iter
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = SEED
    seed_cfg.setdefault("solver", {})["seed"] = SEED
    seed_cfg.setdefault("rolling", {})["n_cycles"] = 1

    bundle = build_config_bundle(seed_cfg)
    scenario = build_scenario(raw_cfg, DATA_DIR)
    nests = scenario.nests
    terrain = build_terrain(scenario, raw_cfg)
    estimator = build_cost_estimator(terrain, raw_cfg)
    uavs = build_uavs(nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, SEED)
    batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)

    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    horizon = RollingHorizon(
        pool, fleet, nests,
        dem=terrain, estimator=estimator,
        horizon_config=bundle.horizon,
        selection_config=bundle.selection,
        problem_config=bundle.problem_model,
        stage_config=bundle.stage_model,
        solver_config=bundle.solver,
        event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    records = horizon.run(n_cycles=1, arrivals=batch.arrivals)
    rec = records[0]
    mu = dict(rec.muas) if not isinstance(rec.muas, dict) else rec.muas
    return {
        "max_iter": max_iter,
        "F1": float(mu.get("f1_reward", 0.0)),
        "F2": float(mu.get("f2_cost", 0.0)),
        "penalty": float(mu.get("penalty", 0.0)),
        "fit": float(mu.get("best_fitness", 0.0)),
        "infeasible": list(rec.infeasible_ids),
        "solve_s": float(mu.get("solve_seconds", 0.0)),
        "n_evals": int(mu.get("n_evals", 0)),
        "tours": {t["uav_id"]: list(t["task_ids"]) for t in rec.tours if t.get("task_ids")},
    }


def main() -> None:
    rows = [run_cycle1(100), run_cycle1(500)]
    print()
    print(f"{'指标':<12}{'100 代':>16}{'500 代':>16}{'Δ(500-100)':>16}")
    for key in ["F1", "F2", "penalty", "fit", "solve_s", "n_evals"]:
        a, b = rows[0][key], rows[1][key]
        print(f"{key:<12}{a:>16.2f}{b:>16.2f}{b - a:>+16.2f}")
    print(f"{'infeasible':<12}{str(rows[0]['infeasible']):>16}{str(rows[1]['infeasible']):>16}")
    print()
    for r in rows:
        print(f"max_iter={r['max_iter']} tours: {r['tours']}")
    # 适应度标尺自洽性：fit = -m*F1 + lambda*path + w_violation*penalty
    # path = F2 - penalty（F2 = path + w_penalty*penalty，w_penalty=1）
    print()
    print("自洽性检查（fit = -m·F1 + λ·path + w·penalty，path=F2-penalty）：")
    for r in rows:
        path = r["F2"] - r["penalty"]
        resid = r["fit"] + 272881.0 * r["F1"] - 1e-4 * path - 0.01 * r["penalty"]
        print(f"  max_iter={r['max_iter']}: path={path:.1f}  "
              f"残差(fit - 公式) = {resid:+.2f}（应≈0；偏离量=m 的误差×F1）")


if __name__ == "__main__":
    main()
