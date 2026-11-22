# -*- coding: utf-8 -*-
"""机队清单 → ``VehicleSpec`` + ``PlanRequest``（无巢口径：只有起点/途经点/终点）。

职责：读 ``uav_config.json``（异构机队 + 逐机 tour），把坐标解析成 ENU 绝对坐标，
      按轮次分组产出可直接交给 ``PathPlanner`` 的请求表。
边界：不做规划、不校验几何可行性（点位净空由 ``run.py`` 预检）、不读规划参数
      （预算只来自清单里的剩余量）。
相关：``common/vehicle_spec.py``、``common/plan_types.py``、``common/flight_budget.py``、
      ``PIPELINE.md``（点位选址与验证口径）。

**为什么没有机巢字段**：path_planning 的规划契约是"起点 + 停留点序列"（全是裸坐标），
机巢是上层任务分配的概念。本实验的起始点由实验自己选址（SDF/棱柱实测 ≥ 8 m），
清单里直接写坐标；``nest_id`` / ``home_nest`` / ``nest_above`` 一概不存在。

清单 schema（**本模块自定义，不沿用上层 task_allocation**）::

    {
      "uavs": [
        {"id": 1, "model": "M30T",
         "mass_kg": 9.5, "v_max_mps": 15.0, "v_up_mps": 6.0, "v_dn_mps": 3.0,
         "a_max_mps2": 3.0, "f_max_n": 200.0, "f_min_n": 78.0,
         "max_range_m": 20000.0, "max_time_s": 1200.0,
         "budget": {"endurance_remaining_s": 900.0, "range_remaining_m": 4000.0}}
      ],
      "tours": [
        {"uav_id": 1, "round": 1, "t0": 0.0,
         "start": {"enu": [x, y, z]},
         "via": [{"id": 101, "at": {"enu": [x, y, z]}, "service_time_s": 60.0}],
         "end": {"id": 102, "at": {"enu": [x, y, z]}, "service_time_s": 500.0}}
      ]
    }

``at``/``start`` 两种写法：``{"enu": [x, y, z]}``（绝对坐标）、
``{"enu_agl": [x, y, h]}``（水平坐标 + 真高，按地面高程换算）。

轮次语义：``round`` 从 1 起连续；``t0`` 必须等于 ``(round-1) × cycle_length_s``
（决策节拍口径，见 README §4.1）——写死校验是为了让"周期"不是口头约定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from path_planning.common.flight_budget import Budget
from path_planning.common.plan_types import PlanRequest, PlanStop
from path_planning.common.vehicle_spec import VehicleSpec
from .experiment_config import ExperimentConfig

__all__ = ["FleetInput", "load_fleet_input"]

_VEHICLE_KEYS = {
    "id", "model", "mass_kg", "v_max_mps", "v_up_mps", "v_dn_mps",
    "a_max_mps2", "f_max_n", "f_min_n", "max_range_m", "max_time_s", "budget",
}
_TOUR_KEYS = {"uav_id", "round", "t0", "start", "via", "end"}
_STOP_KEYS = {"id", "at", "service_time_s", "kind"}


@dataclass(frozen=True)
class FleetInput:
    """清单解析结果：逐机机体 + 按轮分组的请求表。

    ``rounds`` 的轮号从 1 起连续；同一轮内请求按 ``uav_id`` 升序（协调层"机号大的
    让路"的判据顺序）。``requests`` 是全部轮次的平铺（审计逐机遍历用）。
    """

    vehicles: dict[int, VehicleSpec]
    rounds: dict[int, tuple[PlanRequest, ...]]
    requests: tuple[PlanRequest, ...]
    cycle_length_s: float

    @property
    def n_rounds(self) -> int:
        return len(self.rounds)


def load_fleet_input(
    path: str | Path,
    cfg: ExperimentConfig,
    scenario,
) -> FleetInput:
    """读清单并解析成按轮分组的 ENU 请求表。

    Raises:
        ValueError: schema 不符、机号重复/未登记、轮次不连续、
                    ``t0`` 与节拍口径不一致。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"机队清单根节点必须是对象：{path}")

    uavs_raw = raw.get("uavs")
    tours_raw = raw.get("tours")
    if not isinstance(uavs_raw, list) or not uavs_raw:
        raise ValueError(f"清单缺少非空 ``uavs``：{path}")
    if not isinstance(tours_raw, list) or not tours_raw:
        raise ValueError(f"清单缺少非空 ``tours``：{path}")

    vehicles: dict[int, VehicleSpec] = {}
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
        b = item["budget"]
        if not isinstance(b, Mapping):
            raise ValueError(f"机 {uid} 的 budget 必须是对象")
        budgets[uid] = Budget(
            endurance_remaining_s=float(b["endurance_remaining_s"]),
            range_remaining_m=float(b["range_remaining_m"]),
        )

    cycle = float(cfg.cycle_length_s)
    rounds: dict[int, list[PlanRequest]] = {}
    seen: set[int] = set()
    for tour in sorted(tours_raw, key=lambda t: (int(t.get("round", 1)), int(t["uav_id"]))):
        if not isinstance(tour, Mapping):
            raise ValueError(f"tours 元素必须是对象：{tour!r}")
        _reject(tour, _TOUR_KEYS, "tours")
        uid = int(tour["uav_id"])
        if uid not in vehicles:
            raise ValueError(f"tour 引用了未登记的机号 {uid}")
        if uid in seen:
            raise ValueError(f"机 {uid} 出现多个 tour：同机同时只允许一条未结束航迹")
        seen.add(uid)
        rnd = int(tour["round"])
        if rnd < 1:
            raise ValueError(f"round 必须 ≥ 1：机 {uid} 的 round={rnd}")
        t0 = float(tour["t0"])
        expect = (rnd - 1) * cycle
        if abs(t0 - expect) > 1e-6:
            raise ValueError(
                f"机 {uid} 的 t0={t0:g} 与节拍口径不符：round={rnd} 应为 {expect:g}"
                f"（(round-1)×cycle_length_s）"
            )
        stops = tuple(_stop(s, scenario) for s in _via(tour)) + (_stop(tour["end"], scenario),)
        rounds.setdefault(rnd, []).append(
            PlanRequest(
                uav_id=uid,
                vehicle=vehicles[uid],
                start_position=_point(tour["start"], scenario),
                start_velocity=(0.0, 0.0, 0.0),
                t0=t0,
                stops=stops,
                budget=budgets[uid],
                meta={"source": "uav_config.json", "round": rnd},
            )
        )

    if not rounds:
        raise ValueError(f"清单没有可用 tour：{path}")
    if sorted(rounds) != list(range(1, max(rounds) + 1)):
        raise ValueError(f"轮次必须从 1 起连续：实际 {sorted(rounds)}")

    flat: list[PlanRequest] = []
    grouped: dict[int, tuple[PlanRequest, ...]] = {}
    for rnd in sorted(rounds):
        reqs = tuple(sorted(rounds[rnd], key=lambda q: q.uav_id))
        grouped[rnd] = reqs
        flat.extend(reqs)
    return FleetInput(
        vehicles=vehicles, rounds=grouped, requests=tuple(flat), cycle_length_s=cycle
    )


