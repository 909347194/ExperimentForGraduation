# -*- coding: utf-8 -*-
"""exp03_event_rolling 实体装配：场景 + 任务批次 + burst 任务生成 + 事件翻译。

在 exp01 build 的基础上增加：
- burst 任务生成（events.schedule.burst_tasks → 合入 arrivals）
- 事件时刻表 → HorizonConfig 翻译（faults / nest_closures）
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import paths  # noqa: F401

from task_allocation.common.scenario import build_nest_scenario
from task_allocation.common import scenario_data
from task_allocation.common.task_scenario import (
    DOMAIN_DEM_EXTENT,
    DOMAIN_NEST_ANCHORED,
    SyntheticTaskConfig,
    build_synthetic_tasks,
    build_task_batches,
    dem_planar_extent,
    region_planar_envelope,
)
from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
from task_allocation.methods.muas.cost.vertical_section import (
    VerticalSectionCostEstimator,
)
from task_allocation.methods.utils.building_layer import BuildingLayer

__all__ = [
    "TaskBatch",
    "build_scenario",
    "build_terrain",
    "build_building_layer",
    "build_cost_estimator",
    "build_uavs",
    "build_task_config",
    "build_arrivals",
    "inject_burst_tasks",
]


@dataclass(frozen=True)
class TaskBatch:
    initial: list[Task]
    arrivals: dict[int, list[Task]]

    def all_tasks(self) -> list[Task]:
        return [t for cycle in sorted(self.arrivals) for t in self.arrivals[cycle]]

    def per_cycle_counts(self) -> dict[int, int]:
        return {c: len(v) for c, v in sorted(self.arrivals.items())}


# ---------------------------------------------------------------------------
# 场景 / 地形（与 exp01 相同）
# ---------------------------------------------------------------------------


def _scenario_dir(cfg: Mapping[str, Any], data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    return scenario_data.scenario_dir((cfg.get("data") or {}).get("scenario"))


def build_scenario(cfg: Mapping[str, Any], data_dir: Path | None = None) -> Any:
    data_cfg = cfg.get("data", {}) or {}
    if data_cfg.get("source", "synthetic") != "file":
        raise RuntimeError("data.source 必须为 file")
    problem_cfg = cfg["problem"]
    d = _scenario_dir(cfg, data_dir)
    csv_path = d / data_cfg["path"]
    dem_name = data_cfg.get("dem")
    dem_path = (d / dem_name) if dem_name else None
    return build_nest_scenario(
        csv_path,
        count=problem_cfg.get("n_nests"),
        capacity=problem_cfg.get("nest_capacity", 10),
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=dem_path,
    )


def build_terrain(scenario: Any, cfg: Mapping[str, Any]) -> DEMTerrain | None:
    if scenario is None or scenario.dem is None:
        return None
    cost_cfg = cfg.get("cost", {}) or {}
    step_m = float(cost_cfg.get("dem_step_m", 200.0))
    margin = float(cost_cfg.get("dem_margin_m", 20000.0))
    xs = [n.x for n in scenario.nests]
    ys = [n.y for n in scenario.nests]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    tasks_cfg = cfg.get("tasks", {}) or {}
    domain = str(tasks_cfg.get("domain", DOMAIN_NEST_ANCHORED) or "").strip().lower()
    if domain == DOMAIN_DEM_EXTENT:
        inset = float(tasks_cfg.get("dem_inset_m", 0.0))
        dx0, dy0, dx1, dy1 = dem_planar_extent(scenario.dem, scenario.frame, inset)
        x0, y0 = min(x0, dx0), min(y0, dy0)
        x1, y1 = max(x1, dx1), max(y1, dy1)
    region_rel = (
        (cfg.get("tasks", {}) or {}).get("region_geojson")
        or (cfg.get("data", {}) or {}).get("region_geojson")
    )
    if region_rel is not None:
        renv = region_planar_envelope(_scenario_dir(cfg) / region_rel, scenario.frame)
        if renv is not None:
            x0, y0 = min(x0, renv[0]), min(y0, renv[1])
            x1, y1 = max(x1, renv[2]), max(y1, renv[3])
    extent = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    return scenario.dem.to_metric_terrain(scenario.frame, step_m=step_m, extent=extent)


def build_building_layer(cfg: Mapping[str, Any], scenario: Any, data_dir: Path | None = None) -> BuildingLayer | None:
    data_cfg = cfg.get("data", {}) or {}
    name = data_cfg.get("buildings")
    if not name or scenario is None:
        return None
    base = _scenario_dir(cfg, data_dir) / str(name)
    if base.is_dir():
        paths_list: list[Path] = sorted(base.glob("*.shp"))
    elif base.suffix.lower() == ".shp":
        paths_list = [base]
    else:
        raise FileNotFoundError(f"建筑数据不存在：{base}")
    if not paths_list:
        raise FileNotFoundError(f"建筑目录没有 .shp 文件：{base}")
    return BuildingLayer.from_shapefiles(paths_list, scenario.frame)


def build_cost_estimator(
    terrain: DEMTerrain | None,
    cfg: Mapping[str, Any],
    buildings: BuildingLayer | None = None,
) -> VerticalSectionCostEstimator:
    c = cfg.get("cost", {}) or {}
    return VerticalSectionCostEstimator(
        dem_terrain=terrain,
        min_clearance=float(c.get("min_clearance", 50.0)),
        max_clearance=float(c.get("max_clearance", 300.0)),
        climb_rate=float(c.get("climb_rate", 0.15)),
        num_samples=int(c.get("num_samples", 200)),
        buildings=buildings,
    )


# ---------------------------------------------------------------------------
# 机队 / 任务
# ---------------------------------------------------------------------------


def build_uavs(nests: Sequence[Nest], cfg: Mapping[str, Any]) -> list[UAV]:
    p = cfg["problem"]
    fleet_file = p.get("fleet_file")
    if fleet_file:
        from task_allocation.common.fleet_spec import build_uavs_from_spec, load_fleet_spec
        spec_path = Path(str(fleet_file))
        if not spec_path.is_absolute():
            spec_path = paths.EXP_DIR / spec_path
        return build_uavs_from_spec(load_fleet_spec(spec_path), nests)
    n_uavs = int(p["n_uavs"])
    speed = float(p.get("uav_speed", 15.0))
    max_range = float(p.get("uav_max_range", 120000.0))
    max_time = p.get("uav_max_time")
    if max_time is None and speed > 0:
        max_time = max_range / speed
    if not nests:
        raise ValueError("无机巢")
    return [
        UAV(
            id=i,
            x=nests[i % len(nests)].x,
            y=nests[i % len(nests)].y,
            z=nests[i % len(nests)].z,
            remaining_range=max_range,
            speed=speed,
            max_time=max_time,
            nest_id=nests[i % len(nests)].id,
        )
        for i in range(n_uavs)
    ]


def build_task_config(cfg: Mapping[str, Any], seed: int) -> SyntheticTaskConfig:
    t = cfg.get("tasks", {}) or {}
    rolling = cfg.get("rolling", {}) or {}
    cycle_length = float(rolling.get("cycle_length", 600.0))
    data_cfg = cfg.get("data", {}) or {}
    tasks_cfg_rg = cfg.get("tasks", {}) or {}
    region_rel = tasks_cfg_rg.get("region_geojson") or data_cfg.get("region_geojson")
    region_path = str(_scenario_dir(cfg) / region_rel) if region_rel else None
    return SyntheticTaskConfig(
        n_tasks=int(t.get("n_tasks", 10)),
        seed=int(seed),
        domain=str(t.get("domain", DOMAIN_NEST_ANCHORED)).strip().lower(),
        dem_inset_m=float(t.get("dem_inset_m", 0.0)),
        max_reject_trials=int(t.get("max_reject_trials", 8)),
        spread_m=float(t.get("spread_m", 12000.0)),
        reward_min=float(t.get("reward_min", 1.0)),
        reward_max=float(t.get("reward_max", 10.0)),
        deadline_min=float(t.get("deadline_min", 3600.0)),
        deadline_max=float(t.get("deadline_max", 14400.0)),
        deadline_cycles_min=float(t.get("deadline_cycles_min", 0.5)),
        deadline_cycles_max=float(t.get("deadline_cycles_max", 2.0)),
        cycle_length=cycle_length,
        anchor_to_nests=bool(t.get("anchor_to_nests", True)),
        region_geojson=region_path,
        sequence_chain_ratio=float(t.get("sequence_chain_ratio", 0.0)),
        sequence_chain_len=int(t.get("sequence_chain_len", 2)),
    )


def build_arrivals(
    nests: Sequence[Nest],
    scenario: Any,
    task_cfg: SyntheticTaskConfig,
    cfg: Mapping[str, Any],
) -> TaskBatch:
    import numpy as np

    rolling_cfg = cfg.get("rolling", {}) or {}
    cycle_length = float(rolling_cfg.get("cycle_length", 600.0))
    n_cycles = int(rolling_cfg.get("n_cycles", 1))

    arr_spec = rolling_cfg.get("arrivals") or {}
    if isinstance(arr_spec, dict) and arr_spec:
        mode = str(arr_spec.get("mode", "poisson")).lower()
    else:
        mode = "poisson"
        arr_spec = {"mode": "poisson", "lambda": 5.0}

    rng = np.random.default_rng(int(task_cfg.seed) + 1000)
    later_counts: list[int] = []
    if mode == "fixed":
        later_counts = [int(c) for c in arr_spec.get("counts", [])]
    elif mode == "poisson":
        lam = float(arr_spec.get("lambda", 5.0))
        later_counts = [int(rng.poisson(lam)) for _ in range(max(n_cycles - 1, 0))]
    elif mode == "uniform":
        lo = int(arr_spec.get("low", 3))
        hi = int(arr_spec.get("high", 7))
        later_counts = [int(rng.integers(lo, hi + 1)) for _ in range(max(n_cycles - 1, 0))]
    while len(later_counts) < max(n_cycles - 1, 0):
        later_counts.append(0)
    later_counts = later_counts[: max(n_cycles - 1, 0)]

    initial = build_synthetic_tasks(
        nests, scenario.frame, scenario.dem, task_cfg, arrival_time=0.0, id_offset=0,
    )
    later = build_task_batches(
        nests, scenario.frame, scenario.dem, task_cfg,
        per_cycle=[0, *later_counts],
        cycle_length=cycle_length, id_offset=len(initial),
    )
    arrivals: dict[int, list[Task]] = {
        cycle: tasks for cycle, tasks in later.items() if cycle >= 2
    }
    arrivals[1] = initial
    return TaskBatch(initial=initial, arrivals=arrivals)


# ---------------------------------------------------------------------------
# burst 任务生成（E2 因子）
# ---------------------------------------------------------------------------


def inject_burst_tasks(
    batch: TaskBatch,
    nests: Sequence[Nest],
    scenario: Any,
    cfg: Mapping[str, Any],
) -> TaskBatch:
    """按 events.schedule.burst_tasks 生成突发高优先级任务并合入 arrivals。

    burst 任务特征：
    - 高奖励（reward > reward_threshold，触发 URGENT_TASK 事件）
    - 紧时间窗（deadline_cycles 更短）
    - 空间分布在机巢附近（可服务）
    """
    import numpy as np

    ev_cfg = cfg.get("events", {}) or {}
    sched = ev_cfg.get("schedule", {}) or {}
    burst_specs = sched.get("burst_tasks", []) or []
    if not burst_specs:
        return batch

    t_cfg = cfg.get("tasks", {}) or {}
    rolling_cfg = cfg.get("rolling", {}) or {}
    cycle_length = float(rolling_cfg.get("cycle_length", 600.0))

    # 找到当前最大 task id
    all_existing = batch.all_tasks()
    next_id = max((t.id for t in all_existing), default=-1) + 1

    new_arrivals = dict(batch.arrivals)
    rng = np.random.default_rng(42)  # burst 位置固定种子

    for spec in burst_specs:
        cycle = int(spec["cycle"])
        n = int(spec.get("n", 2))
        reward = float(spec.get("reward", 9.5))
        deadline_cycles = float(spec.get("deadline_cycles", 1.0))

        burst_tasks = []
        for i in range(n):
            # 在机巢附近生成（spread 较小，保证可服务）
            nest_idx = i % len(nests)
            anchor = nests[nest_idx]
            spread = float(t_cfg.get("spread_m", 12000.0)) * 0.3
            x = anchor.x + float(rng.uniform(-spread, spread))
            y = anchor.y + float(rng.uniform(-spread, spread))
            z = anchor.z
            arrival_time = (cycle - 1) * cycle_length
            latest = arrival_time + deadline_cycles * cycle_length
            task = Task(
                id=next_id,
                x=x, y=y, z=z,
                reward=reward,
                earliest=arrival_time,
                latest=latest,
                arrival_time=arrival_time,
                meta={"burst": True, "burst_cycle": cycle},
            )
            burst_tasks.append(task)
            next_id += 1

        if cycle in new_arrivals:
            new_arrivals[cycle] = list(new_arrivals[cycle]) + burst_tasks
        else:
            new_arrivals[cycle] = burst_tasks

    return TaskBatch(initial=batch.initial, arrivals=new_arrivals)
