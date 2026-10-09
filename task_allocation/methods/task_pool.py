# -*- coding: utf-8 -*-
"""task_pool.py — 动态任务池管理（remain / new / release）。

对应论文 3.1 的任务池定义与第 6 节的闭环更新：

    T_t = T_t^remain ∪ T_t^new ∪ T_t^release

其中：

    T_t^remain   上一周期未执行、继续保留的任务
    T_t^new     本周期新到达任务
    T_t^release  因 UAV 故障等原因重新释放的任务

第 6 节的状态流转约定（本模块据此实现）：

    完成任务       从任务池移除（DONE）
    未选 / 未执行   继续保留（回到 PENDING，下周期算 remain）
    已分配执行中    与 UAV 绑定（EXECUTING），不再进入本周期任务选择
    故障未完成     释放回任务池（release，下周期算 release）
    新到达任务     进入下一周期任务池

与相邻模块的衔接：

    planning_tasks()  ->  methods.selection.run
                     ->  methods.muas.problem.SelectiveMUASProblem.from_entities
    apply_solution()  <-  methods.muas 求解得到的 common.types.AllocationSolution
    release()         <-  methods.uav_state.UAVFleet.mark_fault() 返回的未完成 task_id

注意：``planning_tasks()`` 返回的是**当前周期的 T_t**，是一个已冻结的切面；
``begin_cycle()`` 之后的新到达任务只进池、不进当前 T_t，符合"新到达任务加入
下一周期任务池"的约定。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, Sequence

from task_allocation.common.types import (
    AllocationSolution,
    Task,
    TaskStatus,
)

__all__ = [
    "TaskSource",
    "TaskPoolSnapshot",
    "ApplyResult",
    "TaskPool",
]


class TaskSource(str, Enum):
    """任务进入当前滚动周期 T_t 的来源（论文 3.1 的三项并集）。"""

    REMAIN = "remain"
    NEW = "new"
    RELEASE = "release"


@dataclass(frozen=True)
class TaskPoolSnapshot:
    """滚动周期 t 的任务池切面。

    attributes:
        cycle: 周期序号（从 1 开始）
        tasks: T_t = remain ∪ new ∪ release，即本周期参与任务选择的任务
        remain_ids / new_ids / release_ids: 三个来源的 task_id，互不相交且并集为 tasks
        executing_ids: 已分配执行中的任务，不参与本周期选择
        expired_ids: 本周期被判过期而出池的任务
        current_time: 本周期的时间戳
    """

    cycle: int
    tasks: list[Task]
    remain_ids: tuple[int, ...]
    new_ids: tuple[int, ...]
    release_ids: tuple[int, ...]
    executing_ids: tuple[int, ...]
    expired_ids: tuple[int, ...]
    current_time: float = 0.0

    @property
    def planning_ids(self) -> list[int]:
        return [t.id for t in self.tasks]

    @property
    def counts(self) -> dict[str, int]:
        """论文公式的各项规模，便于写进 metrics.json。"""
        return {
            "n_total": len(self.tasks),
            "n_remain": len(self.remain_ids),
            "n_new": len(self.new_ids),
            "n_release": len(self.release_ids),
            "n_executing": len(self.executing_ids),
            "n_expired": len(self.expired_ids),
        }


@dataclass(frozen=True)
class ApplyResult:
    """``apply_solution`` 的回执，便于滚动层做日志与指标。"""

    executing_ids: list[int]
    returned_ids: list[int]
    uav_ids: list[int]


class TaskPool:
    """动态任务池。

    典型用法（一个滚动周期）::

        pool = TaskPool()                            # 空池（或预装初始任务）
        snap = pool.begin_cycle(new_tasks=arrivals)   # 归类 remain/new/release
        sel = selection.run(snap.tasks, uavs, cfg)    # 第一阶段
        sol = muas_solve(...)                          # 第二阶段
        pool.mark_selected(sel.selected_ids)
        pool.apply_solution(sol)                      # 执行中 / 退回
        pool.complete(done_ids)                       # 反馈：完成
        pool.release(fault_ids, reason="uav_fault")   # 反馈：故障释放

    周期语义：**构造只登记任务，不产生周期**；周期一律由 ``begin_cycle()``
    推进，首次调用得到第 1 个周期，与滚动层的 ``for step in 1..N`` 对齐。
    因此在首次 ``begin_cycle()`` 之前，``planning_tasks()`` 会报错而不是
    静默返回空列表。
    """

    def __init__(
        self,
        tasks: Iterable[Task] | None = None,
        *,
        current_time: float = 0.0,
    ) -> None:
        self._tasks: dict[int, Task] = {}
        self._finished: dict[int, Task] = {}  # DONE / EXPIRED，保留供查历史
        self._source: dict[int, TaskSource] = {}
        self._executing: dict[int, int] = {}  # task_id -> uav_id
        self._planning_ids: list[int] = []  # 当前周期的 T_t
        self._new_pending: set[int] = set()  # 已进池、待下周期标为 NEW
        self._release_pending: set[int] = set()  # 已释放、待下周期标为 RELEASE
        self._cycle = 0
        self._current_time = float(current_time)

        if tasks is not None:
            # 只登记为“已到达待归类”，真正的 T_t 由 begin_cycle() 冻结
            self.add_new(list(tasks))

    # ------------------------------------------------------------------
    # 只读查询
    # ------------------------------------------------------------------

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def current_time(self) -> float:
        return self._current_time

    def planning_tasks(self) -> list[Task]:
        """当前周期的 T_t（remain ∪ new ∪ release），按加入顺序返回。

        首次 ``begin_cycle()`` 之前调用会报错（此时还没有 T_t）。
        """
        self._require_cycle()
        return [self._tasks[i] for i in self._planning_ids if i in self._tasks]

    def planning_snapshot(self) -> TaskPoolSnapshot:
        """当前周期切面的快照（不推进周期）。"""
        self._require_cycle()
        return TaskPoolSnapshot(
            cycle=self._cycle,
            tasks=self.planning_tasks(),
            remain_ids=tuple(i for i in self._planning_ids if self._source.get(i) == TaskSource.REMAIN),
            new_ids=tuple(i for i in self._planning_ids if self._source.get(i) == TaskSource.NEW),
            release_ids=tuple(i for i in self._planning_ids if self._source.get(i) == TaskSource.RELEASE),
            executing_ids=tuple(sorted(self._executing)),
            expired_ids=(),
            current_time=self._current_time,
        )

    def source_of(self, task_id: int) -> TaskSource | None:
        """任务进入当前周期的来源；不在 T_t 中则返回 None。"""
        if task_id not in self._planning_ids:
            return None
        return self._source.get(task_id)

    def executing_tasks(self) -> list[Task]:
        """已分配执行中、与 UAV 绑定、不参与本周期选择的任务。"""
        return [self._tasks[i] for i in sorted(self._executing) if i in self._tasks]

    def uav_of(self, task_id: int) -> int | None:
        """执行该任务的 UAV id；未在执行返回 None。"""
        return self._executing.get(task_id)

    def active_tasks(self) -> list[Task]:
        """池内全部活动任务（T_t ∪ 执行中）。"""
        return list(self._tasks.values())

    def get(self, task_id: int) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise KeyError(f"任务 {task_id} 不在活动任务池中") from None

    def find(self, task_id: int) -> Task | None:
        """活动或已出池任务；找不到返回 None。"""
        return self._tasks.get(task_id) or self._finished.get(task_id)

    def finished_tasks(self) -> list[Task]:
        """已出池任务（DONE / EXPIRED），按出池顺序。"""
        return list(self._finished.values())

    def stats(self) -> dict[str, int]:
        """任务池规模统计，可直接并入 metrics.json。"""
        by_status: dict[str, int] = {}
        for task in self._tasks.values():
            by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
        counts = {
            "n_cycle": self._cycle,
            "n_pool": len(self._tasks),
            "n_planning": len(self._planning_ids),
            "n_executing": len(self._executing),
            "n_finished": len(self._finished),
        }
        counts.update({f"n_status_{k}": v for k, v in sorted(by_status.items())})
        return counts

    def __len__(self) -> int:
        return len(self._tasks)

    def __contains__(self, task_id: int) -> bool:
        return task_id in self._tasks

    def __iter__(self) -> Iterator[Task]:
        return iter(self._tasks.values())

    def _require_cycle(self) -> None:
        if self._cycle == 0:
            raise RuntimeError(
                "尚未开始滚动周期：先调用 begin_cycle() 生成 T_t，再取 planning_tasks()"
            )

    # ------------------------------------------------------------------
    # 周期推进
    # ------------------------------------------------------------------

    def begin_cycle(
        self,
        new_tasks: Sequence[Task] | None = None,
        *,
        current_time: float | None = None,
    ) -> TaskPoolSnapshot:
        """推进到下一滚动周期，返回 T_t 切面。

        做四件事：

        1. 上周期 ``SELECTED`` 但未进入执行的任务回到 ``PENDING``（算 remain）
        2. 归类来源：新增 -> NEW，上周期释放 -> RELEASE，其余 -> REMAIN
        3. 按 ``latest`` 判过期，出池
        4. 冻结本周期 T_t，之后到达的任务只进池、不进本周期 T_t
        """
        if current_time is not None:
            self._current_time = float(current_time)

        if new_tasks:
            self.add_new(new_tasks)

        # 1) 上周期选了但没执行的，退回 PENDING，下周期按 remain 参与
        for task in self._tasks.values():
            if task.status == TaskStatus.SELECTED:
                task.status = TaskStatus.PENDING

        # 2) 归类来源
        for task_id in self._tasks:
            if task_id in self._new_pending:
                self._source[task_id] = TaskSource.NEW
            elif task_id in self._release_pending:
                self._source[task_id] = TaskSource.RELEASE
            else:
                self._source[task_id] = TaskSource.REMAIN
        self._new_pending.clear()
        self._release_pending.clear()

        # 3) 过期检查（执行中的任务不受影响）
        expired = self.expire(self._current_time)

        # 4) 冻结 T_t：活动且未在执行
        self._planning_ids = [t.id for t in self._tasks.values() if t.id not in self._executing]

        self._cycle += 1
        return TaskPoolSnapshot(
            cycle=self._cycle,
            tasks=self.planning_tasks(),
            remain_ids=tuple(i for i in self._planning_ids if self._source.get(i) == TaskSource.REMAIN),
            new_ids=tuple(i for i in self._planning_ids if self._source.get(i) == TaskSource.NEW),
            release_ids=tuple(i for i in self._planning_ids if self._source.get(i) == TaskSource.RELEASE),
            executing_ids=tuple(sorted(self._executing)),
            expired_ids=tuple(t.id for t in expired),
            current_time=self._current_time,
        )

    def add_new(self, tasks: Sequence[Task]) -> list[Task]:
        """登记新到达任务；进入池但要到下一次 ``begin_cycle`` 才进 T_t。"""
        added: list[Task] = []
        for task in tasks:
            if task.id in self._tasks or task.id in self._finished:
                raise ValueError(f"任务 id 重复：{task.id}")
            if task.status == TaskStatus.DONE:
                raise ValueError(f"新到达任务不能是 DONE 状态：{task.id}")
            self._tasks[task.id] = task
            self._new_pending.add(task.id)
            added.append(task)
        return added

    # ------------------------------------------------------------------
    # 状态流转
    # ------------------------------------------------------------------

    def mark_selected(self, task_ids: Iterable[int]) -> list[Task]:
        """第一阶段选中的任务置为 SELECTED。"""
        marked: list[Task] = []
        for task_id in task_ids:
            task = self.get(task_id)
            if task.status == TaskStatus.EXECUTING:
                raise ValueError(f"任务 {task_id} 正在执行，不能标记为待选")
            task.status = TaskStatus.SELECTED
            marked.append(task)
        return marked

    def apply_solution(self, solution: AllocationSolution) -> ApplyResult:
        """把 MUAS 解回写到任务池。

        - 出现在某个 tour 中的任务 -> ``EXECUTING``，并绑定到对应 UAV
        - 进入候选集 ``candidate_task_ids`` 但未被执行的 -> 回到 ``PENDING``
          （对应论文 "T_t^exec ⊆ T_t^sel"，允许 y_j = 0）
        """
        if not solution.validate_unique_tasks():
            raise ValueError("解中存在重复分配的任务，无法回写任务池")

        mapping = solution.task_to_uav()
        executing_ids: list[int] = []
        for task_id, uav_id in mapping.items():
            task = self.get(task_id)
            task.status = TaskStatus.EXECUTING
            self._executing[task_id] = uav_id
            executing_ids.append(task_id)

        returned_ids: list[int] = []
        for task_id in solution.candidate_task_ids:
            if task_id in mapping:
                continue
            task = self.get(task_id)
            if task.status == TaskStatus.SELECTED:
                task.status = TaskStatus.PENDING
            returned_ids.append(task_id)

        # 本周期没进候选的 SELECTED 任务同样退回，避免状态悬挂
        for task in self._tasks.values():
            if task.status == TaskStatus.SELECTED:
                task.status = TaskStatus.PENDING
                if task.id not in returned_ids:
                    returned_ids.append(task.id)

        return ApplyResult(
            executing_ids=executing_ids,
            returned_ids=returned_ids,
            uav_ids=sorted({u for u in mapping.values()}),
        )

    def complete(self, task_ids: Iterable[int]) -> list[Task]:
        """任务完成，移出任务池（DONE）。"""
        done: list[Task] = []
        for task_id in task_ids:
            task = self.get(task_id)
            task.status = TaskStatus.DONE
            self._executing.pop(task_id, None)
            self._source.pop(task_id, None)
            self._planning_ids = [i for i in self._planning_ids if i != task_id]
            self._new_pending.discard(task_id)
            self._release_pending.discard(task_id)
            self._tasks.pop(task_id)
            self._finished[task_id] = task
            done.append(task)
        return done

    def release(self, task_ids: Iterable[int], reason: str = "released") -> list[Task]:
        """把未完成任务释放回任务池（论文 T_t^release）。

        用于 UAV 故障导致的未完成任务、或主动撤销分配。任务回到
        ``PENDING``，解绑 UAV，并在下一次 ``begin_cycle`` 标为 ``RELEASE``。

        归类优先级：若任务自上次周期后既新到又被释放，按 ``RELEASE`` 计
        （释放是更晚发生的事件，也更需要在指标里体现）。

        返回释放的任务，便于调用方记录 ``reason`` 与写日志。
        """
        released: list[Task] = []
        for task_id in task_ids:
            task = self.get(task_id)
            self._executing.pop(task_id, None)
            task.status = TaskStatus.PENDING
            self._new_pending.discard(task_id)  # 释放是更晚事件，取代 NEW 标记
            self._release_pending.add(task_id)
            if reason:
                task.meta["release_reason"] = reason
                task.meta["release_time"] = self._current_time
            released.append(task)
        return released

    def expire(self, current_time: float | None = None) -> list[Task]:
        """超过 ``latest`` 的任务判过期并出池（EXPIRED）。

        正在执行的任务不受影响（已在飞，不因时间窗失效）。
        """
        t = self._current_time if current_time is None else float(current_time)
        if current_time is not None:
            self._current_time = t
        expired: list[Task] = []
        for task_id, task in list(self._tasks.items()):
            if task_id in self._executing:
                continue
            if task.latest is None or t <= task.latest:
                continue
            task.status = TaskStatus.EXPIRED
            self._tasks.pop(task_id)
            self._source.pop(task_id, None)
            self._planning_ids = [i for i in self._planning_ids if i != task_id]
            self._new_pending.discard(task_id)
            self._release_pending.discard(task_id)
            self._finished[task_id] = task
            expired.append(task)
        return expired

    def snapshot_json(self) -> dict[str, object]:
        """当前周期的可机读切面（用于 results/*.json）。"""
        snap = self.planning_snapshot()
        return {
            "cycle": snap.cycle,
            "current_time": snap.current_time,
            "counts": snap.counts,
            "planning_ids": snap.planning_ids,
            "source": {
                "remain": list(snap.remain_ids),
                "new": list(snap.new_ids),
                "release": list(snap.release_ids),
            },
            "executing": list(snap.executing_ids),
        }
