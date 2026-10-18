# -*- coding: utf-8 -*-
"""problem.py — 选择性多机巢 MUAS 模型（论文 3.3）。

职责（回答）：
    任务选择、无人机分配、访问顺序、终点机巢之间是什么关系？目标是什么？

决策变量：
    y_j     任务 j 本周期是否执行
    x_uj    任务 j 是否分配给 UAV u
    π_u     UAV u 的访问顺序
    z_ub    UAV u 的终点机巢

关系：
    sum_u x_uj = y_j
    π_u / z_ub 编码在 UAVTour 中
    允许 y_j=0（选择性）、n_u=0（空闲 UAV）

目标：
    max F1 = sum_j R_j y_j
    min F2 = C(x, π, z, y)   （路径代价 + 惩罚）
    min F3 = max_k T_k        （最坏机航迹时间，论文 §5(3) 负载均衡/公平性项）

适应度（DMDE 最小化标量）：
    四层准字典序（2026-10-18 起默认，方案 A/B 主线）：
        f(s) = W_hard·n_hard + w_exc·excess_m        # T0 物理违约（硬，真 big-M）
              − big_m·F1_eff                          # T1 交付收益（含迟到折减）
              + w_late·L_tot                          # T2 迟到（软，秒级）
              + λ·path + w_time·F3                    # T3 航程/负载均衡
      T0：W_hard = κ·big_m·R_pool —— 任何一次物理违约压倒任意交付收益
          （幽灵 F1 永不入选：纸面可行、执行弹回的航次不计功）；
      T1：big_m 主导 —— 任一 ≥ 最小正收益的收益优势支配任意次级差异
          （先最大化任务收益，再在收益等价的解中最小化成本）；
      T2：迟到秒数软计价 —— 迟到可接受，只与路径同台竞争；
    legacy（lexicographic=False，消融对比）：f(s) = -F1 + λ·path + w_v·penalty
    ；最老口径（w_violation=None）：惩罚随 F2 一起乘 λ。
其中 F3 = max_k T_k 为各 UAV 航迹时间的最大值（瓶颈机），对应 Ming et al. 2017
Eq.1 中的 a·max(Σ_i Σ_j t(i,j)·x(i,j)) 最坏机飞行时间项。w_time=0 时退化为
原双目标形式，行为与重构前完全一致。

本模块不实现 DMDE 搜索；求解器只负责搜索，本模块负责问题定义与解评价。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from task_allocation.common.types import (
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVTour,
)
from task_allocation.methods.muas.constraints.nest_capacity import check_nest_capacity
from task_allocation.methods.muas.constraints.single_constraints import (
    check_range_constraint,
    check_time_constraint,
)
from task_allocation.methods.muas.constraints.coop_constraints import (
    check_sequence_constraint,
    check_sync_constraint,
    check_time_window_constraint,
)
from task_allocation.methods.muas.constraints.evaluator import ConstraintReport


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
    late_reward_decay_seconds: float | None = None
    # 本周期的规划时刻：时间窗游标从它起算，而不是从 0
    # （滚动时域下任务 latest 是绝对时刻，游标起点必须跟着周期走）
    current_time: float = 0.0


def _euclid(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    return float(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
    )


@dataclass
class CostProvider:
    """节点间代价：pairwise 优先，否则欧氏；机巢键 = nest_offset + nest_id。

    另承载 **统一距离口径** ``distance_fn``（:func:`make_distance_fn` 生成的
    DEM 三维航程）。凡要算「飞这一段的物理长度/时间」的地方（航迹时间 F3、
    时间窗 ETA）一律走 :meth:`travel`，不得再直接 ``_euclid`` —— 后者是直线，
    会系统性低估飞行时间（docs/full_review_20261012.md P1-1）。
    """

    pairwise: dict[tuple[int, int], float] = field(default_factory=dict)
    positions: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    nest_offset: int = 1_000_000
    # 统一距离口径：``f(xyz_a, xyz_b) -> 米``；None → 回退欧氏（无 DEM 场景）
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None

    def nest_key(self, nest_id: int) -> int:
        return self.nest_offset + nest_id

    def cost(self, id_a: int, id_b: int) -> float:
        if (id_a, id_b) in self.pairwise:
            return float(self.pairwise[(id_a, id_b)])
        if (id_b, id_a) in self.pairwise:
            return float(self.pairwise[(id_b, id_a)])
        pa = self.positions.get(id_a)
        pb = self.positions.get(id_b)
        if pa is not None and pb is not None:
            return _euclid(pa, pb)
        return 1e6

    def travel(self, a: Sequence[float], b: Sequence[float]) -> float:
        """两点间的**物理航程**（米），统一口径：DEM 三维航程优先。

        与 :meth:`cost` 的区别：``cost`` 查的是节点 id → id 的**代价**（可含
        收益加权等模型化口径），``travel`` 是纯粹的空间长度，用于把距离换算成
        飞行时间（ETA / F3）。两者同源（都由同一个 DEM 估算器派生）。
        """
        if self.distance_fn is not None:
            try:
                return float(self.distance_fn(a, b))
            except Exception:  # 防御：估算器异常不得让求解崩，退回直线
                pass
        return _euclid(tuple(a[:3]), tuple(b[:3]))


def build_cost_provider(
    tasks: Sequence[Task],
    nests: Sequence[Nest],
    uavs: Sequence[UAV],
    pairwise: Mapping[tuple[int, int], float] | None = None,
    nest_offset: int = 1_000_000,
    distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
) -> CostProvider:
    positions: dict[int, tuple[float, float, float]] = {}
    for t in tasks:
        positions[t.id] = t.position
    for n in nests:
        positions[nest_offset + n.id] = n.position
    for u in uavs:
        positions[-(1 + u.id)] = u.position
    return CostProvider(
        pairwise=dict(pairwise or {}),
        positions=positions,
        nest_offset=nest_offset,
        distance_fn=distance_fn,
    )


@dataclass
class SelectiveMUASProblem:
    """选择性多机巢 MUAS 问题实例（单滚动周期）。"""

    tasks: list[Task]
    uavs: list[UAV]
    nests: list[Nest]
    costs: CostProvider
    # 全部无人机（含 BUSY / FAULT），仅用于推导机巢占用；
    # 容量不落第二份状态，一律从 uav.nest_id 现算（design_nest_state.md §0）
    fleet_uavs: list[UAV] | None = None
    config: MUASProblemConfig = field(default_factory=MUASProblemConfig)
    task_by_id: dict[int, Task] = field(init=False, repr=False)
    uav_by_id: dict[int, UAV] = field(init=False, repr=False)
    nest_by_id: dict[int, Nest] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.task_by_id = {t.id: t for t in self.tasks}
        self.uav_by_id = {u.id: u for u in self.uavs}
        self.nest_by_id = {n.id: n for n in self.nests}

    @property
    def n_tasks(self) -> int:
        return len(self.tasks)

    @property
    def n_uavs(self) -> int:
        return len(self.uavs)

    @property
    def n_nests(self) -> int:
        return len(self.nests)

    @property
    def model_mode(self) -> str:
        n, m = self.n_uavs, self.n_tasks
        if m == 0:
            return "empty"
        if n == m and not self.config.allow_unassigned:
            return "balanced"
        if n > m:
            return "overloaded"
        return "selective_srp"

    def relations_from_solution(
        self, solution: AllocationSolution
    ) -> dict[str, Any]:
        """解码 y, x, π, z，并检查 sum_u x_uj == y_j。"""
        y = {tid: 0 for tid in self.task_by_id}
        for tid in solution.executed_task_ids:
            if tid in y:
                y[tid] = 1
        x: dict[tuple[int, int], int] = {}
        pi: dict[int, list[int]] = {}
        z: dict[int, int | None] = {}
        for tour in solution.tours:
            pi[tour.uav_id] = list(tour.task_ids)
            z[tour.uav_id] = tour.end_nest_id
            for tid in tour.task_ids:
                x[(tour.uav_id, tid)] = 1
        relation_ok = True
        for tid, yj in y.items():
            s = sum(x.get((u.id, tid), 0) for u in self.uavs)
            if s != yj:
                relation_ok = False
                break
        return {
            "y": y,
            "x": x,
            "pi": pi,
            "z": z,
            "sum_x_eq_y": relation_ok,
            "unique_tasks": solution.validate_unique_tasks(),
        }

    def empty_solution(self) -> AllocationSolution:
        tours = [
            UAVTour.from_uav(u, task_ids=[], end_nest_id=u.nest_id) for u in self.uavs
        ]
        return AllocationSolution(
            tours=tours,
            candidate_task_ids=[t.id for t in self.tasks],
            executed_task_ids=[],
            f1_reward=0.0,
            f2_cost=0.0,
            feasible=True,
        )

    def solution_from_assignment(
        self,
        assignment: Sequence[tuple[int, int]],
        sequences: Mapping[int, Sequence[int]] | None = None,
        end_nests: Mapping[int, int | None] | None = None,
    ) -> AllocationSolution:
        seq_map: dict[int, list[int]] = {u.id: [] for u in self.uavs}
        if sequences:
            for uid, seq in sequences.items():
                seq_map[int(uid)] = [int(t) for t in seq]
        else:
            for uid, tid in assignment:
                if uid in seq_map and tid in self.task_by_id:
                    seq_map[uid].append(int(tid))
        end_nests = dict(end_nests or {})
        tours: list[UAVTour] = []
        executed: list[int] = []
        for u in self.uavs:
            tids = seq_map.get(u.id, [])
            seen: set[int] = set()
            ordered: list[int] = []
            for tid in tids:
                if tid not in seen and tid in self.task_by_id:
                    seen.add(tid)
                    ordered.append(tid)
            executed.extend(ordered)
            tours.append(
                UAVTour.from_uav(
                    u, task_ids=ordered, end_nest_id=end_nests.get(u.id, u.nest_id)
                )
            )
        sol = AllocationSolution(
            tours=tours,
            candidate_task_ids=[t.id for t in self.tasks],
            executed_task_ids=list(dict.fromkeys(executed)),
        )
        self.evaluate(sol)
        return sol

    def f1_reward(self, solution: AllocationSolution) -> float:
        # 有效奖励 = Σ R_j × 折减系数；折减系数按每任务迟到秒数线性衰减
        # （late_reward_decay_seconds=W 时 max(0, 1 − L/W)）。迟到的量取自
        # check_feasible 透出的 lateness 表（时间窗游标的唯一模拟源）。
        total = 0.0
        decay = self.config.late_reward_decay_seconds
        feas = solution.meta.get("feasibility") or {}
        lateness = feas.get("lateness") or {}
        for tid in solution.executed_task_ids:
            t = self.task_by_id.get(tid)
            if t is None:
                continue
            r = max(t.reward, 0.0)
            if decay is not None and float(decay) > 0:
                late = float(lateness.get(int(tid), 0.0) or 0.0)
                if late > 0.0:
                    r *= max(0.0, 1.0 - late / float(decay))
            total += r
        return float(total)

    def tour_path_cost(self, tour: UAVTour) -> float:
        if not tour.task_ids and tour.end_nest_id is None:
            return 0.0
        cost = 0.0
        prev = -(1 + tour.uav_id)
        for tid in tour.task_ids:
            cost += self.costs.cost(prev, tid)
            prev = tid
        if tour.end_nest_id is not None:
            cost += self.costs.cost(prev, self.costs.nest_key(tour.end_nest_id))
        elif not tour.task_ids:
            return 0.0
        return float(cost)

    def path_cost(self, solution: AllocationSolution) -> float:
        """纯路径代价（不含约束惩罚），供适应度做量纲解耦。"""
        return float(sum(self.tour_path_cost(t) for t in solution.tours))

    def f2_cost(self, solution: AllocationSolution, penalty: float = 0.0) -> float:
        path = sum(self.tour_path_cost(t) for t in solution.tours)
        return float(path + self.config.w_penalty * penalty)

    def violation_penalty(self, solution: AllocationSolution) -> float:
        """解当前的约束惩罚**总量**（硬+软；legacy 加权和口径消费）。

        优先取 :meth:`evaluate` 实际计入 ``f2_cost`` 的惩罚（``meta["penalty"]``），
        否则回落到可行性报告里的惩罚值。四层适应度不消费本方法——
        硬违约走 :meth:`hard_stats`（T0），软违约走 :meth:`soft_violation`（T2）。
        """
        meta = solution.meta or {}
        if "penalty" in meta:
            return float(meta["penalty"])
        feas = meta.get("feasibility") or {}
        return float(feas.get("penalty", 0.0))

    def tour_time(self, tour: UAVTour) -> float:
        """单架 UAV 航迹时间（含飞行 + 等待 + 服务 + 返巢），用于最坏机瓶颈项 F3。

        逐航次模拟到达游标（与 ``coop_constraints.check_time_window_constraint``
        的游标语义一致，但不对迟到计惩罚）：从 UAV 起点出发，按访问顺序累加段飞行
        时间（= 段长 / speed），到点若早于 ``earliest`` 则等待至 ``earliest``，
        离开时累加上 ``default_service_time``，末段返巢到终点机巢。
        返回相对 ``current_time`` 的航迹时长（秒）。

        段长口径：**统一走 DEM 三维航程**（``costs.travel``），不用欧氏直线——
        此前 F3 用直线而代价矩阵用地形代价，同一航次两套长度，F3 与 ETA 系统性
        低估（P1-1）。

        对应 Ming et al. 2017 Eq.1 的 Σ_i Σ_j t(i,j)·x(i,j) 单 UAV 飞行时间；
        F3 = max_k T_k 即其中的 a·max(·) 最坏机项。
        """
        if not tour.task_ids and tour.end_nest_id is None:
            return 0.0
        u = self.uav_by_id.get(tour.uav_id)
        if u is None:
            return 0.0
        speed = max(float(getattr(u, "speed", 0.0) or 0.0), 1e-6)
        t_cursor = float(self.config.current_time)
        prev = tour.start_position
        for tid in tour.task_ids:
            task = self.task_by_id.get(tid)
            if task is None:
                continue
            dist = self.costs.travel(prev, task.position)
            t_cursor += dist / speed
            if task.earliest is not None and t_cursor < task.earliest:
                t_cursor = task.earliest
            t_cursor += float(self.config.default_service_time)
            prev = task.position
        if tour.end_nest_id is not None:
            nest = self.nest_by_id.get(tour.end_nest_id)
            if nest is not None:
                dist = self.costs.travel(prev, nest.position)
                t_cursor += dist / speed
        return float(t_cursor - self.config.current_time)

    def bottleneck_time(self, solution: AllocationSolution) -> float:
        """最坏机航迹时间 F3 = max_k T_k（论文 §5(3) 负载均衡项）。

        空解（无航次）返回 0；若存在非空航次则取各航次 ``tour_time`` 的最大值。
        最小化该项使任务在 UAV 间更均衡，避免单架机成为执行瓶颈。
        """
        if not solution.tours:
            return 0.0
        return max(self.tour_time(t) for t in solution.tours)

    def evaluate(
        self,
        solution: AllocationSolution,
        penalty: float | None = None,
    ) -> AllocationSolution:
        ok, details = self.check_feasible(solution)
        pen = float(details.get("penalty", 0.0)) if penalty is None else float(penalty)
        solution.feasible = ok
        solution.meta = dict(solution.meta)
        solution.meta["feasibility"] = details
        solution.f1_reward = self.f1_reward(solution)
        solution.f2_cost = self.f2_cost(solution, penalty=pen)
        # 实际计入 F2 的惩罚量（供 w_violation 解耦口径读取，避免重复解析报告）
        solution.meta["penalty"] = pen
        solution.meta["bottleneck_time"] = self.bottleneck_time(solution)
        # 2026-10-18：本次评价实际使用的四层权重快照。记录/重算/绘图共用
        # 同一组权重，消灭 auto big_m 逐周期漂移导致的「记录 best_fitness
        # 与重算目标差 6.5~13.6」。
        decay = self.config.late_reward_decay_seconds
        solution.meta["fitness_weights"] = {
            "m": float(self.f1_weight()),
            "w_hard": float(self.hard_weight()),
            "w_excess": float(self.range_excess_weight()),
            "w_late": (None if self.config.w_violation is None
                       else float(self.config.w_violation)),
            "lambda": float(self.config.lambda_cost),
            "w_time": float(self.config.w_time),
            "W_decay": (None if not decay else float(decay)),
        }
        for tour in solution.tours:
            tour.est_cost = self.tour_path_cost(tour)
            tour.est_reward = sum(
                self.task_by_id[tid].reward
                for tid in tour.task_ids
                if tid in self.task_by_id
            )
        return solution

    def scalar_fitness(self, solution: AllocationSolution) -> float:
        """DMDE 最小化的标量适应度。

        四层准字典序（2026-10-18，``lexicographic=True`` 且 ``w_violation``
        给定时的生产口径）::

            f = W_hard·n_hard + w_exc·excess_m       # T0 物理违约（硬）
              − m·F1_eff                              # T1 交付收益
              + w_late·L_tot                          # T2 迟到（软，秒级）
              + λ·path + w_time·F3                    # T3 航程/负载均衡

        - **T0**：``W_hard = κ·m·R_pool``（默认自动推导）使任何一次物理违约
          压倒任意交付收益——幽灵 F1（纸面可行、执行弹回）永不入选；
          ``excess_m``（超限米数）仅在不可行解之间做层内排序。
        - **T1**：``F1_eff`` 含迟到折减（``late_reward_decay_seconds``）；
          ``m`` 保证「多交付 ≥1 个任务」支配一切次级差异。
        - **T2**：迟到秒数软计价（``w_violation``，0.01/s）——迟到可接受，
          只与路径同台竞争；迟到与任务数的权衡由 T1 的折减管。
        - **T3**：``λ·path + w_time·F3``（收益等价时的最小化目标）。

        legacy 加权和（``lexicographic=False``）：``f = -F1 + λ·path +
        w_violation·penalty + w_time·F3``（惩罚不再乘 λ 的解耦口径）。
        最老口径（``w_violation=None``）：惩罚随 F2 一起乘 λ。
        """
        if not solution.meta.get("feasibility"):
            self.evaluate(solution)
        bt = solution.meta.get("bottleneck_time", self.bottleneck_time(solution))
        m = self.f1_weight()
        f1 = float(solution.f1_reward)
        wv = self.config.w_violation
        if wv is None:
            # 历史口径：惩罚随 F2 一起乘 λ（量纲未解耦，秒级惩罚被压缩）
            return float(
                -m * f1
                + self.config.lambda_cost * solution.f2_cost
                + self.config.w_time * bt
            )
        if not self.config.lexicographic:
            # legacy 加权和 + 惩罚量纲解耦（消融对比口径，不含 T0 支配层）
            return float(
                -m * f1
                + self.config.lambda_cost * self.path_cost(solution)
                + float(wv) * self.violation_penalty(solution)
                + self.config.w_time * bt
            )
        # 四层准字典序（生产口径）
        n_hard, excess_m = self.hard_stats(solution)
        return float(
            self.hard_weight() * n_hard
            + self.range_excess_weight() * excess_m
            - m * f1
            + float(wv) * self.soft_violation(solution)
            + self.config.lambda_cost * self.path_cost(solution)
            + self.config.w_time * bt
        )

    # ---- T0 硬违约权重与分解（2026-10-18 四层适应度）-------------------

    def hard_weight(self) -> float:
        """T0 硬违约的支配权重 ``W_hard = κ·m·R_pool``。

        保证任一物理违约的代价超过本周期全部候选任务的收益和——可行解
        永远压到不可行解之前，与交付了多少任务无关。legacy 加权和
        （``lexicographic=False``）不设支配层，返回 1.0。
        """
        if not self.config.lexicographic:
            return 1.0
        if self.config.w_hard_violation is not None:
            return max(0.0, float(self.config.w_hard_violation))
        cached = getattr(self, "_hard_weight_cache", None)
        if cached is None:
            m = self.f1_weight()
            r_pool = sum(max(float(t.reward), 0.0) for t in self.tasks)
            cached = float(self.config.hard_violation_kappa) * m * r_pool
            self._hard_weight_cache = cached
        return float(cached)

    def range_excess_weight(self) -> float:
        """T0 层内 tie-break 权重（米 → 奖励当量）；None = 自动 ``1/m``。"""
        if self.config.w_range_excess is not None:
            return max(0.0, float(self.config.w_range_excess))
        return 1.0 / max(self.f1_weight(), 1.0)

    def hard_stats(self, solution: AllocationSolution) -> tuple[int, float]:
        """``(硬违约条数, 航程超限米数)``（T0 层两个计数基）。"""
        det = solution.meta.get("feasibility") or {}
        if "n_hard" not in det:
            _, det = self.check_feasible(solution)
        return int(det.get("n_hard", 0)), float(det.get("range_excess", 0.0))

    def soft_violation(self, solution: AllocationSolution) -> float:
        """软违约量 = 时间窗迟到秒数合计（T2 层；迟到不是不可行）。"""
        det = solution.meta.get("feasibility") or {}
        if "soft_violation" not in det:
            _, det = self.check_feasible(solution)
        return float(det.get("soft_violation", 0.0))

    # ---- 字典序权重（big-M）------------------------------------------

    def f1_weight(self) -> float:
        """F1 的支配性权重 big_m（lexicographic=False 时为 1.0 = 历史加权和）。"""
        if not self.config.lexicographic:
            return 1.0
        if self.config.big_m is not None:
            return max(1.0, float(self.config.big_m))
        cached = getattr(self, "_f1_weight_cache", None)
        if cached is None:
            cached = self._auto_big_m()
            self._f1_weight_cache = cached
        return float(cached)

    def _auto_big_m(self) -> float:
        """按问题数据推导字典序权重：``1 + 次目标上界 / 最小正收益``。

        保证：任一 ≥ 最小正收益 r_min 的收益优势（即多完成 ≥1 个正收益任务）
        支配任意次目标差异；更细粒度的收益差视为同收益、由成本裁决。
        """
        pos = [float(t.reward) for t in self.tasks if float(t.reward) > 0]
        r_min = min(pos) if pos else 1.0
        return 1.0 + self._secondary_upper_bound() / r_min

    def _secondary_upper_bound(self) -> float:
        """任一解次目标值的保守上界（λ·路径 + 软惩罚 + w_time·最坏机时间）。

        2026-10-18 四层适应度：硬违约已升入 T0 支配层（``W_hard = κ·m·R_pool``，
        与 m 的推导解耦），不再进次目标上界；此处只包软违约（时间窗迟到
        秒数）——按每任务 1e6 s 的保守帽估计，保证 m·r_min 覆盖任何可实现的
        迟到总量差异。
        """
        cfg = self.config
        n_t, n_u, n_b = len(self.tasks), len(self.uavs), len(self.nests)
        # 航次腿数上界：每任务一条链边 + 每航次进/出边
        legs = n_t + 2 * n_u + 2
        c_bound = self._max_edge_cost() * legs
        speed_min = min(
            (max(float(getattr(u, "speed", 0.0) or 0.0), 1e-6) for u in self.uavs),
            default=1.0,
        )
        # 软违约（迟到秒数）保守上界：每任务 1e6 s（≈11.6 天，无实际意义，
        # 只为「一个任务的收益优势压倒任意次级差异」提供不破的保证）。
        v_bound = 1.0e6 * (n_t + 8)
        lam = float(cfg.lambda_cost)
        total = lam * c_bound
        if cfg.w_violation is None:
            total += lam * float(cfg.w_penalty) * v_bound
        else:
            total += float(cfg.w_violation) * v_bound
        total += float(cfg.w_time) * (c_bound / speed_min)
        return float(total)

    def _max_edge_cost(self) -> float:
        """有限边代价上界：pairwise 与实体包围盒对角线（欧氏兜底边）取大。"""
        vals = [
            abs(float(c))
            for c in getattr(self.costs, "pairwise", {}).values()
            if abs(float(c)) < 1e18
        ]
        c_max = max(vals, default=0.0)
        pts = [p.position for p in self.tasks] + [p.position for p in self.uavs]
        pts += [p.position for p in self.nests]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            zs = [p[2] for p in pts]
            diag = (
                (max(xs) - min(xs)) ** 2
                + (max(ys) - min(ys)) ** 2
                + (max(zs) - min(zs)) ** 2
            ) ** 0.5
            c_max = max(c_max, diag)
        return max(c_max, 1.0)

    def check_feasible(
        self, solution: AllocationSolution
    ) -> tuple[bool, dict[str, Any]]:
        """校验解的可行性并累加约束惩罚（论文 §5(4) 约束处理）。

        实现已迁至 ``constraints`` 包：单机约束 → ``single_constraints``、
        协同约束 → ``coop_constraints``、机巢容量 → ``nest_capacity``，最终统一
        汇入 ``evaluator.ConstraintReport``。:meth:`ConstraintReport.as_details`
        透出的 ``violations`` / ``penalty`` / ``nest_capacity`` 与历史口径完全一致
        （供滚动层 horizon 与指标 metrics 消费）。
        """
        cons = self.config.constraints
        report = ConstraintReport()

        # ---- 结构类（misc）----
        if cons.duplicate_task:
            if not solution.validate_unique_tasks():
                report.add_misc("duplicate_task_assignment", cons.w_misc * 1e3)

            rel = self.relations_from_solution(solution)
            if not rel["sum_x_eq_y"]:
                report.add_misc("sum_x_neq_y", cons.w_misc * 1e3)

            cand = set(solution.candidate_task_ids) or set(self.task_by_id)
            for tid in solution.executed_task_ids:
                if tid not in cand and tid not in self.task_by_id:
                    report.add_misc(f"unknown_task_{tid}", cons.w_misc * 1e3)

        if cons.require_all_tasks and not self.config.allow_unassigned:
            missing = [
                t.id for t in self.tasks if t.id not in set(solution.executed_task_ids)
            ]
            if missing:
                report.add_misc("unassigned_not_allowed", 100.0 * len(missing))

        if cons.require_no_idle and not self.config.allow_idle_uav:
            for tour in solution.tours:
                if tour.is_idle:
                    report.add_misc(f"idle_uav_{tour.uav_id}", 100.0)

        # ---- 单机约束（single_constraints）----
        for tour in solution.tours:
            u = self.uav_by_id.get(tour.uav_id)
            if u is None:
                report.add_misc(f"unknown_uav_{tour.uav_id}", cons.w_misc * 1e3)
                continue
            pc = self.tour_path_cost(tour)
            tour_ok = True
            if cons.range:
                rv = check_range_constraint(u, pc, margin=1.0)
                if rv > 0:
                    # excess=rv：超限米数单独进 T0 层内 tie-break（不受
                    # w_range 缩放影响），使不可行解之间也有「少超一点
                    # 更好」的梯度。
                    report.add_range(
                        f"range_uav_{tour.uav_id}", cons.w_range * rv, excess=rv
                    )
                    tour_ok = False
            if cons.endurance:
                tv = check_time_constraint(u, pc)
                if tv > 0:
                    report.add_time(
                        f"time_uav_{tour.uav_id}", cons.w_endurance * tv
                    )
            tour.feasible = tour_ok

        # ---- 终点机巢：合法性 + 可用性 + 可达性 ----
        for tour in solution.tours:
            if tour.end_nest_id is None:
                if tour.task_ids:
                    # 有航次却无终点机巢：返航义务未落账（否则返航零成本、
                    # 容量不计账，适应度被系统性低估，恰是方案 A 要避免的
                    # 「进化不考虑返航」陷阱的残余缺口）→ 计违约使缺口在
                    # 评价中可见。空航次（不飞）无返航义务，不计。
                    report.add_misc(
                        f"end_nest_missing_{tour.uav_id}",
                        cons.w_end_nest_reachability * 1e3,
                    )
                    tour.feasible = False
                continue
            nest = self.nest_by_id.get(tour.end_nest_id)
            if nest is None:
                if cons.nest_availability:
                    report.add_misc(
                        f"bad_nest_{tour.end_nest_id}",
                        cons.w_nest_availability * 1e3,
                    )
                continue
            if cons.nest_availability and not nest.available:
                report.add_misc(
                    f"nest_unavailable_{tour.end_nest_id}",
                    cons.w_nest_availability * 100.0,
                )
            # 末任务 → 终点机巢不可达（地形遮挡 / 禁飞区，代价 inf）→ 判违约。
            # 与 muas_stage._choose_end_nests 的改派过滤共用同一判据。
            if cons.end_nest_reachability and tour.task_ids:
                c = self.costs.cost(
                    tour.task_ids[-1], self.costs.nest_key(nest.id)
                )
                if not math.isfinite(float(c)):
                    report.add_misc(
                        f"end_nest_unreachable_{tour.end_nest_id}",
                        cons.w_end_nest_reachability * 1e3,
                    )
                    tour.feasible = False

        # ---- 机巢容量约束（nest_capacity）----
        if cons.nest_capacity and self.nests:
            assignments = {t.uav_id: t.end_nest_id for t in solution.tours}
            fleet = self.fleet_uavs if self.fleet_uavs is not None else self.uavs
            cap_report = check_nest_capacity(fleet, self.nests, assignments)
            nest_tags = [
                f"nest_capacity_{nid}"
                for nid in cap_report.violations
                if cap_report.per_nest[nid]["overflow"] > 0
            ]
            report.add_nest(
                nest_tags,
                cons.w_nest_capacity * cap_report.penalty,
                capacity=cap_report.as_dict(),
            )

        # ---- 协同约束（coop_constraints）----
        if cons.time_window:
            w_pen, w_tags, w_late = check_time_window_constraint(
                solution.tours,
                self.task_by_id,
                self.uav_by_id,
                current_time=float(self.config.current_time),
                service_time=float(self.config.default_service_time),
                distance_fn=self.costs.travel,
            )
            if w_pen > 0:
                report.add_window(
                    w_tags, cons.w_window * w_pen, lateness=w_late
                )
                # 【任务级时间窗不可行 · 2026-10-12 改造】
                # 不再把「含迟到任务的整条 tour」置 feasible=False。
                # tw_task_* 标签已写入 violations（add_window），horizon._violated_task_ids
                # 对其走任务级归因（仅迟到任务自身被拦），同 tour 的准时任务照常完成。
                # 整 tour 兜底（horizon 中 tour.feasible==False 分支）仅对 range/endurance/
                # 终点机巢等真正「飞行不可行」的情形触发，时间窗违约只影响迟到任务本身。
        if cons.sequence:
            s_pen, s_tags = check_sequence_constraint(solution.tours, self.task_by_id)
            if s_pen > 0:
                report.add_seq(s_tags, cons.w_seq * s_pen)
        if cons.sync:
            sy_pen, sy_tags = check_sync_constraint(solution.tours, self.task_by_id)
            if sy_pen > 0:
                report.add_sync(sy_tags, cons.w_sync * sy_pen)

        return report.is_feasible, report.as_details()

    @classmethod
    def from_entities(
        cls,
        tasks: Sequence[Task],
        uavs: Sequence[UAV],
        nests: Sequence[Nest] | None = None,
        pairwise_cost: Mapping[tuple[int, int], float] | None = None,
        config: MUASProblemConfig | None = None,
        fleet_uavs: Sequence[UAV] | None = None,
        distance_fn: Callable[[Sequence[float], Sequence[float]], float] | None = None,
    ) -> SelectiveMUASProblem:
        """组装问题实例。

        Args:
            fleet_uavs: 全部无人机（含非空闲），用于推导机巢占用；
                        省略时退化为 ``uavs`` 本身。
            distance_fn: 统一距离口径（DEM 三维航程）。由滚动层从代价估算器
                生成并注入；省略 → 评价端 ETA 退回欧氏直线（仅无 DEM 场景）。
        """
        avail = [u for u in uavs if u.is_available]
        nest_list = list(nests or [])
        costs = build_cost_provider(
            tasks, nest_list, avail, pairwise=pairwise_cost, distance_fn=distance_fn
        )
        return cls(
            tasks=list(tasks),
            uavs=avail,
            nests=nest_list,
            costs=costs,
            fleet_uavs=list(fleet_uavs) if fleet_uavs is not None else list(uavs),
            config=config or MUASProblemConfig(),
        )


FitnessEvaluator = Callable[[AllocationSolution], float]


def make_fitness_evaluator(problem: SelectiveMUASProblem) -> FitnessEvaluator:
    def _eval(solution: AllocationSolution) -> float:
        problem.evaluate(solution)
        return problem.scalar_fitness(solution)

    return _eval
