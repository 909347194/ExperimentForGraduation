# -*- coding: utf-8 -*-
"""exp01_smoke 落盘与表格：环账、估计/实测/回填、运行元信息、审计结论。

职责：只把 run.py 产出的对象写成 JSON / CSV 与可读表格；不计算新指标口径。
落盘约定（一次运行一个目录，目录名由 ``config.default_run_dir`` 给出）::

    results/seed_<N>/
        run_meta.json       配置快照 + 场 + 估计器 + 任务指纹
        loop_trace.json     逐周期逐航次账（含失败 detail / 回池 / 航迹摘要）
        failures.json       失败航次单列：失败码 + detail + 逐航段诊断（含 RRT 统计）
        metrics.json        单看就够的几个数（航次、排程、已到达、完成率）
        audit.json          审计判据快照 + 逐条问题（PASS 也写）
        trajectories.json   绘图用航迹折线（1 s 采样；自带起终点巢与经过的任务点）
        figures/            四族 8 张图（I01 / TA01–02 / PP01–03 / S01–02）+ CAPTIONS.md，
                            清单与图注见 plot/registry.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import track as track_mod
import re

from .tasks import fmt_tags, tag_of_id, task_tags


def _dump(path: Path, obj: Any) -> None:
    """原子写：先落 ``.tmp`` 再替换。中途崩掉也不会留半截 JSON。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_meta(
    out_dir: Path,
    *,
    cfg: dict[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    field_info: dict[str, Any],
    estimator_info: dict[str, Any],
    context: dict[str, Any],
    tasks_sha256: str = "",
) -> dict[str, Any]:
    """写 ``run_meta.json``（配置快照 + 场 + 估计器 + 任务指纹），返回该字典。

    与 ``write_trace`` 一样可在**周期 0 之前**调用：先立"这场是什么"，再逐周期
    追加账。这样即便中途崩掉，目录里也已经说得清"跑的是哪套配置"。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "experiment": cfg["experiment"]["name"],
        "field": field_info,
        "context": context,
        "estimator": estimator_info,
        "tasks": {
            "count": len(tasks),
            "sha256": str(tasks_sha256),
            # 按清单里真实出现的周期分组：批次结构不写死在配置里
            "per_cycle": {str(c): sum(1 for t in tasks if int(t["cycle"]) == c)
                          for c in sorted({int(t["cycle"]) for t in tasks})},
        },
        "config": cfg,
    }
    _dump(out_dir / "run_meta.json", meta)
    return meta


def write_trace(
    out_dir: Path,
    plans: Sequence[Any],
    *,
    trajectories: Mapping[int, Mapping[int, Any]] | None = None,
    failures: Mapping[int, str] | None = None,
    overrun_cycles: Sequence[int] | None = None,
) -> dict[str, Any]:
    """把**已经跑完**的周期写成 ``loop_trace.json``，返回该字典。

    ``run.py`` 每完成一个周期就调一次，所以长跑中途挂掉时，已跑周期仍然在盘上；
    收尾的 ``write_all`` 再用完整 ``trace`` 覆盖一次（内容一致，只是补齐汇总项）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    trajs = trajectories or {}
    doc = {
        "cycles": [_cycle(plan, trajs.get(int(plan.cycle), {})) for plan in plans],
        "executed_ids": [int(t) for p in plans for t in p.executed_ids],
        "overrun_cycles": [
            int(v) for v in (overrun_cycles if overrun_cycles is not None
                             else [p.cycle for p in plans if p.overruns])
        ],
        "failures": {int(k): str(v) for k, v in (failures or {}).items()},
    }
    _dump(out_dir / "loop_trace.json", doc)
    return doc


def cycle_table(trace: Any, tags: Mapping[int, str] | None = None) -> str:
    """环账表（逐航次时间来源、航程、失败原因摘要）。``tags`` = 原始 id → T#。"""
    tags = tags or {}
    lines = ["周期  机  估计m  真实m  真实s  时间来源  让路  冲突前→后  超框  任务"]
    for plan in trace.cycles:
        for uid in sorted(plan.report.tickets):
            t = plan.report.tickets[uid]
            tids = (fmt_tags(t.task_ids, tags) if tags
                    else ",".join(str(i) for i in t.task_ids)) or "-"
            lines.append(
                f"{plan.cycle:>4}  {uid:>2}  {plan.est_cost.get(uid, 0.0):>7.1f}"
                f"  {t.path_len_m:>7.1f}  {t.duration_s:>6.2f}  {t.time_source:<9}"
                f"  {plan.report.moved.get(uid, '-'):<6}"
                f"  {plan.report.conflicts_before:>3}→{plan.report.conflicts_after:<3}"
                f"  {'✗' if uid in plan.overruns else ' ':<2}  {tids}"
            )
    if trace.failures:
        lines.append(f"失败：{dict(trace.failures)}")
    return "\n".join(lines)


_STOP_ID = re.compile(r"stop=(\d+)")


def _tag_detail(text: str, tags: Mapping[int, str]) -> str:
    """下层 detail 里的 ``stop=203`` 也写成任务号，跟图上口径一致。"""
    if not tags:
        return text
    return _STOP_ID.sub(lambda m: tag_of_id(int(m.group(1)), tags), text)


def failure_table(trace: Any, tags: Mapping[int, str] | None = None) -> str:
    """失败航次单列：失败码 + 下层 detail（哪一段/哪个点越界）。"""
    tags = tags or {}
    rows = [
        (int(plan.cycle), int(uid), str(t.failure),
         _tag_detail(str(t.detail or "-"), tags))
        for plan in trace.cycles
        for uid, t in plan.report.tickets.items()
        if not t.ok
    ]
    if not rows:
        return "失败航次：无"
    lines = ["失败航次 周期  机  失败码  detail"]
    for cycle, uid, code, detail in rows:
        lines.append(f"       {cycle:>4}  {uid:>2}  {code}  {detail}")
    return "\n".join(lines)


def resolve_table(trace: Any, tags: Mapping[int, str] | None = None) -> str:
    """逐周期的可行性回灌账：写进几条不可飞边、几条实测边、重解几次、还剩几条。"""
    tags = tags or {}
    lines = ["周期  不可飞边  实测边  重解  中间失败  解  排程  到达  回池"]
    for plan in trace.cycles:
        update = plan.cost_update
        n_inf = len(update.infeasible) if update is not None else 0
        n_meas = len(update.facts) if update is not None else 0
        assign = " ".join(
            f"{uid}:{fmt_tags(tids, tags) if tags else ','.join(map(str, tids))}"
            for uid, tids in sorted(plan.assignments.items()) if tids
        ) or "-"
        ok = sum(1 for t in plan.report.tickets.values() if t.ok)
        flown = sum(len(t.task_ids) for t in plan.report.tickets.values() if t.ok)
        lines.append(
            f"{plan.cycle:>4}  {n_inf:>4}  {n_meas:>4}  {plan.n_resolves:>4}"
            f"  {plan.n_intermediate_failures:>8}  {plan.cost_version:<8}"
            f"  {len(plan.report.tickets)} 航次/{ok} 成"
            f"  {len(plan.executed_ids):>3}  {flown:>3}  {len(plan.reopened):>3}  {assign}"
        )
    return "\n".join(lines)


def _ticket(t: Any) -> dict[str, Any]:
    return {
        "uav_id": int(t.uav_id), "ok": bool(t.ok),
        "t0": float(t.t0), "t_end": float(t.t_end),
        "duration_s": float(t.duration_s), "path_len_m": float(t.path_len_m),
        "time_source": str(t.time_source), "failure": t.failure,
        "detail": str(t.detail or ""),
        "failure_diag": dict(getattr(t, "failure_diag", {}) or {}),
        "task_ids": [int(v) for v in t.task_ids],
        # 两端的机巢（下层 id）：航迹的起点 Base 与回收 Base，落盘而不靠坐标反推
        "start_nest_id": (int(t.start_nest_id) if getattr(t, "start_nest_id", None) is not None
                          else None),
        "end_nest_id": (int(t.end_nest_id) if getattr(t, "end_nest_id", None) is not None
                        else None),
        "gate": dict(t.gate or {}),
    }


def _attempt(att: Any) -> dict[str, Any]:
    """一版解的账（只留能对齐"哪一版用了哪张表"的字段，不复制全部航次）。"""
    return {
        "attempt": int(att.attempt_id),
        "snapshot": str(att.snapshot_id),
        "cost_version": str(att.cost_version),
        "est_cost": {int(k): round(float(v), 3) for k, v in att.est_cost.items()},
        "residual_m": {int(k): round(float(v), 3) for k, v in att.residuals.items()},
        "fails": {int(k): str(v) for k, v in att.failures.items()},
    }


def _track(traj: Any, ticket: Any, tags: Mapping[int, str]) -> dict[str, Any]:
    """一条航迹：1 s 折线 + 它自己的起终点机巢与经过的任务点。

    折线自带身份，出图与排查都不必再去 join ``loop_trace``；航次缺票时两端记
    ``None``（宁可显式缺失，也不拿坐标猜一个 Base 出来）。
    """
    tids = [int(v) for v in (getattr(ticket, "task_ids", ()) if ticket else ())]
    return {
        "start_nest_id": (int(ticket.start_nest_id)
                          if getattr(ticket, "start_nest_id", None) is not None else None),
        "end_nest_id": (int(ticket.end_nest_id)
                        if getattr(ticket, "end_nest_id", None) is not None else None),
        "task_ids": tids,
        "tags": [tag_of_id(tid, tags) for tid in tids],
        **track_mod.sample(traj),
    }


def _cycle(plan: Any, trajectories: Mapping[int, Any]) -> dict[str, Any]:
    return {
        "cycle": int(plan.cycle),
        "t_start": float(plan.t_start), "t_end": float(plan.t_end),
        "est_cost": {int(k): float(v) for k, v in plan.est_cost.items()},
        "tickets": {int(k): _ticket(v) for k, v in plan.report.tickets.items()},
        "moved": {int(k): str(v) for k, v in plan.report.moved.items()},
        "conflicts_before": int(plan.report.conflicts_before),
        "conflicts_after": int(plan.report.conflicts_after),
        "unplanned": [int(v) for v in plan.report.unplanned],
        "busy_until": {int(k): float(v) for k, v in plan.busy_until.items()},
        "executed_ids": [int(v) for v in plan.executed_ids],
        "assignments": {int(k): [int(v) for v in vals]
                        for k, vals in plan.assignments.items()},
        "cost_version": str(getattr(plan, "cost_version", "base")),
        # 写进代价的事实：不可飞边（下层 id）与实测长度边数
        "infeasible_edges": [
            [int(a), int(b)]
            for a, b in sorted(getattr(plan, "cost_update", None).infeasible
                               if getattr(plan, "cost_update", None) else ())
        ],
        "n_measured_edges": int(
            len(getattr(plan, "cost_update", None).facts)
            if getattr(plan, "cost_update", None) else 0
        ),
        "unresolved_infeasible_edges": [
            [int(a), int(b)] for a, b in plan.unresolved_infeasible_edges
        ],
        # 写进代价却没生效的不可飞边（应为空；非空 = 回灌没落地，审计据此判 FAIL）
        "infeasible_but_survived": [
            [int(a), int(b)] for a, b in getattr(plan, "infeasible_but_survived", ())
        ],
        # 重解抛出异常时的原文（非空 = 本周期退回首解交付，审计判 FAIL）
        "resolve_error": str(getattr(plan, "resolve_error", "") or ""),
        "overruns": [int(v) for v in plan.overruns],
        "pruned": [int(v) for v in plan.pruned],
        "rolled_back": [int(v) for v in plan.rolled_back],
        "reopened": [int(v) for v in plan.reopened],
        "trajectories": {
            int(uid): {
                "path_len_m": float(traj.path_len_m),
                "duration_s": float(traj.duration),
                "time_source": str(traj.time_source.value),
                "n_segments": int(len(traj.segments)),
            }
            for uid, traj in sorted(trajectories.items())
        },
    }


def task_book(tasks: Sequence[Mapping[str, Any]], trace: Any,
              uav_ids: Iterable[int]) -> dict[str, Any]:
    """任务账：已排程 / 已到达 / 从未排程，以及两种「闲置」分开记。

    ``idle_uavs`` 与审计同尺：整场没有任何**成功航次**的机（飞了但全失败也算闲置）；
    ``never_dispatched_uavs`` 是整场没被派过任何航次的机。两个口径不能混用：
    PP02 的「全程无航次」看后者，审计契约与 console 的「闲置机」看前者。
    """
    all_ids = [int(t["id"]) for t in tasks]
    assigned: set[int] = set()
    flown: set[int] = set()
    ok_uavs: set[int] = set()
    for plan in trace.cycles:
        for uid, tids in plan.assignments.items():
            assigned.update(int(t) for t in tids)
        for uid, t in plan.report.tickets.items():
            if t.ok:
                ok_uavs.add(int(uid))
                flown.update(int(i) for i in t.task_ids)
    dispatched = {int(u) for plan in trace.cycles
                  for u, tids in plan.assignments.items() if tids}
    fleet = {int(u) for u in uav_ids}
    return {
        "total": len(all_ids),
        "assigned_ids": sorted(assigned),
        "flown_ids": sorted(flown),
        "never_assigned_ids": sorted(set(all_ids) - assigned),
        "unflown_ids": sorted(set(all_ids) - flown),
        "idle_uavs": sorted(fleet - ok_uavs),
        "never_dispatched_uavs": sorted(fleet - dispatched),
        "completion_rate": (len(flown) / len(all_ids)) if all_ids else 0.0,
    }


def write_all(
    out_dir: Path,
    *,
    cfg: dict[str, Any],
    trace: Any,
    tasks: list[dict[str, Any]],
    field_info: dict[str, Any],
    estimator_info: dict[str, Any],
    context: dict[str, Any],
    trajectories: Mapping[int, Mapping[int, Any]],
    problems: Sequence[str],
    uav_ids: Iterable[int],
    tasks_sha256: str = "",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_trace(out_dir, trace.cycles, trajectories=trajectories,
                failures=trace.failures, overrun_cycles=trace.overrun_cycles)

    # 失败单列成一份：排查只看这里，不必在多周期的 trace 里翻。
    failed = [
        {
            "cycle": int(plan.cycle),
            "uav_id": int(uid),
            "failure": str(t.failure),
            "detail": str(t.detail or ""),
            "task_ids": [int(v) for v in t.task_ids],
            # 断在哪一程：起点巢 / 应回收巢（失败航次没有航迹，只能靠这两端定位）
            "start_nest_id": (int(t.start_nest_id)
                              if getattr(t, "start_nest_id", None) is not None else None),
            "end_nest_id": (int(t.end_nest_id)
                            if getattr(t, "end_nest_id", None) is not None else None),
            "rolled_back": [int(v) for v in plan.rolled_back],
            "reopened": [int(v) for v in plan.reopened],
            "diagnostics": dict(getattr(t, "failure_diag", {}) or {}),
        }
        for plan in trace.cycles
        for uid, t in sorted(plan.report.tickets.items())
        if not t.ok
    ]
    _dump(out_dir / "failures.json", {"count": len(failed), "failures": failed})

    write_meta(
        out_dir, cfg=cfg, tasks=tasks, field_info=field_info,
        estimator_info=estimator_info, context=context,
        tasks_sha256=tasks_sha256,
    )

    book = task_book(tasks, trace, uav_ids)
    metrics = {
        "cycles": len(trace.cycles),
        "tickets_ok": sum(1 for p in trace.cycles for t in p.report.tickets.values() if t.ok),
        "tickets_total": sum(len(p.report.tickets) for p in trace.cycles),
        "assigned_tasks": len(book["assigned_ids"]),
        "flown_tasks": len(book["flown_ids"]),
        "completion_rate": round(float(book["completion_rate"]), 4),
        "task_total": book["total"],
        "flown_ids": book["flown_ids"],
        "never_assigned_ids": book["never_assigned_ids"],
        "unflown_ids": book["unflown_ids"],
        "idle_uavs": book["idle_uavs"],
        "never_dispatched_uavs": book["never_dispatched_uavs"],
        "overrun_cycles": [int(v) for v in trace.overrun_cycles],
        "failures": {int(k): str(v) for k, v in trace.failures.items()},
        # 周期内重解账：次数 / 中间版失败 / 写进代价的不可飞边 / 残留不可飞边 / 最终代价版本
        "total_resolves": sum(
            max(0, len(p.attempts) - 1) if p.attempts else 0 for p in trace.cycles
        ),
        "intermediate_failures": sum(
            int(getattr(p, "n_intermediate_failures", 0)) for p in trace.cycles
        ),
        "resolve_cycles": [int(c) for c in trace.resolve_cycles],
        # 写进代价的不可飞边条数
        "infeasible_edges_written": sum(
            len(p.cost_update.infeasible) if p.cost_update is not None else 0
            for p in trace.cycles
        ),
        # 写进代价的实测长度条数（已飞航段）
        "measured_edges_written": sum(
            len(p.cost_update.facts) if p.cost_update is not None else 0
            for p in trace.cycles
        ),
        "unresolved_infeasible_edges": sum(
            len(p.unresolved_infeasible_edges) for p in trace.cycles
        ),
        # 写进代价却没落地的不可飞边：应为 0。非 0 说明回灌没生效（审计据此 FAIL）。
        "infeasible_but_survived": sum(
            len(getattr(p, "infeasible_but_survived", ())) for p in trace.cycles
        ),
        # 周期内重解抛异常的次数：非 0 说明这些周期退回了首解交付。
        "resolve_errors": [
            {"cycle": int(p.cycle), "error": str(p.resolve_error)}
            for p in trace.cycles if str(getattr(p, "resolve_error", "") or "")
        ],
        "final_cost_version": str(
            getattr(trace.cycles[-1], "cost_version", "base") if trace.cycles else "base"
        ),
        "tasks_sha256": str(tasks_sha256),
    }
    _dump(out_dir / "metrics.json", metrics)

    audit = {
        "passed": not problems,
        "problems": list(problems),
        "criteria": dict(cfg["audit"]),
    }
    _dump(out_dir / "audit.json", audit)

    tags = task_tags(tasks)
    tickets_of = {int(p.cycle): p.report.tickets for p in trace.cycles}
    sampled = {
        str(cycle): {
            str(uid): _track(traj, tickets_of.get(int(cycle), {}).get(int(uid)), tags)
            for uid, traj in sorted(trajs.items())
        }
        for cycle, trajs in sorted(trajectories.items())
    }
    _dump(out_dir / "trajectories.json", sampled)
    return metrics
