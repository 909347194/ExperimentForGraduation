# -*- coding: utf-8 -*-
"""核心数据结构：航迹、航次请求、规划结果。

职责：定义跨层传递的值对象——``PlanStop``（停留点）、``HermiteSegment``
      （一段三次 Hermite 轨迹）、``Trajectory``（带时间的完整航迹）、
      ``PlanRequest``（一次规划请求）、``PlanResult``（规划结果）。
边界：纯数据 + 数据自洽校验。**轨迹求值、弧长积分、速度检查属算法，
      在 ``methods/timeline/splines.py``，不在此处。**
相关：``common/vehicle_spec.py``（机体规格）、``common/budget.py``（预算）、
      ``common/failure.py``（失败码）、``methods/trajectory_registry.py``（跨周期持有）。

为什么航迹用 Hermite 段而不是节点折线：

梯形积分下段内平均速度等于两端节点速度的均值，所以折线在节点处速度从
``v_i`` 直接跳到 ``v_{i+1}``——物理上意味着无穷大加速度。取 Δt=3 s、
a_max=3 时跳变可达 9 m/s。下游任何速度检查与冲突检测都会因此失效。
Hermite 段（端点位置 + 端点速度）保证位置连续、速度连续（C¹），
这也是冲突检测 0.365 s 弦长误差界成立的前提。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .flight_budget import Budget, BudgetVerdict
from .failure_codes import FAILURES, FailureCode, Severity
from .vehicle_spec import VehicleSpec

__all__ = [
    "Vec3",
    "TimeSource",
    "PlanStop",
    "HermiteSegment",
    "Trajectory",
    "PlanRequest",
    "PlanResult",
]

#: 三维点/向量。单位跟随上下文（位置为 m，速度为 m/s，加速度为 m/s²）。
Vec3 = tuple[float, float, float]

#: 自洽校验容差。位置用 1e-6 m、时间用 1e-6 s，足够吸收浮点累加误差。
_TOL = 1e-6


def _check_vec3(v: Any, name: str) -> None:
    if len(v) != 3:
        raise ValueError(f"{name} 必须是 3 维：{v}")
    if not all(math.isfinite(float(c)) for c in v):
        raise ValueError(f"{name} 含非有限值：{v}")


class TimeSource(str, Enum):
    """航迹时间的来源。**上层必须读这个字段再决定怎么用时间。**"""

    #: 优化器解出的真实时间（可用于冲突检测、可作 busy_until）
    TRAJECTORY = "trajectory"
    #: 巡航速度估算（降级产物，精度差、不可参与冲突检测）
    ESTIMATE = "estimate"


# ---------------------------------------------------------------------------
# 停留点
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanStop:
    """航次中的一个停留点（任务点或机巢）。

    Attributes:
        id:             任务 id（机巢用负 id 或按约定编码）
        position:       停留位置（m，绝对坐标）
        service_time_s: 在此服务/悬停的时长（s）。计入 ``t_end``，**上层不得再叠加**
                        ``default_service_time``——两边各加一次会把 30 s 算成 60 s。
        kind:           ``"task"`` 或 ``"nest"``，仅用于日志与统计
    """

    id: int
    position: Vec3
    service_time_s: float = 0.0
    kind: str = "task"

    def __post_init__(self) -> None:
        _check_vec3(self.position, "PlanStop.position")
        if self.service_time_s < 0.0:
            raise ValueError(f"service_time_s 不能为负：{self.service_time_s}")
        if self.kind not in ("task", "nest"):
            raise ValueError(f"kind 必须是 'task' 或 'nest'：{self.kind!r}")


# ---------------------------------------------------------------------------
# 轨迹段
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HermiteSegment:
    """一段三次 Hermite 轨迹。

    参数化：``p(s)``，``s ∈ [0,1]`` 映射到 ``t ∈ [t0, t1]``，

        p(s) = h00(s)·p0 + h10(s)·Δt·v0 + h01(s)·p1 + h11(s)·Δt·v1

    端点条件：``p(0)=p0``、``p(1)=p1``、``p'(0)=Δt·v0``、``p'(1)=Δt·v1``。

    Attributes:
        t0: 段起始绝对时刻
        t1: 段结束绝对时刻（``t1 > t0``）
        p0: 起点位置
        p1: 终点位置
        v0: 起点速度
        v1: 终点速度
        is_hover: 是否为悬停段（``p0==p1`` 且 ``v0==v1==0``，弧长为 0）
    """

    t0: float
    t1: float
    p0: Vec3
    p1: Vec3
    v0: Vec3
    v1: Vec3
    is_hover: bool = False

    def __post_init__(self) -> None:
        if not (math.isfinite(self.t0) and math.isfinite(self.t1)):
            raise ValueError(f"段时刻必须有限：[{self.t0}, {self.t1}]")
        if self.t1 <= self.t0:
            raise ValueError(f"段时长必须为正：[{self.t0}, {self.t1}]")
        _check_vec3(self.p0, "HermiteSegment.p0")
        _check_vec3(self.p1, "HermiteSegment.p1")
        _check_vec3(self.v0, "HermiteSegment.v0")
        _check_vec3(self.v1, "HermiteSegment.v1")
        if self.is_hover:
            if any(abs(c) > _TOL for c in self.v0) or any(abs(c) > _TOL for c in self.v1):
                raise ValueError("悬停段速度必须为 0")
            if any(abs(a - b) > _TOL for a, b in zip(self.p0, self.p1)):
                raise ValueError("悬停段起终点位置必须相同")

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


# ---------------------------------------------------------------------------
# 航迹
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 规划请求
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanRequest:
    """一次单航次规划请求（本模块的输入）。

    Attributes:
        uav_id:          机号
        vehicle:         该机机体规格（**逐机**，不是全局配置）
        start_position:  起点位置（当前实际位置，可能不在机巢）
        start_velocity:  起点速度（通常为 0；在飞中重规划时非 0）
        t0:              出发时刻。**冻结不优化**——多机让路时动它等于重排整个机队
        stops:           停留点序列（首个通常是机巢出发后的任务点，末个是终点机巢）
        budget:          本轮预算
        meta:            透传（任务 id、优先级、上层 tour 标识等）
    """

    uav_id: int
    vehicle: VehicleSpec
    start_position: Vec3
    start_velocity: Vec3
    t0: float
    stops: tuple[PlanStop, ...]
    budget: Budget
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_vec3(self.start_position, "PlanRequest.start_position")
        _check_vec3(self.start_velocity, "PlanRequest.start_velocity")
        if not math.isfinite(self.t0):
            raise ValueError(f"t0 必须有限：{self.t0}")
        if not self.stops:
            raise ValueError("stops 不能为空")

    @property
    def has_task_stops(self) -> bool:
        """是否有实际任务点（只有机巢往返还航次的空航次返回 False）。"""
        return any(s.kind == "task" for s in self.stops)


# ---------------------------------------------------------------------------
# 规划结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanResult:
    """一次规划的结果（本模块的输出）。

    三个状态维度必须分开看，不能合成一个 ``ok``：

    - ``ok``：完全没有失败码（理想结果）
    - ``deliverable``：有可交付航迹（可能带 DEGRADED 失败码，如估算时间）
    - ``failure`` / ``severity``：失败原因与分级

    典型组合：

    | ok | deliverable | failure | 含义 |
    | -- | -- | -- | -- |
    | T | T | None | 理想 |
    | F | T | TIME_EXCEEDS_BUDGET | 交付的是估算轨迹（time_source=estimate） |
    | F | F | RRT_NO_PATH | 真不可行，上层须改任务 |
    | F | F | KINEMATIC_INCONSISTENT | 内部 bug，**不是**任务不可行 |
    """

    uav_id: int
    trajectory: Trajectory | None = None
    failure: FailureCode | None = None
    detail: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failure is None

    @property
    def deliverable(self) -> bool:
        return self.trajectory is not None

    @property
    def severity(self) -> Severity | None:
        return None if self.failure is None else FAILURES[self.failure].severity

    def budget_verdict(self) -> BudgetVerdict | None:
        """从 diagnostics 里取回预算判定（若曾计算过）。"""
        raw = self.diagnostics.get("budget_verdict")
        return raw if isinstance(raw, BudgetVerdict) else None

    def as_dict(self) -> dict[str, Any]:
        """扁平化导出，供 ``metrics.json`` / 日志落盘。"""
        traj = self.trajectory
        return {
            "uav_id": self.uav_id,
            "ok": self.ok,
            "deliverable": self.deliverable,
            "failure": None if self.failure is None else self.failure.value,
            "severity": None if self.severity is None else self.severity.value,
            "detail": self.detail,
            "time_source": None if traj is None else traj.time_source.value,
            "t0": None if traj is None else traj.t0,
            "t_end": None if traj is None else traj.t_end,
            "duration_s": None if traj is None else traj.duration,
            "flight_s": None if traj is None else traj.flight_duration,
            "hover_s": None if traj is None else traj.hover_duration,
            "path_len_m": None if traj is None else traj.path_len_m,
            "diagnostics": dict(self.diagnostics),
        }

    # --- 构造便捷入口 ---------------------------------------------------

    @classmethod
    def success(
        cls, trajectory: Trajectory, diagnostics: Mapping[str, Any] | None = None
    ) -> PlanResult:
        return cls(
            uav_id=trajectory.uav_id,
            trajectory=trajectory,
            diagnostics=dict(diagnostics or {}),
        )

    @classmethod
    def failed(
        cls,
        uav_id: int,
        failure: FailureCode,
        detail: str = "",
        *,
        trajectory: Trajectory | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> PlanResult:
        return cls(
            uav_id=uav_id,
            trajectory=trajectory,
            failure=failure,
            detail=detail,
            diagnostics=dict(diagnostics or {}),
        )
