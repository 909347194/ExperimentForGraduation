# -*- coding: utf-8 -*-
"""覆盖门禁（coverage gate）：一阶插入可行性检验 + 终态不动点修复。

合法解定义（用户 2026-10-22 裁定，与物理硬约束等价）：

    1. 物理硬约束；
    2. 覆盖门禁：不存在「任务 j∈S 未进入任何 tour，且存在空闲机可开班
       或某航次在硬约束下可插入 j」——违者 voluntary_skip，解非法；
       已选但经一阶检验确无任何可行插入的任务允许不交付，记
       capacity_shortfall（能力不足，不算 voluntary_skip）。

口径（2026-10-24 用户裁定：无超期淘汰、迟到软约束、shortfall 非永久丢失）：

- **窗口不否决**（``window_mode="soft"``）：可装判据只剩硬约束
  （航程 / 巢容量等）；deadline 是软偏好，迟到交付合法（计迟到、
  计完成），由 ``w_late·L`` 计价（T2）。旧「自身窗硬约束」（D1/D2）
  已废除——否则 blown-deadline 任务永远判 shortfall、无法交付，
  「shortfall 非永久丢失」不成立。
- 航程判据 = :meth:`problem.SelectiveMUASProblem.tour_path_cost` 全航程
  （起点 → 任务链 → 终点巢）≤ ``uav.remaining_range``——与
  ``check_range_constraint(margin=1.0)``、``muas_stage._repair_range``
  同一口径。
- ETA 口径与 ``muas_stage._arrival_time_of`` 同源：DEM 三维航程 / speed，
  早到等待 ``earliest``（仅用于 EDD 救援排序，不再判废）。
- 开班（模式 A）终点巢名额：容量账按「起飞释放原巢名额、落地占终点巢
  名额」现算（design_nest_state.md §0：容量从 uav.nest_id 现算，
  不落第二份状态）；``constraints.nest_capacity`` 关闭时不查容量。
- 开班（模式 A）终点巢名额：容量账按「起飞释放原巢名额、落地占终点巢
  名额」现算（design_nest_state.md §0：容量从 uav.nest_id 现算，
  不落第二份状态）；``constraints.nest_capacity`` 关闭时不查容量。

一阶不完备边界（**显式声明，随 gate_stats.window_mode 一同落盘**）：
只判「单任务插入（模式 B）」与「空闲机开班（模式 A）」两种模式，查不到
「j 单独装不下、与另一未覆盖任务拼一班才装得下」的组合可行；``n_shortfall``
记录的是**一阶检验**的失败，不是全局不可行的证明。

用法：

- :func:`audit_coverage` —— 纯检验不改解，返回分账（voluntary_skip /
  capacity_shortfall），供适应度 W_skip 层与诊断落盘；
- :func:`repair_coverage` —— 能救必救（插入现有航次 / 空闲机开班），
  修到不动点；voluntary_skip 由构造归零，残值仅在轮数耗尽时非零
  （防御信号 = 修复未收敛）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from task_allocation.common.types import UAV, UAVTour

WINDOW_MODE = "soft"
_EPS = 1e-6

__all__ = [
    "WINDOW_MODE",
    "GateStats",
    "audit_coverage",
    "repair_coverage",
]


# ── 分账容器 ──────────────────────────────────────────────────────


@dataclass
class GateStats:
    """覆盖门禁分账（gate_stats 落盘口径，随解一并写入 solution.meta）。"""

    n_skip: int = 0
    n_shortfall: int = 0
    n_rescued: int = 0
    rounds: int = 0
    window_mode: str = WINDOW_MODE
    skip_ids: list[int] = field(default_factory=list)
    shortfall_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_skip": int(self.n_skip),
            "n_shortfall": int(self.n_shortfall),
            "n_rescued": int(self.n_rescued),
            "rounds": int(self.rounds),
            "window_mode": self.window_mode,
            "skip_ids": sorted(int(t) for t in self.skip_ids),
            "shortfall_ids": sorted(int(t) for t in self.shortfall_ids),
        }


# ── 基础判据（与 muas_stage/_arrival_time_of、_task_latest 同源）─────


def _task_deadline(problem: Any, tid: int) -> float | None:
    """任务 deadline：``time_window`` 元组优先、``.latest`` 回退。"""
    task = problem.task_by_id.get(tid)
    if task is None:
        return None
    tw = getattr(task, "time_window", None)
    if isinstance(tw, (tuple, list)) and len(tw) == 2 and tw[1] is not None:
        return float(tw[1])
    lst = getattr(task, "latest", None)
    return None if lst is None else float(lst)


def arrival_at(problem: Any, uav: UAV, seq: Sequence[int], pos: int) -> float:
    """航次中第 ``pos`` 个任务的预计到达时刻（含 earliest 等待）。

    与 ``muas_stage._arrival_time_of`` 严格同口径：DEM 三维航程 / speed，
    先「等待至 earliest」再返回——完成闸门判 latest 用的正是等待后时刻。
    """
    speed = max(float(getattr(uav, "speed", 10.0) or 10.0), 1e-6)
    service = float(getattr(problem.config, "default_service_time", 0.0) or 0.0)
    t = float(getattr(problem.config, "current_time", 0.0) or 0.0)
    prev = (float(uav.x), float(uav.y), float(uav.z))
    for i, tid in enumerate(seq):
        task = problem.task_by_id.get(tid)
        if task is None:
            continue
        t += problem.costs.travel(prev, task.position) / speed
        est = getattr(task, "earliest", None)
        if est is not None and t < est:
            t = est
        if i == pos:
            return t
        t += service
        prev = task.position
    return t


def _window_ok(problem: Any, uav: UAV, seq: Sequence[int], pos: int) -> bool:
    """（已废除的判据，保留为恒真占位）迟到软约束下窗口不否决可装性。

    2026-10-24 口径：deadline 是软偏好，迟到由 ``w_late·L`` 计价；
    可装判据只剩硬约束（航程 / 巢容量）。函数恒返回 ``True``，保留
    签名是为不破坏既有调用点。
    """
    return True


def _tour_cost(problem: Any, uav: UAV, seq: Sequence[int], end_nest: int | None) -> float:
    """全航程代价（起点 → 任务链 → 终点巢），与完成闸门同一函数。"""
    if not seq:
        return 0.0
    tour = UAVTour(uav_id=uav.id, task_ids=list(seq), end_nest_id=end_nest)
    return float(problem.tour_path_cost(tour))


def _best_end_nest(
    problem: Any,
    uav: UAV,
    seq: Sequence[int],
    *,
    cap_ok: Any = None,
    prefer: Sequence[int] | None = None,
) -> tuple[int | None, float]:
    """终点巢候选：返回 ``(巢 id, 全航程代价)``；不可达返回 ``(None, inf)``。

    ``prefer`` 给定时只在其中选（模式 A 的容量可行巢）；否则全巢最小代价
    （与 ``_feasible_insert`` 返航腿最近巢保守估同口径）。
    """
    cands = list(prefer) if prefer is not None else [n.id for n in problem.nests]
    best_id, best_cost = None, float("inf")
    for nid in cands:
        if cap_ok is not None and not cap_ok(nid):
            continue
        c = _tour_cost(problem, uav, seq, nid)
        if c < best_cost - 1e-12:
            best_id, best_cost = nid, c
    return best_id, best_cost


def _range_ok(cost: float, uav: UAV) -> bool:
    return cost <= float(getattr(uav, "remaining_range", float("inf"))) + _EPS


# ── 机巢容量账（起飞释放原巢名额、落地占终点巢名额）────────────────


def _nest_loads(
    problem: Any,
    routes: Mapping[int, Sequence[int]],
    end_nests: Mapping[int, int | None],
) -> dict[int | None, int]:
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


def _capacity_enabled(problem: Any) -> bool:
    cons = getattr(problem.config, "constraints", None)
    return bool(getattr(cons, "nest_capacity", True))


# ── 两种一阶检验模式 ─────────────────────────────────────────────


def _check_open(
    problem: Any,
    uav: UAV,
    tid: int,
    loads: Mapping[int | None, int],
) -> bool:
    """模式 A：空闲机开单任务航次是否可行（航程 + 巢容量；窗口不否决）"""
    task = problem.task_by_id.get(tid)
    if task is None:
        return False
    if not _window_ok(problem, uav, [tid], 0):
        return False
    cap_on = _capacity_enabled(problem)
    home = uav.nest_id

    def cap_ok(nid: int) -> bool:
        if not cap_on:
            return True
        n = problem.nest_by_id.get(nid)
        if n is None or not bool(getattr(n, "available", True)):
            return False
        delta = 1 - (1 if nid == home else 0)  # 起飞释放原巢名额
        return loads.get(nid, 0) + delta <= int(n.capacity)

    nid, cost = _best_end_nest(
        problem, uav, [tid],
        cap_ok=(cap_ok if cap_on else None),
    )
    return nid is not None and _range_ok(cost, uav)


def _check_insert(
    problem: Any,
    uav: UAV,
    seq: Sequence[int],
    end_nest: int | None,
    tid: int,
) -> bool:
    """模式 B：tid 插入现有航次是否存在可行位（全链航程；窗口不否决）。"""
    for pos in range(len(seq) + 1):
        cand = list(seq[:pos]) + [tid] + list(seq[pos:])
        end = end_nest
        if end is None:
            end, cost = _best_end_nest(problem, uav, cand)
        else:
            cost = _tour_cost(problem, uav, cand, end)
        if end is None or not _range_ok(cost, uav):
            continue
        if _window_ok(problem, uav, cand, pos):
            return True
    return False


# ── 主入口 ────────────────────────────────────────────────────────


def _iter_context(
    problem: Any,
    routes: Mapping[int, Sequence[int]],
    end_nests: Mapping[int, int | None],
):
    for u in problem.uavs:
        seq = list(routes.get(u.id) or [])
        yield u, seq, end_nests.get(u.id, u.nest_id)


def _classify(
    problem: Any,
    routes: Mapping[int, Sequence[int]],
    end_nests: Mapping[int, int | None],
    tid: int,
    loads: Mapping[int | None, int],
) -> bool:
    """tid 是否存在一阶可行插入（True = 能装却没装 ⇒ voluntary_skip）。"""
    for u, seq, end in _iter_context(problem, routes, end_nests):
        if not seq:
            if _check_open(problem, u, tid, loads):
                return True
        elif _check_insert(problem, u, seq, end, tid):
            return True
    return False


def audit_coverage(
    problem: Any,
    routes: Mapping[int, Sequence[int]],
    end_nests: Mapping[int, int | None],
    *,
    task_ids: Sequence[int] | None = None,
) -> GateStats:
    """纯检验不改解：S 内未交付任务逐个分账 voluntary_skip / capacity_shortfall。

    ``task_ids`` 缺省 = ``problem.tasks``（本周期已选集 S）。
    """
    s_ids = list(task_ids) if task_ids is not None else [t.id for t in problem.tasks]
    assigned = {tid for seq in routes.values() for tid in seq}
    loads = _nest_loads(problem, routes, end_nests)
    stats = GateStats()
    for tid in s_ids:
        if tid in assigned:
            continue
        if _classify(problem, routes, end_nests, tid, loads):
            stats.n_skip += 1
            stats.skip_ids.append(int(tid))
        else:
            stats.n_shortfall += 1
            stats.shortfall_ids.append(int(tid))
    return stats


def repair_coverage(
    problem: Any,
    routes: Mapping[int, Sequence[int]],
    end_nests: Mapping[int, int | None],
    *,
    task_ids: Sequence[int] | None = None,
    max_rounds: int = 8,
) -> tuple[dict[int, list[int]], dict[int, int | None], GateStats]:
    """能救必救，修到不动点（覆盖门禁的构造性实现）。

    - 模式 B 优先（填充现有航次，取全链代价最小位），模式 A 兜底
      （空闲机开班，终点巢取容量可行的最近巢）；
    - deferred 按 deadline 升序（EDD，最紧急先救；仅排序启发式，不判废）；
    - 不做挤出（插入只要求全链航程可行，不破坏他人硬约束，
      迟到一律进 T2 计价），因此通常一轮收敛；``max_rounds`` 仅防御性上限。
    - 返回的 gate_stats：``n_skip`` = 修复后仍存在可行插入却未装的残值
      （正常恒 0；非 0 = 轮数耗尽/未收敛的防御信号），
      ``n_shortfall`` = 一阶检验能力不足（允许不交付）。
    """
    work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
    ends: dict[int, int | None] = dict(end_nests)
    for u in problem.uavs:
        work.setdefault(u.id, [])
        ends.setdefault(u.id, u.nest_id)

    s_ids = list(task_ids) if task_ids is not None else [t.id for t in problem.tasks]
    rescued = 0
    rounds = 0
    for _ in range(max(1, int(max_rounds))):
        rounds += 1
        assigned = {tid for seq in work.values() for tid in seq}
        deferred = [tid for tid in s_ids if tid not in assigned]
        if not deferred:
            break
        deferred.sort(key=lambda t: (_task_deadline(problem, t) or float("inf"), t))
        changed = False
        for tid in deferred:
            loads = _nest_loads(problem, work, ends)
            plan = _best_insert_plan(problem, work, ends, tid, loads)
            if plan is None:
                continue
            kind, uid, pos, nest = plan
            if kind == "insert":
                work[uid].insert(pos, tid)
            else:
                work[uid] = [tid]
                ends[uid] = nest
            rescued += 1
            changed = True
        if not changed:
            break

    stats = audit_coverage(problem, work, ends, task_ids=s_ids)
    stats.n_rescued = rescued
    stats.rounds = rounds
    return work, ends, stats


def _best_insert_plan(
    problem: Any,
    routes: Mapping[int, Sequence[int]],
    end_nests: Mapping[int, int | None],
    tid: int,
    loads: Mapping[int | None, int],
) -> tuple[str, int, int, int | None] | None:
    """最优可行安置：``("insert", uav, pos, None)`` 或 ``("open", uav, 0, nest)``。

    模式 B 全链代价最小优先；模式 A 兜底（含巢容量账）。
    """
    task = problem.task_by_id.get(tid)
    if task is None:
        return None
    best: tuple[str, int, int, int | None] | None = None
    best_cost = float("inf")
    for u, seq, end in _iter_context(problem, routes, end_nests):
        if not seq:
            continue
        for pos in range(len(seq) + 1):
            cand = list(seq[:pos]) + [tid] + list(seq[pos:])
            use_end = end
            if use_end is None:
                use_end, cost = _best_end_nest(problem, u, cand)
            else:
                cost = _tour_cost(problem, u, cand, use_end)
            if use_end is None or not _range_ok(cost, u):
                continue
            if not _window_ok(problem, u, cand, pos):
                continue
            if cost < best_cost - 1e-12:
                best_cost = cost
                best = ("insert", u.id, pos, None)
    if best is not None:
        return best
    # 模式 A：空闲机开班
    for u, seq, _ in _iter_context(problem, routes, end_nests):
        if seq:
            continue
        if not _window_ok(problem, u, [tid], 0):
            continue
        cap_on = _capacity_enabled(problem)
        home = u.nest_id

        def cap_ok(nid: int, _home: int | None = home) -> bool:
            if not cap_on:
                return True
            n = problem.nest_by_id.get(nid)
            if n is None or not bool(getattr(n, "available", True)):
                return False
            delta = 1 - (1 if nid == _home else 0)
            return loads.get(nid, 0) + delta <= int(n.capacity)

        nid, cost = _best_end_nest(
            problem, u, [tid], cap_ok=(cap_ok if cap_on else None)
        )
        if nid is None or not _range_ok(cost, u):
            continue
        if cost < best_cost - 1e-12:
            best_cost = cost
            best = ("open", u.id, 0, nid)
    return best
