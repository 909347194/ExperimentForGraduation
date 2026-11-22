# -*- coding: utf-8 -*-
"""实验运行时：配置 → 场景 → 全域场 → 真几何闸 → 门面，一次装配到位。

职责：把四个实验都要做的装配收在一处（场景、场/缓存、棱柱、``PathPlanner``），
      并提供 ``requests_from`` 把机队清单接进来。
边界：不决定实验流程（每个实验的 ``run.py`` 自己决定排什么、跑几轮、记什么）。
相关：``experiment_config.py``、``world_field_store.py``、``fleet_spec.py``、
      ``path_planning/api.py``。

为什么不让每个实验各写一份装配：四份复制会在"建场带、走廊规则、误差口径"上
逐份漂移，最后没人说得清 exp02 的净空为什么和 exp01 不一样。**配置仍是一实验
一份**——共享的是代码，不是数值。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import numpy as np

from path_planning.api import PathPlanner
from path_planning.common.scenario_loader import load_scenario
from .experiment_config import (
    ExperimentConfig,
    load_experiment_config,
)
from .fleet_spec import FleetInput, load_fleet_input
from .world_field_store import (
    load_or_build_field,
    prisms_of,
)
from path_planning.methods.planning.tour_planner import TourPlanningContext
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.trajectory_registry import TrajectoryRegistry

__all__ = ["Runtime", "load_runtime"]


@dataclass
class Runtime:
    """一次实验的装配结果 + 装配耗时（都进 ``run_meta.json``）。"""

    config: ExperimentConfig
    scenario: Any
    field: WorldField
    prisms: PrismClearance
    planner: PathPlanner
    field_info: dict[str, Any]
    timings_s: dict[str, float] = dc_field(default_factory=dict)

    @property
    def ctx(self) -> TourPlanningContext:
        return self.planner.context

    def requests_from(self, fleet_path: str | Path) -> FleetInput:
        return load_fleet_input(fleet_path, self.config, self.scenario)


def load_runtime(
    config_path: str | Path,
    *,
    registry: TrajectoryRegistry | None = None,
    corridor_rule=None,
) -> Runtime:
    """装配：场景 → 场（可缓存）→ 棱柱 → 门面。``corridor_rule`` 缺省取配置。"""
    t_cfg = time.time()
    cfg = load_experiment_config(config_path)
    timings = {"config_s": time.time() - t_cfg}

    t0 = time.time()
    scenario = load_scenario(
        cfg.scenario.data_dir,
        name=cfg.scenario.name,
        nest_count=cfg.scenario.nest_count,
        terrain_step_m=float(cfg.scenario.terrain_step_m),
        bbox_margin_m=float(cfg.scenario.bbox_margin_m),
    )
    timings["scenario_s"] = time.time() - t0

    field, field_info = load_or_build_field(cfg, scenario)
    timings["field_s"] = float(field_info.get("elapsed_s", 0.0))

    t0 = time.time()
    prisms = prisms_of(
        scenario, field, include_static_nofly=bool(cfg.field.include_static_nofly)
    )
    timings["prisms_s"] = time.time() - t0

    ctx = TourPlanningContext(
        field=field,
        params=cfg.params,
        corridor_rule=corridor_rule or cfg.corridor.to_rule(),
        alt_range_m=_alt_range(field, cfg, scenario),
        prisms=prisms,
        ground_fn=scenario.ground_height,
        agl_range_m=tuple(float(v) for v in cfg.agl_range_m),
        seed=int(cfg.seeds[0]),
        endpoint_clearance_m=cfg.endpoint_clearance_m,
        endpoint_exempt_radius_m=float(cfg.endpoint_exempt_radius_m),
    )
    planner = PathPlanner(ctx, registry=registry, e_geo_m=float(cfg.e_geo_m))
    return Runtime(
        config=cfg, scenario=scenario, field=field, prisms=prisms,
        planner=planner, field_info=field_info, timings_s=timings,
    )


def _alt_range(field: WorldField, cfg: ExperimentConfig, scenario: Any) -> tuple[float, float]:
    """NLP 用的**绝对** z 飞行带：场内一格边距 + AGL 上界。

    上界按 ``ground_max + agl_max`` 截断，不是 ``ground_min + agl_max``：广州地面
    从 2.3 m 到 75 m，按最低地面算出的天花板只有 103 m，而清障 6 m 需要机组在
    高楼上方飞到 ~110 m（实测按 ground_min 算时复核净空掉到 4.17 m）。按下界算
    会静默把"可飞高度"压到一条横贯全城的低空天花板。
    代价是低洼处的实际真高可以超过 ``agl_max``（最多到 ``ground_max +
    agl_max``）；这条取舍与 ``z_envelope_from_ground`` 是同一件事的两面，
    都写在 ``docs/architecture.md`` §3.2。
    """
    g = field.grid
    dz = float(g.spacing[2])
    z0 = float(g.origin[2]) + dz
    z1 = float(g.origin[2]) + dz * (int(g.shape[2]) - 1) - dz
    x0, y0, x1, y1 = (float(v) for v in scenario.data_bounds_xy)
    step = max(30.0, 4.0 * float(g.spacing[0]))
    gx = np.arange(x0, x1 + step, step)
    gy = np.arange(y0, y1 + step, step)
    mesh_x, mesh_y = np.meshgrid(gx, gy, indexing="ij")
    ground = np.asarray(scenario.ground_height(mesh_x.ravel(), mesh_y.ravel()), dtype=float)
    ceiling = float(np.nanmax(ground)) + float(cfg.agl_range_m[1])
    return (z0, max(z0 + dz, min(z1, ceiling)))
