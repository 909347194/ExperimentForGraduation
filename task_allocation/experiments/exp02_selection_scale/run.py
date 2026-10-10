# -*- coding: utf-8 -*-
"""exp02_selection_scale 入口：量化第一阶段「任务选择」的规模压缩收益。

问题背景（Chapter3_README §1(4)）：
    当 |T_t| >> K_avail(t) 时，把全部任务直接交给精细组合优化会显著扩大
    搜索空间。第一阶段用 Top-αK 把 T_t 压到 T_t^sel，据称能提高实时性。

exp01_smoke 跑在「机多任务少」一侧（10 任务 / 24 机，αK=60），Top-αK 从不
触发，无法验证该机制。本实验固定 K_avail=8，把任务规模拉到 50/100/200，
分别测量：

    T_cost   建三维代价矩阵的耗时（O(n²)，在第一阶段**之前**就发生）
    T_sel    第一阶段耗时 + 压缩率 |T_t| → |T_t^sel|
    T_muas   第二阶段对候选集求解的耗时
    T_full   【对照】第二阶段直接对全量任务求解（不经过第一阶段）

由此回答两个问题：
    1. 压缩是否真的发生了？压到多少？
    2. 分层省下的搜索时间，是否被 O(n²) 代价构建吃掉？
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[2]
DATA_DIR = ROOT / "task_allocation" / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_allocation.common.scenario import build_nest_scenario  # noqa: E402
from task_allocation.common.task_scenario import (  # noqa: E402
    SyntheticTaskConfig,
    build_synthetic_tasks,
)
from task_allocation.common.types import UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)
from task_allocation.methods.selection import run as run_selection  # noqa: E402
from task_allocation.methods.selection import SelectionConfig  # noqa: E402


def load_config(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("需要 PyYAML")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def selection_config_from(cfg: dict, current_time: float = 0.0) -> SelectionConfig:
    d = cfg.get("selection", {}) or {}
    from task_allocation.methods.selection.priority import PriorityConfig, PriorityWeights

    weights = d.get("weights") or {}
    pc = PriorityConfig(
        alpha=float(d.get("alpha", 2.5)),
        synergy_radius=float(d.get("synergy_radius", 5000.0)),
        wait_scale=float(d.get("wait_scale", 3600.0)),
        current_time=current_time,
    )
    if weights:
        pc.weights = PriorityWeights(**{k: float(v) for k, v in weights.items()}).normalized()
    return SelectionConfig(priority=pc, current_time=current_time)


def main() -> int:
    cfg_path = EXP_DIR / "config.yaml"
    cfg = load_config(cfg_path)
    out_dir = EXP_DIR / cfg["experiment"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 场景：机巢 + DEM + 局部平面 ────────────────────────────────
    data_cfg = cfg["data"]
    problem_cfg = cfg["problem"]
    scenario = build_nest_scenario(
        DATA_DIR / data_cfg["path"],
        count=problem_cfg["n_nests"],
        capacity=problem_cfg["nest_capacity"],
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=DATA_DIR / data_cfg["dem"],
    )
    terrain = scenario.dem.to_metric_terrain(scenario.frame, step_m=200.0)

    uav_cfg = problem_cfg
    n_uavs = int(uav_cfg["n_uavs"])
    uavs = [
        UAV(id=i, x=0.0, y=0.0, z=0.0, nest_id=i % problem_cfg["n_nests"], remaining_range=1e9)
        for i in range(n_uavs)
    ]

    # ── 算法配置 ─────────────────────────────────────────────────
    solver_cfg = DMDEConfig(
        pop_size=int(cfg["solver"].get("pop_size", 20)),
        max_generations=int(cfg["solver"].get("max_generations", 60)),
        seed=int(cfg["experiment"].get("seed", 42)),
    )
    m = cfg.get("problem", {})
    problem_model = MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        lambda_cost=float(m.get("lambda_cost", 1.0e-4)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
    )
    s = cfg.get("stage", {}) or {}
    stage_cfg = MUASStageConfig(
        prune_unprofitable=bool(s.get("prune_unprofitable", True)),
        prune_max_rounds=int(s.get("prune_max_rounds", 8)),
        prune_margin=float(s.get("prune_margin", 0.0)),
        prune_wait_bonus=float(s.get("prune_wait_bonus", 1.0)),
        wait_scale=float(s.get("wait_scale", 3600.0)),
    )
    baseline_scales = set(cfg["run"].get("full_muas_baseline_scales") or [])

    results: list[dict[str, Any]] = []
    print(f"[exp02] K_avail={n_uavs}, n_nests={problem_cfg['n_nests']}, "
          f"alpha={cfg['selection'].get('alpha', 2.5)}")

    for scale in cfg["scales"]:
        n_tasks = int(scale)
        tcfg = SyntheticTaskConfig(
            n_tasks=n_tasks,
            seed=int(cfg["experiment"].get("seed", 42)) + n_tasks,
            spread_m=float(cfg["tasks"].get("spread_m", 25000.0)),
            reward_min=float(cfg["tasks"].get("reward_min", 1.0)),
            reward_max=float(cfg["tasks"].get("reward_max", 10.0)),
            deadline_min=float(cfg["tasks"].get("deadline_min", 3600.0)),
            deadline_max=float(cfg["tasks"].get("deadline_max", 28800.0)),
            anchor_to_nests=bool(cfg["tasks"].get("anchor_to_nests", True)),
        )
        tasks = build_synthetic_tasks(scenario.nests, scenario.frame, scenario.dem, tcfg)

        row: dict[str, Any] = {"n_tasks": n_tasks, "n_uav_avail": n_uavs}

        # 1) 三维代价矩阵（第一阶段之前就发生）
        t0 = time.perf_counter()
        cb = build_pairwise_costs(tasks, uavs, scenario.nests, dem=terrain)
        row["t_cost_matrix_ms"] = (time.perf_counter() - t0) * 1000.0
        n_pairs = n_uavs * n_tasks + n_tasks * n_tasks
        row["n_pairs"] = n_pairs

        # 2) 第一阶段：任务选择
        sel_cfg = selection_config_from(cfg, current_time=0.0)
        t0 = time.perf_counter()
        sel = run_selection(
            tasks, uavs, sel_cfg, pairwise_cost=cb.provider.pairwise
        )
        row["t_selection_ms"] = (time.perf_counter() - t0) * 1000.0
        row["selection"] = dict(sel.diagnostics)
        row["n_selected"] = len(sel.selected)
        row["compression_ratio"] = round(n_tasks / max(len(sel.selected), 1), 3)

        # 3) 第二阶段：对候选集求解
        t0 = time.perf_counter()
        res = run_selective_muas(
            sel.selected, uavs, scenario.nests, cb,
            problem_config=problem_model, stage_config=stage_cfg,
            solver_config=solver_cfg,
        )
        row["t_muas_ms"] = (time.perf_counter() - t0) * 1000.0
        row["muas"] = {
            "model_type": res.meta.get("model_type") if getattr(res, "meta", None) else None,
            "n_executed": len(res.solution.executed_task_ids),
            "n_deferred": len(sel.selected) - len(res.solution.executed_task_ids),
            "f1_reward": round(res.solution.f1_reward, 3),
            "f2_cost": round(res.solution.f2_cost, 1),
        }
        row["t_total_ms"] = row["t_cost_matrix_ms"] + row["t_selection_ms"] + row["t_muas_ms"]

        # 4) 对照：不经过第一阶段，直接对全量任务求解
        if n_tasks in baseline_scales:
            t0 = time.perf_counter()
            res_full = run_selective_muas(
                tasks, uavs, scenario.nests, cb,
                problem_config=problem_model, stage_config=stage_cfg,
                solver_config=solver_cfg,
            )
            row["t_muas_full_ms"] = (time.perf_counter() - t0) * 1000.0
            row["muas_full"] = {
                "n_executed": len(res_full.solution.executed_task_ids),
                "f1_reward": round(res_full.solution.f1_reward, 3),
                "f2_cost": round(res_full.solution.f2_cost, 1),
            }

        results.append(row)
        print(
            f"[exp02] N={n_tasks:>4} | cost={row['t_cost_matrix_ms']:>8.0f}ms "
            f"sel={row['t_selection_ms']:>7.1f}ms muas={row['t_muas_ms']:>8.0f}ms | "
            f"sel {n_tasks}->{row['n_selected']} ({row['compression_ratio']}x) | "
            f"exec={row['muas']['n_executed']}"
            + (f" | full-muas={row.get('t_muas_full_ms', 0):.0f}ms" if "t_muas_full_ms" in row else "")
        )

    # ── 产物 ─────────────────────────────────────────────────────
    metrics = {
        "status": "ok",
        "experiment": cfg["experiment"]["name"],
        "K_avail": n_uavs,
        "alpha": cfg["selection"].get("alpha", 2.5),
        "top_alpha_K": round(float(cfg["selection"].get("alpha", 2.5)) * n_uavs, 1),
        "scales": results,
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "experiment": cfg["experiment"]["name"],
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "config_path": str(cfg_path.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[exp02] wrote {out_dir / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
