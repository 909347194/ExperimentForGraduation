# -*- coding: utf-8 -*-
"""上层适配器：``task_allocation.methods.rolling.RollingHorizon`` → ``AllocatorPort``。

职责：推进一个滚动周期，把该周期的航次快照翻成 ``TourOrder``（起飞点、任务点、
       终点机巢、冻结出发时刻），连同 ``est_cost`` 与执行集一起交付给环。
边界：不重排、不改解、不碰 MUAS/DMDE；选择与求解全部留在上层。

**起飞点口径（v1 的硬前提）**：上层在周期末把机位挪回机巢、任务判完成，因此
本适配器要求每个航次都有 ``start_nest_id``——即"上一周期已落地、本周期从巢起飞"。
出现"在飞中改派"（起点在途）时直接报错：那种情形需要上层把指派与结算拆开、
让 ``busy_until`` 接管，不在本适配器的职责内（见 README §5）。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from task_allocation.methods.muas.edge_keys import translate_by_role
from task_allocation.methods.rolling.horizon import RollingHorizon

from ..protocol import CostUpdate, CycleSnapshot, TourOrder, TourStop


class RollingHorizonAdapter:
    """把滚动时域装成 ``AllocatorPort``。"""

    def __init__(self, horizon: RollingHorizon) -> None:
        self.horizon = horizon
        self.cycle_length = float(horizon.hcfg.cycle_length)
        if self.cycle_length <= 0.0:
            raise ValueError(f"cycle_length 必须为正：{self.cycle_length}")
        # 巢址只在构造时取一次：``horizon`` 的巢表在运行期不增删。
        self._nests_by_id = self._index_nests()

    # ------------------------------------------------------------------
    def step(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        """一拍完成（首解即提交）：保留给不启用周期内重解的调用方。"""
        record = self.horizon.run_cycle(new_tasks, advance=True)
        return self._snapshot(record)

    # ------------------------------------------------------------------
    # 周期三段式（编排层重解用）：prepare → [apply_cost_update] → resolve → commit
    # ------------------------------------------------------------------
    def prepare(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        """冻结本周期并解首版（**不写**任务池/机队）。"""
        record = self.horizon.begin_cycle(new_tasks, advance=True)
        return self._snapshot(record)

    def apply_cost_update(self, update: CostUpdate) -> CostUpdate | None:
        """回灌单入境：下层 id → 上层节点键，再交给 ``horizon`` 写成代价。

        翻译必须在这里做，不能在下层：下层的逐段账只有任务 id 与机巢 id，
        机巢在上层键空间里是 ``nest_offset + id``；不翻译就会把边记到任务
        ``id=nest_offset+n`` 上（一个根本不存在的节点）。

        角色由 ``update.nest_ends`` 给定（返航边终点＝机巢），**不靠 id 数值猜**：
        exp01 里机巢 ``0/1/2`` 与任务 ``1..30`` 撞号，按集合猜会把内部段
        ``1 → 5`` 翻成 ``机巢1 → 任务5``，写错一条根本不存在的边（静默失效）。

        Returns:
            翻译后**实际生效**的单子（仅供直接调用者核对；环不拿它记账——
            台账统一保留下层 id，见 ``protocol.CostUpdate``）。
            空单返回 ``None``。
        """
        if update is None or update.is_empty:
            return None
        # 必须一次物化成元组：``translate`` 会被 facts / infeasible 各调一次，
        # 生成器第二次就是空的，边会被静默丢光。
        task_ids = tuple(int(t.id) for t in self.horizon.pool.active_tasks())
        nest_ids = tuple(int(n.id) for n in self.horizon.nests)
        nest_offset = int(self.horizon.nest_offset)
        ends = tuple(update.nest_ends)

        def translate(edges) -> tuple[tuple[int, int], ...]:
            return translate_by_role(
                edges,
                task_ids=task_ids,
                nest_ids=nest_ids,
                nest_offset=nest_offset,
                nest_ends=ends,
            )

        facts: dict[tuple[int, int], float] = {}
        for edge, length_m in update.facts.items():
            for key in translate((edge,)):
                # 同一条边若两条航段被翻到同一个键，取较长者（与 facts 侧同口径）
                facts[key] = max(facts.get(key, 0.0), float(length_m))
        translated = CostUpdate(
            facts=facts,
            infeasible=frozenset(translate(update.infeasible)),
            source_attempt=int(update.source_attempt),
            reason=str(update.reason),
        )
        self.horizon.apply_cost_update(translated)
        return translated

    def resolve(self) -> CycleSnapshot:
        """在同一快照上重解一版（不推进时间、不写任务池/机队）。"""
        record = self.horizon.resolve_with(None)
        return self._snapshot(record, attempt=int(record.n_resolves))

    def commit(self) -> CycleSnapshot:
        """提交当前（最终）版：任务池与机队只写一次。"""
        record = self.horizon.commit()
        return self._snapshot(record)

    def cost_state(self) -> dict[str, Any]:
        """代价层状态（审计：版本、本周期不可飞边条数）。"""
        return self.horizon.cost_state()

    # ------------------------------------------------------------------
    def _snapshot(self, record: Any, *, attempt: int = 0) -> CycleSnapshot:
        t_end = float(record.current_time)
        tours = [t for t in (record.tours or []) if t.get("task_ids")]
        orders = tuple(
            self._order(tour, t0=t_end - self.cycle_length, cycle=int(record.cycle))
            for tour in tours
        )
        costs = {
            int(t["uav_id"]): float(t.get("est_cost", 0.0)) for t in tours
        }
        return CycleSnapshot(
            cycle=int(record.cycle),
            t_start=t_end - self.cycle_length,
            t_end=t_end,
            orders=orders,
            executed_ids=tuple(int(t) for t in record.executed_ids),
            est_cost=costs,
            snapshot_id=f"{int(record.cycle)}:{getattr(record, 'cost_version', 'base')}",
            cost_version=str(getattr(record, "cost_version", "base")),
            attempt=int(attempt),
        )

    # ------------------------------------------------------------------
    def reopen(self, task_ids: Sequence[int]) -> tuple[int, ...]:
        """把事后判定不可交付的任务重开回上层任务池（下一周期重投）。

        只走 ``TaskPool.reopen`` 这一个正式入口；上层算法不感知"回池"，
        它只会在下一周期看到这些任务以 ``RELEASE`` 来源回到候选集。
        """
        ids = [int(t) for t in task_ids]
        if not ids:
            return ()
        self.horizon.pool.reopen(ids)
        return tuple(sorted(ids))

    # ------------------------------------------------------------------
    def _order(self, tour: Mapping[str, Any], *, t0: float, cycle: int) -> TourOrder:
        uid = int(tour["uav_id"])
        if tour.get("start_nest_id") is None:
            raise ValueError(
                f"机 {uid} 的航次起点不在机巢（start_nest_id=None）：环 v1 只支持从巢起飞，"
                "在飞中改派需上层先接入 busy_until"
            )
        stops = [self._nest_stop(int(tour["start_nest_id"])), *(
            self._task_stop(int(tid)) for tid in tour["task_ids"]
        )]
        if tour.get("end_nest_id") is None:
            raise ValueError(f"机 {uid} 的航次没有终点机巢（end_nest_id=None）")
        stops.append(self._nest_stop(int(tour["end_nest_id"])))
        return TourOrder(
            uav_id=uid,
            t0=float(t0),
            start_position=(float(tour["start"][0]), float(tour["start"][1]),
                            float(tour["start"][2])),
            stops=tuple(stops),
            meta={"cycle": int(cycle)},
            # 失败回池靠它认身份，不从 stops 反推（机巢停留点也在里面）。
            task_ids=tuple(int(t) for t in (tour.get("task_ids") or [])),
        )

    def _index_nests(self) -> dict[int, Any]:
        """机巢 id → 巢对象。

        优先 ``horizon.nests``（公开属性）；只有极简测试假件没有该属性时，
        才退回它自带的 ``_nest_by_id``——不让"取巢址"这一件小事卡住适配器。
        """
        table = getattr(self.horizon, "nests", None)
        if table is not None:
            return {int(n.id): n for n in table}
        return {int(k): v for k, v in (getattr(self.horizon, "_nest_by_id", {}) or {}).items()}

    def _nest_stop(self, nest_id: int) -> TourStop:
        nest = self._nests_by_id.get(int(nest_id))
        if nest is None:
            raise ValueError(f"未知机巢 {nest_id}：上层的解引用了不存在的巢")
        return TourStop(id=int(nest_id), kind="nest", position=(nest.x, nest.y, nest.z))

    def _task_stop(self, task_id: int) -> TourStop:
        task = self.horizon.pool.find(int(task_id))
        if task is None:
            raise ValueError(f"任务 {task_id} 已不在池中：无法为已出池任务排航次")
        service = float((task.meta or {}).get("service_time_s", 0.0))
        return TourStop(
            id=int(task_id), kind="task", position=(task.x, task.y, task.z),
            service_time_s=service,
        )
