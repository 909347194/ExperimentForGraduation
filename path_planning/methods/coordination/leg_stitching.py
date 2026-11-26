# -*- coding: utf-8 -*-
"""按航段拆/拼：把一条 tour 拆成航段几何，改完一段再拼回整条。

职责：``legs_of`` 从轨迹里切出航段（含每段的 ``t0``、终点服务时长），``stitch_leg``
      把"重解后的某一段"塞回原位——前段不动、后段整体顺移。
边界：不做求解、不做净空判定；只保证时间连续、悬停与停留点不被吞掉。
相关：``local_replan.py``（用本模块的切分与拼接）、``global_recheck.py``（编排）、
      ``docs/architecture.md`` §5「协调层的结构性坑」。

**为什么必须按航段拆**（原缺陷）：旧实现把整条 tour 按 5 m 采样成一条折线交给一次
NLP，"起点 → 终点"一次解完，途中停留点与悬停段全部消失（实测 3 段/229.2 s 被压成
1 段/87.2 s）。几何虽然更短，但航次的服务语义被悄悄改掉——审计的「分段边界」正是
为此而设。按航段拆之后，只重解与冲突时间窗相关的那一段，其余段几何与悬停原样保留。

**为什么后段是"顺移"而不是"重解"**：``t0`` 冻结的口径是"出发时刻由上游定"，前段变长
只应把后续段的绝对时刻整体推后，不应改变它们的内部速度剖面；重解会让每段的
时间剖面互相牵连，等于替上层重排整条航次。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np

from path_planning.common.plan_types import HermiteSegment, Trajectory
from path_planning.methods.timeline.splines import segment_position, trajectory_arc_length

__all__ = ["LegGeometry", "LegBlock", "legs_of", "leg_blocks", "stitch_leg",
           "resolve_legs", "target_index"]

#: 采样步长（与 ``global_recheck._polyline_of`` 同口径）
_SAMPLE_STEP_M = 5.0


@dataclass(frozen=True)
class LegGeometry:
    """一个航段的初值几何与时间窗。

    Attributes:
        path:             折线初值（ENU，``(n,3)``，按 ``_SAMPLE_STEP_M`` 采样）
        t0:               该段出发时刻（绝对）
        t_end:            该段到达时刻（绝对，不含服务悬停）
        stop_id:          段终点对应的停留点 id（``None`` = 无对应停留点）
        service_after_s:  到点后的服务悬停时长
    """

    path: np.ndarray
    t0: float
    t_end: float
    stop_id: int | None = None
    service_after_s: float = 0.0


@dataclass(frozen=True)
class LegBlock:
    """一段的原始段序列 + 紧随其后的悬停段（拼回时按块处理）。"""

    flight: tuple[HermiteSegment, ...]
    hover: HermiteSegment | None = None


def leg_blocks(traj: Trajectory) -> tuple[LegBlock, ...]:
    """按悬停段切块：每块 = 若干飞行段 + 其后的悬停段（若有）。"""
    blocks: list[LegBlock] = []
    flight: list[HermiteSegment] = []
    for seg in traj.segments:
        if seg.is_hover:
            if flight:
                blocks.append(LegBlock(tuple(flight), seg))
                flight = []
            else:
                # 连续悬停（目前不会出现）：并到上一块的悬停之后，按独立块处理
                blocks.append(LegBlock((), seg))
        else:
            flight.append(seg)
    if flight:
        blocks.append(LegBlock(tuple(flight), None))
    return tuple(blocks)


def legs_of(traj: Trajectory, step_m: float = _SAMPLE_STEP_M) -> tuple[LegGeometry, ...]:
    """把轨迹切成航段几何（同一份切块，保证下标与 ``leg_blocks`` 对齐）。"""
    step = max(float(step_m), 1e-6)
    legs: list[LegGeometry] = []
    for block in leg_blocks(traj):
        if not block.flight:
            continue
        chunks: list[np.ndarray] = []
        for seg in block.flight:
            n = max(2, int(np.ceil(seg.chord_m / step)) + 1)
            chunks.append(segment_position(seg, np.linspace(0.0, 1.0, n)))
        path = np.vstack(chunks)
        legs.append(LegGeometry(
            path=path, t0=float(block.flight[0].t0), t_end=float(block.flight[-1].t1),
            service_after_s=0.0 if block.hover is None else float(block.hover.duration),
        ))
    return tuple(legs)


def stitch_leg(
    base: Trajectory, blocks: Sequence[LegBlock], index: int, new_leg: Trajectory
) -> Trajectory:
    """把第 ``index`` 段换成 ``new_leg``，后段按时间差顺移。

    Raises:
        ValueError: 段下标越界，或新段端点与旧段端点不一致（拼不成连续航次）。
    """
    if not 0 <= index < len(blocks):
        raise ValueError(f"航段下标越界：{index} / {len(blocks)}")
    old = blocks[index].flight
    if not old:
        raise ValueError(f"第 {index} 段没有飞行段，无法替换")
    if any(abs(a - b) > 1e-6 for a, b in zip(old[0].p0, new_leg.segments[0].p0)):
        raise ValueError("新段起点与旧段起点不一致：拼接会断开位置")
    if any(abs(a - b) > 1e-6 for a, b in zip(old[-1].p1, new_leg.end_position)):
        raise ValueError("新段终点与旧段终点不一致：拼接会断开位置")

    delta = float(new_leg.flight_duration) - sum(float(s.duration) for s in old)
    segs: list[HermiteSegment] = []
    for i, block in enumerate(blocks):
        if i < index:
            segs.extend(block.flight)
            if block.hover is not None:
                segs.append(block.hover)
        elif i == index:
            segs.extend(new_leg.segments)
            if block.hover is not None:
                segs.append(_shift(block.hover, delta))
        else:
            segs.extend(_shift(seg, delta) for seg in block.flight)
            if block.hover is not None:
                segs.append(_shift(block.hover, delta))

    return Trajectory(
        uav_id=base.uav_id, segments=tuple(segs),
        path_len_m=trajectory_arc_length(tuple(segs)),
        time_source=base.time_source, hover_weight=base.hover_weight,
        meta={**dict(base.meta), "legs": len(blocks), "replanned_leg": int(index)},
    )


def resolve_legs(
    legs: Sequence[LegGeometry] | None, path, t0: float
) -> tuple[LegGeometry, ...]:
    """取航段几何：显式 ``legs`` 优先，否则把 ``path`` 当单段用（合成测试口径）。"""
    if legs is not None:
        return tuple(legs)
    if path is None:
        raise ValueError("必须给 path 或 legs 之一")
    arr = np.asarray(path, dtype=np.float64)
    return (LegGeometry(path=arr, t0=float(t0), t_end=float(t0) + 1.0),)


def target_index(legs: Sequence[LegGeometry], target_time: float | None) -> int:
    """挑"该重解哪一段"：落在时间段内的那一段；不在任何段内则取时间最近的一段。"""
    if target_time is None or len(legs) == 1:
        return 0
    for i, leg in enumerate(legs):
        if leg.t0 - 1e-9 <= float(target_time) <= leg.t_end + 1e-9:
            return i
    gaps = [
        max(leg.t0 - float(target_time), 0.0, float(target_time) - leg.t_end)
        for leg in legs
    ]
    return int(np.argmin(gaps))


def _shift(seg: HermiteSegment, dt: float) -> HermiteSegment:
    """整段时间平移（时长不变 → 速度剖面不变）。"""
    if abs(dt) <= 1e-12:
        return seg
    return replace(seg, t0=seg.t0 + dt, t1=seg.t1 + dt)
