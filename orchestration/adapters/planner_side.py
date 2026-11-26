# -*- coding: utf-8 -*-
"""下层适配器：``TourOrder`` ⇄ ``path_planning.api.PathPlanner``。

职责：把环的航次消息翻成 ``PlanRequest``（逐机机体、冻结 ``t0``、停留点序列、
       本次预算），调用门面排一批 + 协调，再把结果压成 ``TourTicket``。
边界：不排航迹、不改几何、不推断机体能力；缺机体规格直接报错。

**与下层的唯一接触点**：本文件是"深路径禁令"在集成层的落点——环只认
``PathPlanner.plan_fleet / busy_until / prune`` 三个方法，``methods/`` 一律不碰。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

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
        self.last_trajectories = {}  # 空周期必须清空，否则上一周期的航迹会当代本周期产出被落盘
        requests = [self._request(o) for o in orders]
        if not requests:
            return PlannerReport(tickets={})
        order_by_uid = {int(o.uav_id): o for o in orders}
        fleet = self.planner.plan_fleet(requests)
        self.last_trajectories = {
            int(uid): fleet.trajectory_of(uid)
            for uid in fleet.results
            if fleet.trajectory_of(uid) is not None
        }
        tickets = {
            int(uid): self._ticket(order_by_uid[int(uid)], res, fleet.trajectory_of(uid))
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

    def reset_cycle(self, uav_ids: Sequence[int]) -> tuple[int, ...]:
        """撤下上一版解的已发布航迹（同周期重解前必做）。

        为什么必须显式撤：``plan_fleet`` 把注册表里**其它机**的航迹当作既有
        时空约束（那是跨周期口径）。上一版刚排出来的航迹如果留在表里，第二版
        会把自己上一版当成"别人在飞"去躲——解越重越保守，且永远无法把上一版
        改掉。重解 = 本周期这一版作废，故先注销这些机号再排。
        """
        dropped: list[int] = []
        for uid in sorted({int(u) for u in uav_ids}):
            if self.planner.registry.unregister(uid) is not None:
                dropped.append(int(uid))
        return tuple(dropped)

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
        # 首段起点的**记账 id** 用 UAV 负键 -(1+机号)，与上层 tour_edges 的 UAV 起点
        # 同构（muas/edge_keys.tour_edges）；不能用起点巢 id——任务与机巢共用整数
        # 编号（巢 1/2 撞任务 1/2），拿巢 id 记账会在回灌翻译时把实测写到任务边上。
        origin_id = -(1 + int(order.uav_id))
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
            # ``origin_id`` 让下层逐段账的第一条腿也有端点（UAV 负键）。
            # 与上层 tour_edges 的首段起点同构，回灌按边直接对上，不依赖 id 猜角色。
            meta={
                "cycle_order": dict(order.meta),
                "source": "orchestration",
                "origin_id": origin_id,
            },
        )

    def _stop(self, stop: TourStop) -> PlanStop:
        return PlanStop(
            id=int(stop.id),
            position=tuple(stop.position),
            service_time_s=float(self.service_time.get(int(stop.id), stop.service_time_s)),
            kind=str(stop.kind),
        )

    @staticmethod
    def _nest_ids(order: TourOrder) -> tuple[int | None, int | None]:
        """航次两端的机巢（下层 id）：``stops`` 首项起飞、末项回收。

        回灌时靠终点定"哪条边是返航边"；出图与排查靠两端说清"从哪个 Base 起飞、
        回到哪个 Base"——机巢与任务共用整数编号，角色只能由航次结构给出。
        """
        if not order.stops:
            return None, None
        first, last = order.stops[0], order.stops[-1]
        start = int(first.id) if str(first.kind) == "nest" else None
        end = int(last.id) if str(last.kind) == "nest" else None
        return start, end

    @staticmethod
    def _ticket(order: TourOrder, res: PlanResult, traj: Trajectory | None) -> TourTicket:
        gate = dict(res.diagnostics.get("global_gate") or {}) or None
        start_nest_id, end_nest_id = PathPlannerAdapter._nest_ids(order)
        if traj is None:
            return TourTicket(
                uav_id=int(order.uav_id),
                ok=False,
                t0=float(order.t0),  # 冻结的出发时刻：失败航次也要能对到"哪一拍"
                time_source="failed",
                failure=str(getattr(res.failure, "value", res.failure) or "unknown"),
                gate=gate,
                detail=str(res.detail or ""),
                failure_diag=_jsonable_diag(res.diagnostics),
                task_ids=tuple(int(t) for t in order.task_ids),
                leg_costs=tuple(res.leg_costs),
                failure_severity=res.failure_severity,
                start_nest_id=start_nest_id,
                end_nest_id=end_nest_id,
            )
        return TourTicket(
            uav_id=int(order.uav_id),
            ok=True,
            task_ids=tuple(int(t) for t in order.task_ids),
            t0=float(traj.t0),
            t_end=float(traj.t_end),
            duration_s=float(traj.duration),
            path_len_m=float(traj.path_len_m),
            time_source=_source_of(traj),
            gate=gate,
            leg_costs=tuple(res.leg_costs),
            failure_severity=res.failure_severity,
            start_nest_id=start_nest_id,
            end_nest_id=end_nest_id,
        )


def _source_of(traj: Trajectory) -> str:
    """下层的 ``TimeSource`` → 环的三值口径（与 ``api._source_of`` 同一约定）。"""
    value = str(traj.time_source.value)
    return "planned" if value in ("trajectory", "planned") else value


def _same_point(a, b, tol: float = 1e-6) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


def _jsonable_diag(diag: Any) -> dict[str, Any]:
    """把下层诊断洗成 JSON 原生类型。

    数组不原样落盘：``fallback_path`` 是整条折线，写进 trace 只会把文件撑大；
    几何类的 ndarray 一律压成"点数 + 首点 + 包络"，够排查又够轻。

    **例外**：``prefix_nodes`` / ``failed_leg_path`` 按点原样保留。它们是失败航次
    唯一可画的几何（prefix 已解段 + 断点段），压成包络就画不出"哪几段飞成、
    哪一段断"；一个航次的点数在几十量级，代价可接受。
    """
    if not isinstance(diag, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in diag.items():
        key = str(k)
        if key == "fallback_path":
            continue
        out[key] = (
            _polyline(v) if key in _KEEP_GEOMETRY else _jsonable_value(v)
        )
    return out


#: 失败航次诊断里需要保留完整折线的键（其余几何仍压成包络）
_KEEP_GEOMETRY = ("prefix_nodes", "failed_leg_path")


def _polyline(value: Any) -> list[list[float]] | None:
    """``(N, 3)`` 折线 → ``[[x, y, z], ...]``（2 位小数）；空值原样返回 None。"""
    if value is None:
        return None
    arr = np.asarray(value, dtype=float).reshape(-1, 3)
    return [[round(float(v), 2) for v in row] for row in arr]


def _jsonable_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, np.ndarray):
        arr = np.asarray(value, dtype=float)
        if arr.ndim == 1 and arr.size <= 3:
            return [round(float(v), 3) for v in arr]
        if arr.ndim == 2 and arr.shape[1] == 3:
            return {
                "points": int(arr.shape[0]),
                "x": [round(float(arr[:, 0].min()), 1), round(float(arr[:, 0].max()), 1)],
                "y": [round(float(arr[:, 1].min()), 1), round(float(arr[:, 1].max()), 1)],
                "z": [round(float(arr[:, 2].min()), 1), round(float(arr[:, 2].max()), 1)],
            }
        return {"size": int(arr.size)}
    if isinstance(value, (list, tuple)):
        return [_jsonable_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable_value(v) for k, v in value.items()}
    return str(value)
