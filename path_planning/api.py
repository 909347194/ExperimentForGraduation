# -*- coding: utf-8 -*-
"""对外唯一入口：``PathPlanner``。

职责：上层（``horizon`` / ``closed_loop``）只从这里进，做两件事——排一条 tour、
      把一批 tour 协调到无冲突，并维护 ``TrajectoryRegistry``。
边界：不实现任何算法；所有能力都在 ``methods/`` 内，本文件只做装配与契约归一。
相关：``docs/architecture.md`` §1、改造方案 §10.1/§10.2。

**为什么必须扁平**：深路径（``methods.coordination.global_recheck.coordinate_conflicts``）
一旦被上层直接 import，接口就跟着内部文件改名一起漂移，"哪些东西能改、哪些不能"
再也说不清。门面只有两条路：排一条、协调一批。

**为什么不在这里改 ``t0``**：``t0`` 来自上层排程，改它等于重排机队（见 README §2.2）。
本门面接收冻结的 ``t0``，只把"几何变了 → 时间重解"这一条原子操作做实。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from path_planning.common.plan_params import PlanParams
from path_planning.common.plan_types import PlanRequest, PlanResult, Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.conflict.conflict_detector import DetectionConfig
from path_planning.methods.coordination.global_recheck import (
    CoordinationOutcome,
    coordinate_conflicts,
)
from path_planning.methods.planning.tour_planner import TourPlanningContext, plan_tour
from path_planning.methods.spatial.prism_clearance import PrismClearance
from path_planning.methods.timeline.nlp_config import NlpConfig
from path_planning.methods.trajectory_registry import TrajectoryRegistry

__all__ = ["FleetPlan", "PathPlanner"]


@dataclass(frozen=True)
class FleetPlan:
    """一批 tour 的规划 + 协调结果。

    Attributes:
        results:  逐机规划结果（含失败码；``ok=False`` 的机不参与协调）
        changed:  被让路改写过几何的机号 → 新航迹（未改动的机不出现）
        outcome:  协调结果（全部无冲突时 ``ok=True``；没有可交付航迹时为 None）
    """

    results: Mapping[int, PlanResult]
    changed: Mapping[int, Trajectory] = field(default_factory=dict)
    outcome: CoordinationOutcome | None = None

    @property
    def ok(self) -> bool:
        """所有机都规划成功且协调后无冲突。"""
        if any(not r.ok for r in self.results.values()):
            return False
        return self.outcome is None or self.outcome.ok

    def trajectory_of(self, uav_id: int) -> Trajectory | None:
        """协调后的最终航迹（被让路的取新几何，否则取原规划）。"""
        return self.changed.get(uav_id) or self.results[uav_id].trajectory


class PathPlanner:
    """顶层门面：场景依赖进构造，请求进方法，状态在 Registry 里。

    Args:
        context:    场景侧依赖（全域场、参数、走廊规则、飞行带、真几何障碍）
        registry:   航迹注册表；None = 内部新建
        e_geo_m:    几何误差标量（进检测半径膨胀）；异构机队按最保守机型取值
    """

    def __init__(
        self,
        context: TourPlanningContext,
        *,
        registry: TrajectoryRegistry | None = None,
        e_geo_m: float = 0.0,
    ) -> None:
        self.context = context
        self.registry = registry or TrajectoryRegistry()
        self.e_geo_m = float(e_geo_m)

    # --- 单机 ---------------------------------------------------------

    def plan(self, request: PlanRequest, *, register: bool = True) -> PlanResult:
        """排一条 tour；成功则把航迹注册为"已发布时空障碍"。

        ``register=False`` 用于"先排一批、统一协调"的场景：此时还没协调完，先登记
        会让协调层把尚未让路的航迹当成既有约束，重复检测同一批新航迹。
        """
        result = plan_tour(request, self.context)
        if register and result.trajectory is not None:
            self.registry.register(result.trajectory, source=_source_of(result.trajectory))
        return result

    # --- 多机 ---------------------------------------------------------

    def plan_fleet(
        self, requests: Sequence[PlanRequest], *, max_rounds: int | None = None
    ) -> FleetPlan:
        """批量规划 + 协调（同机型分组协调；异构机队见 ``_groups`` 说明）。"""
        results: dict[int, PlanResult] = {}
        for req in requests:
            results[req.uav_id] = self.plan(req, register=False)
        deliverable = [r.trajectory for r in results.values() if r.trajectory is not None]
        outcome = self.coordinate(
            deliverable,
            vehicles={req.uav_id: req.vehicle for req in requests},
            max_rounds=max_rounds,
        )

        changed: dict[int, Trajectory] = {}
        final = dict(outcome.trajectories) if outcome is not None else {}
        for uid, traj in final.items():
            original = results[uid].trajectory
            if original is not None and traj is not original:
                changed[uid] = traj
            self.registry.replace(traj, source=_source_of(traj))
        return FleetPlan(results=results, changed=changed, outcome=outcome)

    def coordinate(
        self,
        trajectories: Sequence[Trajectory],
        *,
        vehicle: VehicleSpec | None = None,
        vehicles: Mapping[int, VehicleSpec] | None = None,
        detection: DetectionConfig | None = None,
        max_rounds: int | None = None,
    ) -> CoordinationOutcome | None:
        """对给定航迹集合做冲突消解；``None`` = 没有航迹可协调。

        异构机队说明：``coordinate_conflicts`` 只接受单一 ``vehicle``（复核与真几何闸
        按它取值），故这里按 ``vehicle.model`` 分组、逐组协调。**同批混机型时跨组冲突
        不在本函数职责内**——那需要上层按机型裁决，或改成逐机机体检（见架构文档未决项）。
        """
        if not trajectories:
            return None
        groups = _groups(trajectories, vehicle=vehicle, vehicles=vehicles)
        merged: CoordinationOutcome | None = None
        for model, (group_trajs, group_vehicle) in groups.items():
            det = detection or DetectionConfig.from_params(
                self.context.params, group_vehicle, e_geo_m=self.e_geo_m
            )
            out = coordinate_conflicts(
                group_trajs,
                field=self.context.field,
                params=self.context.params,
                vehicle=group_vehicle,
                detection=det,
                nlp_for=lambda uid, v=group_vehicle: _nlp_for(self.context, v),
                prisms=self.context.prisms,
                max_rounds=max_rounds,
            )
            merged = out if merged is None else _merge_outcomes(merged, out, model)
        return merged

    # --- 状态 ---------------------------------------------------------

    def busy_until(self, uav_id: int) -> float:
        """该机占用到何时（上层 ``busy_until`` 的取数口）。"""
        return self.registry.busy_until(uav_id)

    def prune(self, now: float) -> tuple[int, ...]:
        return self.registry.prune(now)


# ---------------------------------------------------------------------------
# 装配细节
# ---------------------------------------------------------------------------


def _nlp_for(ctx: TourPlanningContext, vehicle: VehicleSpec) -> NlpConfig:
    """协调重解用的 NLP 基配置：不带走廊（局部域跨段）、不带几何引导管（由调用方给）。"""
    return NlpConfig.from_params(
        ctx.params, vehicle,
        clearance_m=float(ctx.params.clearance_m),
        alt_range_m=ctx.alt_range_m,
    )


def _source_of(traj: Trajectory) -> str:
    return "estimate" if traj.time_source.value == "estimate" else "planned"


def _groups(
    trajectories: Sequence[Trajectory],
    *,
    vehicle: VehicleSpec | None,
    vehicles: Mapping[int, VehicleSpec] | None,
) -> dict[str, tuple[tuple[Trajectory, ...], VehicleSpec]]:
    """按机型分组：每组的机体必须唯一，否则复核限值无从取。

    优先级：显式 ``vehicles`` 映射（逐机机体）→ 单一 ``vehicle``（全部同型）。
    """
    if vehicles is not None:
        missing = [t.uav_id for t in trajectories if t.uav_id not in vehicles]
        if missing:
            raise ValueError(f"缺机 {missing} 的机体规格，无法按机型分组协调")
        groups: dict[str, list[Trajectory]] = {}
        models: dict[str, VehicleSpec] = {}
        for traj in trajectories:
            v = vehicles[traj.uav_id]
            groups.setdefault(v.model, []).append(traj)
            models.setdefault(v.model, v)
        return {m: (tuple(items), models[m]) for m, items in groups.items()}
    if vehicle is None:
        raise ValueError("必须给出 vehicle 或 vehicles 映射：复核与真几何闸都要按机体取限值")
    return {vehicle.model: (tuple(trajectories), vehicle)}


def _merge_outcomes(
    left: CoordinationOutcome, right: CoordinationOutcome, group: str
) -> CoordinationOutcome:
    """把分组协调的结果并成一份（航迹合并、轮次取长、失败码取有）。"""
    trajs = dict(left.trajectories)
    trajs.update(right.trajectories)
    rounds = tuple(left.rounds) + tuple(right.rounds)
    reason = left.reason if left.ok else right.reason
    return CoordinationOutcome(
        ok=left.ok and right.ok,
        trajectories=trajs,
        rounds=rounds,
        reason=reason,
        report=right.report or left.report,
        detail={**dict(left.detail), **dict(right.detail), "last_group": group},
    )
