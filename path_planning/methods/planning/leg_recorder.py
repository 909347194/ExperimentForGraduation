# -*- coding: utf-8 -*-
"""逐段账收集器：把"每一腿飞了多长、失败在哪一腿"攒成 ``LegCostRecord`` 序列。

职责：给 ``tour_planner`` 一个只有"追加"与"出口成表"的记账对象，使那条长流程的
      每条 return 路径都用同一处写账（漏写一条腿就是审计对不上账）。
边界：不估长度、不猜时间；没有实测值就记 ``None``。
相关：``common/leg_cost_records.py``（编码与失败分级）、
      ``methods/planning/tour_planner.py``（唯一使用者）。

``from_id`` 的口径：首段起点是上层带进来的 ``meta["origin_id"]``（UAV 起点键
``-(1+机号)``，与上层 tour_edges 同构），后续段是上一停靠点。上层没带 origin
时记 -1（"未知端点"），不拿第一个停靠点冒充。也不能拿起点机巢 id 冒充：任务与
机巢共用整数编号（巢 1/2 撞任务 1/2），回填时会被翻到错误的任务边上。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from path_planning.common.failure_codes import FailureCode
from path_planning.common.leg_cost_records import (
    as_dicts,
    failed_record,
    planned_record,
)
from path_planning.common.plan_types import LegCostRecord, PlanRequest, PlanStop, TimeSource, Trajectory

__all__ = ["LegRecorder", "origin_id_of", "time_source_of"]


def origin_id_of(request: PlanRequest) -> int:
    """首段起点 id：``meta["origin_id"]``，缺省 -1（未知，不猜）。"""
    raw = request.meta.get("origin_id") if request.meta else None
    return -1 if raw is None else int(raw)


def time_source_of(leg: Trajectory) -> str:
    """下层 ``TimeSource`` → 环的三值口径（``planned`` / ``estimate``）。"""
    return "estimate" if leg.time_source is TimeSource.ESTIMATE else "planned"


class LegRecorder:
    """一条航次的逐段账（按航段顺序追加，出口只调一次 ``as_dicts``）。"""

    def __init__(self, origin_id: int) -> None:
        self._origin = int(origin_id)
        self._records: list[LegCostRecord] = []

    # -- 端点 -----------------------------------------------------------
    def from_id(self, index: int, stops: Sequence[PlanStop]) -> int:
        """第 ``index`` 条腿的起点 id（首段 = origin，其余 = 上一停靠点）。"""
        if index == 0:
            return self._origin
        return int(stops[index - 1].id)

    # -- 写入 -----------------------------------------------------------
    def planned(self, from_id: int, to_id: int, leg: Trajectory) -> None:
        self._records.append(
            planned_record(
                from_id,
                to_id,
                length_m=float(leg.path_len_m),
                duration_s=float(leg.duration),
                time_source=time_source_of(leg),
            )
        )

    def estimated(self, from_id: int, to_id: int, leg: Trajectory) -> None:
        """定速降级剖面：有几何长度，但时间来源必须记 ``estimate``。"""
        self._records.append(
            planned_record(
                from_id,
                to_id,
                length_m=float(leg.path_len_m),
                duration_s=float(leg.duration),
                time_source="estimate",
            )
        )

    def failed(
        self, from_id: int, to_id: int, code: FailureCode | str
    ) -> None:
        """失败段：失败码 + 码表分级，长度/时长留空（没有实测就不猜）。"""
        self._records.append(failed_record(from_id, to_id, code))

    # -- 出口 -----------------------------------------------------------
    def as_dicts(self) -> list[dict[str, Any]]:
        return as_dicts(self._records)

    def records(self) -> tuple[LegCostRecord, ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterable[LegCostRecord]:
        return iter(self._records)
