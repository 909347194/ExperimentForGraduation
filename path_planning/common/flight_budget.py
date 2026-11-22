# -*- coding: utf-8 -*-
"""航次预算：续航（能量口径）与航程（距离口径）双独立判据。

职责：承载一架机本轮剩余的能量预算与距离预算，给出占用率、绑定项判定与
      对应的失败码。
边界：只做判定与记账，不扣减、不修改任何状态（扣减由上层 ``consume_range``
      或本模块 ``api`` 显式执行）。
相关：``common/types.py``（``PlanRequest.budget``）、``common/failure.py``
      （``ENDURANCE_EXCEEDED`` / ``RANGE_EXCEEDED``）、``docs/time_mapping.md`` §5。

两条预算必须**分开存、各自报码**，不合成单一标量：补救动作不同（续航超出
要减任务/换电，航程超出要换机/中途换电）。合成一个数会让上层无法决策。

口径提醒：``endurance_remaining_s`` 是**能量**口径（电池能飞多久），
``range_remaining_m`` 是**距离**口径（还能飞多远）。二者独立、互不派生——
同样 6 km 航程的机体，可能"电池只够 300 s"（重载）也可能"能撑 450 s"（轻载），
而它们绑定的失效模式本就不同。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .failure_codes import FailureCode

__all__ = [
    "Binding",
    "Budget",
    "BudgetVerdict",
]


class Binding(str, Enum):
    """本轮实际先绑定的预算项。"""

    NONE = "none"
    ENDURANCE = "endurance"
    RANGE = "range"


@dataclass(frozen=True)
class BudgetVerdict:
    """一次预算判定的结果。

    Attributes:
        ok:          两条预算是否都未超
        binding:     占用率更高的那一项（相等时按续航优先，见 ``clamp``）
        time_ratio:  续航占用率 = T_total / endurance_remaining
        range_ratio: 航程占用率 = L_total / range_remaining
        failure:     超限时的失败码（两者都超取占用率更高者）
        headroom_s:  续航剩余余量（秒，可为负）
        headroom_m:  航程剩余余量（米，可为负）
    """

    ok: bool
    binding: Binding
    time_ratio: float
    range_ratio: float
    failure: FailureCode | None
    headroom_s: float
    headroom_m: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "binding": self.binding.value,
            "time_ratio": self.time_ratio,
            "range_ratio": self.range_ratio,
            "failure": None if self.failure is None else self.failure.value,
            "headroom_s": self.headroom_s,
            "headroom_m": self.headroom_m,
        }


@dataclass(frozen=True)
class Budget:
    """一架机本轮的预算（不可变；扣减请生成新的副本）。

    Attributes:
        endurance_remaining_s: 剩余可飞时间（s，> 0）——能量口径
        range_remaining_m:     剩余可飞距离（m，> 0）——距离口径
    """

    endurance_remaining_s: float
    range_remaining_m: float

    def __post_init__(self) -> None:
        if not (self.endurance_remaining_s > 0.0):
            raise ValueError(
                f"endurance_remaining_s 必须为正：{self.endurance_remaining_s}"
            )
        if not (self.range_remaining_m > 0.0):
            raise ValueError(f"range_remaining_m 必须为正：{self.range_remaining_m}")

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def check(
        self,
        time_total_s: float,
        path_len_m: float,
        *,
        hover_weight: float = 1.0,
        time_part_s: float | None = None,
    ) -> BudgetVerdict:
        """判定两条预算。

        Args:
            time_total_s: 航次总时长（含悬停，已按 ``hover_weight`` 加权前的原值）
            path_len_m:   航迹总弧长（**必须是重积分得到的真实弧长**，
                          不能用节点折线长——折线系统性低估 5~15%，
                          见 ``docs/time_mapping.md`` §4）
            hover_weight: 悬停加权系数 κ_h。1.0 = 日历口径（本轮无功率模型）；
                          能量口径下典型 1.5~2.0
            time_part_s:  可选，单独给出加权的飞行时长；给了就不再从
                          ``time_total_s`` 推（避免调用方重复折算）

        Returns:
            ``BudgetVerdict``。``hover_weight`` 的方向必须诚实：κ_h=1 意味着
            允许的悬停量偏多，即续航判据**偏乐观**。
        """
        if time_total_s < 0.0:
            raise ValueError(f"time_total_s 不能为负：{time_total_s}")
        if path_len_m < 0.0:
            raise ValueError(f"path_len_m 不能为负：{path_len_m}")
        if hover_weight <= 0.0:
            raise ValueError(f"hover_weight 必须为正：{hover_weight}")

        t_used = float(time_total_s) if time_part_s is None else float(time_part_s)

        time_ratio = t_used / self.endurance_remaining_s
        range_ratio = path_len_m / self.range_remaining_m

        over_time = t_used > self.endurance_remaining_s
        over_range = path_len_m > self.range_remaining_m

        if over_time and over_range:
            # 两者都超：报占用率更高者，它是真正卡住这一轮的那条
            binding = Binding.ENDURANCE if time_ratio >= range_ratio else Binding.RANGE
            failure = (
                FailureCode.ENDURANCE_EXCEEDED
                if binding is Binding.ENDURANCE
                else FailureCode.RANGE_EXCEEDED
            )
            ok = False
        elif over_time:
            binding = Binding.ENDURANCE
            failure = FailureCode.ENDURANCE_EXCEEDED
            ok = False
        elif over_range:
            binding = Binding.RANGE
            failure = FailureCode.RANGE_EXCEEDED
            ok = False
        else:
            # 都未超：binding 报"更接近超限"的那条，供上层做提前预警
            if time_ratio >= range_ratio:
                binding = Binding.ENDURANCE
            else:
                binding = Binding.RANGE
            failure = None
            ok = True

        return BudgetVerdict(
            ok=ok,
            binding=binding,
            time_ratio=time_ratio,
            range_ratio=range_ratio,
            failure=failure,
            headroom_s=self.endurance_remaining_s - t_used,
            headroom_m=self.range_remaining_m - path_len_m,
        )

    # ------------------------------------------------------------------
    # 记账
    # ------------------------------------------------------------------

    def consume(
        self, time_total_s: float, path_len_m: float
    ) -> Budget:
        """扣减后返回新预算（不修改自身）。"""
        return Budget(
            endurance_remaining_s=self.endurance_remaining_s - float(time_total_s),
            range_remaining_m=self.range_remaining_m - float(path_len_m),
        )

    @classmethod
    def from_spec(cls, max_time_s: float, max_range_m: float) -> Budget:
        """满电出巢时的初始预算。"""
        return cls(endurance_remaining_s=float(max_time_s),
                   range_remaining_m=float(max_range_m))

    def as_dict(self) -> dict[str, float]:
        return {
            "endurance_remaining_s": self.endurance_remaining_s,
            "range_remaining_m": self.range_remaining_m,
        }
