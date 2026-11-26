# -*- coding: utf-8 -*-
"""``HermiteSegment``：交付轨迹的一段（三次 Hermite + 端点指令加速度记录）。

职责：定义段的值结构与其自洽校验。
边界：只存数据与校验；求值、弧长、速度/加速度复核在
      ``methods/timeline/splines.py``。
相关：``vec3.py``、``trajectory.py``（段序列）、``plan_types.py``（请求与结果）。

为什么航迹用 Hermite 段而不是节点折线：

梯形积分下段内平均速度等于两端节点速度的均值，所以折线在节点处速度从
``v_i`` 直接跳到 ``v_{i+1}``——物理上意味着无穷大加速度。取 Δt=3 s、
a_max=3 时跳变可达 9 m/s。下游任何速度检查与冲突检测都会因此失效。
Hermite 段（端点位置 + 端点速度）保证位置连续、速度连续（C¹），
这也是冲突检测 0.365 s 弦长误差界成立的前提。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .vec3 import Vec3, check_vec3

__all__ = ["HermiteSegment"]


@dataclass(frozen=True)
class HermiteSegment:
    """一段轨迹段：三次 Hermite 插值 + 可选的端点**指令加速度**记录。

    参数化：``p(s)``，``s ∈ [0,1]`` 映射到 ``t ∈ [t0, t1]``；交付曲线恒为三次：

        p(s) = h00·p0 + h10·Δt·v0 + h01·p1 + h11·Δt·v1

    ``a0/a1`` **不参与插值**——它们是优化器的决策变量（节点加速度），随段一并
    交付，供判据层直接读。为什么不用五次插值把 ``p''`` 钉成 ``a``：五次会引入
    更多节点约束、并改变交付曲线形状（实测会把段内速度顶过 ``v_max``）。三次
    曲线自身的 ``p''`` 与节点 ``a`` 有 ``O(δ/Δt²)`` 的离散差（exp01_smoke 实测
    3.016 vs 3.000），拿 ``p''`` 判加速度会得到假超限；判据改用 ``a`` 就同源了。
    判据入口见 ``splines.segment_command_acceleration``。

    Attributes:
        t0: 段起始绝对时刻
        t1: 段结束绝对时刻（``t1 > t0``）
        p0: 起点位置
        p1: 终点位置
        v0: 起点速度
        v1: 终点速度
        a0: 起点指令加速度（None = 未记录，判据退回 ``p''``）
        a1: 终点指令加速度（与 ``a0`` 同给同不给）
        is_hover: 是否为悬停段（``p0==p1`` 且 ``v0==v1==0``，弧长为 0）
    """

    t0: float
    t1: float
    p0: Vec3
    p1: Vec3
    v0: Vec3
    v1: Vec3
    a0: Vec3 | None = None
    a1: Vec3 | None = None
    is_hover: bool = False

    def __post_init__(self) -> None:
        if not (math.isfinite(self.t0) and math.isfinite(self.t1)):
            raise ValueError(f"段时刻必须有限：[{self.t0}, {self.t1}]")
        if self.t1 <= self.t0:
            raise ValueError(f"段时长必须为正：[{self.t0}, {self.t1}]")
        check_vec3(self.p0, "HermiteSegment.p0")
        check_vec3(self.p1, "HermiteSegment.p1")
        check_vec3(self.v0, "HermiteSegment.v0")
        check_vec3(self.v1, "HermiteSegment.v1")
        if (self.a0 is None) != (self.a1 is None):
            raise ValueError("端点加速度必须同给同不给：a0/a1 只有一个为 None")
        if self.a0 is not None:
            check_vec3(self.a0, "HermiteSegment.a0")
            check_vec3(self.a1, "HermiteSegment.a1")
        if self.is_hover:
            tol = 1e-6
            if any(abs(c) > tol for c in self.v0) or any(abs(c) > tol for c in self.v1):
                raise ValueError("悬停段速度必须为 0")
            if any(abs(a - b) > tol for a, b in zip(self.p0, self.p1)):
                raise ValueError("悬停段起终点位置必须相同")
            if self.a0 is not None and any(abs(c) > tol for c in self.a0 + self.a1):
                raise ValueError("悬停段加速度必须为 0")

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def chord_m(self) -> float:
        """弦长（直线距离）。**不是弧长**，恒小于等于弧长。"""
        return math.dist(self.p0, self.p1)

    def contains_time(self, t: float) -> bool:
        """``t`` 是否落在 ``[t0, t1]``（含端点，段间共享端点）。"""
        return self.t0 <= t <= self.t1
