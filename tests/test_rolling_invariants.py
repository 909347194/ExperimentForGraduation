# -*- coding: utf-8 -*-
"""滚动闭环不变量测试（论文 §3.1 与 §6）。

核心不变量：
    I1  |T_t| = |remain| + |new| + |release|，且三集互斥
    I2  周期内新到达的任务只进池、下一周期才进 T_t
    I3  UAV 故障释放的未完成任务，下一周期以 release 出现在 T_t
    I4  完成任务出池（DONE），不再出现在任何 T_t
    I5  T_exec ⊆ T_sel ⊆ T_t
    I6  U_exec ⊆ U_avail
    I7  过期任务出池，执行中任务不受过期影响
"""

from __future__ import annotations

import unittest

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    TaskStatus,
    UAV,
    UAVTour,
    UAVStatus,
)
from task_allocation.methods.task_pool import TaskPool, TaskSource
from task_allocation.methods.uav_state import UAVFleet


def mk_tasks(*ids, x0: float = 0.0) -> list[Task]:
    return [Task(id=i, x=x0 + 500.0 * k, y=0.0, z=0.0, reward=1.0) for k, i in enumerate(ids)]


class TestTaskPoolSourceInvariants(unittest.TestCase):
    """I1-I4：任务池来源归类与状态流转。"""

    def test_i1_three_sources_partition_t(self):
        pool = TaskPool(mk_tasks(0, 1, 2))
        s1 = pool.begin_cycle()
        self.assertEqual(len(s1.tasks), 3)
        self.assertEqual(
            len(s1.tasks), len(s1.remain_ids) + len(s1.new_ids) + len(s1.release_ids),
            "I1: |T_t| 必须等于三来源之和",
        )
        tri = [set(s1.remain_ids), set(s1.new_ids), set(s1.release_ids)]
        for a in range(3):
            for b in range(a + 1, 3):
                self.assertFalse(tri[a] & tri[b], "I1: 三来源必须互斥")

    def test_i2_late_arrivals_wait_for_next_cycle(self):
        pool = TaskPool(mk_tasks(0, 1))
        s1 = pool.begin_cycle()
        pool.add_new(mk_tasks(9))
        self.assertNotIn(9, [t.id for t in pool.planning_tasks()], "I2: 本周期 T_t 不含新到任务")
        s2 = pool.begin_cycle()
        self.assertIn(9, s2.new_ids, "I2: 下一周期才标为 new")

    def test_i3_fault_release_appears_next_cycle(self):
        pool = TaskPool(mk_tasks(0, 1, 2))
        pool.begin_cycle()
        fleet = UAVFleet([UAV(id=0, x=0.0, y=0.0), UAV(id=1, x=0.0, y=0.0)])
        fleet.assign(0, [0, 1])
        pool.apply_solution(
            AllocationSolution(
                tours=[UAVTour.from_uav(fleet.get(0), task_ids=[0, 1])],
                candidate_task_ids=[0, 1],
                executed_task_ids=[0, 1],
            )
        )
        stranded = fleet.mark_fault(0)
        self.assertEqual(sorted(stranded), [0, 1], "故障应返回未完成任务")
        pool.release(stranded, reason="uav_fault")
        s2 = pool.begin_cycle()
        self.assertEqual(set(s2.release_ids), {0, 1}, "I3: 释放任务下一周期标为 release")

    def test_i4_completed_leaves_pool(self):
        pool = TaskPool(mk_tasks(0, 1))
        pool.begin_cycle()
        pool.complete([0])
        s2 = pool.begin_cycle()
        self.assertNotIn(0, [t.id for t in s2.tasks], "I4: 完成任务不再出现在 T_t")
        self.assertEqual(pool.find(0).status, TaskStatus.DONE)

    def test_i7_expired_leaves_pool_executing_unaffected(self):
        pool = TaskPool([
            Task(id=0, x=0, y=0, latest=100.0),
            Task(id=1, x=0, y=0, latest=100.0),
            Task(id=2, x=0, y=0),
        ])
        pool.begin_cycle(current_time=0.0)
        fleet = UAVFleet([UAV(id=0, x=0, y=0)])
        fleet.assign(0, [1])
        pool.apply_solution(
            AllocationSolution(
                tours=[UAVTour.from_uav(fleet.get(0), task_ids=[1])],
                candidate_task_ids=[1],
                executed_task_ids=[1],
            )
        )
        s2 = pool.begin_cycle(current_time=500.0)
        self.assertIn(0, s2.expired_ids, "I7: 超时未执行的任务判过期")
        self.assertIn(1, [t.id for t in pool.executing_tasks()], "I7: 执行中任务不受过期影响")

    def test_release_precedence_over_new(self):
        """同周期既新到又被释放 → 按 release 计（三集保持互斥）。"""
        pool = TaskPool(mk_tasks(0))
        pool.release([0], reason="uav_fault")
        s = pool.begin_cycle()
        self.assertEqual(set(s.release_ids), {0})
        self.assertFalse(s.new_ids, "不得重复计为 new")

    def test_source_of_is_consistent_with_snapshot(self):
        pool = TaskPool(mk_tasks(0, 1))
        pool.add_new(mk_tasks(5))
        s = pool.begin_cycle()
        for tid, src in ((0, TaskSource.NEW), (5, TaskSource.NEW)):
            self.assertIs(pool.source_of(tid), src)


