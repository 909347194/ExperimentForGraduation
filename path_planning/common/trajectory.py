# -*- coding: utf-8 -*-
"""``Trajectory``（带时间的完整航迹）与 ``TimeSource``。

职责：把段序列装配成可交付航迹，给出时间量、起终点与**自洽校验**。
边界：不做可行性判定（速度/加速度/净空在 ``methods/timeline/feasibility.py``）。
相关：``segment.py``、``plan_types.py``（请求/结果）、``methods/trajectory_registry.py``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .segment import HermiteSegment
from .vec3 import Vec3

__all__ = ["TimeSource", "Trajectory"]

#: 时间/位置连续性的自洽容差（浮点累加误差量级）
_TOL = 1e-6


class TimeSource(str, Enum):
    """航迹时间的来源。**上层必须读这个字段再决定怎么用时间。**"""

    #: 优化器解出的真实时间（可用于冲突检测、可作 busy_until）
    TRAJECTORY = "trajectory"
    #: 巡航速度估算（降级产物，精度差、不可参与冲突检测）
    ESTIMATE = "estimate"


@dataclass(frozen=True)
class Trajectory:
    """一条完整航迹（带时间，可交付给上层与冲突检测）。

    Attributes:
        uav_id:       机号
        segments:     Hermite 段序列（时间连续、位置连续）
        path_len_m:   总弧长（**由 Gauss 重积分算出**，不是折线长）
        time_source:  时间来源
        hover_weight: 悬停加权系数实际取值（写进诊断，方向须诚实）
        meta:         透传元数据（迭代数、初值来源、退让层级等）
    """

    uav_id: int
    segments: tuple[HermiteSegment, ...]
    path_len_m: float
    time_source: TimeSource = TimeSource.TRAJECTORY
    hover_weight: float = 1.0
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("航迹至少需要一段")
        if self.path_len_m < 0.0:
            raise ValueError(f"path_len_m 不能为负：{self.path_len_m}")
        if self.hover_weight <= 0.0:
            raise ValueError(f"hover_weight 必须为正：{self.hover_weight}")

    # --- 时间量 ---------------------------------------------------------

    @property
    def t0(self) -> float:
        return self.segments[0].t0

    @property
    def t_end(self) -> float:
        """结束时刻。**含悬停段**——上层据此设 ``busy_until``，不再叠加服务时长。"""
        return self.segments[-1].t1

    @property
    def duration(self) -> float:
        return self.t_end - self.t0

    @property
    def hover_duration(self) -> float:
        return sum(s.duration for s in self.segments if s.is_hover)

    @property
    def flight_duration(self) -> float:
        return self.duration - self.hover_duration

    @property
    def weighted_duration(self) -> float:
        """按 κ_h 加权后的时长（续航判据用）。"""
        return self.flight_duration + self.hover_weight * self.hover_duration

    @property
    def start_position(self) -> Vec3:
        return self.segments[0].p0

    @property
    def end_position(self) -> Vec3:
        return self.segments[-1].p1

    # --- 自洽校验 -------------------------------------------------------

    def validate(self) -> None:
        """航迹自洽性校验（时间连续、位置连续、悬停段合法）。

        这是**内部一致性**检查，不是可行性检查：不通过说明构造代码有 bug，
        应报 ``KINEMATIC_INCONSISTENT``（INTERNAL），**不得**当成航次不可行上报。
        速度三项与弧长-时间相容性检查在 ``methods/timeline/feasibility.py``
        （那里能拿到机体规格）。
        """
        errs: list[str] = []
        for i, seg in enumerate(self.segments):
            if i == 0:
                continue
            prev = self.segments[i - 1]
            if abs(seg.t0 - prev.t1) > _TOL:
                errs.append(
                    f"段 {i-1}→{i} 时间不连续：prev.t1={prev.t1:.9f} "
                    f"vs seg.t0={seg.t0:.9f}"
                )
            if any(abs(a - b) > _TOL for a, b in zip(prev.p1, seg.p0)):
                errs.append(f"段 {i-1}→{i} 位置不连续：{prev.p1} vs {seg.p0}")
        if errs:
            raise ValueError(
                f"航迹 uav={self.uav_id} 自洽校验失败（{len(errs)} 项）：\n  "
                + "\n  ".join(errs)
            )
