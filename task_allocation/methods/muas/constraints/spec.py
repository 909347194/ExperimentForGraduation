# -*- coding: utf-8 -*-
"""spec.py — ConstraintSpec：约束唯一判据注册表（第二阶段 P0 契约层）。

来源：`task_allocation/docs/stage2_final_scheme_2026-11-06.md` §6 要素 6 /
§8 P0「ConstraintSpec 唯一 judge() 接口；四处另写判据统一到该接口但**保持
现有语义不变**，语义漂移先记录进台账、修复后移 P1/P2」。

本模块只做三件事，**零行为变化**（bit-exact）：

1. **注册表**：每条约束一个 :class:`ConstraintSpec`（唯一 id / 对象层级 /
   硬软 / 单位 / 归属层 / 处置 / ``judge()``）。``judge()`` 一律**委托**既有
   实现（``single_constraints`` / ``coop_constraints`` / ``nest_capacity``），
   不重写任何判定；
2. **另写判据收口**（plan §4.5 的四处漂移点）：把散落在
   ``decode_repair._best_order``（直读 ``task.latest``）、``decode_repair.
   _seq_violation``（单调下降判定）、``coverage_gate``（自写容量/航程账）与
   ``stage_runner``（四个采纳判据表达式）的「第二套判据」以**具名视图
   （view）**收口到本模块——各视图保留调用点的现语义原样（包一层，不改判），
   统一入口便于 P1/P2 消除漂移时只动一处；
3. **漂移测量辅助**：提供各视图对的比较函数（:mod:`criteria_drift` 消费），
   使「同一对象两套判据的不一致条数」可量化。

判据归属矩阵与漂移消解计划见定稿 §4.5 / §8；本模块不决定语义，
只让语义**可寻址**。

视图命名约定：
- ``*_model``：问题模型口径（``problem.check_feasible`` 一链，唯一产出口径）；
- ``*_decode``：解码/修复链内的另写判据（现状语义，P1/P2 收敛到 model）；
- ``*_gate``：覆盖门禁自写账（``coverage_gate``，现状语义）；
- ``accept_*_view``：采纳判据（``stage_runner`` 各后处理点，现状语义）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from task_allocation.methods.muas.constraints.coop_constraints import (
    check_sequence_constraint,
    check_time_window_constraint,
    task_window,
)
from task_allocation.methods.muas.constraints.nest_capacity import (
    check_nest_capacity,
)
from task_allocation.methods.muas.constraints.single_constraints import (
    check_range_constraint,
    check_time_constraint,
)

__all__ = [
    "JudgeResult",
    "ConstraintSpec",
    "REGISTRY",
    "get_spec",
    # 判据（模型口径，唯一产出口径）
    "judge_unique_tasks",
    "judge_range",
    "judge_endurance",
    "judge_time_window",
    "judge_sequence",
    "judge_nest_capacity",
    # 另写判据收口（视图）
    "window_deadline_model",
    "window_deadline_decode",
    "sequence_decode_violated",
    "sequence_model_inversions",
    "nest_capacity_gate_loads",
    "nest_capacity_gate_slot_ok",
    "range_gate_ok",
    "accept_polish_eps_view",
    "accept_monotone_view",
    "accept_cost_polish_view",
    "accept_f_view",
]


# ---------------------------------------------------------------------------
# 判定结果载体
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeResult:
    """单条约束的一次判定结果。

    ``amount`` 为该判据口径下的违背量（0 = 满足）；``tags`` 与既有
    违约标签同名（``range_uav_*`` / ``tw_task_*`` / ``nest_capacity_*`` …），
    ``raw`` 保留既有实现的原始返回（不转换、不丢信息）。
    """

    violated: bool
    amount: float
    tags: tuple[str, ...] = ()
    raw: Any = None


@dataclass(frozen=True)
class ConstraintSpec:
    """约束规格：唯一 id + 判据 + 归属层 + 处置。"""

    constraint_id: str
    layer: str          # encode | layer1 | layer2 | layer3
    hardness: str       # hard | soft
    unit: str           # m | s | count | dimensionless
    disposition: str    # 结构去重 | 投影 | 惩罚 | 出池 | 构造补全 | 终态校验 …
    judge: Callable[..., JudgeResult]
    description: str = ""


# ---------------------------------------------------------------------------
# 判据实现（全部委托既有函数，语义逐位不变）
# ---------------------------------------------------------------------------


def judge_unique_tasks(solution: Any) -> JudgeResult:
    """任务唯一性（结构）：``AllocationSolution.validate_unique_tasks``。"""
    ok = bool(solution.validate_unique_tasks())
    return JudgeResult(violated=not ok, amount=0.0 if ok else 1.0, raw=ok)


def judge_range(uav: Any, assigned_distance: float) -> JudgeResult:
    """航程约束：委托 ``single_constraints.check_range_constraint(margin=1.0)``。"""
    excess = float(check_range_constraint(uav, assigned_distance, margin=1.0))
    return JudgeResult(
        violated=excess > 0.0,
        amount=excess,
        raw=excess,
    )


def judge_endurance(uav: Any, assigned_distance: float) -> JudgeResult:
    """续航约束：委托 ``single_constraints.check_time_constraint``。"""
    excess = float(check_time_constraint(uav, assigned_distance))
    return JudgeResult(violated=excess > 0.0, amount=excess, raw=excess)


def judge_time_window(
    tours: Sequence[Any],
    tasks: Mapping[int, Any],
    uavs: Mapping[int, Any],
    *,
    current_time: float = 0.0,
    service_time: float = 0.0,
    speed_default: float = 10.0,
    distance_fn: Callable | None = None,
) -> JudgeResult:
    """时间窗约束（软）：委托 ``coop_constraints.check_time_window_constraint``。"""
    penalty, tags, lateness = check_time_window_constraint(
        tours,
        tasks,
        uavs,
        current_time=current_time,
        service_time=service_time,
        speed_default=speed_default,
        distance_fn=distance_fn,
    )
    return JudgeResult(
        violated=penalty > 0.0,
        amount=float(penalty),
        tags=tuple(tags),
        raw=(penalty, tags, lateness),
    )


def judge_sequence(tours: Sequence[Any], tasks: Mapping[int, Any]) -> JudgeResult:
    """时序约束（硬，同航次内）：委托 ``coop_constraints.check_sequence_constraint``。"""
    penalty, tags = check_sequence_constraint(tours, tasks)
    return JudgeResult(
        violated=penalty > 0.0,
        amount=float(penalty),
        tags=tuple(tags),
        raw=(penalty, tags),
    )


def judge_nest_capacity(
    uavs: Sequence[Any],
    nests: Sequence[Any],
    assignments: Mapping[int, int | None],
) -> JudgeResult:
    """机巢容量约束（硬）：委托 ``nest_capacity.check_nest_capacity``。"""
    report = check_nest_capacity(uavs, nests, assignments)
    tags = tuple(
        f"nest_capacity_{nid}"
        for nid in report.violations
        if report.per_nest[nid]["overflow"] > 0
    )
    return JudgeResult(
        violated=not report.ok,
        amount=float(report.penalty),
        tags=tags,
        raw=report,
    )


# ---------------------------------------------------------------------------
# 注册表（定稿 §4.2 约束归属矩阵的机器可读形态）
# ---------------------------------------------------------------------------


REGISTRY: dict[str, ConstraintSpec] = {
    s.constraint_id: s
    for s in (
        ConstraintSpec(
            "unique_tasks", "encode", "hard", "count", "结构去重",
            judge_unique_tasks, "一任务一机：无重复分配、无未知任务",
        ),
        ConstraintSpec(
            "range", "layer2", "hard", "m", "弹出/改派任务直到航程 ≤ limit",
            judge_range, "航程/能源：起点→任务链→终点巢全航程 ≤ 剩余航程",
        ),
        ConstraintSpec(
            "endurance", "layer2", "hard", "s", "与航程对称的续航投影",
            judge_endurance, "飞行时间/续航：全航迹时长 ≤ UAV.max_time",
        ),
        ConstraintSpec(
            "time_window", "layer1", "soft", "s", "迟到计价（不修复、不淘汰）",
            judge_time_window, "时间窗迟到：迟到计价、计完成（软约束）",
        ),
        ConstraintSpec(
            "sequence", "layer2", "hard", "count", "构造性归位",
            judge_sequence, "先后顺序：同航次内按 sequence_order 升序",
        ),
        ConstraintSpec(
            "nest_capacity", "layer2", "hard", "count", "换巢 / MCF 分配",
            judge_nest_capacity, "机巢合法性/容量：终点巢占用 ≤ capacity",
        ),
    )
}


def get_spec(constraint_id: str) -> ConstraintSpec:
    return REGISTRY[constraint_id]


# ---------------------------------------------------------------------------
# 另写判据收口（四处漂移点，plan §4.5）——现语义原样包一层
# ---------------------------------------------------------------------------


# ① 时间窗：deadline 字段两视图 --------------------------------------------
# model：`coop_constraints.task_window`（time_window 元组优先、latest 回退）；
# decode：`decode_repair._best_order` 现状直读 `task.latest`（元组口径被绕过）。
# 二者仅在 Task 携带 time_window 元组且元组[1] != latest 时漂移。


def window_deadline_model(task: Any) -> float | None:
    """模型口径 deadline：``coop_constraints.task_window(task)[1]``。"""
    return task_window(task)[1]


def window_deadline_decode(task: Any) -> float | None:
    """解码口径 deadline（``_best_order`` 现语义）：直读 ``task.latest``。"""
    lst = getattr(task, "latest", None)
    return float(lst) if lst is not None else None


# ② 时序：两视图 ------------------------------------------------------------
# decode：`decode_repair._seq_violation` 现语义 = 组内出现「序号下降」即违约
#   （单调判定，带 1e-9 松弛）；
# model：`coop_constraints.check_sequence_constraint` = 组内逆序对计数。
# 二者在「是否存在违约」层面等价（存在下降 ⇔ 存在逆序对），漂移只可能来自
# 1e-9 松弛与计数/布尔的粒度差。


def sequence_decode_violated(pairs: Sequence[tuple[Any, float]]) -> bool:
    """解码口径时序判定（``_seq_violation`` 现语义）。

    ``pairs`` = 按访问顺序的 ``(sequence_group, sequence_order)``；
    ``group is None`` 的条目跳过。链内出现「序号较已见最大值下降 > 1e-9」
    即判违约。
    """
    seen: dict[Any, float] = {}
    for grp, order in pairs:
        if grp is None:
            continue
        prev = seen.get(grp)
        if prev is not None and order < prev - 1e-9:
            return True
        seen[grp] = order if prev is None else max(prev, order)
    return False


def sequence_model_inversions(pairs: Sequence[tuple[Any, float]]) -> int:
    """模型口径时序违背量（``check_sequence_constraint`` 同判据）= 逆序对个数。"""
    groups: dict[Any, list[float]] = {}
    for grp, order in pairs:
        if grp is None:
            continue
        groups.setdefault(grp, []).append(float(order))
    inv = 0
    for orders in groups.values():
        for i in range(len(orders)):
            for j in range(i + 1, len(orders)):
                if orders[i] > orders[j]:
                    inv += 1
    return inv


# ③ 机巢容量/航程：coverage_gate 自写账（现语义）---------------------------


def range_gate_ok(cost: float, uav: Any, *, eps: float = 1e-6) -> bool:
    """门禁口径航程判据（``coverage_gate._range_ok`` 现语义）。

    与 ``judge_range(margin=1.0)`` 的通过/违约判定等价（同 eps），但保留
    独立视图供漂移测量；P1/P2 收敛到 ``judge_range`` 时只动本函数。
    """
    return cost <= float(getattr(uav, "remaining_range", float("inf"))) + eps


def nest_capacity_gate_loads(
    problem: Any,
    routes: Mapping[int, Sequence[int]],
    end_nests: Mapping[int, int | None],
) -> dict[int | None, int]:
    """门禁口径容量账（``coverage_gate._nest_loads`` 现语义）。

    起飞释放原巢名额、落地占终点巢名额，从 ``uav.nest_id`` 现算
    （design_nest_state.md §0：不落第二份状态）。
    """
    fleet = problem.fleet_uavs or problem.uavs
    loads: dict[int | None, int] = {}
    for u in fleet:
        loads[u.nest_id] = loads.get(u.nest_id, 0) + 1
    for uid, seq in routes.items():
        if not seq:
            continue
        u = problem.uav_by_id.get(uid)
        if u is None:
            continue
        loads[u.nest_id] = loads.get(u.nest_id, 0) - 1
        e = end_nests.get(uid, u.nest_id)
        loads[e] = loads.get(e, 0) + 1
    return loads


def nest_capacity_gate_slot_ok(
    problem: Any,
    loads: Mapping[int | None, int],
    nest_id: int,
    home_nest_id: int | None,
) -> bool:
    """门禁口径「开班落位」容量判定（``coverage_gate._check_open.cap_ok`` 现语义）。

    ``delta = 1 - (nid == home)``：起飞释放原巢名额；巢不可用/缺失判 False。
    """
    n = problem.nest_by_id.get(nest_id)
    if n is None or not bool(getattr(n, "available", True)):
        return False
    delta = 1 - (1 if nest_id == home_nest_id else 0)
    return loads.get(nest_id, 0) + delta <= int(n.capacity)


# ④ 采纳判据（stage_runner 各后处理点，现语义）----------------------------
# 目标统一判据 accept_f_view（P3 落地；现仅作漂移对照，不接线）。


def accept_polish_eps_view(
    n_accepted: int, cand_f: float, cur_f: float, eps: float
) -> bool:
    """polish / memetic 择优现语义（``stage_runner`` polish_eps 判据）。"""
    return n_accepted > 0 and cand_f < cur_f - eps


def accept_monotone_view(
    old_exec: set,
    new_exec: set,
    cand_n_hard: int,
    cur_n_hard: int,
    cand_f: float,
    cur_f: float,
    eps: float,
) -> bool:
    """coverage_first 单调后处理现语义（覆盖优先字典序）。"""
    return new_exec >= old_exec and cand_n_hard <= cur_n_hard and (
        len(new_exec) > len(old_exec) or cand_f < cur_f - eps
    )


def accept_cost_polish_view(
    old_exec: set,
    new_exec: set,
    cand_n_hard: int,
    cur_n_hard: int,
    cand_excess: float,
    cur_excess: float,
    cand_path: float,
    cur_path: float,
    eps: float,
) -> bool:
    """交付后成本 polish 现语义（执行集/硬违约/超限不退化 + 航程严格下降）。

    ⚠️ 成本门按**航程分量**（米）裁决，不是 f 单位——与
    ``accept_f_view`` 的漂移即 plan §4.5 第 4 条（P3 消除）。
    """
    return (
        new_exec >= old_exec
        and cand_n_hard <= cur_n_hard
        and cand_excess <= cur_excess + 1e-9
        and cand_path < cur_path - eps
    )


def accept_f_view(
    cand_f: float,
    cur_f: float,
    eps: float,
    *,
    cand_n_hard: int,
    cur_n_hard: int,
    cand_exec: set | None = None,
    cur_exec: set | None = None,
    cand_late: float = 0.0,
    cur_late: float = 0.0,
) -> bool:
    """目标统一采纳判据（定稿 §4.2：``f(cand) < f(cur) − ε`` 且约束向量不退化）。

    P3 接线；现仅用于漂移测量（与 accept_cost_polish_view 对照）。
    约束向量 = (n_hard, 未交付集, 迟到秒) 逐分量不退化。
    """
    if cand_n_hard > cur_n_hard:
        return False
    if cand_late > cur_late + 1e-9:
        return False
    if cand_exec is not None and cur_exec is not None and not cand_exec >= cur_exec:
        return False
    return cand_f < cur_f - eps
