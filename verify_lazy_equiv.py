# -*- coding: utf-8 -*-
"""一次性验证脚本（不入库）：旧 eager 路径 vs 新惰性路径的 horizon 逐位对比。

用法：
    python verify_lazy_equiv.py out_new.json     # 新代码（惰性）
    git stash                                    # 回到旧代码
    python verify_lazy_equiv.py out_old.json     # 旧代码（全量 eager）
    git stash pop
    python verify_lazy_equiv.py --diff out_old.json out_new.json

缩小规模：n_cycles=4、max_iter=30（只验证接线正确性，不追求指标质量）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent / "task_allocation" / "experiments" / "exp01_smoke"
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP))
sys.path.insert(0, str(ROOT))

from paths import DATA_DIR  # noqa: E402
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


def run_once() -> list[dict]:
    raw = load_config(EXP / "config.yaml")
    seed_cfg = copy.deepcopy(dict(raw))
    seed_cfg.setdefault("experiment", {})["seed"] = SEED
    seed_cfg.setdefault("solver", {})["seed"] = SEED
    seed_cfg.setdefault("rolling", {})["n_cycles"] = 4
    seed_cfg.setdefault("solver", {})["max_iter"] = 30

    bundle = build_config_bundle(seed_cfg)
    scenario = build_scenario(raw, DATA_DIR)
    nests = scenario.nests
    terrain = build_terrain(scenario, raw)
    estimator = build_cost_estimator(terrain, raw)
    uavs = build_uavs(nests, raw)
    task_cfg = build_task_config(raw, SEED)
    batch = build_arrivals(nests, scenario, task_cfg, raw)

    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    horizon = RollingHorizon(
        pool, fleet, nests, dem=terrain, estimator=estimator,
        horizon_config=bundle.horizon,
        selection_config=bundle.selection,
        problem_config=bundle.problem_model,
        stage_config=bundle.stage_model,
        solver_config=bundle.solver,
        event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    records = horizon.run(n_cycles=bundle.n_cycles, arrivals=batch.arrivals)
    return [r.as_dict() for r in records]


def main() -> int:
    if sys.argv[1:] and sys.argv[1] == "--diff":
        old = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        new = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
        # solve_seconds 是挂钟计时（机器噪声），不参与确定性对比
        for rec in old + new:
            rec.get("muas", {}).pop("solve_seconds", None)
        if old == new:
            print(f"[diff] 逐位一致（除 solve_seconds 计时噪声）："
                  f"{len(old)} 个周期记录完全相同 ✔")
            return 0
        print("[diff] 不一致！")
        for i, (a, b) in enumerate(zip(old, new), 1):
            if a != b:
                for k in sorted(set(a) | set(b)):
                    if a.get(k) != b.get(k):
                        print(f"  周期{i} 字段 {k}:\n    old={a.get(k)}\n    new={b.get(k)}")
        return 1
    out = run_once()
    Path(sys.argv[1]).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[verify] wrote {sys.argv[1]}（{len(out)} 周期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
