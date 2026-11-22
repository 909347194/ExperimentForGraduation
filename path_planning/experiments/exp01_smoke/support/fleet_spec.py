# -*- coding: utf-8 -*-
"""机队清单 → ``VehicleSpec`` + ``PlanRequest``。

职责：读 ``uav_config.json``（异构机队 + 每个机号的航次），把"机巢/任务点"解析成
      ENU 绝对坐标，产出可直接交给 ``PathPlanner.plan_fleet`` 的请求表。
边界：不做规划、不校验几何可行性、不读参数（预算只来自清单里的剩余量）。
相关：``common/vehicle_spec.py``、``common/plan_types.py``、``common/flight_budget.py``、
      ``docs/architecture.md`` §3（机队清单为何自主设计）。

清单 schema（**本模块自定义，不沿用上层 task_allocation**）::

    {
      "uavs": [
        {"id": 1, "model": "M30T", "nest_id": 0,
         "mass_kg": 9.5, "v_max_mps": 15.0, "v_up_mps": 6.0, "v_dn_mps": 3.0,
         "a_max_mps2": 3.0, "f_max_n": 117.0, "f_min_n": 78.0,
         "max_range_m": 12000.0, "max_time_s": 2400.0,
         "budget": {"endurance_remaining_s": 1800.0, "range_remaining_m": 9000.0}}
      ],
      "tours": [
        {"uav_id": 1, "t0": 0.0,
         "stops": [{"id": 101, "kind": "task",
                    "at": {"nest": 1, "agl_m": 18.0}, "service_time_s": 25.0}]}
      ]
    }

``at`` 三种写法：``{"nest": k, "agl_m": h}``（机巢上方真高 h）、
``{"enu": [x, y, z]}``（绝对坐标）、``{"enu_agl": [x, y, h]}``（水平坐标 + 真高）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from path_planning.common.flight_budget import Budget
from path_planning.common.plan_types import PlanRequest, PlanStop
from path_planning.common.vehicle_spec import VehicleSpec
from .experiment_config import ExperimentConfig

__all__ = ["FleetInput", "load_fleet_input", "nest_above"]

_VEHICLE_KEYS = {
    "id", "model", "nest_id", "mass_kg", "v_max_mps", "v_up_mps", "v_dn_mps",
    "a_max_mps2", "f_max_n", "f_min_n", "max_range_m", "max_time_s", "budget",
}


@dataclass(frozen=True)
class FleetInput:
    """清单解析结果：逐机机体 + 逐航次请求。

    ``requests`` 的顺序就是协调时"先到先得"的判据顺序（机号大的让路），
    所以清单一律按 ``uav_id`` 升序排，不按 JSON 里出现的顺序。
    """

    vehicles: dict[int, VehicleSpec]
    requests: tuple[PlanRequest, ...]
    nests: dict[int, tuple[float, float, float]]
    #: 机号 → 所属机巢号（多周期实验要用它决定"回哪个巢"）
    home_nest: dict[int, int] = field(default_factory=dict)


def load_fleet_input(
    path: str | Path,
    cfg: ExperimentConfig,
    scenario,
    *,
    require_tours: bool = True,
) -> FleetInput:
    """读清单并解析成 ENU 请求表。

    ``require_tours=False``：只取机队与机巢（多周期实验的航次由环每周 cyclically
    派，不在清单里写死），此时 ``requests`` 为空。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"机队清单根节点必须是对象：{path}")

    nests = {int(n.id): (float(n.x), float(n.y), float(n.z)) for n in scenario.nests}
    uavs_raw = raw.get("uavs")
    tours_raw = raw.get("tours") if require_tours else raw.get("tours", [])
    if not isinstance(uavs_raw, list) or not uavs_raw:
        raise ValueError(f"清单缺少非空 ``uavs``：{path}")
    if require_tours and (not isinstance(tours_raw, list) or not tours_raw):
        raise ValueError(f"清单缺少非空 ``tours``：{path}")

    vehicles: dict[int, VehicleSpec] = {}
    nests_of: dict[int, int] = {}
    budgets: dict[int, Budget] = {}
    for item in uavs_raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"uavs 元素必须是对象：{item!r}")
        _reject(item, _VEHICLE_KEYS, "uavs")
        uid = int(item["id"])
        if uid in vehicles:
            raise ValueError(f"uavs 里机号 {uid} 重复")
        vehicles[uid] = VehicleSpec(
            model=str(item["model"]),
            mass_kg=float(item["mass_kg"]),
            v_max_mps=float(item["v_max_mps"]),
            v_up_mps=float(item["v_up_mps"]),
            v_dn_mps=float(item["v_dn_mps"]),
            a_max_mps2=float(item["a_max_mps2"]),
            f_max_n=float(item["f_max_n"]),
            f_min_n=float(item["f_min_n"]),
            max_range_m=float(item["max_range_m"]),
            max_time_s=float(item["max_time_s"]),
        )
        vehicles[uid].validate()
        nests_of[uid] = int(item["nest_id"])
        b = item["budget"]
        if not isinstance(b, Mapping):
            raise ValueError(f"机 {uid} 的 budget 必须是对象")
        budgets[uid] = Budget(
            endurance_remaining_s=float(b["endurance_remaining_s"]),
            range_remaining_m=float(b["range_remaining_m"]),
        )

    requests: list[PlanRequest] = []
    seen: set[int] = set()
    for tour in sorted(tours_raw, key=lambda t: int(t["uav_id"])):
        uid = int(tour["uav_id"])
        if uid not in vehicles:
            raise ValueError(f"tour 引用了未登记的机号 {uid}")
        if uid in seen:
            raise ValueError(f"机 {uid} 出现多个 tour：同机同时只允许一条未结束航迹")
        seen.add(uid)
        if int(tour.get("nest_id", nests_of[uid])) != nests_of[uid]:
            raise ValueError(f"tour 的 nest_id 与 uavs 不一致（机 {uid}）")
        start = _point({"nest": nests_of[uid], "agl_m": cfg.takeoff_agl_m}, nests, scenario)
        stops = tuple(_stop(s, nests, scenario) for s in tour["stops"])
        requests.append(
            PlanRequest(
                uav_id=uid,
                vehicle=vehicles[uid],
                start_position=start,
                start_velocity=(0.0, 0.0, 0.0),
                t0=float(tour["t0"]),
                stops=stops,
                budget=budgets[uid],
                meta={"source": "uav_config.json"},
            )
        )
    return FleetInput(
        vehicles=vehicles, requests=tuple(requests), nests=nests, home_nest=dict(nests_of)
    )


