# -*- coding: utf-8 -*-
"""机巢容量约束测试。

覆盖 `docs/design_nest_state.md` 的核心结论：
    - 占用从 uav.nest_id 推导（含飞行中），不落第二份状态
    - 按「末态归属」校验，而非「返回架数」
    - 机巢关闭时留在原地不算违反，但禁止新降落
    - 落地期硬校验：先校验后写入，超容不静默接受
"""

from __future__ import annotations

import unittest

from task_allocation.common.types import AllocationSolution, Nest, UAV, UAVTour, UAVStatus
from task_allocation.methods.muas.constraints.nest_capacity import (
    CapacityReport,
    check_nest_capacity,
    choose_end_nests,
    fleet_occupancy,
    free_slots,
    nest_occupancy,
)
from task_allocation.methods.uav_state import UAVFleet


def make_nests(capacity: int = 4, n: int = 2, available: bool = True) -> list[Nest]:
    return [
        Nest(id=i, x=float(i * 1000), y=0.0, z=0.0, capacity=capacity, available=available)
        for i in range(n)
    ]


def make_uav(uid: int, nest_id: int | None, status: UAVStatus = UAVStatus.IDLE) -> UAV:
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, nest_id=nest_id, status=status)


class TestOccupancyDerivation(unittest.TestCase):
    """占用一律从 nest_id 推导，含飞行中 / 未降落。"""

    def test_in_flight_uav_still_counts(self):
        uavs = [make_uav(0, 0), make_uav(1, 0, UAVStatus.BUSY), make_uav(2, 1)]
        self.assertEqual(nest_occupancy(uavs, 0), 2, "BUSY（飞行中）仍占用归属巢名额")
        self.assertEqual(nest_occupancy(uavs, 1), 1)
        self.assertEqual(fleet_occupancy(uavs), {0: 2, 1: 1})

    def test_unassigned_uav_not_counted(self):
        uavs = [make_uav(0, None), make_uav(1, 0)]
        self.assertEqual(nest_occupancy(uavs, 0), 1)
        self.assertNotIn(None, fleet_occupancy(uavs))

    def test_free_slots(self):
        nests = make_nests(capacity=4, n=2)
        uavs = [make_uav(0, 0), make_uav(1, 0)]
        self.assertEqual(free_slots(nests, fleet_occupancy(uavs)), {0: 2, 1: 4})

    def test_faulty_uav_counts(self):
        uavs = [make_uav(0, 0, UAVStatus.FAULT)]
        self.assertEqual(nest_occupancy(uavs, 0), 1, "FAULT 默认保守占名额（不超卖）")


