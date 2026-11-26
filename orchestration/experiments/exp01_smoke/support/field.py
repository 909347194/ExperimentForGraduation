# -*- coding: utf-8 -*-
"""真场装配：data/guangzhou → 全城 SDF 场 → 棱柱 → 下层规划门面。

职责：一次建好**全城** WorldField（不是按 OD 建场；走廊只是从这张场里取样），
      再把它和棱柱、参数装进 ``PathPlanner``。
边界：不改场、不改算法；走廊与高度规则全部来自 config.yaml。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from path_planning.api import PathPlanner
from path_planning.common.grid_spec import GridSpec
from path_planning.common.plan_params import PlanParams
from path_planning.common.scenario_loader import load_scenario
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.planning.tour_planner import TourPlanningContext
from path_planning.methods.spatial.corridor_window import CorridorRule, required_pad_m
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.spatial.world_field_builder import (
    build_world_field,
    obstacle_prisms,
)

from .config import REPO_ROOT, resolve


def load_guangzhou(cfg: dict[str, Any]):
    """用固定坐标框加载全部机巢；两侧必须用同一批机巢定框。"""
    d = cfg["data"]
    return load_scenario(
        resolve(d["scenario_dir"]),
        name="guangzhou",
        nest_count=int(d["nest_count"]),
        terrain_step_m=float(d["terrain_step_m"]),
        bbox_margin_m=float(d["bbox_margin_m"]),
        # 广州 DEM 的 nodata 是水域；口径由本实验配置定，不在共享层设默认
        water_level_m=(
            None if d.get("water_level_m") is None else float(d["water_level_m"])
        ),
    )


def field_contract(cfg: dict[str, Any], scenario) -> dict[str, float]:
    """建场契约：OD 上限 → 派生 pad，建场与请求准入共用同一处结果。

    ``pad_m`` 不再写死。参考实现 ``pp1`` 的契约是：单段水平长度不超过
    ``max_od_length_m``，建场按 ``required_pad_m`` 给该上限下的最坏走廊窗口
    留足外扩；请求期超长直接拒为 ``OD_TOO_LONG``，不到底层撞
    ``WindowOutsideField``。这里把两处共同依赖的派生值算一次。

    ``max_od_length_m`` 支持 ``"city_diagonal"``（场景 bounds 的城市对角线；
    端点都在城市内，更长 OD 不存在）或显式米数。
    """
    w = cfg["field"]["world"]
    raw = w["max_od_length_m"]
    if isinstance(raw, str) and raw.strip() == "city_diagonal":
        x0, y0, x1, y1 = (float(v) for v in scenario.bounds_xy)
        max_od = math.hypot(x1 - x0, y1 - y0)
    else:
        max_od = float(raw)
    if not math.isfinite(max_od) or max_od <= 0.0:
        raise ValueError(f"field.world.max_od_length_m 非法：{raw!r} → {max_od}")
    pad = required_pad_m(
        corridor_rule(cfg),
        tuple(float(v) for v in cfg["field"]["spacing_m"]),
        max_od,
        extra_m=float(w["pad_extra_m"]),
        min_pad_m=float(w["pad_min_m"]),
    )
    return {
        "max_od_length_m": max_od,
        "pad_m": pad,
        "pad_extra_m": float(w["pad_extra_m"]),
        "pad_min_m": float(w["pad_min_m"]),
    }


def _cache_path(cfg: dict[str, Any], scenario) -> Path:
    meta = {
        "bounds": [round(float(v), 3) for v in scenario.data_bounds_xy],
        "buildings": 0 if scenario.buildings is None else int(scenario.buildings.count),
        "nofly": len(tuple(scenario.nofly)),
        "field": cfg["field"],
        # 契约派生值依赖场景边界，光看 config 判断不出 pad 变了；必须进键，
        # 否则改 OD 上限会命中旧场，而请求期却按新上限准入，两边静默错开。
        "contract": field_contract(cfg, scenario),
        # 地形填充口径决定地面高程，进而决定整张 SDF。不纳入键会让"改了填充"命中
        # 旧场：报错不报、数字错得看不出来，是最难查的那类 bug。
        "water_level_m": cfg["data"].get("water_level_m"),
    }
    key = hashlib.sha256(
        json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return resolve(cfg["field"]["cache_dir"]) / f"guangzhou_{key}.npz"


def load_or_build_field(cfg: dict[str, Any], scenario) -> tuple[WorldField, dict[str, Any]]:
    """全城建场；命中缓存直接加载，避免每次重跑都付 80–120 s。"""
    path = _cache_path(cfg, scenario)
    contract = field_contract(cfg, scenario)
    t0 = time.time()
    if path.is_file():
        with np.load(path, allow_pickle=False) as b:
            grid = GridSpec(
                origin=tuple(float(v) for v in b["origin"]),
                spacing=tuple(float(v) for v in b["spacing"]),
                shape=tuple(int(v) for v in b["shape"]),
                yaw_rad=0.0,
            )
            field = WorldField(grid=grid, sdf=b["sdf"].astype(np.float32, copy=False))
        return field, {
            "source": "cache",
            "path": str(path),
            "elapsed_s": time.time() - t0,
            **contract,
        }

    f = cfg["field"]
    field = build_world_field(
        scenario,
        spacing=tuple(float(v) for v in f["spacing_m"]),
        alt_max_m=float(f["alt_max_m"]),
        pad_m=contract["pad_m"],
        include_static_nofly=bool(f["include_static_nofly"]),
        keep_occupancy=False,
        sample_bias_m=float(f["sample_bias_m"]),
        max_nodes=int(f["max_nodes"]),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        sdf=np.asarray(field.sdf, dtype=np.float32),
        origin=np.asarray(field.grid.origin, dtype=np.float64),
        spacing=np.asarray(field.grid.spacing, dtype=np.float64),
        shape=np.asarray(field.grid.shape, dtype=np.int64),
    )
    return field, {
        "source": "built",
        "path": str(path),
        "elapsed_s": time.time() - t0,
        **contract,
    }


def plan_params(cfg: dict[str, Any]) -> PlanParams:
    """配置里的 planning 段逐字段映射到库参数（缺键/多键都报错）。"""
    raw = dict(cfg["path"]["planning"])
    names = {f.name for f in PlanParams.__dataclass_fields__.values()}
    missing, extra = sorted(names - set(raw)), sorted(set(raw) - names)
    if missing or extra:
        raise ValueError(f"path.planning 键不匹配：缺 {missing}，多 {extra}")
    values: dict[str, Any] = {}
    for name in names:
        default = getattr(PlanParams(), name)
        value = raw[name]
        if isinstance(default, tuple):
            values[name] = tuple(value)
        elif isinstance(default, int):
            values[name] = int(value)
        elif isinstance(default, float):
            values[name] = float(value)
        else:  # pragma: no cover
            raise TypeError(f"未覆盖的参数类型：{name} {type(default)}")
    return PlanParams(**values)


def corridor_rule(cfg: dict[str, Any]) -> CorridorRule:
    c = cfg["path"]["corridor"]
    return CorridorRule(
        width_ratio=(None if c.get("width_ratio") is None else float(c["width_ratio"])),
        half_width_min_m=float(c.get("half_width_min_m", 0.0)),
        margin_m=float(c.get("margin_m", 0.0)),
    )


def absolute_band(field: WorldField, params: PlanParams) -> tuple[float, float]:
    """AGL 带 → 全域绝对 z 带（与 reference pp1 / exp01 runtime 同口径）。"""
    g = field.grid
    dz = float(g.spacing[2])
    ground_min = float(g.origin[2])
    top = ground_min + dz * (int(g.shape[2]) - 1) - dz
    lo = ground_min + float(params.alt_agl_min_m)
    hi = min(top, ground_min + float(params.alt_agl_max_m))
    return (lo, max(lo + dz, hi))


def prisms_of(scenario, field: WorldField, cfg: dict[str, Any]) -> PrismClearance:
    g = field.grid
    dz = float(g.spacing[2])
    z_hi = float(g.origin[2]) + dz * (int(g.shape[2]) - 1)
    prisms = obstacle_prisms(
        scenario,
        z_lo=float(g.origin[2]),
        z_hi=z_hi,
        include_static_nofly=bool(cfg["field"]["include_static_nofly"]),
    )
    return PrismClearance(prisms)


def build_planner(
    cfg: dict[str, Any],
    scenario,
    field: WorldField,
    prisms: PrismClearance,
    vehicles: dict[int, VehicleSpec],
) -> tuple[PathPlanner, PlanParams, dict[str, Any]]:
    params = plan_params(cfg)
    mw = cfg["mission_waypoints"]
    # 产生侧（任务选点）与求解侧（RRT 端点自检）必须同一个净空口径。求解侧的端点阈值是
    # ``clearance_m + 竖直量化容差``；不设豁免圈时端点也按它判。这里的断言把"两边各写
    # 一个数"的坑堵在入口，免得再出现"生成侧合格、规划侧拒收"的假失败。
    solve_clearance = float(params.clearance_m) + float(field.grid.spacing[2])
    if float(mw["endpoint_exempt_radius_m"]) <= 0.0:
        declared = float(mw["endpoint_clearance_m"])
        if declared + 1e-9 < solve_clearance:
            raise ValueError(
                "mission_waypoints.endpoint_clearance_m="
                f"{declared:g} 小于求解侧端点阈值 {solve_clearance:g}"
                "（= path.planning.clearance_m + 栅格 z 间距）：端点净空口径不一致"
            )
    ctx = TourPlanningContext(
        field=field,
        params=params,
        corridor_rule=corridor_rule(cfg),
        alt_range_m=absolute_band(field, params),
        agl_range_m=tuple(float(v) for v in mw["agl_range_m"]),
        prisms=prisms,
        seed=int(cfg["experiment"]["seed"]),
        endpoint_clearance_m=float(mw["endpoint_clearance_m"]),
        endpoint_exempt_radius_m=float(mw["endpoint_exempt_radius_m"]),
        ground_height=scenario.ground_height,
        sdf_quant_tolerance_m=float(field.grid.spacing[2]),
        max_od_length_m=field_contract(cfg, scenario)["max_od_length_m"],
    )
    planner = PathPlanner(ctx, e_geo_m=float(cfg["path"]["e_geo_m"]))
    context = {
        "alt_range_m": list(ctx.alt_range_m),
        "agl_range_m": list(ctx.agl_range_m),
        "field_contract": field_contract(cfg, scenario),
        "field_grid": {
            "origin": list(field.grid.origin),
            "spacing": list(field.grid.spacing),
            "shape": list(field.grid.shape),
        },
    }
    return planner, params, context