def _via(tour: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    via = tour.get("via") or []
    if not isinstance(via, list):
        raise ValueError(f"via 必须是列表：{tour!r}")
    return via


def _stop(raw: Mapping[str, Any], scenario) -> PlanStop:
    _reject(raw, _STOP_KEYS, "stops")
    return PlanStop(
        id=int(raw["id"]),
        position=_point(raw["at"], scenario),
        service_time_s=float(raw.get("service_time_s", 0.0)),
        kind=str(raw.get("kind", "task")),
    )


def _point(at: Mapping[str, Any], scenario) -> tuple[float, float, float]:
    """``at`` 两种写法 → ENU 绝对坐标（``enu_agl`` 按地面高程换算）。"""
    if not isinstance(at, Mapping):
        raise ValueError(f"at 必须是对象：{at!r}")
    if "enu" in at:
        x, y, z = (float(v) for v in at["enu"])
        return (x, y, z)
    if "enu_agl" in at:
        x, y = (float(v) for v in at["enu_agl"][:2])
        h = float(at["enu_agl"][2])
        return (x, y, float(scenario.ground_height([x], [y])[0]) + h)
    raise ValueError(f"at 必须是 enu / enu_agl 之一：{dict(at)}")


def _reject(section: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(section) - allowed - {"_note"})
    if extra:
        raise ValueError(f"{where} 出现未知键 {extra}")
