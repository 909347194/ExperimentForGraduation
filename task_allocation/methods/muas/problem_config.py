# -*- coding: utf-8 -*-
"""problem_config.py — MUAS 模型开关与评价权重配置。

职责（回答）：哪些约束开着？惩罚与各层权重取多少？

- :class:`ConstraintConfig`：约束开关与惩罚权重（实验可配置层，论文 §5(4)）；
- :class:`MUASProblemConfig`：问题/评价口径（字典序、覆盖门禁、权重、时钟）。

本模块只放配置结构，不含任何评价逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class ConstraintConfig:
    """约束开关与惩罚权重（实验可配置层，对齐论文 §5(4) 约束处理）。

    设计要点：每类约束**独立开关**。置 ``False`` 时该约束既不参与可行性
    判定、也不计入惩罚，用于消融对比与论文口径取舍（例如「本模型聚焦
    容量—航程—机巢选择，交付时间窗暂不纳入」这类声明，只需改 yaml 一个
    布尔值，不必动代码）。

    分组：

        结构类      duplicate_task / require_all_tasks / require_no_idle
        单机安全    range（航程）/ endurance（飞行时间，续航）
        协同        time_window（交付时间窗）/ sequence（时序）/ sync（同时到达）
        机巢        nest_capacity（容量）/ nest_availability（可用性）
                    end_nest_reachability（末任务 → 机巢可达性）

    权重与开关一一对应（``w_*``），仅对**已启用**的约束生效。
    """

    # ── 结构类 ──────────────────────────────────────────────────
    duplicate_task: bool = True  # 任务重复分配 / 未知任务 / sum_u x_uj = y_j
    require_all_tasks: bool = True  # allow_unassigned=False 时禁止 y_j=0
    require_no_idle: bool = True  # allow_idle_uav=False 时禁止空闲机

    # ── 单机（飞行安全类）────────────────────────────────────────
    range: bool = True  # 航程
    endurance: bool = True  # 飞行时间 / 续航

    # ── 协同 ────────────────────────────────────────────────────
    time_window: bool = True  # 交付时间窗
    sequence: bool = True  # 时序 / 前置依赖
    sync: bool = True  # 同时到达

    # ── 机巢 ────────────────────────────────────────────────────
    nest_capacity: bool = True  # 容量
    nest_availability: bool = True  # 可用性 / 存在性
    end_nest_reachability: bool = True  # 末任务 → 终点机巢可达性

    # ── 惩罚权重（仅对启用的约束生效）────────────────────────────
    w_misc: float = 1.0
    w_range: float = 1.0
    w_endurance: float = 1.0
    w_window: float = 1.0
    w_seq: float = 1.0
    w_sync: float = 1.0
    w_nest_capacity: float = 1.0
    w_nest_availability: float = 1.0
    w_end_nest_reachability: float = 1.0

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "ConstraintConfig":
        """从 yaml 映射装配；未给出的项保持默认（缺省 = 全部启用、权重 1）。"""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(d or {}).items() if k in known})


@dataclass
class MUASProblemConfig:
    """模型开关与评价权重。"""

    allow_unassigned: bool = True
    allow_idle_uav: bool = True
    # 最坏机航迹时间项权重（论文 §5(3) a·max(Σt)）：最小化瓶颈机任务时间，
    # 实现负载均衡/公平性。默认 0（仅双目标）；>0 时接入 F3=max_k T_k。
    w_time: float = 0.0
    w_penalty: float = 1.0
    # 约束惩罚直接进入适应度的权重（单位：奖励当量 / 惩罚单位）。
    # 历史口径下惩罚与路径代价混进同一个 F2 再乘 λ（λ≈1e-4 是为把「米」级
    # 路径代价折成奖励当量而设），同处 F2 的「秒」级惩罚因此被一并压没，
    # 软约束形同虚设。给定本值时惩罚**不再乘 λ**，量纲得以解耦：
    # 取 0.01~0.02 意味着「迟到 300 s ≈ 3~6 点奖励」，足以抵消单任务收益。
    # None = 沿用历史口径 λ·w_penalty（保持旧行为与既有测试不变）。
    w_violation: float | None = None
    # ── 2026-10-18 四层准字典序：硬违约（物理）的支配权重 ──────────────
    # T0 层 ``W_hard·n_hard + w_exc·excess_m``：任何一次物理违约（航程超限/
    # 续航/时序/同步/机巢容量/终点不可达/结构）压倒任意交付收益，杜绝「纸面
    # 可行、执行弹回」的幽灵 F1——旧口径 misc×1e3×wv 只折 10 单位，而单任务
    # 收益 m·R≈2.7e5，修 uav_9 前的 16 次弹回正是这类幽灵被计功。
    #   None = 自动：κ·m·R_pool（κ=hard_violation_kappa，R_pool=本周期候选
    #          任务奖励和）⇒ 可行解永远压到不可行解之前；
    #   显式值 = 固定权重（测试/消融用；0 = 关闭 T0 层）。
    w_hard_violation: float | None = None
    # T0 层内 tie-break：航程超限米数权重（米 → 奖励当量）。None = 自动 1/m：
    # 1km 超程≈1000 单位，在不可行解间制造「少超更好」的梯度，又永远翻不了
    # 记账（要抵一次违约计数需 ~4.5e5 km）。
    w_range_excess: float | None = None
    # W_hard 自动推导的安全系数：W_hard = hard_violation_kappa · m · R_pool。
    hard_violation_kappa: float = 10.0
    # 约束开关与各类惩罚权重（实验可配置）；判定见 constraints.*
    constraints: ConstraintConfig = field(default_factory=ConstraintConfig)
    # 机巢容量惩罚权重（论文 §4.3）。历史字段，保留作缺省来源：
    # 若未显式给出 constraints.w_nest_capacity，则取其值。
    w_nest_capacity: float = 1.0
    lambda_cost: float = 1.0
    # ----双目标的单标量化口径（评价规则，方案 B 主线）------------------
    # lexicographic=True（默认）：**字典序**——先最大化任务收益 F1，再在收益
    # 等价的解中最小化次目标（路径代价/约束惩罚/最坏机时间）。实现保持 DMDE
    # 单适应度框架（不碰选择机制）：f = -big_m·F1 + 次目标，big_m 使任一
    # ≥ 最小正收益的收益优势支配任意次目标差异；更细粒度的收益差视为同收益、
    # 由成本裁决（与 Ming 2017 单标量框架的唯一区别是 F1 项的支配性权重）。
    #   big_m=None → 按问题数据自动推导（1 + 次目标上界 / 最小正收益）；
    #   big_m>0 → 显式给定（测试/消融用）。
    # lexicographic=False → 历史加权和 f = -F1 + λ·F2（消融对比口径）。
    lexicographic: bool = True
    big_m: float | None = None
    default_service_time: float = 0.0
    # 迟到奖励折减容忍 W（秒）：晚到任务的有效奖励
    # ``eff = R × max(0, 1 − L/W)``（L = 迟到秒数）。迟到不是废飞
    # （2026-10-15 协议）：晚到仍算完成并计奖励，但按迟到时长线性折减，
    # 与 w_violation 的秒级惩罚同向压实「按时服务」。
    # None / <=0 = 关闭折减（历史口径：F1 全额，迟到只经 w_violation 计价）。
    # 注意：coverage_count_first=True（默认）下 F1 不进搜索主阶梯，折减只
    # 影响 F1 指标口径与零奖励剔除判据，搜索期迟到压力来自 w_late·L。
    late_reward_decay_seconds: float | None = None
    # ── 2026-10-22 覆盖门禁（coverage gate）两层适应度 ───────────────
    # True：合法解 = 物理硬约束 + 覆盖门禁（S 内无 voluntary_skip）；
    # f 在 T0 与 T1 之间加 W_skip·n_voluntary_skip 层；F1 砍迟到折减、
    # 回归纯完成计数（迟到单一定价走 w_late·L，消双计）；
    # _secondary_upper_bound 拆 1e6 虚构帽、换推导帽。
    # False（默认）= 现行四层准字典序口径，逐位不变。
    lexicographic_coverage: bool = False
    # W_skip 显式权重；None = 自动同 W_hard（κ·m·R_pool）。
    w_skip_violation: float | None = None
    # ── 2026-10-23 覆盖个数第一键、成本第二键（生产口径）──────────────
    # True（默认）：T1 层从收益和 big-M（−m·F1_eff）换成覆盖缺口**个数**
    #   W_cov·(N − n_delivered)——「本周期谁交付得多」是第一比较键；
    #   交付个数相同再比成本（w_late·L + λ·path + w_time·F3），成本项
    #   以全分辨率裁决，不再被收益构成的微小差异挤出（旧口径收益和入梯
    #   时 m·ΔR 压倒一切成本差 → 成本层形同虚设）。
    #   F1=ΣR_j 退为指标，不进搜索主阶梯。
    # False = 2026-10-18 四层准字典序（−m·F1_eff 收益和为 T1，消融对照）。
    coverage_count_first: bool = True
    # W_cov 显式权重（个数 → 适应度单位）；None = 自动 1 + 次目标上界。
    # 门槛语义：只需大于「最大可能的成本差」，保证少交付 1 个劣于任意
    # 航程/迟到差异；不是旧式 m·R≈2.7e5 的巨额完成奖励。
    w_coverage: float | None = None
    # 本周期的规划时刻：时间窗游标从它起算，而不是从 0
    # （滚动时域下任务 latest 是绝对时刻，游标起点必须跟着周期走）
    current_time: float = 0.0
