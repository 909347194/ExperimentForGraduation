# -*- coding: utf-8 -*-
"""exp01_smoke 审计：逐航次契约 + 闭环账 + 真场/高度合规。

判据：原有 6 条契约不变（t0 允许实验侧错峰偏移），另加真场生效、
任务全完成、入口未拒、AGL/绝对高度与净空合规。不改算法、不写文件。
"""

from __future__ import annotations

from typing import Any


def audit_trace(
    trace: Any,
    cfg: dict[str, Any],
    *,
    tasks: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    scene_meta: dict[str, Any] | None = None,
    allowed_offsets: set[float] | None = None,
    fleet_ids: list[int] | None = None,
) -> list[str]:
    problems: list[str] = []
    from .tasks import task_tags, tag_of_id
    tags = task_tags(tasks) if tasks else {}

    def _join(items) -> str:
        return "、".join(str(i) for i in items) if items else "无"

    tol = float(cfg["loop"]["overrun_tol_s"])
    audit_cfg = cfg["audit"]
    offsets = allowed_offsets or {0.0}

    if not trace.cycles:
        return ["环没有产出任何周期"]

    last_cycle = int(trace.cycles[-1].cycle)
    for plan in trace.cycles:
        tickets = plan.report.tickets
        if not tickets:
            # 末尾空周期是环的收手信号（本周期无航次、也无新任务可派），不算缺陷；
            # 中途空周期意味着"还有池子在等"，那才是编排缺陷。
            if int(plan.cycle) != last_cycle:
                problems.append(f"周期 {plan.cycle}：没有航次（该周期无航次）")
            continue
        if set(tickets) != set(plan.est_cost):
            problems.append(
                f"周期 {plan.cycle}：航次与估计代价机号不一致 "
                f"{sorted(tickets)} vs {sorted(plan.est_cost)}"
            )
        expect_overrun = {
            int(uid) for uid, t in tickets.items()
            if t.ok and float(t.t_end) > float(plan.t_end) + tol
        }
        if set(int(v) for v in plan.overruns) != expect_overrun:
            problems.append(
                f"周期 {plan.cycle}：overruns={list(plan.overruns)} 与自算 "
                f"{sorted(expect_overrun)} 不一致"
            )
        for uid, t in tickets.items():
            uid = int(uid)
            if t.ok:
                delta = float(t.t0) - float(plan.t_start)
                if not any(abs(delta - off) <= 1e-6 for off in offsets):
                    problems.append(
                        f"周期 {plan.cycle} 机 {uid}：t0={t.t0:.3f} 相对周期起点偏移 "
                        f"{delta:.3f} 不在允许错峰集合 {sorted(offsets)}"
                    )
                if abs((float(t.t0) + float(t.duration_s)) - float(t.t_end)) > 1e-6:
                    problems.append(f"周期 {plan.cycle} 机 {uid}：t_end 与 t0+duration 不符")
                if audit_cfg.get("require_gate_ok") and not (t.gate or {}).get("ok", False):
                    problems.append(f"周期 {plan.cycle} 机 {uid}：交付闸未通过 gate={t.gate}")
                busy = float(plan.busy_until.get(uid, 0.0))
                if busy + 1e-6 < float(t.t_end):
                    problems.append(
                        f"周期 {plan.cycle} 机 {uid}：busy_until={busy:.2f} < t_end={t.t_end:.2f}"
                    )
            else:
                if audit_cfg.get("require_all_tickets_ok"):
                    problems.append(f"周期 {plan.cycle} 机 {uid}：航次失败 {t.failure}")
                if audit_cfg.get("require_failure_detail", True) and not str(
                    getattr(t, "detail", "") or ""
                ).strip():
                    problems.append(
                        f"周期 {plan.cycle} 机 {uid}：失败航次没带 detail，事后无法自查原因"
                    )
                if float(plan.busy_until.get(uid, 0.0)) > 1e-9:
                    problems.append(f"周期 {plan.cycle} 机 {uid}：失败航次却占了 busy_until")
                if not plan.rolled_back:
                    problems.append(f"周期 {plan.cycle} 机 {uid}：失败但任务没回池")

    for cycle in trace.overrun_cycles:
        plan = next(p for p in trace.cycles if int(p.cycle) == int(cycle))
        if not plan.overruns:
            problems.append(f"overrun_cycles 里的周期 {cycle} 没有超框机号")

    # ---- 闭环账（v3：只核对"重解 = 事实回灌"，不再核对 λ 回填）-----------
    for plan in trace.cycles:
        update = getattr(plan, "cost_update", None)
        n_resolves = int(getattr(plan, "n_resolves", 0) or 0)
        if update is not None and update.is_empty:
            problems.append(f"周期 {plan.cycle}：回灌单为空却记了一次重解")
        if update is not None and n_resolves < 1:
            problems.append(f"周期 {plan.cycle}：有回灌单但没发生重解")
        if n_resolves > 1:
            problems.append(
                f"周期 {plan.cycle}：重解 {n_resolves} 次，"
                "超出『同周期只回灌一次』的界定"
            )
        # 回灌唯一可被证伪的一条：写进代价的不可飞边必须真的从终版里消失。
        # 只核对"重解决了"是结构性的（环自己保证），查不出"写错了一条不存在的键"
        # 这种静默失效；故环把"写了却没落地"的边如实记下来，这里只判空。
        survived = tuple(getattr(plan, "infeasible_but_survived", ()) or ())
        if survived:
            problems.append(
                f"周期 {plan.cycle}：写进代价的不可飞边仍出现在最终解里 "
                f"{_join(sorted(survived))}——回灌未落地（多半是边的键翻错）"
            )
        # 重解抛过异常就是"没重解成"，账上不许算作成功的一版。
        err = str(getattr(plan, "resolve_error", "") or "")
        if err:
            problems.append(
                f"周期 {plan.cycle}：周期内重解抛出异常，已退回首解交付（{err}）"
            )

    if audit_cfg.get("require_reassignment"):
        if not reassigned_tasks(trace):
            problems.append("没有任何任务在后续周期改派到别的机（重投链路没跑通）")

    # ---- 任务全完成（按"有航迹"计，不按"派出"计）--------------------
    if audit_cfg.get("require_full_completion") and tasks:
        all_ids = {int(t["id"]) for t in tasks}
        flown = {
            int(tid)
            for plan in trace.cycles
            for t in plan.report.tickets.values() if t.ok
            for tid in t.task_ids
        }
        if all_ids != flown:
            problems.append(
                f"任务未全部交付航迹：{len(flown)}/{len(all_ids)} 有航迹，"
                f"缺 {_join([tag_of_id(i, tags) for i in sorted(all_ids - flown)[:10]])}"
            )

    # ---- 闲置机与未完成任务不得并存（用户契约）------------------------
    # 「闲置」本身合法（不要求全员上阵）；违规的是**同时**出现
    # 「有任务从没飞」和「有机整场没起飞」——那说明上层把可用运力留在巢里、
    # 却让任务留在池里，是编排缺陷而非能力不足。
    if audit_cfg.get("require_no_idle_with_pending", True):
        if fleet_ids:
            flown, unflown, idle = _delivery_and_idle(trace, tasks, fleet_ids)
            if unflown and idle:
                problems.append(
                    f"闲置机 {_join([f'机{i}' for i in sorted(idle)])} 与未完成任务 "
                    f"{_join([tag_of_id(i, tags) for i in sorted(unflown)])} 并存："
                    "可用运力留在巢里，任务却从未排上"
                )
            # 单侧如实报：未完成但有任务飞行、全部完成但有闲置 —— 都合法
    if audit_cfg.get("require_endpoint_compliant"):
        for plan in trace.cycles:
            for uid, ticket in plan.report.tickets.items():
                if not ticket.ok and "ENDPOINT_OUT_OF_BAND" in str(ticket.failure):
                    problems.append(
                        f"周期 {plan.cycle} 机 {uid}：点位被 methods 入口拒绝 "
                        f"（{ticket.failure}）"
                    )
        if tasks:
            agl_lo, agl_hi = (float(v) for v in cfg["mission_waypoints"]["agl_range_m"])
            clear = float(cfg["mission_waypoints"]["endpoint_clearance_m"])
            z_band = context.get("alt_range_m") if context else None
            for t in tasks:
                agl = float(t["agl_m"])
                z = float(t["z"])
                if not (agl_lo <= agl <= agl_hi):
                    problems.append(f"任务 {tag_of_id(t['id'], tags)}：AGL {agl:.2f} 越出真高带")
                if z_band is not None and not (float(z_band[0]) <= z <= float(z_band[1])):
                    problems.append(
                        f"任务 {tag_of_id(t['id'], tags)}：绝对 z {z:.2f} 越出绝对高度带 {z_band}")
                if float(t["sdf_m"]) < clear or float(t["prism_m"]) < clear:
                    problems.append(
                        f"任务 {tag_of_id(t['id'], tags)}：净空 sdf={t['sdf_m']} "
                        f"prism={t['prism_m']} < {clear}"
                    )
    if scene_meta is not None:
        if int(scene_meta.get("building_count", 0)) <= 0:
            problems.append("真场未生效：建筑数为 0")
        if int(scene_meta.get("nests", 0)) < len(cfg["data"]["active_nest_ids"]):
            problems.append("真场未生效：机巢数不足")
    return problems


