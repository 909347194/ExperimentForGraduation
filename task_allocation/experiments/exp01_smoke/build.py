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


def _scenario_dir(cfg: Mapping[str, Any], data_dir: Path | None = None) -> Path:
    """场景数据目录：显式传入（测试/外部）优先，否则按 ``data.scenario`` 解析。

    2026-10-27 起 data/ 按场景分目录（``data/<scenario>/``），解析统一走
    :mod:`task_allocation.common.scenario_data`。
    """
    if data_dir is not None:
        return Path(data_dir)
    return scenario_data.scenario_dir((cfg.get("data") or {}).get("scenario"))


def build_scenario(
    cfg: Mapping[str, Any], data_dir: Path | None = None
) -> Any:
    """按 ``data`` 段构建机巢场景（含 DEM 与局部平面）。

    ``data_dir`` 为场景数据目录覆盖（缺省 = ``data/<data.scenario>/``）。
    """
    data_cfg = cfg.get("data", {}) or {}
    if data_cfg.get("source", "synthetic") != "file":
        raise RuntimeError("data.source 必须为 file：本实验依赖真实机巢与 DEM")

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
    """把经纬度 DEM 重采样成局部米制网格，供垂直切面代价估算使用。

    网格范围 = （机巢包围盒 ∪ 任务域包络）+ ``cost.dem_margin_m``。

    任务域必须显式并进来：``tasks.domain=dem_extent`` 时任务撒在 DEM 全域，
    而 DEM 相对机巢包围盒最大外扩可达 23 km（本仓数据实测东边界差 3.0 km，
    约 2.9% 的 DEM 内任务会落在"仅按机巢包围盒"的网格之外），
    那时三维代价会退化到网格边界值。
    """
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

    # 区域多边形门禁：任务域把区域包络并进来（城关区口径），保证代价
    # 网格覆盖区域内任务，三维代价不退化到边界值。
    region_rel = (cfg.get("data", {}) or {}).get("region_geojson")
    if region_rel is not None:
        renv = region_planar_envelope(_scenario_dir(cfg) / region_rel, scenario.frame)
        if renv is not None:
            x0, y0 = min(x0, renv[0]), min(y0, renv[1])
            x1, y1 = max(x1, renv[2]), max(y1, renv[3])

    extent = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
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
    rolling = cfg.get("rolling", {}) or {}
    cycle_length = float(rolling.get("cycle_length", 600.0))
    # 区域门禁 geojson（城关区）：相对场景数据目录（data/<scenario>/），解析为绝对路径传入
    data_cfg = cfg.get("data", {}) or {}
    region_rel = data_cfg.get("region_geojson")
    region_path = str(_scenario_dir(cfg) / region_rel) if region_rel else None
    return SyntheticTaskConfig(
        n_tasks=int(t.get("n_tasks", 10)),
        seed=int(seed),
        # 撒点域：dem_extent（DEM 全域，需求不确定）| nest_anchored（锚定机巢，消融对照）
        # 缺省保持 nest_anchored，与 SyntheticTaskConfig 默认值一致——
        # 切换全域必须在 yaml 里显式写，避免悄悄改变其它实验的口径。
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
    )


def build_arrivals(
    nests: Sequence[Nest],
    scenario: Any,
    task_cfg: SyntheticTaskConfig,
    cfg: Mapping[str, Any],
) -> TaskBatch:
    """生成第 1 周期初始任务与后续各周期的到达批次。

    后续各周期的新到达任务数由 ``rolling.arrivals`` 决定（默认 Poisson 随机流，
    体现真正的动态到达；也可 ``fixed`` 用确定列表、``uniform`` 用均匀随机）。
    随机流以 ``task_cfg.seed`` 为种子，保证同配置可复现。

    ``build_task_batches`` 把 ``per_cycle`` 下标 0 映射到第 1 周期，因此在前面补
    一个 0 对齐到周期 2，避免首批任务被静默丢弃（历史 bug：不补 0 时第 2 周期
    的批次落到周期 1 并被过滤掉，导致 ``n_arrived`` 与实际进池任务数不一致）。
    """
    import numpy as np  # 仅本函数用，避免顶层强行依赖

    rolling_cfg = cfg.get("rolling", {}) or {}
    cycle_length = float(rolling_cfg.get("cycle_length", 600.0))
    n_cycles = int(rolling_cfg.get("n_cycles", 1))

    # ── 解析到达过程设定 ──────────────────────────────────────────
    arr_spec = rolling_cfg.get("arrivals") or {}
    if isinstance(arr_spec, dict) and arr_spec:
        mode = str(arr_spec.get("mode", "poisson")).lower()
    elif rolling_cfg.get("arrivals_per_cycle"):
        # 向后兼容：老配置用固定列表
        arr_spec = {"mode": "fixed", "counts": list(rolling_cfg["arrivals_per_cycle"])}
        mode = "fixed"
    else:
        mode = "poisson"
        arr_spec = {"mode": "poisson", "lambda": 5.0}

    # 第 2 周期起每周期的新到达任务数（共 n_cycles-1 个）
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
    # 对齐长度（不足补 0，超出截断）
    while len(later_counts) < max(n_cycles - 1, 0):
        later_counts.append(0)
    later_counts = later_counts[: max(n_cycles - 1, 0)]

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
