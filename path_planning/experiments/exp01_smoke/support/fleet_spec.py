# -*- coding: utf-8 -*-
"""机队清单 → ``VehicleSpec`` + ``PlanRequest``（无巢口径：只有起点/途经点/终点）。

职责：读 ``uav_config.json``（异构机队 + 逐机 tour），把坐标解析成 ENU 绝对坐标，
      按周期分组产出可直接交给 ``PathPlanner`` 的请求表。
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
         {"uav_id": 1, "cycle": 1, "t0": 0.0,
          "start": {"xy": [x, y], "point_type": "nest"},
          "via": [{"id": 101, "at": {"xy": [x, y], "point_type": "task"},
                    "service_time_s": 60.0}],
          "end": {"id": 102, "at": {"xy": [x, y], "point_type": "nest"},
                   "service_time_s": 500.0}}
      ]
    }

``at``/``start`` 的新写法只有 ``{"xy": [x, y], "point_type": ...}``：
``point_type=nest`` 取巢址正上方悬停点，``point_type=task`` 取任务作业点；
高度由本实验 ``support/waypoint_policy.py``（任务产生侧）按实验配置解析：表面基线 →
间隙 → 净空阶梯，抬到上界仍不合规就直接报错要求换点，不交给算法兜底。

周期语义：``cycle`` 从 1 起连续；``t0`` 必须等于 ``(cycle-1) × cycle_length_s``
（决策周期口径，见 README §4.1）——写死校验是为了让"周期"不是口头约定。

**离巢错峰**：清单里的 ``t0`` 是**周期基准**；同一周期内**起点相同**的航次（= 同巢）
按机号升序错峰，第 k 台的生效 ``t0`` = 基准 + k × ``runtime.depart_stagger_s``。
理由：同刻同起点的两个空中悬停点距离恒为 0，检测器必判冲突，而位置被端点钉死、协调层
动不了 t=0 那个重合点（A4 已把起降移出规划域，起降安全本来就由离巢时序保证）。
错峰后相邻机在同一时刻已沿航迹拉开，几何上不再重合——**不是**靠时间窗错开：
C1 时间分离证书只比整条航迹的时间窗，错几秒根本不会触发它（见
``methods/conflict/pair_certificates.py`` 的 C1 与 ``docs/architecture.md`` §4）。
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
from .waypoint_policy import POINT_TYPES, SiteClearance, WaypointPolicyConfig, resolve_waypoint

__all__ = ["FleetInput", "load_fleet_input", "stagger_offsets"]

_VEHICLE_KEYS = {
    "id", "model", "mass_kg", "v_max_mps", "v_up_mps", "v_dn_mps",
    "a_max_mps2", "f_max_n", "f_min_n", "max_range_m", "max_time_s", "budget",
}
_TOUR_KEYS = {"uav_id", "cycle", "t0", "start", "via", "end", "allow_estimate_fallback"}
_STOP_KEYS = {"id", "at", "service_time_s", "kind"}


@dataclass(frozen=True)
class FleetInput:
    """清单解析结果：逐机机体 + 按周期分组的请求表。

    ``cycles`` 的周期号从 1 起连续；同一周期内请求按 ``uav_id`` 升序（协调层"机号大的
    让路"的判据顺序）。``requests`` 是全部周期的平铺（审计逐机遍历用）。
    """

    vehicles: dict[int, VehicleSpec]
    cycles: dict[int, tuple[PlanRequest, ...]]
    requests: tuple[PlanRequest, ...]
    cycle_length_s: float

    @property
    def n_cycles(self) -> int:
        return len(self.cycles)


@dataclass(frozen=True)
class _TourRecord:
    """清单里一条 tour 的**周期基准**口径（错峰前的 ``t0``）。"""

    uid: int
    cycle: int
    t0: float
    start: tuple[float, float, float]
    stops: tuple[PlanStop, ...]
    allow_estimate_fallback: bool


def stagger_offsets(
    starts: Mapping[int, tuple[float, float, float]], stagger_s: float
) -> dict[int, float]:
    """同起点航次的离巢错峰偏移：组内第 k 台（按机号升序）= ``k × stagger_s``。

    只对**同一周期内起点相同**的航次错峰：不同起点的航次不共享悬停点，平白推迟只是浪费时隙。
    起点按 1 mm 取整比较（清单坐标手写，浮点零差不需要容差）。``stagger_s ≤ 0`` 时全部为 0。
    """
    offsets = {int(uid): 0.0 for uid in starts}
    if stagger_s <= 0.0:
        return offsets
    groups: dict[tuple[float, float, float], list[int]] = {}
    for uid in sorted(offsets):
        key = tuple(round(float(v), 3) for v in starts[uid])
        groups.setdefault(key, []).append(uid)  # type: ignore[arg-type]
    for members in groups.values():
        for k, uid in enumerate(members):
            offsets[uid] = k * float(stagger_s)
    return offsets


def load_fleet_input(
    path: str | Path,
    cfg: ExperimentConfig,
    scenario,
    site: SiteClearance,
) -> FleetInput:
    """读清单并解析成按周期分组的 ENU 请求表。

    ``site`` 是装配层注入的净空端口（场 + 棱柱 + 飞行上界），点位在产生时就用它
    做净空阶梯；没有它就无法保证"合格的点"，因此不提供缺省。

    Raises:
        ValueError: schema 不符、机号重复/未登记、周期不连续、
                    ``t0`` 与周期口径不一致、点位抬到上界仍不合规。
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
    stagger = float(cfg.depart_stagger_s)
    waypoints = WaypointPolicyConfig(
        nest_hover_agl_m=cfg.mission_waypoints.nest_hover_agl_m,
        task_clearance_agl_m=cfg.mission_waypoints.task_clearance_agl_m,
        # 阶梯步长 = 竖直格距：比一个体素还细的抬升在 SDF 上分辨不出来
        lift_step_m=float(cfg.field.spacing_m[2]),
    )
    records: list[_TourRecord] = []
    seen: set[int] = set()
    for tour in sorted(tours_raw, key=lambda t: (int(t.get("cycle", 1)), int(t["uav_id"]))):
        if not isinstance(tour, Mapping):
            raise ValueError(f"tours 元素必须是对象：{tour!r}")
        _reject(tour, _TOUR_KEYS, "tours")
        uid = int(tour["uav_id"])
        if uid not in vehicles:
            raise ValueError(f"tour 引用了未登记的机号 {uid}")
        if uid in seen:
            raise ValueError(f"机 {uid} 出现多个 tour：同机同时只允许一条未结束航迹")
        seen.add(uid)
        rnd = int(tour["cycle"])
        if rnd < 1:
            raise ValueError(f"周期号必须 ≥ 1：机 {uid} 的 cycle={rnd}")
        t0 = float(tour["t0"])
        expect = (rnd - 1) * cycle
        if abs(t0 - expect) > 1e-6:
            raise ValueError(
                f"机 {uid} 的 t0={t0:g} 与周期口径不符：cycle={rnd} 应为 {expect:g}"
                f"（(cycle-1)×cycle_length_s）"
            )
        stops = tuple(_stop(s, waypoints, scenario, site) for s in _via(tour)) + (
            _stop(tour["end"], waypoints, scenario, site),
        )
        records.append(_TourRecord(
            uid=uid, cycle=rnd, t0=t0,
            start=_point(tour["start"], waypoints, scenario, site), stops=stops,
            # 只有清单显式写 true 的航次才允许"优化失败 → 定速剖面"的降级交付。
            allow_estimate_fallback=bool(tour.get("allow_estimate_fallback", False)),
        ))

    cycles: dict[int, list[PlanRequest]] = {}
    for rnd in sorted({r.cycle for r in records}):
        group = [r for r in records if r.cycle == rnd]
        offsets = stagger_offsets({r.uid: r.start for r in group}, stagger)
        for r in group:
            offset = offsets[r.uid]
            cycles.setdefault(rnd, []).append(
                PlanRequest(
                    uav_id=r.uid,
                    vehicle=vehicles[r.uid],
                    start_position=r.start,
                    start_velocity=(0.0, 0.0, 0.0),
                    t0=r.t0 + offset,
                    stops=r.stops,
                    budget=budgets[r.uid],
                    meta={
                        "source": "uav_config.json", "cycle": rnd,
                        "t0_base_s": r.t0, "depart_offset_s": offset,
                    },
                    allow_estimate_fallback=r.allow_estimate_fallback,
                )
            )

    if not cycles:
        raise ValueError(f"清单没有可用 tour：{path}")
    if sorted(cycles) != list(range(1, max(cycles) + 1)):
        raise ValueError(f"周期必须从 1 起连续：实际 {sorted(cycles)}")

    flat: list[PlanRequest] = []
    grouped: dict[int, tuple[PlanRequest, ...]] = {}
    for rnd in sorted(cycles):
        reqs = tuple(sorted(cycles[rnd], key=lambda q: q.uav_id))
        grouped[rnd] = reqs
        flat.extend(reqs)
    return FleetInput(
        vehicles=vehicles, cycles=grouped, requests=tuple(flat), cycle_length_s=cycle
    )


