# -*- coding: utf-8 -*-
"""对外契约的值对象：``PlanStop``（停留点）、``PlanRequest``（请求）、``PlanResult``（结果）。

职责：定义上层与本库之间的输入/输出结构，以及"调用方填错"这一层的构造期校验。
边界：纯数据 + 数据自洽校验。**轨迹求值、弧长积分、速度检查属算法，在
      ``methods/timeline/splines.py``；段与航迹的定义在 ``segment.py`` /
      ``trajectory.py``。**
相关：``vec3.py``、``segment.py``、``trajectory.py``、``common/vehicle_spec.py``、
      ``common/flight_budget.py``（预算）、``common/failure_codes.py``（失败码）、
      ``methods/trajectory_registry.py``（跨周期持有）。

本模块**再导出** ``Vec3`` / ``TimeSource`` / ``HermiteSegment`` / ``Trajectory``：
分层上它们各自独立成文件，但对外仍是一个契约包，调用方的
``from path_planning.common.plan_types import Trajectory`` 不必改。

失败码分层（本模块只负责第一层）：

| 层 | 谁的问题 | 表现 | 处置 |
| -- | -- | -- | -- |
| 调用方填错 | 上层构造请求时写错 | 构造期 ``ValueError``，0 秒 | 修调用方 |
| 这条航段飞不了 | 任务本身不可行 | ``PlanResult.failure`` 业务码 | 上层改任务 |
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from .failure_codes import FAILURES, FailureCode, Severity
from .flight_budget import Budget, BudgetVerdict
from .segment import HermiteSegment
from .trajectory import TimeSource, Trajectory
from .vec3 import Vec3, check_vec3
from .vehicle_spec import VehicleSpec

__all__ = [
    "Vec3",
    "TimeSource",
    "PlanStop",
    "HermiteSegment",
    "Trajectory",
    "PlanRequest",
    "PlanResult",
    "LegCostRecord",
]


@dataclass(frozen=True)
class PlanStop:
    """航次中的一个停留点（任务点或机巢）。

    Attributes:
        id:             停留点 id。任务与机巢共用整数编号（如任务 1..30、机巢 0/1/2），
                        角色只看 ``kind``，不靠 id 数值猜
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
        check_vec3(self.position, "PlanStop.position")
        if self.service_time_s < 0.0:
            raise ValueError(f"service_time_s 不能为负：{self.service_time_s}")
        if self.kind not in ("task", "nest"):
            raise ValueError(f"kind 必须是 'task' 或 'nest'：{self.kind!r}")


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
        allow_estimate_fallback: 某段优化失败时是否允许降级为定速巡航剖面交付
                         （``time_source=estimate``）。默认 False = 失败即整条不可交付；
                         只有显式声明的航次才允许降级，降级航迹不得参与冲突检测。
    """

    uav_id: int
    vehicle: VehicleSpec
    start_position: Vec3
    start_velocity: Vec3
    t0: float
    stops: tuple[PlanStop, ...]
    budget: Budget
    meta: Mapping[str, Any] = field(default_factory=dict)
    allow_estimate_fallback: bool = False

    def __post_init__(self) -> None:
        check_vec3(self.start_position, "PlanRequest.start_position")
        check_vec3(self.start_velocity, "PlanRequest.start_velocity")
        if not math.isfinite(self.t0):
            raise ValueError(f"t0 必须有限：{self.t0}")
        if not self.stops:
            raise ValueError("stops 不能为空")
        # 任务与机巢**共用整数编号**（exp01 里任务 1..30、机巢 0/1/2），
        # 所以"任务 2"与"终点机巢 2"是合法的两个点，只有同角色同 id 才是重复/漏填。
        keys = [(str(s.kind), int(s.id)) for s in self.stops]
        if len(set(keys)) != len(keys):
            raise ValueError(f"stops 的 (kind, id) 重复：{keys}（同角色同 id 才算同一点）")

    @property
    def has_task_stops(self) -> bool:
        """是否有实际任务点（只有机巢往返还航次的空航次返回 False）。"""
        return any(s.kind == "task" for s in self.stops)


@dataclass(frozen=True)
class LegCostRecord:
    """一条已规划航段的**事实**账：从哪个点到哪个点、实测多长多久、失败与否。

    这是跨包契约（编排层与上层都读它），所以字段只表达"下层看到了什么"：

    - ``length_m`` / ``duration_s``：交付航段的实测值；失败段或几何没解出来时为
      ``None``，**不得填估计值**（否则上层无法区分"实测"与"猜的"）。
    - ``time_source``：``planned`` = 时空联合优化；``estimate`` = 定速降级剖面；
      ``failed`` = 该段没有交付航迹。
    - ``failure_code`` / ``severity``：失败码与 ``FailureCode`` 码表里的分级，
      成对出现。``severity`` 由 ``common/failure_codes.py`` 唯一给出，禁止在此另设。

    上层用它做边覆盖与硬禁边（``infeasible`` 才禁边），编排层用它算残差；
    两侧都不许从 ``diagnostics["legs"]`` 的松散文档里反推这些事实。
    """

    from_id: int
    to_id: int
    length_m: float | None = None
    duration_s: float | None = None
    time_source: str = "planned"
    failure_code: str | None = None
    severity: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.from_id, int) or not isinstance(self.to_id, int):
            raise ValueError(
                f"LegCostRecord 的端点必须是整数 id：{self.from_id!r} → {self.to_id!r}"
            )
        if self.length_m is not None and self.length_m < 0.0:
            raise ValueError(f"LegCostRecord.length_m 不能为负：{self.length_m}")
        if self.duration_s is not None and self.duration_s < 0.0:
            raise ValueError(f"LegCostRecord.duration_s 不能为负：{self.duration_s}")
        if self.time_source not in ("planned", "estimate", "failed"):
            raise ValueError(f"LegCostRecord.time_source 非法：{self.time_source!r}")
        if (self.failure_code is None) != (self.severity is None):
            raise ValueError(
                f"失败码与严重性必须成对给出：code={self.failure_code!r} "
                f"severity={self.severity!r}"
            )

    @property
    def ok(self) -> bool:
        return self.failure_code is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_id": int(self.from_id),
            "to_id": int(self.to_id),
            "length_m": None if self.length_m is None else float(self.length_m),
            "duration_s": None if self.duration_s is None else float(self.duration_s),
            "time_source": str(self.time_source),
            "failure_code": None if self.failure_code is None else str(self.failure_code),
            "severity": None if self.severity is None else str(self.severity),
        }


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
    | F | T | ENDURANCE_EXCEEDED | 预算下界就判死，按定速剖面交付（estimate） |
    | F | T | TIME_EXCEEDS_BUDGET | 求解预算超限，交付估算时间 |
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

    @property
    def failure_severity(self) -> str | None:
        """失败分级字符串（``None`` = 无失败码）。契约层只透出，不做处置。"""
        sev = self.severity
        return None if sev is None else sev.value

    @property
    def leg_costs(self) -> tuple[LegCostRecord, ...]:
        """逐段事实账（``diagnostics["leg_costs"]`` 的强类型视图）。

        失败航次同样有：已解前缀各段是 ``planned``，断掉的那段是 ``failed``（带
        失败码与分级，长度可能为 ``None``）。没有这个字段的老结果返回空元组——
        调用方必须能容忍"下层没报"。
        """
        from .leg_cost_records import from_dicts  # 局部 import：避免契约层循环依赖

        raw = self.diagnostics.get("leg_costs")
        return from_dicts(raw if isinstance(raw, (list, tuple)) else None)

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
            "leg_costs": [r.as_dict() for r in self.leg_costs],
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