class TestCapacityCheck(unittest.TestCase):
    """按末态归属校验，而非按返回架数。"""

    def test_full_capacity_is_ok(self):
        nests = make_nests(capacity=3, n=1)
        uavs = [make_uav(i, 0) for i in range(3)]
        rep = check_nest_capacity(uavs, nests, {})
        self.assertTrue(rep.ok, "刚好满不算违反")
        self.assertEqual(rep.per_nest[0]["overflow"], 0)

    def test_overflow_by_one_detected(self):
        nests = make_nests(capacity=3, n=1)
        uavs = [make_uav(i, 0) for i in range(3)] + [make_uav(3, 1)]
        rep = check_nest_capacity(uavs, nests, {3: 0})  # 外来 1 架
        self.assertFalse(rep.ok)
        self.assertIn(0, rep.violations)
        self.assertEqual(rep.per_nest[0]["overflow"], 1)
        self.assertGreater(rep.penalty, 0.0)

    def test_arrival_fits_when_same_nest_uavs_depart(self):
        """用户问的场景：可用容量 2，却分配 3 架返回。

        若这 3 架里有 2 架本来就归属该巢（起飞让出名额），末态 = 3 <= 4，可行。
        """
        nests = make_nests(capacity=4, n=1)
        uavs = [make_uav(0, 0), make_uav(1, 0), make_uav(2, 1), make_uav(3, 1)]
        # free_slots = 2，但 0/1 号原地不动，2/3 号外来
        rep = check_nest_capacity(uavs, nests, {0: 0, 1: 0, 2: 0, 3: 0})
        self.assertEqual(rep.per_nest[0]["arrivals"], 2)
        self.assertEqual(rep.per_nest[0]["occupied_after"], 4)
        self.assertTrue(rep.ok, "末态 4 <= 4，不应按「返回 4 架 > 空位 2」误判")

    def test_all_foreign_arrivals_overflow(self):
        nests = make_nests(capacity=4, n=1)
        uavs = [make_uav(0, 0), make_uav(1, 0), make_uav(2, 1), make_uav(3, 1)]
        rep = check_nest_capacity(uavs, nests, {2: 0, 3: 0})
        self.assertEqual(rep.per_nest[0]["occupied_after"], 4)
        self.assertTrue(rep.ok)

    def test_departure_frees_slot(self):
        nests = make_nests(capacity=2, n=2)
        uavs = [make_uav(0, 0), make_uav(1, 0), make_uav(2, 1)]
        # 0 号改降 1 号巢，1 号原地；外来 2 号降到 0 号巢
        rep = check_nest_capacity(uavs, nests, {0: 1, 1: 0, 2: 0})
        self.assertEqual(rep.per_nest[0]["departures"], 1)
        self.assertEqual(rep.per_nest[0]["occupied_after"], 2)
        self.assertTrue(rep.ok, "起飞让出名额后外来降落可容纳")

    def test_no_change_when_end_equals_current(self):
        nests = make_nests(capacity=2, n=1)
        uavs = [make_uav(0, 0), make_uav(1, 0)]
        rep = check_nest_capacity(uavs, nests, {0: 0, 1: 0})
        self.assertEqual(rep.per_nest[0]["arrivals"], 0)
        self.assertEqual(rep.per_nest[0]["departures"], 0)
        self.assertTrue(rep.ok)

    def test_unavailable_nest_with_arrival_is_violation(self):
        nests = make_nests(capacity=4, n=2, available=False)
        uavs = [make_uav(0, 1)]
        rep = check_nest_capacity(uavs, nests, {0: 0}, strict=True)
        self.assertFalse(rep.ok)
        self.assertIn(0, rep.violations, "机巢关闭却被选作终点 → 违反")
        self.assertGreater(rep.penalty, 0.0)

    def test_unavailable_nest_with_staying_uav_is_ok(self):
        """设计文档 §8：机巢关闭时已有 UAV 保持归属，仅禁止新分配。"""
        nests = make_nests(capacity=4, n=1, available=False)
        uavs = [make_uav(0, 0)]
        rep = check_nest_capacity(uavs, nests, {0: 0}, strict=True)
        self.assertTrue(rep.ok, "原地不动不算违反")

    def test_non_strict_ignores_availability(self):
        nests = make_nests(capacity=4, n=1, available=False)
        uavs = [make_uav(0, 1)]
        rep = check_nest_capacity(uavs, nests, {0: 0}, strict=False)
        self.assertTrue(rep.ok, "strict=False 时只查容量")

    def test_report_is_serialisable(self):
        nests = make_nests(capacity=1, n=1)
        uavs = [make_uav(0, 0), make_uav(1, 1)]
        rep = check_nest_capacity(uavs, nests, {1: 0})
        d = rep.as_dict()
        self.assertIn("per_nest", d)
        self.assertIn("violations", d)
        self.assertIsInstance(rep, CapacityReport)


class TestChooseEndNests(unittest.TestCase):
    """容量感知的终点机巢改派（设计文档 §7 ①）。"""

    def test_prefers_nest_with_free_slots(self):
        nests = make_nests(capacity=2, n=2)
        uavs = [make_uav(0, 0), make_uav(1, 0), make_uav(2, 1)]
        costs = {0: {0: 10.0, 1: 20.0}, 1: {0: 10.0, 1: 20.0}, 2: {0: 30.0, 1: 5.0}}
        end = choose_end_nests(uavs, nests, costs)
        # 0/1 号归属 0 号巢（容量 2 已满），1 号巢只有 2 号
        self.assertEqual(end[2], 1)
        rep = check_nest_capacity(uavs, nests, end)
        self.assertTrue(rep.ok, f"改派结果应不超容，实际 {rep.per_nest}")

    def test_departing_uavs_release_slots(self):
        nests = make_nests(capacity=1, n=2)
        uavs = [make_uav(0, 0), make_uav(1, 1)]
        costs = {0: {0: 1.0, 1: 2.0}, 1: {0: 1.0, 1: 2.0}}
        end = choose_end_nests(uavs, nests, costs)
        rep = check_nest_capacity(uavs, nests, end)
        self.assertTrue(rep.ok)

    def test_falls_back_when_all_full(self):
        nests = make_nests(capacity=1, n=2)
        uavs = [make_uav(0, 0), make_uav(1, 1), make_uav(2, None)]
        costs = {2: {0: 5.0, 1: 1.0}}
        end = choose_end_nests(uavs, nests, costs, planning_uav_ids=[2])
        self.assertIn(end[2], (0, 1), "全满时仍取代价最小的巢，交由 penalty 兜底")

    def test_skips_unavailable_nest(self):
        nests = make_nests(capacity=5, n=2)
        nests[0].available = False
        uavs = [make_uav(0, 1)]
        costs = {0: {0: 1.0, 1: 99.0}}
        end = choose_end_nests(uavs, nests, costs, planning_uav_ids=[0])
        self.assertEqual(end[0], 1, "不可用巢不应被选")