def _via(tour: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    via = tour.get("via") or []
    if not isinstance(via, list):
        raise ValueError(f"via 必须是列表：{tour!r}")
    return via


def _stop(
    raw: Mapping[str, Any], waypoints: WaypointPolicyConfig, scenario, site: SiteClearance
) -> PlanStop:
    _reject(raw, _STOP_KEYS, "stops")
    return PlanStop(
        id=int(raw["id"]),
        position=_point(raw["at"], waypoints, scenario, site),
        service_time_s=float(raw.get("service_time_s", 0.0)),
        kind=str(raw.get("kind", "task")),
    )


def _point(
    at: Mapping[str, Any],
    waypoints: WaypointPolicyConfig,
    scenario,
    site: SiteClearance,
) -> tuple[float, float, float]:
    """清单点位 → ENU 绝对坐标（唯一入口：``xy`` + ``point_type``）。"""
    if not isinstance(at, Mapping):
        raise ValueError(f"at 必须是对象：{at!r}")
    xy = at.get("xy")
    if not isinstance(xy, (list, tuple)) or len(xy) != 2:
        raise ValueError(f"at 必须写成 {{\"xy\": [x, y], \"point_type\": ...}}：{dict(at)}")
    point_type = str(at.get("point_type", ""))
    if point_type not in POINT_TYPES:
        raise ValueError(f"point_type 必须是 {list(POINT_TYPES)} 之一：{at!r}")
    return resolve_waypoint(float(xy[0]), float(xy[1]), point_type, waypoints, scenario, site)


def _reject(section: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(section) - allowed - {"_note"})
    if extra:
        raise ValueError(f"{where} 出现未知键 {extra}")
