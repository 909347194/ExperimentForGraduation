# -*- coding: utf-8 -*-
r"""跨层消息契约：闭环一周里上下层实际交换的东西。

职责：定义环内消息类型与两个端口的形状（``AllocatorPort`` / ``PlannerPort``），
      以及环产出的轨迹账（``CyclePlan`` / ``LoopTrace``）。
边界：不含任何处理逻辑，也不 import 两侧的算法模块——只有类型与 Protocol。
相关：``orchestrator.py``（环驱动）、``adapters/``（两侧适配）。

**为什么不复用两侧的 ``PlanRequest`` / ``UAVTour``**：那两者分别是各层的内部
口径（``PlanRequest`` 带机体规格与预算、``UAVTour`` 带 est_cost 与机巢决策），
直接互传等于让一层依赖另一层的字段语义。环里只走"这条航次去哪、什么时候走"
和"这条航次真实用多久"，转换在 ``adapters/`` 完成。

**``overrun`` 为什么要在契约里显式表达**：上层当前的执行模型是"周期末即完成"
（``_advance_execution``），它假定每条航次都在本周期内飞完。一旦真实
\(T_{\mathrm{traj}}\) 超过周期长度，上层仍会把任务判完成、把机位挪回机巢。
这个矛盾必须由环**当场报出来**，不能靠"通常不会超"来掩盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注；运行期不依赖 path_planning
    from path_planning.common.plan_types import LegCostRecord

Vec3 = tuple[float, float, float]


# ---------------------------------------------------------------------------
# 交换物
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TourStop:
    """一条航次里的一个停留点（起点/任务点/终点机巢统一形状）。"""

    id: int
    kind: str  # "nest" | "task"
    position: Vec3
    service_time_s: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in ("nest", "task"):
            raise ValueError(f"stop.kind 只能是 nest/task：{self.kind}")
        if len(self.position) != 3:
            raise ValueError(f"stop.position 必须是三维：{self.position}")
        if self.service_time_s < 0.0:
            raise ValueError(f"service_time_s 不能为负：{self.service_time_s}")


@dataclass(frozen=True)
class TourOrder:
    """上层 → 下层：本周期决定派出去的一条航次。

    ``t0`` 是**冻结的出发时刻**（上层排程定的），下层只按它排、不改它。
    ``stops`` 首项是起飞点、末项是终点机巢（与 ``PlanRequest.stops`` 同构）。
    ``task_ids`` 是这条航次真正承载的任务 id——失败回池时靠它认身份，
    不从 ``stops`` 里反推（机巢停留点也在里面）。
    """

    uav_id: int
    t0: float
    start_position: Vec3
    stops: tuple[TourStop, ...]
    meta: Mapping[str, Any] = field(default_factory=dict)
    task_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.stops:
            raise ValueError(f"机 {self.uav_id} 的航次没有任何停留点")
        if self.stops[-1].kind != "nest":
            raise ValueError(f"机 {self.uav_id} 的航次末项必须是终点机巢")


@dataclass(frozen=True)
class TourTicket:
    """下层 → 上层：一条航次的真实代价（不是估计）。

    ``time_source``：``planned`` = 时空联合优化的航迹；``estimate`` = 退化到
    定速估算（下层已声明降级）；``failed`` = 无交付航迹，``failure`` 给失败码。

    ``gate``：下层交付闸的三元组（``ok`` / ``worst_clearance_m`` / ``first``），取自
    ``PlanResult.diagnostics["global_gate"]``。有了它，"这条航迹凭什么能交付"不必
    回头翻日志；失败航次也能说清是稠密净空还是真几何拦下的。
    """

    uav_id: int
    ok: bool
    t0: float
    t_end: float = 0.0
    duration_s: float = 0.0
    path_len_m: float = 0.0
    time_source: str = "planned"
    failure: str | None = None
    gate: Mapping[str, Any] | None = None
    # 下层给的失败说明（哪一段/哪个点/什么原因）。失败航次必须能凭它自查，
    # 不允许只留一个失败码——否则事后只能反编译代码猜原因。
    detail: str = ""
    # 下层诊断（失败航次才有实际内容）：航段号、端点、走廊半宽、RRT 逐种子统计。
    # 已清洗成 JSON 原生类型，数组只留几何摘要——落盘可直接读，不背 numpy。
    failure_diag: Mapping[str, Any] = field(default_factory=dict)
    # 本条航次真正承载的任务 id：失败回池与"飞完"计数都按它认身份
    task_ids: tuple[int, ...] = ()
    # 逐段事实账（下层原样报出）：实测代价与不可飞边的**唯一**数据来源。
    # 失败航次也带——已解前缀是 ``planned``，断掉那段是 ``failed``。
    leg_costs: tuple["LegCostRecord", ...] = ()
    # 本条航次两端的机巢（下层 id）：起点 = 起飞巢，终点 = 回收巢。机巢与任务
    # 共用整数编号，角色只能由航次结构给出；落盘后"这条航迹从哪个 Base 起飞、
    # 回到哪个 Base"才不必再靠坐标反推。
    start_nest_id: int | None = None
    end_nest_id: int | None = None
    # 失败分级（``infeasible`` / ``degraded`` / ``internal``）：由下层码表给出，
    # 上层据此决定"写不可飞 / 照常回池 / 当 bug 查"，不在本层另设判据。
    failure_severity: str | None = None

    def __post_init__(self) -> None:
        if self.ok and self.failure is not None:
            raise ValueError(f"机 {self.uav_id}：成功航次不得带失败码")
        if not self.ok and self.time_source != "failed":
            raise ValueError(f"机 {self.uav_id}：失败航次的 time_source 必须是 failed")
        if self.failure_severity is not None and self.failure_severity not in (
            "infeasible", "degraded", "internal"
        ):
            raise ValueError(
                f"机 {self.uav_id}：failure_severity 非法：{self.failure_severity!r}"
            )


@dataclass(frozen=True)
class PlannerReport:
    """一次"排一批"的结果（下层对本周期的答卷）。"""

    tickets: Mapping[int, TourTicket]
    moved: Mapping[int, str] = field(default_factory=dict)
    conflicts_before: int = 0
    conflicts_after: int = 0
    elapsed_s: float = 0.0
    #: 有单但没拿到航迹的机号（与 ``tickets`` 里 ``ok=False`` 同集，显式留一份便于对账）
    unplanned: tuple[int, ...] = ()


@dataclass(frozen=True)
class CycleSnapshot:
    """上层一个周期的决定（运行完一个周期后取到的切面）。"""

    cycle: int
    t_start: float
    t_end: float
    orders: tuple[TourOrder, ...]
    executed_ids: tuple[int, ...] = ()
    est_cost: Mapping[int, float] = field(default_factory=dict)
    #: 快照标签（``cycle:cost_version``）；重解版必须与首版可区分
    snapshot_id: str = ""
    #: 该版所用的代价版本（``base`` / ``cycle:n``）
    cost_version: str = "base"
    #: 该版是第几次尝试（0 = 首解）
    attempt: int = 0


@dataclass(frozen=True)
class CostUpdate:
    r"""编排层 → 上层：本周期"下层量到了什么"的一张事实回灌单。

    只表达两类**事实**，都以数值进代价表，不携带任何估计倍率：

    - ``facts``：已飞航段的实测长度（米）。上层按底表同一权重口径重定该边代价
      \(c_{ij}=\ell^{real}_{ij}\cdot w_j\)——同一快照、同一批 OD，故为事实而非校准。
    - ``infeasible``：几何不可飞的边（``severity=infeasible`` 且在
      ``feedback.INFEASIBLE_CODES`` 白名单内）。上层把它写成一个极大数值，含该边的
      航次由**任务分配内部的航程检查**判死，不再另设可行性闸门。
    - 空单（两类皆空）= 不重解：残差再大也只进诊断。

    寿命固定为**周期内**：下一次 ``commit`` 即清空。跨周期"记住这条边多长/不能飞"
    只在同一批 OD 反复出现的场景成立，当前不由本结构承担。

    键空间约定：``facts`` / ``infeasible`` / ``nest_ends`` 一律用**下层 id**
    （任务 id / 机巢 id）。翻译成上层节点键只发生在适配器内部，因为只有它知道
    ``nest_offset``；环的台账（``CyclePlan.cost_update`` /
    ``unresolved_infeasible_edges`` / ``infeasible_but_survived``）全部保留下层 id，
    一张账只有一套编号。
    """

    facts: Mapping[tuple[int, int], float] = field(default_factory=dict)
    infeasible: frozenset[tuple[int, int]] = frozenset()
    #: 终点落在机巢的那几条（返航边），供上层决定谁加 ``nest_offset``；
    #: 取自 ``facts ∪ infeasible``，空集＝全部按任务端点翻译。
    nest_ends: frozenset[tuple[int, int]] = frozenset()
    source_attempt: int = 0
    reason: str = "infeasible_edges"

    def __post_init__(self) -> None:
        facts = {(int(a), int(b)): float(v) for (a, b), v in dict(self.facts).items()}
        for edge, length in facts.items():
            if length <= 0.0:
                raise ValueError(f"实测长度必须为正：{edge} → {length}")
        object.__setattr__(self, "facts", facts)
        for edge in set(self.infeasible) | set(self.nest_ends):
            if len(edge) != 2:
                raise ValueError(f"边必须是 (from_id, to_id)：{edge!r}")
        stray = set(self.nest_ends) - (set(facts) | set(self.infeasible))
        if stray:
            raise ValueError(f"nest_ends 必须取自 facts ∪ infeasible，多余：{sorted(stray)}")

    @property
    def edges(self) -> frozenset[tuple[int, int]]:
        """本单涉及的全部边（实测 + 不可飞）。"""
        return frozenset(self.facts) | frozenset(self.infeasible)

    @property
    def is_empty(self) -> bool:
        return not self.facts and not self.infeasible

    @property
    def n_edges(self) -> int:
        return len(self.edges)


@dataclass(frozen=True)
class ResolveAttempt:
    """一版解的账（``S0`` = 首次规划，``S1..`` = 同周期重解）。

    ``residuals`` 是"估计 vs 实测"的逐机偏差（米）；``cost_version`` 是本版
    使用的代价版本标签（``base`` / ``cycle:1`` …），便于事后对齐"哪一版用哪张表"。
    中间版的失败**只许进诊断**：完成/不可行/机位占用一律看最终 commit 那一版。
    """

    attempt_id: int
    snapshot_id: str
    tickets: Mapping[int, TourTicket]
    est_cost: Mapping[int, float] = field(default_factory=dict)
    residuals: Mapping[int, float] = field(default_factory=dict)
    cost_version: str = "base"
    cycle: int = 0
    #: 本版失败航次的失败码（机号 → 码）；成功版为空
    failures: Mapping[int, str] = field(default_factory=dict)

    @property
    def n_failed(self) -> int:
        return sum(1 for t in self.tickets.values() if not t.ok)

    @property
    def n_ok(self) -> int:
        return sum(1 for t in self.tickets.values() if t.ok)


@dataclass(frozen=True)
class CyclePlan:
    """环对一个周期的记账：上层派了什么、下层排了多久、谁超框。"""

    cycle: int
    t_start: float
    t_end: float
    report: PlannerReport
    est_cost: Mapping[int, float]
    busy_until: Mapping[int, float]
    # 上层本周期判为"已派出"的任务（``CycleSnapshot.executed_ids`` 原样带过来）
    executed_ids: tuple[int, ...] = ()
    # t_end 晚于周期结束的机号：上层"周期末即完成"的模型盖不住它们
    overruns: tuple[int, ...] = ()
    pruned: tuple[int, ...] = ()
    # 本周期失败回池的任务 id（来自失败航次的 ``TourOrder.task_ids``）：下一周期可重投
    rolled_back: tuple[int, ...] = ()
    # 本周期结束后真正回灌给上层任务池的 id（开通回池时等于 ``rolled_back``）
    reopened: tuple[int, ...] = ()
    # 本周期上层派出的任务归属：机号 → 任务 id（用于核对"重分配"是否真的换了机）
    assignments: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    # ---- 周期内重解（in-cycle resolve）------------------------------------
    # 逐版尝试：``attempts[0]`` 是首解，其后是同周期重解。空 = 老路径（未启用）。
    attempts: tuple[ResolveAttempt, ...] = ()
    # 中间版里失败、但最终版成功的航次数：只作诊断，不计入完成、不占机位
    n_intermediate_failures: int = 0
    # 最终交付版使用的代价版本（``base`` = 未回填；``cycle:n`` = 第 n 版回灌后）
    cost_version: str = "base"
    # 本周期回灌单实际生效的那张（未启用/空单为 None）
    cost_update: CostUpdate | None = None
    # 重解额度用完后**仍然**落在最终解里的不可飞边（下层 id）：如实报出，不偷偷吞掉。
    # 保留**下层 id**（任务/机巢原编号），是对"最终交付解"的原始事实陈述，不经翻译，
    # 便于与下层日志逐条对账。
    unresolved_infeasible_edges: tuple[tuple[int, int], ...] = ()
    # 回灌单写进代价的不可飞边里，**终版仍然飞了**的那些（下层 id）：正常应为空。
    # 这条是回灌唯一可被证伪的指标——非空即"写进代价的事实没生效"（键翻错/没落进视图）。
    infeasible_but_survived: tuple[tuple[int, int], ...] = ()
    # 周期内重解抛出异常时的原文（``类型: 消息``）：非空即本周期退回首解交付。
    # 不设 bool 开关：能把"哪个异常"写清楚，事后才不必翻日志。
    resolve_error: str = ""

    @property
    def final_tickets(self) -> Mapping[int, TourTicket]:
        """最终交付版的航次（未启用重解时即 ``report.tickets``）。"""
        if self.attempts:
            return self.attempts[-1].tickets
        return self.report.tickets

    @property
    def n_resolves(self) -> int:
        """周期内重解次数（首解不计）。"""
        return max(0, len(self.attempts) - 1)


@dataclass(frozen=True)
class LoopTrace:
    """整个闭环的账。``overrun_cycles`` 非空 = 上层执行模型与本模块口径不合。"""

    cycles: tuple[CyclePlan, ...]
    executed_ids: tuple[int, ...]
    overrun_cycles: tuple[int, ...]
    failures: Mapping[int, str] = field(default_factory=dict)

    @property
    def planned_duration_s(self) -> float:
        return sum(
            t.duration_s for c in self.cycles for t in c.report.tickets.values() if t.ok
        )

    @property
    def attempts(self) -> tuple[ResolveAttempt, ...]:
        """全部周期内逐版尝试（首版 + 重解），按周期顺序展开。"""
        return tuple(a for c in self.cycles for a in c.attempts)

    @property
    def resolve_cycles(self) -> tuple[int, ...]:
        """发生过周期内重解的周期号。"""
        return tuple(c.cycle for c in self.cycles if c.n_resolves > 0)


# ---------------------------------------------------------------------------
# 端口（两侧适配器要满足的形状）
# ---------------------------------------------------------------------------


@runtime_checkable
class AllocatorPort(Protocol):
    """上层端口：推进一个滚动周期，给出本周期派出的航次。

    ``step`` 是**唯一必需**的能力。周期内重解用的是四个可选能力，缺一个就
    退回"一拍完成"的老路径（零行为变化）：

    - ``prepare(new_tasks)``：冻结本周期快照并解首版，不写任务池/机队；
    - ``apply_cost_update(update)``：把 ``CostUpdate`` 装进当前快照的代价视图
      （建议回传翻译后的单，环用它记账）；
    - ``resolve()``：在**同一快照**上重解，不推进时间、不写任务池/机队；
    - ``commit()``：把当前解一次性回写任务池与机队（每周期只写一次）。
    """

    def step(self, new_tasks: Sequence[Any] | None = None) -> CycleSnapshot:
        ...


@runtime_checkable
class PlannerPort(Protocol):
    """下层端口：按一批冻结 t0 的航次排真实航迹，并维护已发布航迹。"""

    def plan_cycle(self, orders: Sequence[TourOrder]) -> PlannerReport:
        ...

    def busy_until(self, uav_id: int) -> float:
        ...

    def prune(self, now: float) -> tuple[int, ...]:
        ...