def _stop(raw: Mapping[str, Any], nests, scenario) -> PlanStop:
    _reject(raw, {"id", "kind", "at", "service_time_s"}, "stops")
    return PlanStop(
        id=int(raw["id"]),
        position=_point(raw["at"], nests, scenario),
        service_time_s=float(raw.get("service_time_s", 0.0)),
        kind=str(raw.get("kind", "task")),
    )


def nest_above(
    nest_id: int,
    nests: Mapping[int, tuple[float, float, float]],
    agl_m: float,
) -> tuple[float, float, float]:
    """机巢正上方 ``agl_m`` 的 ENU 点（起点平台/回巢点的唯一算法）。"""
    if int(nest_id) not in nests:
        raise ValueError(f"机巢 {nest_id} 不存在；可选 {sorted(nests)}")
    x, y, z_ground = nests[int(nest_id)]
    return (x, y, z_ground + float(agl_m))


def _point(at: Mapping[str, Any], nests, scenario) -> tuple[float, float, float]:
    """``at`` 三种写法 → ENU 绝对坐标（``agl`` 按地面高程换算）。"""
    if "nest" in at:
        return nest_above(int(at["nest"]), nests, float(at["agl_m"]))
    if "enu" in at:
        x, y, z = (float(v) for v in at["enu"])
        return (x, y, z)
    if "enu_agl" in at:
        x, y = (float(v) for v in at["enu_agl"][:2])
        h = float(at["enu_agl"][2])
        return (x, y, float(scenario.ground_height([x], [y])[0]) + h)
    raise ValueError(f"at 必须是 nest / enu / enu_agl 之一：{dict(at)}")


def _reject(section: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(section) - allowed)
    if extra:
        raise ValueError(f"{where} 出现未知键 {extra}")
