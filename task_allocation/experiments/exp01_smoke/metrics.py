# -*- coding: utf-8 -*-
"""exp01_smoke 产物组装：指标汇总、运行元信息、分配结果与终端报表。

只做「内存对象 → 可机读 dict」与打印，不跑算法、不解析 yaml。
三个产物的分工（见 ``experiments/README.md``）：

    results/metrics.json    数值指标：summary + 各周期诊断（不含分配结构）
    results/run_meta.json   运行元信息：seed、时间戳、配置路径、DEM 网格
    results/solution.json   分配结构：任务表 + 机巢表 + 各周期航次与收敛轨迹
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import paths  # noqa: F401  # sys.path 引导：挂载仓库根，供 task_allocation.* 导入

from task_allocation.common.types import Nest, Task, UAV

from build import TaskBatch
from config import ConfigBundle

__all__ = [
    "RunFacts",
    "build_metrics",
    "build_run_meta",
    "build_solution",
    "write_json",
    "print_cycle_report",
    "print_summary",
]


# ---------------------------------------------------------------------------
# 一次运行的全部事实
# ---------------------------------------------------------------------------


@dataclass
class RunFacts:
    """滚动求解结束后，组装产物所需的全部上下文。

    把散落的实体收在一处，避免 ``build_*`` 函数出现十几个位置参数。
    """

    bundle: ConfigBundle
    scenario: Any
    nests: list[Nest]
    terrain: Any
    uavs: list[UAV]
    batch: TaskBatch
    records: list[Any]
    pool: Any
    fleet: Any
    wall_seconds: float


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _nest_rows(nests: Sequence[Nest]) -> list[dict[str, Any]]:
    return [
        {
            "id": n.id,
            "x": round(n.x, 3),
            "y": round(n.y, 3),
            "z": round(n.z, 3),
            "capacity": n.capacity,
            "lat": n.meta.get("lat"),
            "lon": n.meta.get("lon"),
        }
        for n in nests
    ]


def _task_row(t: Task) -> dict[str, Any]:
    return {
        "id": int(t.id),
        "x": round(float(t.x), 3),
        "y": round(float(t.y), 3),
        "z": round(float(t.z), 3),
        "reward": round(float(t.reward), 4),
        "arrival_time": round(float(t.arrival_time), 3),
        "earliest": t.earliest,
        "latest": t.latest,
        "status": t.status.value,
        "completed_at": t.meta.get("completed_at"),
    }


# ---------------------------------------------------------------------------
# 产物
# ---------------------------------------------------------------------------


def build_metrics(f: RunFacts) -> dict[str, Any]:
    """汇总指标（不含分配结构与收敛轨迹，那些进 solution.json）。"""
    records = f.records
    finished = f.pool.finished_tasks()
    completed = [t for t in finished if t.meta.get("completed_at") is not None]
    waits = [float(t.meta["completed_at"]) - float(t.arrival_time) for t in completed]

    tasks = f.batch.all_tasks()
    n_arrived = len(tasks)
    total_cost = sum(float(r.muas.get("f2_cost", 0.0)) for r in records)
    total_reward = sum(float(r.muas.get("f1_reward", 0.0)) for r in records)
    solve_ms = sum(float(r.muas.get("solve_seconds", 0.0)) for r in records) * 1000.0
    k_avail = [r.selection.get("n_uav_avail", 0) for r in records]
    n_cycles = int(f.bundle.horizon.n_cycles)

    return {
        "status": "ok",
        "experiment": f.bundle.experiment.get("name"),
        "n_cycles": n_cycles,
        "problem": {
            "n_tasks_initial": len(f.batch.initial),
            "n_arrived": n_arrived,
            "n_arrived_per_cycle": {
                str(k): v for k, v in f.batch.per_cycle_counts().items()
            },
            "n_uavs": len(f.uavs),
            "n_nests": len(f.nests),
            "nest_capacity": f.nests[0].capacity if f.nests else None,
        },
        "local_frame": {
            "lat0": f.scenario.frame.lat0,
            "lon0": f.scenario.frame.lon0,
        },
        "nests": _nest_rows(f.nests),
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
            "n_remaining_pool": len(f.pool),
            "wall_seconds": round(f.wall_seconds, 3),
        },
        "fleet": f.fleet.snapshot_json(),
        "pool": f.pool.snapshot_json(),
    }


def build_run_meta(
    f: RunFacts, *, cfg_path: Path, root: Path = paths.ROOT
) -> dict[str, Any]:
    """运行元信息：复现实验所需的环境与配置快照。"""
    cost_cfg = f.bundle.cost
    terrain_meta = None
    if f.terrain is not None:
        terrain_meta = {
            "width": f.terrain.meta.width,
            "height": f.terrain.meta.height,
            "step_m": float(cost_cfg.get("dem_step_m", 200.0)),
            "bounds": [round(v, 1) for v in f.terrain.meta.bounds],
        }

    return {
        "experiment": f.bundle.experiment.get("name"),
        "seed": f.bundle.seed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(Path(cfg_path).relative_to(root)),
        "repo_root": str(root),
        "dem_terrain": terrain_meta,
    }


def build_solution(f: RunFacts) -> dict[str, Any]:
    """分配结构：任务表 + 机巢表 + 各周期航次与 DMDE 收敛轨迹。"""
    return {
        "experiment": f.bundle.experiment.get("name"),
        "local_frame": {
            "lat0": f.scenario.frame.lat0,
            "lon0": f.scenario.frame.lon0,
        },
        "nests": _nest_rows(f.nests),
        "tasks": [_task_row(t) for t in f.batch.all_tasks()],
        "cycles": [
            {
                "cycle": r.cycle,
                "current_time": round(float(r.current_time), 3),
                "tours": list(r.tours),
                "cost_history": list(r.cost_history),
            }
            for r in f.records
        ],
    }


# ---------------------------------------------------------------------------
# 终端报表
# ---------------------------------------------------------------------------


def print_cycle_report(records: Sequence[Any]) -> None:
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
            f"T_exec={muas.get('n_executed', 0)}"
            f"(保留 {muas.get('n_deferred', 0)}) | "
            f"F1={muas.get('f1_reward', 0.0):.2f} "
            f"F2={muas.get('f2_cost', 0.0):.0f}m | "
            f"{muas.get('solve_seconds', 0.0):.2f}s"
        )


def print_summary(metrics: dict[str, Any], n_arrived: int, out_dir: Path) -> None:
    sm = metrics["summary"]
    print(
        f"[exp01] 汇总：完成 {sm['n_completed']}/{n_arrived}"
        f"({sm['completion_rate']:.1%})，平均等待 {sm['avg_wait']:.0f}s，"
        f"总代价 {sm['total_cost']:.0f}m，求解 {sm['solve_time_ms']:.0f}ms，"
        f"池内剩余 {sm['n_remaining_pool']}"
    )
    print(f"[exp01] wrote {Path(out_dir) / 'metrics.json'}")
