# -*- coding: utf-8 -*-
"""任务池重开：已完成的任务在事后被判不可交付时，必须能回到候选集。"""

from __future__ import annotations

import unittest

from task_allocation.common.types import Task
from task_allocation.methods.task_pool import TaskPool


def _task(tid: int) -> Task:
    return Task(id=tid, x=float(tid), y=0.0, z=30.0)


class TestTaskPoolReopen(unittest.TestCase):
    def test_reopen_puts_done_task_back_into_candidates(self) -> None:
        pool = TaskPool()
        pool.add_new([_task(1), _task(2)])
        pool.begin_cycle(current_time=0.0)
        pool.complete([1, 2])

        pool.reopen([1])
        snap = pool.begin_cycle(current_time=1800.0)

        self.assertIn(1, snap.release_ids)
        self.assertIn(1, [t.id for t in snap.tasks])
        self.assertNotIn(2, [t.id for t in snap.tasks])
        self.assertEqual(pool.find(1).meta["reopen_reason"], "post_infeasible")

    def test_reopen_only_accepts_finished_tasks(self) -> None:
        pool = TaskPool()
        pool.add_new([_task(3)])
        with self.assertRaises(KeyError):
            pool.reopen([3])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
