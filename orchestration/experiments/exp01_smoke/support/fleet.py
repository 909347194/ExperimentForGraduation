# -*- coding: utf-8 -*-
"""机队装配：uav_config.json → 上层 UAV 与下层 VehicleSpec。

职责：按 enabled 过滤后，一份机体记录分别构造两侧对象；字段名差异
      （上层 speed vs 下层 v_max_mps）显式映射，不从另一侧反推。
边界：不改机体参数、不判可行性。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from path_planning.common.vehicle_spec import VehicleSpec
from task_allocation.common.types import Nest, UAV

from .config import EXP_DIR

_SPEC_KEYS = (
    "model", "mass_kg", "v_max_mps", "v_up_mps", "v_dn_mps", "a_max_mps2",
    "f_max_n", "f_min_n", "max_range_m", "max_time_s",
)


def load_uav_config(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path is not None else EXP_DIR / "uav_config.json"
    with p.open("r", encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = [r for r in doc["uavs"] if bool(r.get("enabled", False))]
    if not rows:
        raise ValueError(f"uav_config 没有启用的无人机：{p}")
    ids = [int(r["id"]) for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"启用机号重复：{ids}")
    for r in rows:
        missing = [k for k in _SPEC_KEYS + ("speed", "nest_id") if k not in r]
        if missing:
            raise ValueError(f"机 {r.get('id')} 缺字段 {missing}")
    return rows


def upper_uavs(rows: list[dict[str, Any]], nests: dict[int, Nest]) -> list[UAV]:
    out: list[UAV] = []
    for r in rows:
        nest = nests.get(int(r["nest_id"]))
        if nest is None:
            raise ValueError(f"机 {r['id']} 引用了未启用机巢 {r['nest_id']}")
        out.append(UAV(
            id=int(r["id"]), x=nest.x, y=nest.y, z=nest.z,
            speed=float(r["speed"]),
            max_range=float(r["max_range_m"]),
            max_time=float(r["max_time_s"]),
            remaining_range=float(r["max_range_m"]),
            nest_id=int(r["nest_id"]),
            meta={"model": str(r["model"])},
        ))
    return out


def lower_vehicles(rows: list[dict[str, Any]]) -> dict[int, VehicleSpec]:
    return {
        int(r["id"]): VehicleSpec(**{k: r[k] for k in _SPEC_KEYS})
        for r in rows
    }
