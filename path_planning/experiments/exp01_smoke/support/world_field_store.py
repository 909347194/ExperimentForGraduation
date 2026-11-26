# -*- coding: utf-8 -*-
"""全域场的构建与落盘缓存，以及真几何棱柱表。

职责：``load_or_build_field`` —— 按场身份键复用/新建 ``WorldField``；
      ``prisms_of`` —— 从同一批棱柱造 ``PrismClearance``，供真几何净空闸。
边界：不装配场景（``scenario_loader``）、不规划、不改场。
相关：``methods/spatial/world_field_builder.py``、``methods/spatial/prism_clearance.py``。

为什么缓存：全域建场实测 85 s / 857 MB（Guangzhou，dx=4 m，agl 0–120 m）。四个
实验都要用同一张场，每个实验各建一次等于白烧 6 分钟；而场只要参数不变就是
同一张。键里带齐 origin/spacing/shape/agl/pad/nofly/bias，换一个参数就换文件，
不会出现"复用了别的口径的场"这种静默错误。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from path_planning.common.grid_spec import GridSpec
from .experiment_config import ExperimentConfig
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.spatial.world_field_builder import (
    build_world_field,
    obstacle_prisms,
)

__all__ = ["field_cache_path", "load_or_build_field", "prisms_of"]


def field_cache_path(cfg: ExperimentConfig, scenario_meta: dict[str, Any]) -> Path | None:
    """缓存文件路径；``cache_dir`` 为空表示不缓存。"""
    if cfg.field.cache_dir is None:
        return None
    return Path(cfg.field.cache_dir) / f"{cfg.field_key(scenario_meta)}.npz"


def load_or_build_field(cfg: ExperimentConfig, scenario) -> tuple[WorldField, dict[str, Any]]:
    """按需建场；返回 ``(field, info)``，``info`` 记录来源与耗时，进 ``run_meta.json``。

    ``info["source"]`` 为 ``"cache"`` 或 ``"built"``——结果里必须写清楚这次用的是
    复用场还是新建场，否则"为什么两次跑出来数不一样"无从查起。
    """
    meta = _scenario_meta(scenario)
    path = field_cache_path(cfg, meta)
    t0 = time.time()
    if path is not None and path.is_file():
        field = _load(path)
        return field, {"source": "cache", "path": str(path), "elapsed_s": time.time() - t0}

    field = build_world_field(
        scenario,
        spacing=tuple(float(v) for v in cfg.field.spacing_m),
        alt_max_m=float(cfg.field.alt_max_m),
        pad_m=float(cfg.field.pad_m),
        include_static_nofly=bool(cfg.field.include_static_nofly),
        keep_occupancy=False,
        sample_bias_m=float(cfg.field.sample_bias_m),
    )
    info = {"source": "built", "path": None, "elapsed_s": time.time() - t0, **meta}
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _save(path, field, cfg, meta)
        info["path"] = str(path)
    return field, info


def prisms_of(scenario, field: WorldField, *, include_static_nofly: bool) -> PrismClearance:
    """与建场**同一批**棱柱 → 真几何净空检查器（口径见 architecture §4）。"""
    z_lo = float(field.grid.origin[2])
    dz = float(field.grid.spacing[2])
    z_hi = z_lo + dz * (int(field.grid.shape[2]) - 1)
    prisms = obstacle_prisms(
        scenario, z_lo=z_lo, z_hi=z_hi, include_static_nofly=include_static_nofly
    )
    return PrismClearance(prisms)


# ---------------------------------------------------------------------------
# 落盘细节
# ---------------------------------------------------------------------------


def _scenario_meta(scenario) -> dict[str, Any]:
    # 地形填充口径必须进键：它决定地面高程 → 决定整张 SDF。漏掉会让"改了填充"
    # 静默命中旧场，数字错得看不出来。
    fill = (getattr(scenario, "meta", None) or {}).get("terrain_fill") or {}
    return {
        "data_bounds_xy": [round(float(v), 3) for v in scenario.data_bounds_xy],
        "building_count": 0 if scenario.buildings is None else int(scenario.buildings.count),
        "nofly_count": len(tuple(scenario.nofly)),
        "nest_ids": list(scenario.nest_ids),
        "terrain_fill": [fill.get("method"), fill.get("water_level_m")],
    }


def _save(path: Path, field: WorldField, cfg: ExperimentConfig, meta: dict[str, Any]) -> None:
    grid = field.grid
    np.savez(
        path,
        sdf=np.asarray(field.sdf, dtype=np.float32),
        origin=np.asarray(grid.origin, dtype=np.float64),
        spacing=np.asarray(grid.spacing, dtype=np.float64),
        shape=np.asarray(grid.shape, dtype=np.int64),
        meta=json.dumps({"scenario": meta, "config": cfg.field_key(meta)}),
    )


def _load(path: Path) -> WorldField:
    with np.load(path, allow_pickle=False) as bundle:
        sdf = bundle["sdf"].astype(np.float32, copy=False)
        origin = tuple(float(v) for v in bundle["origin"])
        spacing = tuple(float(v) for v in bundle["spacing"])
        shape = tuple(int(v) for v in bundle["shape"])
    grid = GridSpec(origin=origin, spacing=spacing, shape=shape, yaw_rad=0.0)
    return WorldField(grid=grid, sdf=sdf)
