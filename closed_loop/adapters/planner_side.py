# -*- coding: utf-8 -*-
"""下层适配器：``TourOrder`` ⇄ ``path_planning.api.PathPlanner``。

职责：把环的航次消息翻成 ``PlanRequest``（逐机机体、冻结 ``t0``、停留点序列、
       本轮预算），调用门面排一批 + 协调，再把结果压成 ``TourTicket``。
边界：不排航迹、不改几何、不推断机体能力；缺机体规格直接报错。

**与下层的唯一接触点**：本文件是"深路径禁令"在集成层的落点——环只认
``PathPlanner.plan_fleet / busy_until / prune`` 三个方法，``methods/`` 一律不碰。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from path_planning.api import PathPlanner
from path_planning.common.flight_budget import Budget
from path_planning.common.plan_types import PlanRequest, PlanResult, PlanStop, Trajectory
from path_planning.common.vehicle_spec import VehicleSpec

from ..protocol import PlannerReport, TourOrder, TourStop, TourTicket


class PathPlannerAdapter:
    """把下层门面装成 ``PlannerPort``。

    Args:
        planner:      ``PathPlanner`` 实例（场景依赖已在构造时注入）
        vehicles:     机号 → 机体规格（**逐机**，不从上层 ``speed`` 反推）
        service_time_of_task: 任务服务时长（秒）；缺省 0，不猜
    """

    def __init__(
        self,
        planner: PathPlanner,
        *,
        vehicles: Mapping[int, VehicleSpec],
        service_time_of_task: Mapping[int, float] | None = None,
    ) -> None:
        self.planner = planner
        self.vehicles = dict(vehicles)
        self.service_time = dict(service_time_of_task or {})
        #: 上一批交付的航迹（机号 → 航迹；失败机不在表里）。环不复用几何，
        #: 只给实验层落盘用——注册表会在周期末 prune 掉旧航迹，落盘得赶在前面。
        self.last_trajectories: dict[int, Trajectory] = {}

    # ------------------------------------------------------------------
    def plan_cycle(self, orders: Sequence[TourOrder]) -> PlannerReport:
        self.last_trajectories = {}  # 空拍必须清空，否则上一拍的航迹会当代本拍产出被落盘
        requests = [self._request(o) for o in orders]
        if not requests:
            return PlannerReport(tickets={})
        fleet = self.planner.plan_fleet(requests)
        self.last_trajectories = {
            int(uid): fleet.trajectory_of(uid)
            for uid in fleet.results
            if fleet.trajectory_of(uid) is not None
        }
        tickets = {
            int(uid): self._ticket(uid, res, fleet.trajectory_of(uid))
            for uid, res in fleet.results.items()
        }
        unplanned = tuple(sorted(uid for uid, t in tickets.items() if not t.ok))
        outcome = fleet.outcome
        rounds = () if outcome is None else outcome.rounds
        after = None if outcome is None else outcome.report
        moves = {} if outcome is None else dict(outcome.detail.get("moves", {}))
        return PlannerReport(
            tickets=tickets,
            moved={int(k): str(v) for k, v in moves.items()},
            conflicts_before=int(rounds[0].conflicts_before) if rounds else 0,
            conflicts_after=0 if after is None else len(after.conflicts),
            elapsed_s=0.0 if after is None else float(after.elapsed_s),
            unplanned=unplanned,
        )

    def busy_until(self, uav_id: int) -> float:
        return float(self.planner.busy_until(int(uav_id)))

    def prune(self, now: float) -> tuple[int, ...]:
        return tuple(int(u) for u in self.planner.prune(float(now)))

    # ------------------------------------------------------------------
    def _request(self, order: TourOrder) -> PlanRequest:
        vehicle = self.vehicles.get(int(order.uav_id))
        if vehicle is None:
            raise ValueError(
                f"缺机 {order.uav_id} 的机体规格：环不从上层的 speed/max_range 反推机体能力"
            )
        stops = tuple(self._stop(s) for s in order.stops)
        # 上层的航次把"起点机巢"也放进 stops；下层的起点由 start_position 单给，
        # 若照抄就会多出一条长度 0 的 leg（起点→起点），把逐段账和净空豁免都搅乱。
        if stops and stops[0].kind == "nest" and _same_point(stops[0].position, order.start_position):
            stops = stops[1:]
        if not stops:
            raise ValueError(f"机 {order.uav_id} 的航次去掉起点机巢后没有停留点")
        return PlanRequest(
            uav_id=int(order.uav_id),
            vehicle=vehicle,
            start_position=tuple(order.start_position),
            start_velocity=(0.0, 0.0, 0.0),
            t0=float(order.t0),
            stops=stops,
            budget=Budget(
                endurance_remaining_s=float(vehicle.max_time_s),
                range_remaining_m=float(vehicle.max_range_m),
            ),
            meta={"cycle_order": dict(order.meta), "source": "closed_loop"},
        )

    def _stop(self, stop: TourStop) -> PlanStop:
        return PlanStop(
            id=int(stop.id),
            position=tuple(stop.position),
            service_time_s=float(self.service_time.get(int(stop.id), stop.service_time_s)),
            kind=str(stop.kind),
        )

    @staticmethod
    def _ticket(uid: int, res: PlanResult, traj: Trajectory | None) -> TourTicket:
        gate = dict(res.diagnostics.get("global_gate") or {}) or None
        if traj is None:
            return TourTicket(
                uav_id=int(uid),
                ok=False,
                t0=0.0,
                time_source="failed",
                failure=str(getattr(res.failure, "value", res.failure) or "unknown"),
                gate=gate,
            )
        return TourTicket(
            uav_id=int(uid),
            ok=True,
            t0=float(traj.t0),
            t_end=float(traj.t_end),
            duration_s=float(traj.duration),
            path_len_m=float(traj.path_len_m),
            time_source=_source_of(traj),
            gate=gate,
        )


def _source_of(traj: Trajectory) -> str:
    """下层的 ``TimeSource`` → 环的三值口径（与 ``api._source_of`` 同一约定）。"""
    value = str(traj.time_source.value)
    return "planned" if value in ("trajectory", "planned") else value


def _same_point(a, b, tol: float = 1e-6) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