def _delivery_and_idle(
    trace: Any, tasks: list[dict[str, Any]] | None, fleet_ids: list[int]
) -> tuple[set[int], set[int], set[int]]:
    """``(已飞任务, 未飞任务, 闲置机)``：都按「成功航次」口径，同一把尺子。

    已飞 = 出现于任一成功航次 ``task_ids``；未飞 = 任务清单里其余全部；
    闲置 = 整场没有任何成功航次的机（``fleet_ids`` 减去出场机号）。
    任务清单缺省时只算闲置，不判「未完成」。
    """
    flown: set[int] = set()
    seen_uavs: set[int] = set()
    for plan in trace.cycles:
        for uid, t in plan.report.tickets.items():
            if not t.ok:
                continue
            seen_uavs.add(int(uid))
            flown.update(int(tid) for tid in t.task_ids)
    unflown = {int(t["id"]) for t in (tasks or [])} - flown
    idle = {int(uid) for uid in fleet_ids} - seen_uavs
    return flown, unflown, idle


def reassigned_tasks(trace: Any) -> list[tuple[int, int, int, int, int]]:
    """同一**任务**在后续周期改派到别的机：``(前周期, 前机, 后周期, 后机, 任务)``。

    按任务 id 记账：只有同一任务真的被两台机先后承载才算一条。
    """
    seen: dict[int, tuple[int, int]] = {}
    out: list[tuple[int, int, int, int, int]] = []
    for plan in trace.cycles:
        for uid, tids in plan.assignments.items():
            for tid in tids:
                tid = int(tid)
                if tid in seen and seen[tid][1] != int(uid):
                    out.append((seen[tid][0], seen[tid][1], int(plan.cycle), int(uid), tid))
                seen[tid] = (int(plan.cycle), int(uid))
    return out
