# -*- coding: utf-8 -*-
"""实验运行时：配置 → 场景 → 全域场 → 真几何闸 → 门面，一次装配到位。

职责：把四个实验都要做的装配收在一处（场景、场/缓存、棱柱、``PathPlanner``），
      并提供 ``requests_from`` 把机队清单接进来。
边界：不决定实验流程（每个实验的 ``run.py`` 自己决定排什么、跑几周期、记什么）。
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

from path_planning.api import PathPlanner
from path_planning.common.scenario_loader import load_scenario
from .experiment_config import (
    ExperimentConfig,
    load_experiment_config,
)
from .fleet_spec import FleetInput, load_fleet_input
from .waypoint_policy import SiteClearance
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

    def site_clearance(self) -> SiteClearance:
        """任务产生侧的净空端口：同一份场与棱柱、同一条飞行上界、同一个求解阈值。

        生产侧按**求解阈值**（交付 + SDF 量化容差）抬点：端点必须落在 NLP 可行域里；
        交付/审计仍按较小的交付阈值判，两个口径不混用。
        """
        params = self.config.params
        return SiteClearance(
            sdf_at=lambda x, y, z: float(self.field.at_one(x, y, z)),
            prism_clearance_at=lambda x, y, z: float(
                self.prisms.min_clearance([(x, y, z)], 1.0)
            ),
            z_ceiling_m=float(self.ctx.alt_range_m[1]),
            clearance_m=float(params.clearance_m) + float(self.ctx.sdf_quant_tolerance_m),
        )

    def requests_from(self, fleet_path: str | Path) -> FleetInput:
        return load_fleet_input(
            fleet_path, self.config, self.scenario, self.site_clearance()
        )


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
        # 空洞填水面高程（广州 DEM 的 nodata=255 就是北缘珠江）；None 则最近邻
        water_level_m=cfg.scenario.water_level_m,
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
        alt_range_m=_alt_range(field, cfg),
        prisms=prisms,
        seed=int(cfg.seeds[0]),
        endpoint_clearance_m=cfg.endpoint_clearance_m,
        endpoint_exempt_radius_m=float(cfg.endpoint_exempt_radius_m),
        agl_range_m=(float(cfg.params.alt_agl_min_m), float(cfg.params.alt_agl_max_m)),
        ground_height=scenario.ground_height,
        sdf_quant_tolerance_m=float(cfg.field.spacing_m[2]),
    )
    planner = PathPlanner(ctx, registry=registry, e_geo_m=float(cfg.e_geo_m))
    return Runtime(
        config=cfg, scenario=scenario, field=field, prisms=prisms,
        planner=planner, field_info=field_info, timings_s=timings,
    )


def _alt_range(field: WorldField, cfg: ExperimentConfig) -> tuple[float, float]:
    """真高带 ``[alt_agl_min_m, alt_agl_max_m]`` → NLP/RRT 用的**绝对** z 区间。

    基准是全城最低地面（``grid.origin[2]``，与参考实现 pp1 的
    ``z_envelope_from_ground`` 同一口径）：逐点真高带要求 DEM 进 CasADi，非光滑且昂贵，
    是已知近似。上界再受场顶约束（场外 SDF 是外推值，不可用于飞行）。
    """
    g = field.grid
    dz = float(g.spacing[2])
    ground_min = float(g.origin[2])
    top = ground_min + dz * (int(g.shape[2]) - 1) - dz
    lo = ground_min + float(cfg.params.alt_agl_min_m)
    hi = min(top, ground_min + float(cfg.params.alt_agl_max_m))
    return (lo, max(lo + dz, hi))
