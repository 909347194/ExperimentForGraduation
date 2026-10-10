# -*- coding: utf-8 -*-
"""exp01_smoke 实体装配：机巢场景 / DEM 地形 / 机队 / 任务批次。

纯构建函数：吃 ``config.yaml`` 的节与数据路径，吐实体对象。
不跑算法、不写文件、不打印；缺数据就抛异常，不静默回退成合成场景。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import paths  # noqa: F401  # sys.path 引导：挂载仓库根，供 task_allocation.* 导入

from task_allocation.common.scenario import build_nest_scenario
from task_allocation.common.task_scenario import (
    SyntheticTaskConfig,
    build_synthetic_tasks,
    build_task_batches,
)
from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
from task_allocation.methods.muas.cost.vertical_section import (
    VerticalSectionCostEstimator,
)

__all__ = [
    "TaskBatch",
    "build_scenario",
    "build_terrain",
    "build_cost_estimator",
    "build_uavs",
    "build_task_config",
    "build_arrivals",
]


# ---------------------------------------------------------------------------
# 任务批次
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskBatch:
    """一次实验的全部任务及其到达时刻表。

    Attributes:
        initial:  第 1 周期开始时到达的任务（``tasks.n_tasks``）。
        arrivals: ``{周期号(1-based): [Task, ...]}``，直接喂给 ``RollingHorizon.run``。
    """

    initial: list[Task]
    arrivals: dict[int, list[Task]]

    def all_tasks(self) -> list[Task]:
        """实际进入滚动闭环的全部任务（等于 ``n_arrived`` 的口径）。"""
        return [t for cycle in sorted(self.arrivals) for t in self.arrivals[cycle]]

    def per_cycle_counts(self) -> dict[int, int]:
        return {c: len(v) for c, v in sorted(self.arrivals.items())}


# ---------------------------------------------------------------------------
# 场景 / 地形
# ---------------------------------------------------------------------------


def build_scenario(
    cfg: Mapping[str, Any], data_dir: Path = paths.DATA_DIR
) -> Any:
    """按 ``data`` 段构建机巢场景（含 DEM 与局部平面）。"""
    data_cfg = cfg.get("data", {}) or {}
    if data_cfg.get("source", "synthetic") != "file":
        raise RuntimeError("data.source 必须为 file：本实验依赖真实机巢与 DEM")

    problem_cfg = cfg["problem"]
    csv_path = Path(data_dir) / data_cfg["path"]
    dem_name = data_cfg.get("dem")
    dem_path = (Path(data_dir) / dem_name) if dem_name else None

    return build_nest_scenario(
        csv_path,
        count=problem_cfg.get("n_nests"),
        capacity=problem_cfg.get("nest_capacity", 10),
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=dem_path,
    )


def build_terrain(scenario: Any, cfg: Mapping[str, Any]) -> DEMTerrain | None:
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


def build_cost_estimator(
    terrain: DEMTerrain | None, cfg: Mapping[str, Any]
) -> VerticalSectionCostEstimator:
    """按 ``cost`` 段构建三维地理代价估算器。"""
    c = cfg.get("cost", {}) or {}
    return VerticalSectionCostEstimator(
        dem_terrain=terrain,
        min_clearance=float(c.get("min_clearance", 50.0)),
        max_clearance=float(c.get("max_clearance", 300.0)),
        climb_rate=float(c.get("climb_rate", 0.15)),
        num_samples=int(c.get("num_samples", 200)),
    )


# ---------------------------------------------------------------------------
# 机队 / 任务
# ---------------------------------------------------------------------------


def build_uavs(nests: Sequence[Nest], cfg: Mapping[str, Any]) -> list[UAV]:
    """按 ``problem`` 段生成机队：轮转驻留在各机巢，占用对应泊位。"""
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


def build_task_config(cfg: Mapping[str, Any], seed: int) -> SyntheticTaskConfig:
    """按 ``tasks`` 段构建合成任务生成器参数。"""
    t = cfg.get("tasks", {}) or {}
    return SyntheticTaskConfig(
        n_tasks=int(t.get("n_tasks", 10)),
        seed=int(seed),
        spread_m=float(t.get("spread_m", 12000.0)),
        reward_min=float(t.get("reward_min", 1.0)),
        reward_max=float(t.get("reward_max", 10.0)),
        deadline_min=float(t.get("deadline_min", 3600.0)),
        deadline_max=float(t.get("deadline_max", 14400.0)),
        anchor_to_nests=bool(t.get("anchor_to_nests", True)),
    )


def build_arrivals(
    nests: Sequence[Nest],
    scenario: Any,
    task_cfg: SyntheticTaskConfig,
    cfg: Mapping[str, Any],
) -> TaskBatch:
    """生成第 1 周期初始任务与后续各周期的到达批次。

    ``rolling.arrivals_per_cycle`` 的语义是**第 2 周期起每周期的新到达任务数**
    （见 config.yaml 注释）。而 ``build_task_batches`` 把 ``per_cycle`` 下标 0 映射到
    第 1 周期，因此这里在前面补一个 0 对齐到周期 2，避免首批任务被静默丢弃
    （历史 bug：不补 0 时第 2 周期的批次落到周期 1 并被过滤掉，
    导致 ``n_arrived`` 与实际进池任务数不一致）。
    """
    rolling_cfg = cfg.get("rolling", {}) or {}
    cycle_length = float(rolling_cfg.get("cycle_length", 600.0))
    later_counts = [int(c) for c in (rolling_cfg.get("arrivals_per_cycle") or [])]

    initial = build_synthetic_tasks(
        nests,
        scenario.frame,
        scenario.dem,
        task_cfg,
        arrival_time=0.0,
        id_offset=0,
    )
    later = build_task_batches(
        nests,
        scenario.frame,
        scenario.dem,
        task_cfg,
        per_cycle=[0, *later_counts],
        cycle_length=cycle_length,
        id_offset=len(initial),
    )

    arrivals: dict[int, list[Task]] = {
        cycle: tasks for cycle, tasks in later.items() if cycle >= 2
    }
    arrivals[1] = initial
    return TaskBatch(initial=initial, arrivals=arrivals)
