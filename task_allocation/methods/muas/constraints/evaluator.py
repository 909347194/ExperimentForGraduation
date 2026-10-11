# -*- coding: utf-8 -*-
"""evaluator.py — 约束违背的统一聚合（论文 §5(4) 约束处理）。

把 single / coop / nest_capacity 三类约束的违背量聚合成
:class:`ConstraintReport`，作为 ``problem.check_feasible`` 的唯一产出口径，保证：

    * ``violations``（违规标签列表）与 ``penalty``（标量惩罚）强一致——
      ``is_feasible`` 恒等价于 ``total_violation == 0``；
    * 各约束分量可独立归因（:meth:`ConstraintReport.violation_breakdown`）；
    * :meth:`ConstraintReport.as_details` 透出
      ``{violations, penalty, nest_capacity}``，供滚动层 (horizon) 与指标
      (metrics) 消费，行为完全向后兼容。

参考借鉴 Experiments-For-Dissertation 的 ``FitnessResult`` 设计
（``total_violation`` 为唯一判据 + ``violation_breakdown`` 归因），
但本模块只负责「约束违背聚合」，不重复计算目标函数（目标在 ``problem.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConstraintReport:
    """约束违背聚合结果（三类约束的统一口径）。

    各分量量纲不同（航程=米、时间=秒、时序=逆序对个数），仅用于惩罚项与
    「违背量/代价」比率，**不可按物理量单独解读**（同参考项目约定）。
    """

    range_violation: float = 0.0
    time_violation: float = 0.0
    seq_violation: float = 0.0
    window_violation: float = 0.0
    sync_violation: float = 0.0
    nest_penalty: float = 0.0
    misc_penalty: float = 0.0
    nest_capacity: dict = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    @property
    def total_violation(self) -> float:
        """全部约束违背量之和（惩罚项的唯一来源）。"""
        return (
            self.range_violation
            + self.time_violation
            + self.seq_violation
            + self.window_violation
            + self.sync_violation
            + self.nest_penalty
            + self.misc_penalty
        )

    @property
    def is_feasible(self) -> bool:
        return not self.violations

    # ---- 分量累加（写入标签 + 对应桶）----

    def add_range(self, tag: str, amount: float) -> None:
        self.violations.append(tag)
        self.range_violation += amount

    def add_time(self, tag: str, amount: float) -> None:
        self.violations.append(tag)
        self.time_violation += amount

    def add_seq(self, tag: str, amount: float) -> None:
        self.violations.append(tag)
        self.seq_violation += amount

    def add_window(self, tags: list[str], amount: float) -> None:
        self.violations.extend(tags)
        self.window_violation += amount

    def add_sync(self, tags: list[str], amount: float) -> None:
        self.violations.extend(tags)
        self.sync_violation += amount

    def add_misc(self, tag: str, amount: float) -> None:
        self.violations.append(tag)
        self.misc_penalty += amount

    def add_nest(
        self,
        tags: list[str],
        amount: float,
        *,
        capacity: dict | None = None,
    ) -> None:
        self.violations.extend(tags)
        self.nest_penalty += amount
        if capacity is not None:
            self.nest_capacity = capacity

    # ---- 输出 ----

    def violation_breakdown(self) -> dict[str, float]:
        """按约束类型分解违背量，用于定位不可行的具体原因。"""
        return {
            "range": self.range_violation,
            "time": self.time_violation,
            "seq": self.seq_violation,
            "window": self.window_violation,
            "sync": self.sync_violation,
            "nest": self.nest_penalty,
            "misc": self.misc_penalty,
        }

    def as_details(self) -> dict:
        """向后兼容的 ``check_feasible`` 产出（horizon / metrics 消费口径）。"""
        return {
            "violations": list(self.violations),
            "penalty": float(self.total_violation),
            "nest_capacity": self.nest_capacity,
        }
