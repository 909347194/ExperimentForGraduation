# -*- coding: utf-8 -*-
"""适配器测试：``TourOrder`` → ``PlanRequest`` 的字段映射（不建场、不求解）。

真航迹的 ``Trajectory`` → ``TourTicket`` 转换在场景级验证里覆盖；这里只钉
"冻结 t0、逐机机体、预算口径、服务时长不被凭空补齐"这几条容易漂的语义。
"""

from __future__ import annotations

import unittest

from closed_loop.adapters.planner_side import PathPlannerAdapter
from closed_loop.protocol import TourOrder, TourStop
from path_planning.common.plan_types import PlanResult, Trajectory
from path_planning.common.vehicle_spec import VehicleSpec

VEHICLE = VehicleSpec(
    model="M30T", mass_kg=9.5, v_max_mps=15.0, v_up_mps=6.0, v_dn_mps=3.0,
    a_max_mps2=3.0, f_max_n=200.0, f_min_n=78.0, max_range_m=20000.0,
    max_time_s=1200.0,
)


def _order(*, task_ids=(11, 12), t0=540.0) -> TourOrder:
    stops = [TourStop(id=0, kind="nest", position=(-880.0, 1350.0, 13.1))]
    for tid in task_ids:
        stops.append(TourStop(id=tid, kind="task", position=(100.0, 20.0, 60.0)))
    stops.append(TourStop(id=1, kind="nest", position=(-486.0, -1458.0, 12.0)))
    return TourOrder(
        uav_id=1, t0=t0, start_position=(-880.0, 1350.0, 13.1), stops=tuple(stops),
    )


class TestRequestMapping(unittest.TestCase):
    def _adapter(self, vehicles=None) -> PathPlannerAdapter:
        return PathPlannerAdapter(
            planner=object(),  # 只测映射，不触求解
            vehicles={1: VEHICLE} if vehicles is None else vehicles,
            service_time_of_task={11: 30.0},
        )

    def test_frozen_t0_and_stop_order(self) -> None:
        req = self._adapter()._request(_order())
        self.assertEqual(req.t0, 540.0)
        self.assertEqual(req.start_position, (-880.0, 1350.0, 13.1))

    def test_start_nest_is_not_duplicated_as_a_stop(self) -> None:
        """上层的起点机巢与 ``start_position`` 重合时必须丢掉，否则多一条 0 长度 leg。"""
        req = self._adapter()._request(_order())
        self.assertEqual([s.id for s in req.stops], [11, 12, 1])
        self.assertEqual([s.kind for s in req.stops], ["task", "task", "nest"])
        self.assertTrue(req.has_task_stops)

    def test_budget_from_vehicle_nominal_not_upper_state(self) -> None:
        req = self._adapter()._request(_order())
        self.assertEqual(req.budget.endurance_remaining_s, 1200.0)
        self.assertEqual(req.budget.range_remaining_m, 20000.0)

    def test_service_time_only_from_explicit_source(self) -> None:
        req = self._adapter()._request(_order())
        by_id = {s.id: s.service_time_s for s in req.stops}
        self.assertEqual(by_id[11], 30.0)   # 显式给了
        self.assertEqual(by_id[12], 0.0)    # 没给就问不出，不自作主张

    def test_missing_vehicle_raises_instead_of_guessing(self) -> None:
        with self.assertRaises(ValueError):
            self._adapter(vehicles={})._request(_order())


class TestTicketMapping(unittest.TestCase):
    def test_failed_result_maps_to_failed_ticket(self) -> None:
        res = PlanResult.failed(uav_id=1, failure=_no_path(), detail="no rrt path")
        ticket = PathPlannerAdapter._ticket(1, res, None)
        self.assertFalse(ticket.ok)
        self.assertEqual(ticket.time_source, "failed")
        self.assertIsNotNone(ticket.failure)

    def test_trajectory_maps_to_planned_ticket(self) -> None:
        traj = _straight_trajectory()
        res = PlanResult(uav_id=1, trajectory=traj)
        ticket = PathPlannerAdapter._ticket(1, res, traj)
        self.assertTrue(ticket.ok)
        self.assertEqual(ticket.time_source, "planned")
        self.assertAlmostEqual(ticket.t0, 540.0)
        self.assertAlmostEqual(ticket.t_end, 560.0)
        self.assertAlmostEqual(ticket.duration_s, 20.0)


def _no_path():
    from path_planning.common.failure_codes import FailureCode

    return FailureCode.RRT_NO_PATH


def _straight_trajectory() -> Trajectory:
    from path_planning.common.plan_types import HermiteSegment

    seg = HermiteSegment(
        t0=540.0, t1=560.0,
        p0=(-880.0, 1350.0, 13.1), p1=(-780.0, 1330.0, 60.0),
        v0=(0.0, 0.0, 0.0), v1=(0.0, 0.0, 0.0),
    )
    return Trajectory(uav_id=1, segments=(seg,), path_len_m=110.0)


if __name__ == "__main__":
    unittest.main()
