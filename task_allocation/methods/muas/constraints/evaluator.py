# -*- coding: utf-8 -*-
"""evaluator.py — 约束违背的统一聚合（论文 §5(4) 约束处理）。

把 single / coop / nest_capacity 三类约束的违背量聚合成
:class:`ConstraintReport`，作为 ``problem.check_feasible`` 的唯一产出口径，保证：

    * ``violations``（违规标签列表）与 ``penalty``（标量惩罚）强一致——
      ``total_violation`` 为全部违背量之和；
    * ``is_feasible`` 恒等价于 ``n_hard == 0``（物理可行；迟到是软违约，
      不判不可行，2026-10-18 硬/软分桶）；
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

    2026-10-18 四层适应度：违约分**硬**（物理不可行：航程/续航/时序/同步/
    机巢容量/终点不可达/结构）与**软**（时间窗迟到秒数）两桶——
      * 硬违约进适应度 T0 层，权重 W_hard=κ·m·R_pool（真 big-M）：任何一次
        物理违约压倒任意交付收益，杜绝「纸面可行、执行弹回」的幽灵 F1；
      * 软违约（迟到）进 T2 层按秒软计价：迟到可接受，只与路径同台竞争；
      * ``is_feasible`` 只认硬违约（与执行闸门 ``range_uav_*`` /
        ``tour_infeasible_*`` 弹回、``tw_task`` 不弹回的标签口径一致），
        迟到不再把解判成不可行。
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
    # 每任务迟到秒数（tw_task_* 的任务级归因值）：供 F1 奖励折减与
    # 滚动层记录「迟到交付」（2026-10-15 协议：迟到不是废飞）。
    lateness: dict = field(default_factory=dict)
    # 硬违约（物理）：标签列表 + 违约量合计 + 航程超限米数（层内 tie-break）
    hard_tags: list[str] = field(default_factory=list)
    hard_violation: float = 0.0
    range_excess: float = 0.0

    @property
    def total_violation(self) -> float:
        """全部约束违背量之和（惩罚项的历史口径来源，硬+软）。"""
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
    def soft_violation(self) -> float:
        """软违约量（仅时间窗迟到秒数；迟到可接受、按秒软计价）。"""
        return self.window_violation

    @property
    def n_hard(self) -> int:
        """硬违约条数（T0 层计数基数：每个违约标签计一次）。"""
        return len(self.hard_tags)

    @property
    def is_feasible(self) -> bool:
        """物理可行 = 零硬违约；迟到（软）不判不可行。"""
        return not self.hard_tags

    # ---- 分量累加（写入标签 + 对应桶）----
    # 除 add_window（软：迟到秒数）外，其余全部同时进硬桶（T0 层）。

    def add_range(self, tag: str, amount: float, *, excess: float | None = None) -> None:
        self.violations.append(tag)
        self.range_violation += amount
        self.hard_tags.append(tag)
        self.hard_violation += amount
        self.range_excess += float(amount if excess is None else excess)

    def add_time(self, tag: str, amount: float) -> None:
        self.violations.append(tag)
        self.time_violation += amount
        self.hard_tags.append(tag)
        self.hard_violation += amount

    def add_seq(self, tags: str | list[str], amount: float) -> None:
        """累加时序违约（进硬桶）。

        2026-10-29 修复：调用方 ``feasibility.check_solution_feasibility``
        传的是**标签列表**（与 add_window / add_sync 同风格），而旧实现按
        单个字符串 ``append`` ⇒ ``violations`` 变成嵌套列表
        ``[['seq_group_7']]``，下游（horizon 按标签前缀归因）解析不到。
        时序此前恒为 0（缺 ``Task.sequence_group``），该缺陷从未被触发。
        现兼容两种写法：字符串或列表都按标签展开。
        """
        tags = [tags] if isinstance(tags, str) else list(tags)
        self.violations.extend(tags)
        self.seq_violation += amount
        self.hard_tags.extend(tags)
        self.hard_violation += amount

    def add_window(
        self,
        tags: list[str],
        amount: float,
        *,
        lateness: dict | None = None,
    ) -> None:
        self.violations.extend(tags)
        self.window_violation += amount
        if lateness:
            self.lateness.update({int(k): float(v) for k, v in lateness.items()})

    def add_sync(self, tags: list[str], amount: float) -> None:
        self.violations.extend(tags)
        self.sync_violation += amount
        self.hard_tags.extend(tags)
        self.hard_violation += amount

    def add_misc(self, tag: str, amount: float) -> None:
        self.violations.append(tag)
        self.misc_penalty += amount
        self.hard_tags.append(tag)
        self.hard_violation += amount

    def add_nest(
        self,
        tags: list[str],
        amount: float,
        *,
        capacity: dict | None = None,
    ) -> None:
        self.violations.extend(tags)
        self.nest_penalty += amount
        self.hard_tags.extend(tags)
        self.hard_violation += amount
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
            "hard": self.hard_violation,
            "soft": self.soft_violation,
            "n_hard": float(self.n_hard),
            "range_excess": self.range_excess,
        }

    def as_details(self) -> dict:
        """向后兼容的 ``check_feasible`` 产出（horizon / metrics 消费口径）。"""
        return {
            "violations": list(self.violations),
            "penalty": float(self.total_violation),
            "nest_capacity": self.nest_capacity,
            "lateness": dict(self.lateness),
            # 2026-10-18 硬/软分解（四层适应度消费；老键保持不变）
            "hard_violation": float(self.hard_violation),
            "soft_violation": float(self.soft_violation),
            "n_hard": int(self.n_hard),
            "range_excess": float(self.range_excess),
            "feasible": bool(self.is_feasible),
        }