class TestFleetApplySolution(unittest.TestCase):
    """落地期：先校验后写入，nest_id 是唯一写点。"""

    def _fleet(self, capacity: int = 4, n_nests: int = 2):
        nests = make_nests(capacity=capacity, n=n_nests)
        fleet = UAVFleet([make_uav(i, i % n_nests) for i in range(4)])
        return nests, fleet

    def test_apply_solution_updates_nest_id(self):
        nests, fleet = self._fleet()
        sol = AllocationSolution(
            tours=[UAVTour.from_uav(fleet.get(0), task_ids=[10], end_nest_id=1)],
            candidate_task_ids=[10],
            executed_task_ids=[10],
        )
        fleet.apply_solution(sol, nests)
        self.assertEqual(fleet.get(0).nest_id, 1, "z_ub 落地应更新 nest_id")
        self.assertEqual(fleet.get(0).status, UAVStatus.BUSY)

    def test_strict_rejects_overflow_and_writes_nothing(self):
        nests, fleet = self._fleet(capacity=1, n_nests=1)
        # 4 架都归属 0 号巢（容量 1）→ 任何再分配都超容
        for i in range(4):
            fleet.reassign_nest(i, 0)
        sol = AllocationSolution(
            tours=[UAVTour.from_uav(fleet.get(3), task_ids=[10], end_nest_id=0)],
            candidate_task_ids=[10],
            executed_task_ids=[10],
        )
        with self.assertRaises(Exception):
            fleet.apply_solution(sol, nests, strict_capacity=True)
        self.assertNotEqual(fleet.get(3).status, UAVStatus.BUSY, "校验失败不得进入 BUSY")

    def test_non_strict_warns_but_writes(self):
        nests, fleet = self._fleet(capacity=1, n_nests=1)
        for i in range(4):
            fleet.reassign_nest(i, 0)
        sol = AllocationSolution(
            tours=[UAVTour.from_uav(fleet.get(3), task_ids=[10], end_nest_id=0)],
            candidate_task_ids=[10],
            executed_task_ids=[10],
        )
        seen = []
        fleet.apply_solution(sol, nests, strict_capacity=False, on_violation=seen.append)
        self.assertTrue(seen, "应通过回调告警")
        self.assertEqual(fleet.get(3).nest_id, 0)

    def test_reassign_nest_moves_occupancy(self):
        nests, fleet = self._fleet()
        self.assertEqual(fleet.nest_occupancy(nests)[0], 2)
        fleet.reassign_nest(0, 1)
        self.assertEqual(fleet.nest_occupancy(nests)[0], 1)
        self.assertEqual(fleet.nest_occupancy(nests)[1], 3)

    def test_decommission_frees_slot(self):
        nests, fleet = self._fleet()
        fleet.decommission(0)
        self.assertIsNone(fleet.get(0).nest_id)
        self.assertEqual(fleet.nest_occupancy(nests)[0], 1)

    def test_takeoff_and_landing_do_not_change_occupancy(self):
        """设计文档 §5.2：起飞/降落一律不改容量。"""
        nests, fleet = self._fleet()
        before = fleet.nest_occupancy(nests)
        fleet.assign(0, [10, 11])
        self.assertEqual(fleet.nest_occupancy(nests), before, "起飞不释放名额")
        fleet.complete_tasks(0, [10, 11])
        self.assertEqual(fleet.nest_occupancy(nests), before, "降落不新增占用")
        fleet.mark_fault(1)
        self.assertEqual(fleet.nest_occupancy(nests), before, "故障不释放名额")


if __name__ == "__main__":
    unittest.main()
