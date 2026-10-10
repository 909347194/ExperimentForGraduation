"""exp01_smoke 入口：在真实机巢 / DEM 场景上端到端跑通两阶段 + 滚动闭环。

链路（对齐 Chapter3_README §8 技术路线）::

    data/(机巢CSV + DEM) ──► common.scenario.build_nest_scenario ──► Nest / LocalFrame
    config.yaml(tasks:)   ──► common.task_scenario.build_synthetic_tasks ──► Task
                                    ↓
    TaskPool.begin_cycle  ──► T_t = remain ∪ new ∪ release
                                    ↓
    selection.run         ──► T_t^sel   （优先级 → 可行性 → 边际收益）
                                    ↓
    muas.run_selective_muas ─► T_t^exec （分配 + 顺序 + 终点机巢，DMDE）
                                    ↓
    pool.apply_solution / fleet.apply_solution ──► 状态回写（含 z_ub）
                                    ↓
    完成反馈 ──► t+1（未执行的任务保留在池中，下周期以 REMAIN 重新参与）

所有可调参数均在 ``config.yaml``；本文件只做装配与产出，不写死任何数值。
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
ROOT = EXP_DIR.parents[2]  # 仓库根目录
DATA_DIR = ROOT / "task_allocation" / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_allocation.common.scenario import build_nest_scenario  # noqa: E402
from task_allocation.common.task_scenario import (  # noqa: E402
    SyntheticTaskConfig,
    build_synthetic_tasks,
    build_task_batches,
)
from task_allocation.common.types import UAV  # noqa: E402
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain  # noqa: E402
from task_allocation.methods.muas.cost.vertical_section import (  # noqa: E402
    VerticalSectionCostEstimator,
)
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import MUASStageConfig  # noqa: E402
from task_allocation.methods.rolling.event_trigger import (  # noqa: E402
    EventTriggerConfig,
)
from task_allocation.methods.rolling.horizon import (  # noqa: E402
    HorizonConfig,
    RollingHorizon,
)
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402


def load_config(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("需要 PyYAML：请在 pyproject.toml 中加入 pyyaml 依赖后安装")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_scenario(cfg: dict):
    """按 data/problem 段构建机巢场景。"""
    data_cfg = cfg.get("data", {}) or {}
    if data_cfg.get("source", "synthetic") != "file":
        return None

    problem_cfg = cfg["problem"]
    csv_path = DATA_DIR / data_cfg["path"]
    dem_name = data_cfg.get("dem")
    dem_path = (DATA_DIR / dem_name) if dem_name else None

    return build_nest_scenario(
        csv_path,
        count=problem_cfg.get("n_nests"),
        capacity=problem_cfg.get("nest_capacity", 10),
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=dem_path,
    )


def build_terrain(scenario, cfg: dict) -> DEMTerrain | None:
    """把经纬度 DEM 重采样成局部米制网格，供垂直切面代价估算使用。

    网格范围 = 机巢包围盒 + ``cost.dem_margin_m``，需覆盖任务散布范围，
    否则代价估算会退化到网格边界值。
    """
    if scenario is None or scenario.dem is None:
        return None

    cost_cfg = cfg.get("cost", {}) or {}
    step_m = float(cost_cfg.get("dem_step_m", 200.0))
    margin = float(cost_cfg.get("dem_margin_m", 20000.0))

    xs = [n.x for n in scenario.nests]
    ys = [n.y for n in scenario.nests]
    extent = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
    return scenario.dem.to_metric_terrain(scenario.frame, step_m=step_m, extent=extent)


def build_uavs(nests, cfg: dict) -> list[UAV]:
    """按 problem 段生成机队：轮转驻留在各机巢，占用对应泊位。"""
    p = cfg["problem"]
    n_uavs = int(p["n_uavs"])
    speed = float(p.get("uav_speed", 15.0))
    max_range = float(p.get("uav_max_range", 120000.0))
    if not nests:
        raise ValueError("无机巢，无法生成机队")
    return [
        UAV(
            id=i,
            x=nests[i % len(nests)].x,
            y=nests[i % len(nests)].y,
            z=nests[i % len(nests)].z,
            remaining_range=max_range,
            speed=speed,
            nest_id=nests[i % len(nests)].id,
        )
        for i in range(n_uavs)
    ]


def build_task_config(cfg: dict) -> SyntheticTaskConfig:
    t = cfg.get("tasks", {}) or {}
    return SyntheticTaskConfig(
        n_tasks=int(t.get("n_tasks", 10)),
        seed=int(cfg["experiment"].get("seed", 42)),
        spread_m=float(t.get("spread_m", 12000.0)),
        reward_min=float(t.get("reward_min", 1.0)),
        reward_max=float(t.get("reward_max", 10.0)),
        deadline_min=float(t.get("deadline_min", 3600.0)),
        deadline_max=float(t.get("deadline_max", 14400.0)),
        anchor_to_nests=bool(t.get("anchor_to_nests", True)),
    )


def main() -> int:
    cfg_path = EXP_DIR / "config.yaml"
    cfg = load_config(cfg_path)

    out_dir = EXP_DIR / cfg["experiment"]["output_dir"]
    plot_dir = EXP_DIR / cfg["experiment"]["plot_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    plot_dir.mkdir(parents=True, exist_ok=True)

    verbose = bool(cfg.get("run", {}).get("verbose", True))
    t_wall = time.time()

    # ── 场景 ──────────────────────────────────────────────────────
    scenario = build_scenario(cfg)
    if scenario is None:
        raise RuntimeError("data.source 必须为 file：本实验依赖真实机巢与 DEM")
    nests = scenario.nests
    terrain = build_terrain(scenario, cfg)
    if verbose:
        print(
            f"[exp01] 场景：{scenario.count} 机巢，容量={nests[0].capacity}，"
            f"DEM={'已接入' if terrain is not None else '未接入'}，"
            f"frame=({scenario.frame.lat0:.6f}, {scenario.frame.lon0:.6f})"
        )

    cost_cfg = cfg.get("cost", {}) or {}
    estimator = VerticalSectionCostEstimator(
        dem_terrain=terrain,
        min_clearance=float(cost_cfg.get("min_clearance", 50.0)),
        max_clearance=float(cost_cfg.get("max_clearance", 300.0)),
        climb_rate=float(cost_cfg.get("climb_rate", 0.15)),
        num_samples=int(cost_cfg.get("num_samples", 200)),
    )

    # ── 实体 ──────────────────────────────────────────────────────
    uavs = build_uavs(nests, cfg)
    task_cfg = build_task_config(cfg)

    rolling_cfg = cfg.get("rolling", {}) or {}
    cycle_length = float(rolling_cfg.get("cycle_length", 600.0))
    n_cycles = int(rolling_cfg.get("n_cycles", 1))

    initial = build_synthetic_tasks(
        nests, scenario.frame, scenario.dem, task_cfg,
        arrival_time=0.0, id_offset=0,
    )
    # arrivals_per_cycle 从第 2 周期起；下标 0 对应 cycle 2
    later_counts = [int(c) for c in rolling_cfg.get("arrivals_per_cycle", []) or []]
    later = build_task_batches(
        nests, scenario.frame, scenario.dem, task_cfg,
        per_cycle=later_counts, cycle_length=cycle_length, id_offset=len(initial),
    )
    arrivals = {c: tasks for c, tasks in later.items() if c >= 2}
    arrivals[1] = initial

    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)

    # ── 算法配置（全部来自 yaml）──────────────────────────────────
    m = cfg.get("muas", {}) or {}
    problem_config = MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        lambda_cost=float(m.get("lambda_cost", 1.0e-4)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        default_service_time=float(m.get("default_service_time", 0.0)),
    )
    stage_config = MUASStageConfig(
        max_participating_uavs=m.get("max_participating_uavs", None),
        participating_policy=str(m.get("participating_policy", "cheapest")),
        prune_unprofitable=bool(m.get("prune_unprofitable", True)),
        prune_margin=float(m.get("prune_margin", 0.0)),
        prune_max_rounds=int(m.get("prune_max_rounds", 8)),
        prune_wait_bonus=float(m.get("prune_wait_bonus", 1.0)),
        wait_scale=float(m.get("wait_scale", 3600.0)),
        end_nest_in_encoding=bool(m.get("end_nest_in_encoding", True)),
        lamarckian=bool(m.get("lamarckian", True)),
    )
    s = cfg.get("solver", {}) or {}
    solver_config = DMDEConfig(
        pop_size=int(s.get("population", 20)),
        max_generations=int(s.get("max_iter", 40)),
        zeta=int(s.get("zeta", 3)),
        delta=float(s.get("delta", 0.3)),
        seed=s.get("seed", cfg["experiment"].get("seed")),
        verbose=bool(s.get("verbose", False)),
        log_interval=int(s.get("log_interval", 10)),
    )
    e = (rolling_cfg.get("events", {}) or {})
    event_config = EventTriggerConfig(
        on_uav_fault=bool(e.get("on_uav_fault", True)),
        on_nest_unavailable=bool(e.get("on_nest_unavailable", True)),
        on_urgent_task=bool(e.get("on_urgent_task", True)),
        reward_threshold=float(e.get("reward_threshold", 8.0)),
        slack_threshold=float(e.get("slack_threshold", 900.0)),
    )
    horizon_config = HorizonConfig(
        n_cycles=n_cycles,
        cycle_length=cycle_length,
        complete_after_cycle=bool(rolling_cfg.get("complete_after_cycle", True)),
        recharge_at_nest=bool(rolling_cfg.get("recharge_at_nest", True)),
        uav_max_range=float(cfg["problem"].get("uav_max_range", 120000.0)),
        faults={int(k): [int(x) for x in v] for k, v in (rolling_cfg.get("faults", {}) or {}).items()},
        strict_capacity=bool(rolling_cfg.get("strict_capacity", False)),
    )

    horizon = RollingHorizon(
        pool,
        fleet,
        nests,
        dem=terrain,
        estimator=estimator,
        horizon_config=horizon_config,
        selection_config=RollingHorizon.selection_config_from_dict(cfg.get("selection")),
        problem_config=problem_config,
        stage_config=stage_config,
        solver_config=solver_config,
        event_config=event_config,
        nest_offset=int(cost_cfg.get("nest_offset", 1_000_000)),
    )

    # ── 滚动求解 ──────────────────────────────────────────────────
    records = horizon.run(n_cycles=n_cycles, arrivals=arrivals)

    if verbose:
        for r in records:
            muas = r.muas
            print(
                f"[exp01] 周期 {r.cycle} @t={r.current_time:7.1f}s | "
                f"T_t={r.pool_counts.get('n_total', 0)}"
                f"(remain {r.pool_counts.get('n_remain', 0)}/"
                f"new {r.pool_counts.get('n_new', 0)}/"
                f"release {r.pool_counts.get('n_release', 0)}) "
                f"K_avail={r.selection.get('n_uav_avail', 0)} | "
                f"T_sel={muas.get('n_selected', 0)} → "
                f"T_exec={r.muas.get('n_executed', 0)}"
                f"(保留 {r.muas.get('n_deferred', 0)}) | "
                f"F1={muas.get('f1_reward', 0.0):.2f} "
                f"F2={muas.get('f2_cost', 0.0):.0f}m | "
                f"{muas.get('solve_seconds', 0.0):.2f}s"
            )

    # ── 指标 ──────────────────────────────────────────────────────
    finished = pool.finished_tasks()
    completed = [t for t in finished if t.meta.get("completed_at") is not None]
    waits = [
        float(t.meta["completed_at"]) - float(t.arrival_time) for t in completed
    ]
    n_arrived = len(initial) + sum(len(v) for v in later.values())
    total_cost = sum(float(r.muas.get("f2_cost", 0.0)) for r in records)
    total_reward = sum(float(r.muas.get("f1_reward", 0.0)) for r in records)
    solve_ms = sum(float(r.muas.get("solve_seconds", 0.0)) for r in records) * 1000.0
    k_avail = [r.selection.get("n_uav_avail", 0) for r in records]

    metrics: dict[str, Any] = {
        "status": "ok",
        "experiment": cfg["experiment"]["name"],
        "n_cycles": n_cycles,
        "problem": {
            "n_tasks_initial": len(initial),
            "n_arrived": n_arrived,
            "n_uavs": len(uavs),
            "n_nests": len(nests),
            "nest_capacity": nests[0].capacity,
        },
        "local_frame": {"lat0": scenario.frame.lat0, "lon0": scenario.frame.lon0},
        "nests": [
            {
                "id": n.id,
                "x": round(n.x, 3),
                "y": round(n.y, 3),
                "z": round(n.z, 3),
                "capacity": n.capacity,
                "lat": n.meta["lat"],
                "lon": n.meta["lon"],
            }
            for n in nests
        ],
        "cycles": [r.as_dict() for r in records],
        "summary": {
            "n_completed": len(completed),
            "completion_rate": (len(completed) / n_arrived) if n_arrived else 0.0,
            "avg_wait": (sum(waits) / len(waits)) if waits else 0.0,
            "total_reward": round(total_reward, 4),
            "total_cost": round(total_cost, 3),
            "solve_time_ms": round(solve_ms, 2),
            "K_avail_mean": (sum(k_avail) / len(k_avail)) if k_avail else 0.0,
            "n_deferred_last": records[-1].muas.get("n_deferred", 0) if records else 0,
            "n_remaining_pool": len(pool),
            "wall_seconds": round(time.time() - t_wall, 3),
        },
        "fleet": fleet.snapshot_json(),
        "pool": pool.snapshot_json(),
    }

    run_meta = {
        "experiment": cfg["experiment"]["name"],
        "seed": cfg["experiment"]["seed"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(cfg_path.relative_to(ROOT)),
        "repo_root": str(ROOT),
        "dem_terrain": (
            None
            if terrain is None
            else {
                "width": terrain.meta.width,
                "height": terrain.meta.height,
                "step_m": float(cost_cfg.get("dem_step_m", 200.0)),
                "bounds": [round(v, 1) for v in terrain.meta.bounds],
            }
        ),
    }

    if cfg.get("run", {}).get("save_results", True):
        (out_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "run_meta.json").write_text(
            json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if verbose:
        sm = metrics["summary"]
        print(
            f"[exp01] 汇总：完成 {sm['n_completed']}/{n_arrived}"
            f"({sm['completion_rate']:.1%})，平均等待 {sm['avg_wait']:.0f}s，"
            f"总代价 {sm['total_cost']:.0f}m，求解 {sm['solve_time_ms']:.0f}ms，"
            f"池内剩余 {sm['n_remaining_pool']}"
        )
        print(f"[exp01] wrote {out_dir / 'metrics.json'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
