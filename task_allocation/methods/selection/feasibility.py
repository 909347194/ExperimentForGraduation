# -*- coding: utf-8 -*-
"""feasibility.py — 快速可行性检查（论文 3.5）。

1. prefilter — 初步过滤（候选预筛选之后、边际选择之前）
2. static_unreachable — 入池时的静态物理不可达判定（往返航程口径）
3. postcheck — 候选集粗预算检查（边际选择之后）

行进代价口径（两套几何合一）：``travel = cost_hint[t.id]``（三维地理代价，
与基础优先级 C_i(t)、ΔC 插入式估计、postcheck 预算同源）优先，缺省回退
欧氏距离。**航程检查按往返口径**（2×travel vs range_margin×remaining_range）：
航次必须返航，单程可达 ≠ 可服务。

拒绝理由只有物理/终态原因（用户原则「任务不被完成只有物理约束
被违反」）：无机可用、往返超航程（prefilter）、状态终局 ``status_*``（入池已判
EXPIRED/UNREACHABLE/DONE）、机群预算超限 ``exceed_fleet_budget``（postcheck）。
时间窗不是拒绝理由——迟到的定价走适应度：覆盖门禁主口径下 F1 为纯完成计数、
迟到经 ``w_late·L`` 单一软计价；兼容口径（非 gate）才按
``eff = R×max(0, 1−L/W)`` 折减计 F1（见 muas/config.py 的
late_reward_decay_seconds）。时间窗预筛仅在 ``check_time_window=True`` 的
消融口径保留。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from task_allocation.common.types import ScoredTask, Task, TaskStatus, UAV

# 统一距离口径：``f(xyz_a, xyz_b) -> 米``（DEM 三维航程）；None → 欧氏直线
DistanceFn = Callable[[Sequence[float], Sequence[float]], float]


@dataclass
class FeasibilityConfig:
    # 往返航程检查的安全裕度。**往返口径下 margin 直接作用于往返航程**
    # （2×单程 ≤ margin×remaining_range）——旧单程口径的 0.95 等效半径
    # 是 76km 单程，不可直接迁移到往返口径（那会把可达半径腰斩到 38km）。
    # 1.0 = 物理上限，与完成闸门 ``check_range_constraint(margin=1.0)``
    # 同口径：exp01 实证 T105/T119 往返 78.5/77.0km 的单任务航次被正常
    # 服务（est_cost 79765m ≤ 80km），0.95 会把它们误杀。
    # <1.0 = 额外保守（排除物理可达的边际远站任务），仅消融时使用。
    range_margin: float = 1.0
    # 时间窗预筛总开关。**默认 False**（用户原则）：
    # 「任务不被完成只有物理约束被违反」——迟到是适应度定价问题
    # （门禁口径 w_late·L 软计价；兼容口径 eff = R×max(0, 1−L/W) 折减，
    # 见 problem.f1_reward），不是参选资格。
    # 生产路径不再以任何时间理由拒绝候选；仅消融对照时显式开启。
    check_time_window: bool = False
    budget_factor: float = 1.0
    current_time: float = 0.0
    # 消融口径专用：check_time_window=True 时的迟到容忍 W（秒）。
    # 0.0 = 历史口径（直飞到达 > latest 即拒）。
    late_tolerance_seconds: float = 0.0


def _euclid(a: Sequence[float], b: Sequence[float]) -> float:
    return float(
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
    )


def fleet_max_range(
    uavs: Sequence[UAV], default: float | None = None
) -> float | None:
    """机队最强满电航程（异构机队取全队最大值）。

    **永久性不可达判定必须用它**（设计关键点 1）：任务/巢位
    静态 + 每周期换电回满 ⇒ 「永久不可达」只在**最强机也够不着**时成立；
    用单机/短机航程判定会在异构下误杀长航程机可服务的任务。
    无可用参考值（无机且无缺省）时返回 None（判定自动放宽为不筛）。
    """
    vals = [u.full_charge_range(default) for u in uavs]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def _pair_cost(
    uav: UAV,
    task: Task,
    cost_hint: dict[int, float] | None = None,
    pairwise_cost: "Mapping[tuple[int, int], float] | None" = None,
    distance_fn: DistanceFn | None = None,
) -> float:
    """单机→单任务行进代价。优先级：pairwise 逐机三维 > cost_hint 统一三维
    > distance_fn > 欧氏。

    ``pairwise_cost``（滚动层惰性代价表，键 ``(-(1+uav_id), task_id)``）与
    ``cost_hint`` 同源同尺；异构化后可行性判定**逐机**取
    代价——「最近可用机」可能航程不够、更远的长航程机才装得下，不能用
    全队最小值一刀切。仅提供 ``cost_hint``（无 pairwise）时退回统一三维
    代价口径（历史行为，各机同值），与既有测试/调用方逐位一致。
    """
    if pairwise_cost is not None:
        key = (-(1 + uav.id), task.id)
        if key in pairwise_cost:
            return float(pairwise_cost[key])
    if cost_hint and task.id in cost_hint:
        return float(cost_hint[task.id])
    if distance_fn is not None:
        return float(distance_fn(uav.position, task.position))
    return _euclid(uav.position, task.position)


def _nearest_avail_uav(
    task: Task,
    uavs: list[UAV],
    distance_fn: DistanceFn | None = None,
) -> tuple[UAV | None, float]:
    """最近可用机及其距离。

    ``distance_fn`` 给出时按 **DEM 三维航程** 选最近（与代价矩阵、ETA 同源）；
    否则退回欧氏直线。选最近机这件事本身也必须用统一口径：直线距离最近 ≠
    地形航程最近（山谷两侧两点直线很近、实际要绕）。
    """
    avail = [u for u in uavs if u.is_available]
    if not avail:
        return None, float("inf")
    best_u = None
    best_d = float("inf")
    for u in avail:
        if distance_fn is not None:
            d = float(distance_fn(u.position, task.position))
        else:
            d = _euclid(u.position, task.position)
        if d < best_d:
            best_d = d
            best_u = u
    return best_u, best_d


def prefilter(
    scored: list[ScoredTask],
    uavs: list[UAV],
    config: FeasibilityConfig | None = None,
    cost_hint: dict[int, float] | None = None,
    distance_fn: DistanceFn | None = None,
    nests: Sequence[Nest] | None = None,
    max_range: float | None = None,
    pairwise_cost: Mapping[tuple[int, int], float] | None = None,
) -> tuple[list[ScoredTask], list[ScoredTask]]:
    """初步可行性过滤（**只拦物理不可达与终态**）。

    Args:
        cost_hint: ``task_id -> 三维行进代价``（与优先级 C_i(t) 同源）。
            **兼容回退**：未提供逐机 ``pairwise_cost``（或键未命中）时，以
            该任务的统一三维代价作为各机行进代价（历史行为，各机同值）。
            异构机队应传 ``pairwise_cost`` 以获得真正的逐机判定。
        distance_fn: 统一距离口径（DEM 三维航程）。本函数中用于 (a)
            ``_pair_cost`` 的逐机回退、(b) 机巢往返几何可达判据；两者都
            缺 ``distance_fn`` 时才退欧氏直线。
        nests: 机巢列表（几何可达性判据，与 ``static_unreachable`` 同源）。
            非 None 且 ``max_range`` 有效时，往返超限先分两种归因。
        max_range: **机队最强**满电航程（米，异构机队取全队最大值，见
            :func:`fleet_max_range`）。与 ``nests`` 共同决定「真几何不可达」
            与「当前无可用机」的分界。
        pairwise_cost: 逐机三维代价表（键 ``(-(1+uav_id), task_id)``，与
            ``cost_hint`` 同源同尺）。异构化后可行性判定逐机取代价
            （短航程机装不下 ≠ 任务不可服务）；优先级最高，缺项时依次
            回退 ``cost_hint`` → ``distance_fn`` → 欧氏。

    拒绝理由以物理原因为主：``no_available_uav``（无机可用）与
    ``exceed_round_trip_range``（**真几何不可达**——所有机巢往返都超限）；
    另有终态原因 ``status_{expired,unreachable,done}``（入池已判定的状态终局）。

    口径对齐：往返超限但**几何可达**（存在机巢往返 ≤ 满电航程）
    时，归因为 ``no_uav_in_range``（机队服务不可达——典型为机队漂移致
    空巢），**不再伪装成物理不可达**。旧口径把资源约束误记成物理约束，
    使任务既不进 UNREACHABLE（分母照算）、又每周期被拒（分子不计），
    完成率虚低且无法解释。两类归因分开后，由滚动层按
    ``service_patience`` 把服务不可达任务单列出池。
    时间窗不再作为拒绝理由（用户原则：迟到由适应度定价，
    不是参选资格）；``miss_time_window`` 仅在 ``check_time_window=True``
    的消融口径出现。
    """
    cfg = config or FeasibilityConfig()
    passed: list[ScoredTask] = []
    rejected: list[ScoredTask] = []
    avail = [u for u in uavs if u.is_available]
    if not avail:
        for s in scored:
            s.filter_reason = "no_available_uav"
            rejected.append(s)
        return passed, rejected

    for s in scored:
        t = s.task
        reason: str | None = None
        if t.status in (TaskStatus.EXPIRED, TaskStatus.UNREACHABLE, TaskStatus.DONE):
            reason = f"status_{t.status.value}"
        else:
            # 逐机可行性（异构化）：∃ 可用机 u 满足
            # 2·c(t,u) ≤ range_margin·remaining_range(u)。同构等剩余航程时
            # 与旧「最近机 + cost_hint 全队最小值」口径逐位一致；异构下
            # 不被短航程机误拒（更远的长航程机可能才装得下）。
            served: list[tuple[UAV, float]] = []
            for u in avail:
                c_u = _pair_cost(u, t, cost_hint, pairwise_cost, distance_fn)
                if 2.0 * c_u <= cfg.range_margin * max(u.remaining_range, 0.0):
                    served.append((u, c_u))
            if not served:
                # 往返口径：航次必须返航（终点机巢由内层 MCF 选，最近巢
                # 往返是下界），单程可达 ≠ 可服务。T52 实证：单程 39.9km
                # < 0.95×80km 放行过，往返 79.7km 实则物理不可达，旧单程
                # 口径下它每周期空转 churn 到淘汰。
                #
                # 归因分离：可用机都装不下时，物理可达性应由
                # 「最近**机巢**」判定（静态、与机队状态无关）。机队漂移致
                # 空巢时，几何可达任务会因当前无可用机而超限——那是服务
                # 能力不足（no_uav_in_range），不是物理不可达
                # （exceed_round_trip_range）。异构机队下 max_range 传
                # **机队最强满电航程**（fleet_max_range），只有最强机也
                # 够不着才判真几何不可达（设计关键点 1）。
                geo_reachable = False
                if nests and max_range and max_range > 0:
                    for n in nests:
                        dn = (
                            float(distance_fn(n.position, t.position))
                            if distance_fn is not None
                            else _euclid(n.position, t.position)
                        )
                        if 2.0 * dn <= max_range:
                            geo_reachable = True
                            break
                reason = (
                    "no_uav_in_range" if geo_reachable else "exceed_round_trip_range"
                )
            else:
                u, travel = min(served, key=lambda p: p[1])
                if cfg.check_time_window and t.latest is not None:
                    eta = travel / max(u.speed, 1e-6)
                    # 消融口径：迟到容忍 W 内放行，超过则拒（奖励已趋零）。
                    if cfg.current_time + eta > t.latest + cfg.late_tolerance_seconds:
                        reason = "miss_time_window"
        if reason is not None:
            s.filter_reason = reason
            rejected.append(s)
        else:
            s.filter_reason = None
            passed.append(s)
    return passed, rejected


def static_unreachable(
    task: Task,
    nests: Sequence[Nest],
    max_range: float,
    distance_fn: DistanceFn | None = None,
    margin: float = 1.0,
) -> bool:
    """静态物理不可达判定：对**所有**机巢的往返航程都超过满电可及范围。

    任务位置与巢位固定、UAV 每周期满电（``recharge_at_nest``）⇒ 判定与
    周期状态无关，入池时一次性完成。命中即「过于偏远」——用户原则
    下唯一合法的未完成原因：不是解决所有任务，能力范围外
    的任务不完成是正常的、符合理论的。调用方应把任务标记为
    ``TaskStatus.UNREACHABLE`` 出池，避免它每周期空转 churn（进选择、
    被规划器丢弃、直到超期）把指标误导成「淘汰」。

    Args:
        max_range: **机队最强满电航程**（异构机队取全队 ``max_range`` 的最大值，
            见 :func:`fleet_max_range`）。永久性判定必须用最强机：「永久不可达」
            只在最强机也够不着时成立，用短航程机会误杀长航程机可服务的任务。
        margin: 物理裕度。**默认 1.0 = 物理上限**，与完成闸门
            ``check_range_constraint(margin=1.0)`` 同口径。规划保守度
            （0.95）属于 stage 内部的 participation/provisioning 决策，
            不是物理边界——用它做静态判定会误杀物理可达的边际远站任务
            （exp01 实证：T105/T119 往返 78.5/77.0km 可服）。
        distance_fn: 统一距离口径（DEM 三维航程）；None 回退欧氏直线。
    """
    if not nests or max_range <= 0:
        return False
    range_limit = margin * float(max_range)
    p = task.position
    for n in nests:
        q = n.position
        d = float(distance_fn(q, p)) if distance_fn is not None else _euclid(q, p)
        if 2.0 * d <= range_limit:
            return False
    return True


def postcheck(
    candidate: list[ScoredTask],
    uavs: list[UAV],
    config: FeasibilityConfig | None = None,
    cost_hint: dict[int, float] | None = None,
    distance_fn: DistanceFn | None = None,
) -> tuple[list[ScoredTask], list[ScoredTask]]:
    cfg = config or FeasibilityConfig()
    if not candidate:
        return [], []
    avail = [u for u in uavs if u.is_available]
    budget = cfg.budget_factor * sum(max(u.remaining_range, 0.0) for u in avail)

    def approx_cost(s: ScoredTask) -> float:
        if cost_hint and s.task.id in cost_hint:
            return float(cost_hint[s.task.id])
        if s.approx_delta_cost > 0:
            return s.approx_delta_cost
        _, d = _nearest_avail_uav(s.task, uavs, distance_fn=distance_fn)
        return d

    ordered = sorted(
        candidate,
        key=lambda s: (s.marginal_score if s.marginal_score else s.priority),
        reverse=True,
    )
    kept: list[ScoredTask] = []
    dropped: list[ScoredTask] = []
    acc = 0.0
    for s in ordered:
        c = approx_cost(s)
        if budget <= 0 or acc + c <= budget + 1e-9:
            acc += c
            kept.append(s)
        else:
            s.filter_reason = "exceed_fleet_budget"
            dropped.append(s)
    kept_ids = {s.task.id for s in kept}
    kept_stable = [s for s in candidate if s.task.id in kept_ids]
    return kept_stable, dropped