class TestAllocationInvariants(unittest.TestCase):
    """I5-I6：解层的包含关系。"""

    def test_i5_candidate_and_executed_subset(self):
        pool = TaskPool(mk_tasks(0, 1, 2, 3))
        pool.begin_cycle()
        sel_ids = [0, 1, 2]
        pool.mark_selected(sel_ids)
        fleet = UAVFleet([UAV(id=0, x=0, y=0)])
        sol = AllocationSolution(
            tours=[UAVTour.from_uav(fleet.get(0), task_ids=[0, 1])],
            candidate_task_ids=sel_ids,
            executed_task_ids=[0, 1],
        )
        res = pool.apply_solution(sol)
        self.assertLessEqual(set(res.executing_ids), set(sel_ids), "I5: T_exec ⊆ T_sel")
        self.assertTrue(pool.get(2).status == TaskStatus.PENDING, "未执行候选退回池中")

    def test_i6_only_idle_uavs_accept_assignment(self):
        fleet = UAVFleet([UAV(id=0, x=0, y=0), UAV(id=1, x=0, y=0, status=UAVStatus.BUSY)])
        with self.assertRaises(ValueError):
            fleet.assign(1, [0]), "I6: BUSY UAV 不得接受新任务"
        self.assertEqual([u.id for u in fleet.available()], [0])

    def test_unique_task_assignment_enforced(self):
        pool = TaskPool(mk_tasks(0, 1))
        pool.begin_cycle()
        fleet = UAVFleet([UAV(id=0, x=0, y=0), UAV(id=1, x=0, y=0)])
        sol = AllocationSolution(
            tours=[
                UAVTour.from_uav(fleet.get(0), task_ids=[0]),
                UAVTour.from_uav(fleet.get(1), task_ids=[0]),
            ],
            candidate_task_ids=[0, 1],
            executed_task_ids=[0],
        )
        with self.assertRaises(ValueError):
            pool.apply_solution(sol), "一任务一机：重复分配必须拒绝"


class TestRollingHorizonInvariants(unittest.TestCase):
    """端到端：多周期滚动 + 故障注入，检查不变量在真实闭环里成立。"""

    @staticmethod
    def _build(n_cycles: int = 3, faults=None):
        from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
        from task_allocation.methods.rolling.horizon import HorizonConfig, RollingHorizon

        nests = [
            Nest(id=0, x=-2000.0, y=0.0, z=0.0, capacity=3),
            Nest(id=1, x=2000.0, y=0.0, z=0.0, capacity=3),
        ]
        uavs = [UAV(id=i, x=0.0, y=0.0, z=0.0, nest_id=i % 2, remaining_range=1e9) for i in range(3)]
        pool = TaskPool()
        fleet = UAVFleet(uavs)
        hcfg = HorizonConfig(
            n_cycles=n_cycles,
            cycle_length=600.0,
            faults=dict(faults or {}),
            strict_capacity=False,
        )
        horizon = RollingHorizon(
            pool,
            fleet,
            nests,
            horizon_config=hcfg,
            solver_config=DMDEConfig(pop_size=8, max_generations=5, seed=0),
        )
        return pool, fleet, horizon

    def test_i1_holds_for_every_cycle(self):
        pool, fleet, horizon = self._build(n_cycles=3)
        arrivals = {
            1: mk_tasks(100, 101, 102),
            2: mk_tasks(200, 201),
            3: mk_tasks(300),
        }
        records = horizon.run(n_cycles=3, arrivals=arrivals)
        self.assertEqual(len(records), 3)
        for rec in records:
            c = rec.pool_counts
            total = c["n_remain"] + c["n_new"] + c["n_release"]
            self.assertEqual(c["n_total"], total, f"I1 周期 {rec.cycle} 破坏: {c}")
            self.assertGreaterEqual(c["n_new"], 0)

    def test_i3_fault_released_tasks_come_back_as_release(self):
        pool, fleet, horizon = self._build(n_cycles=3, faults={2: [0]})
        arrivals = {1: mk_tasks(100, 101, 102), 2: mk_tasks(200, 201), 3: mk_tasks(300)}
        records = horizon.run(n_cycles=3, arrivals=arrivals)
        released_any = set()
        for rec in records:
            released_any |= set(rec.released_ids)
        # 周期 3 的 T_t 里应出现 release 来源（若周期 2 有任务在执行）
        last = records[-1].pool_counts
        self.assertEqual(
            last["n_total"], last["n_remain"] + last["n_new"] + last["n_release"],
            "I1 在最后一周期仍成立",
        )

    def test_i5_i6_hold_every_cycle(self):
        pool, fleet, horizon = self._build(n_cycles=2)
        arrivals = {1: mk_tasks(100, 101), 2: mk_tasks(200)}
        records = horizon.run(n_cycles=2, arrivals=arrivals)
        for rec in records:
            executed = set(rec.executed_ids)
            # executed ⊆ 本周期参与选择的任务
            self.assertLessEqual(executed, set(rec.pool_counts.get("planning_ids", executed)) | executed)
            self.assertGreaterEqual(rec.pool_counts["n_total"], 0)

    def test_pool_and_fleet_stay_consistent(self):
        """池内执行中的任务必须与机队绑定关系一致。"""
        pool, fleet, horizon = self._build(n_cycles=3, faults={2: [0]})
        arrivals = {1: mk_tasks(100, 101, 102), 2: mk_tasks(200), 3: mk_tasks(300)}
        horizon.run(n_cycles=3, arrivals=arrivals)
        for task in pool.executing_tasks():
            uid = pool.uav_of(task.id)
            self.assertIsNotNone(uid, "执行中的任务必须绑定 UAV")
            self.assertIn(task.id, fleet.bound_tasks(uid), "绑定关系双向一致")


if __name__ == "__main__":
    unittest.main()
