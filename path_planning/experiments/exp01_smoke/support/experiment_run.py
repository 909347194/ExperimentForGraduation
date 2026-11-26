# -*- coding: utf-8 -*-
"""实验主流程：两周期多机 tour 规划 + 跨周期既有航迹协调 → 指标 → 落盘（本实验私有）。

职责：每个周期都走生产门面 ``plan_fleet``（本实验不再自己拼冲突检测输入）：
1. 周期1：同批多机规划 + 新—新协调，交付航迹注册进 ``TrajectoryRegistry``；
2. 周期2：先 ``prune`` 落地航迹，再由 ``plan_fleet`` 自动取 Registry 中仍在飞的
   旧航迹作为冻结障碍（``movable_ids`` 只含本批新机）→ 新—旧冲突消解 → 回写；
3. 逐周期指标 + 跨周期证据（触发的冲突对、旧侧冻结、时间重叠、busy_until/prune 账）落盘。

这一条正是本实验要检验的设计：门面自己完成"新—旧"接线，旧侧冻结靠显式白名单，
**不靠机号大小**（本实验故意让旧机机号 5/6 大于新机 3/4）。

边界：不装配运行时（``runtime.py``）、不审计（``audit_checks.py`` / ``audit.py``）、
      不画图。点位预检在 ``run.py``（跑之前把选址口径守住）。
相关：``path_planning/api.py``、``methods/trajectory_registry.py``、
      ``support/flight_metrics.py``、``support/result_store.py``。
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from path_planning.common.plan_types import Trajectory
from path_planning.methods.conflict.conflict_detector import DetectionReport
from .flight_metrics import fleet_metrics
from .result_store import dump_json, plan_result_to_dict, trajectory_to_dict
from .runtime import Runtime

__all__ = ["CycleRun", "print_summary", "run_cycles_experiment"]


@dataclass(frozen=True)
class CycleRun:
    """一周期的运行结果（给 ``run.py`` 打印/落盘用）。"""

    index: int
    results: Mapping[int, Any]                 # uid → PlanResult
    delivered: Mapping[int, Trajectory]        # uid → 最终交付航迹
    changed: Mapping[int, Trajectory]          # 被让路改写过的机号
    outcome: Any                               # CoordinationOutcome | None
    pre_report: DetectionReport | None         # 周期2+：协调前检测（触发证据）
    existing_before: tuple[Trajectory, ...]    # 周期2+：Registry 快调（旧侧）
    old_changed: tuple[int, ...]               # 旧侧被改动的机号（应恒为空）
    pruned: tuple[int, ...]                    # 本周期开始前落地结算的机号
    frozen_ids: tuple[int, ...] = ()           # 门面显式冻结的旧侧机号


def run_cycles_experiment(
    runtime: Runtime,
    fleet,
    out_dir: str | Path,
    *,
    meta_extra: Mapping[str, Any] | None = None,
) -> tuple[dict[int, CycleRun], dict[str, Any], dict[str, str]]:
    """跑全部周期并落盘；返回 ``(逐周期结果, metrics, 文件路径)``。"""
    out_dir = Path(out_dir)
    t_start = time.time()
    planner = runtime.planner
    cycle = float(fleet.cycle_length_s)

    runs: dict[int, CycleRun] = {}
    traj_dicts: dict[str, Any] = {}
    trace_cycles: list[dict[str, Any]] = []
    metrics_cycles: dict[str, Any] = {}
    cross: dict[str, Any] = {
        "cycle_length_s": cycle,
        "pruned": {},
        "existing_before": {},
        "pre_conflicts": {},
        "old_frozen": {},
        "frozen_ids": {},
        "overlap_s": {},
        "busy_until_after": {},
    }

    for r_idx in sorted(fleet.cycles):
        reqs = fleet.cycles[r_idx]
        now = (r_idx - 1) * cycle
        pruned = planner.prune(now)
        cross["pruned"][str(r_idx)] = list(pruned)

        # 生产门面自己取 Registry 旧航迹、自己冻结旧侧；实验只记录证据。
        plan = planner.plan_fleet(reqs)
        results = plan.results
        delivered = {
            uid: traj for uid in plan.results
            if (traj := plan.trajectory_of(uid)) is not None
        }
        changed = dict(plan.changed)
        outcome = plan.outcome
        pre_report = plan.pre_report
        existing_before = plan.existing
        # 旧侧机号可比新侧更大；只要被改就是编排出了问题，如实记录（audit 判失败）。
        old_changed = tuple(
            sorted(
                int(t.uav_id) for t in existing_before
                if outcome is not None
                and outcome.trajectories.get(int(t.uav_id)) is not t
            )
        )

        runs[r_idx] = CycleRun(
            index=r_idx, results=results, delivered=delivered, changed=changed,
            outcome=outcome, pre_report=pre_report, existing_before=existing_before,
            old_changed=old_changed, pruned=pruned,
            frozen_ids=tuple(plan.frozen_ids),
        )

        # ---- 落盘素材 ----
        for uid, traj in sorted(delivered.items()):
            traj_dicts[f"{r_idx}:{uid}"] = trajectory_to_dict(traj)

        trace_cycles.append(_trace_payload(r_idx, reqs, runs[r_idx], cycle))
        metrics_cycles[str(r_idx)] = fleet_metrics(
            delivered,
            vehicles=fleet.vehicles,
            requests=reqs,
            field=runtime.field,
            prisms=runtime.prisms,
            params=runtime.config.params,
            outcome=outcome,
            changed_ids=changed,
            alt_range_m=runtime.ctx.alt_range_m,
        )
        cross["existing_before"][str(r_idx)] = [t.uav_id for t in existing_before]
        cross["pre_conflicts"][str(r_idx)] = _pre_conflicts(pre_report, existing_before)
        cross["old_frozen"][str(r_idx)] = {
            "changed": list(old_changed), "ok": not old_changed,
        }
        cross["frozen_ids"][str(r_idx)] = list(plan.frozen_ids)
        cross["overlap_s"][str(r_idx)] = _overlaps(delivered, existing_before)
        cross["busy_until_after"][str(r_idx)] = {
            str(uid): planner.busy_until(uid) for uid in sorted(delivered)
        }

    elapsed = time.time() - t_start
    metrics = {"cycles": metrics_cycles, "cross_cycle": cross}
    solution = {
        "experiment": runtime.config.name,
        "cycle_length_s": cycle,
        "cycles": {
            str(r.index): {
                "t0": (r.index - 1) * cycle,
                "results": {
                    str(uid): plan_result_to_dict(
                        # 落盘必须是最终交付几何（协调层换了让路几何后 PlanResult
                        # 里仍挂首周期原航迹，照原对象写出去审计就会读到会撞的线）
                        dataclasses.replace(res, trajectory=r.delivered.get(uid, res.trajectory))
                    )
                    for uid, res in r.results.items()
                },
                "changed": sorted(int(k) for k in r.changed),
                "existing_before": [t.uav_id for t in r.existing_before],
                "old_changed": list(r.old_changed),
                "frozen_ids": list(r.frozen_ids),
                "pruned": list(r.pruned),
                "registry_busy_until": cross["busy_until_after"][str(r.index)],
                "coordination": _coordination(r.outcome),
            }
            for r in runs.values()
        },
    }
    run_meta = {
        "experiment": runtime.config.name,
        "seed": int(runtime.ctx.seed),
        "cycle_length_s": cycle,
        "cycles": [
            {
                "index": r_idx,
                "t0": (r_idx - 1) * cycle,
                "uavs": [req.uav_id for req in fleet.cycles[r_idx]],
                "stops": [
                    {"uav_id": req.uav_id,
                     "stops": [{"id": s.id, "kind": s.kind,
                                "position": list(s.position),
                                "service_time_s": s.service_time_s}
                               for s in req.stops]}
                    for req in fleet.cycles[r_idx]
                ],
            }
            for r_idx in sorted(fleet.cycles)
        ],
        "field": runtime.field_info,
        "field_summary": runtime.field.summary(),
        "timings_s": {**runtime.timings_s, "cycles_s": round(elapsed, 3)},
        **({} if meta_extra is None else dict(meta_extra)),
    }

    files = write_outputs(
        out_dir,
        runtime=runtime,
        solution=solution,
        metrics=metrics,
        run_meta=run_meta,
        trace={"cycles": trace_cycles, "elapsed_s": round(elapsed, 3)},
        trajectories=traj_dicts,
    )
    return runs, metrics, files


def write_outputs(
    out_dir: Path,
    *,
    runtime: Runtime,
    solution: Mapping[str, Any],
    metrics: Mapping[str, Any],
    run_meta: Mapping[str, Any],
    trace: Mapping[str, Any],
    trajectories: Mapping[str, Any],
) -> dict[str, str]:
    """六份 JSON 落盘（含多周期专属的 cycle_trace / cycle_trajectories）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "solution": out_dir / "solution.json",
        "metrics": out_dir / "metrics.json",
        "cycle_trace": out_dir / "cycle_trace.json",
        "cycle_trajectories": out_dir / "cycle_trajectories.json",
        "run_meta": out_dir / "run_meta.json",
        "effective_config": out_dir / "effective_config.json",
    }
    dump_json(paths["solution"], solution)
    dump_json(paths["metrics"], metrics)
    dump_json(paths["cycle_trace"], trace)
    dump_json(paths["cycle_trajectories"], trajectories)
    dump_json(paths["run_meta"], run_meta)
    dump_json(paths["effective_config"], _effective_config(runtime))
    return {k: str(v) for k, v in paths.items()}


def _effective_config(runtime: Runtime) -> dict[str, Any]:
    """本次实际运行的完整参数快照（含解析后的场网格与飞行带）。"""
    cfg = runtime.config
    return {
        "experiment": cfg.name,
        "seeds": list(cfg.seeds),
        "scenario": str(cfg.scenario.data_dir),
        "field": {
            "spacing_m": list(cfg.field.spacing_m),
            "alt_max_m": cfg.field.alt_max_m,
            "pad_m": cfg.field.pad_m,
            "include_static_nofly": cfg.field.include_static_nofly,
            "sample_bias_m": cfg.field.sample_bias_m,
        },
        "corridor": {
            "half_width_m": cfg.corridor.half_width_m,
            "width_ratio": cfg.corridor.width_ratio,
            "half_width_min_m": cfg.corridor.half_width_min_m,
            "margin_m": cfg.corridor.margin_m,
        },
        "mission_waypoints": {
            "nest_hover_agl_m": cfg.mission_waypoints.nest_hover_agl_m,
            "task_clearance_agl_m": cfg.mission_waypoints.task_clearance_agl_m,
        },
        "resolved": {
            "field_grid": {
                "origin": list(runtime.field.grid.origin),
                "spacing": list(runtime.field.grid.spacing),
                "shape": list(runtime.field.grid.shape),
            },
            "alt_range_m": list(runtime.ctx.alt_range_m),
        },
        "planning": cfg.params.as_dict(),
        "runtime": {
            "e_geo_m": cfg.e_geo_m,
            "endpoint_clearance_m": cfg.endpoint_clearance_m,
            "endpoint_exempt_radius_m": cfg.endpoint_exempt_radius_m,
            "cycle_length_s": cfg.cycle_length_s,
            "depart_stagger_s": cfg.depart_stagger_s,
        },
    }


# ---------------------------------------------------------------------------
# 报告片段
# ---------------------------------------------------------------------------


def _pre_conflicts(
    report: DetectionReport | None, existing: tuple[Trajectory, ...]
) -> list[dict[str, Any]]:
    """协调前的冲突对（触发证据）：区分新-旧 / 新-新，记最近距离与 CPA 时刻。"""
    if report is None:
        return []
    old_ids = {t.uav_id for t in existing}
    out = []
    for p in report.conflicts:
        kinds = sorted({"old" if uid in old_ids else "new" for uid in p.ids})
        out.append({
            "pair": list(p.ids),
            "kind": "new_old" if kinds == ["new", "old"] else "new_new",
            "min_distance": None if p.min_distance == float("inf") else round(p.min_distance, 3),
            "cpa_time": None if p.cpa_time is None else round(p.cpa_time, 3),
            "radius": round(p.radius, 3),
        })
    return out


def _overlaps(
    delivered: Mapping[int, Trajectory], existing: tuple[Trajectory, ...]
) -> dict[str, float]:
    """新航迹与既有航迹的时间窗重叠量（>0 = 确在"在飞中规划"）。"""
    out: dict[str, float] = {}
    for uid, traj in sorted(delivered.items()):
        best = 0.0
        for old in existing:
            lo = max(traj.t0, old.t0)
            hi = min(traj.t_end, old.t_end)
            best = max(best, hi - lo)
        out[str(uid)] = round(best, 3)
    return out


def _coordination(outcome: Any) -> dict[str, Any] | None:
    if outcome is None:
        return None
    return {
        "ok": bool(outcome.ok),
        "reason": None if outcome.reason is None else outcome.reason.value,
        "iterations": [
            {
                "iteration": int(r.round_index),
                "conflicts_before": int(r.conflicts_before),
                "conflicts_after": int(r.conflicts_after),
                "replanned": list(r.replanned),
                "failed": list(r.failed),
            }
            for r in outcome.rounds
        ],
        "moves": dict(outcome.detail.get("moves", {})),
        "remaining_conflicts": (
            0 if outcome.report is None else len(outcome.report.conflicts)
        ),
    }


def _trace_payload(r_idx: int, reqs, run: CycleRun, cycle: float) -> dict[str, Any]:
    """cycle_trace.json 的一周期：逐机航次 + 协调 + 触发证据。"""
    tickets = []
    for req in reqs:
        res = run.results[req.uav_id]
        traj = run.delivered.get(req.uav_id)
        gate = (res.diagnostics or {}).get("global_gate") or {}
        tickets.append({
            "uav_id": req.uav_id,
            "t0": round(float(req.t0), 6),
            # 周期基准与离巢错峰分开记：航次的 t0 = 基准 + 偏移（见 PIPELINE「离巢错峰」）
            "t0_base_s": round(float(req.meta.get("t0_base_s", req.t0)), 6),
            "depart_offset_s": round(float(req.meta.get("depart_offset_s", 0.0)), 6),
            "start_position": [round(float(v), 6) for v in req.start_position],
            "stops": [{"id": s.id, "kind": s.kind, "position": list(s.position),
                       "service_time_s": s.service_time_s} for s in req.stops],
            "ok": bool(res.ok),
            "failure": None if res.failure is None else res.failure.value,
            "detail": str(res.detail)[:300],
            "t_end": None if traj is None else round(float(traj.t_end), 6),
            "duration_s": None if traj is None else round(float(traj.duration), 3),
            "path_len_m": None if traj is None else round(float(traj.path_len_m), 2),
            "changed_by_coordination": req.uav_id in run.changed,
            "gate": {
                "ok": bool(gate.get("ok", False)),
                "worst_clearance_m": None if gate.get("worst_clearance_m") is None
                else round(float(gate["worst_clearance_m"]), 3),
            },
        })
    return {
        "cycle": r_idx,
        "t0": (r_idx - 1) * cycle,
        "tickets": tickets,
        "pruned": list(run.pruned),
        "existing_before": [t.uav_id for t in run.existing_before],
        "pre_conflicts": _pre_conflicts(run.pre_report, run.existing_before),
        "old_changed": list(run.old_changed),
        "frozen_ids": list(run.frozen_ids),
        "coordination": _coordination(run.outcome),
    }


# ---------------------------------------------------------------------------
# 摘要打印
# ---------------------------------------------------------------------------


def print_summary(runtime: Runtime, fleet, runs: Mapping[int, CycleRun],
                  metrics: Mapping[str, Any], out_dir: str | Path) -> None:
    """控制台摘要：逐周期逐机 + 协调 + 跨周期证据。"""
    print(f"[{runtime.config.name}] 场景 {runtime.config.scenario.data_dir.name}  "
          f"场 {runtime.field_info['source']}  周期 {fleet.cycle_length_s:g} s")
    for r_idx in sorted(runs):
        run = runs[r_idx]
        print(f"  周期{r_idx}（t0={(r_idx-1)*fleet.cycle_length_s:g}）："
              f"prune 摘除 {list(run.pruned)}"
              + (f"，既有航迹 {[t.uav_id for t in run.existing_before]}"
                 if run.existing_before else ""))
        for uid, res in run.results.items():
            traj = run.delivered.get(uid)
            print(
                f"    机 {uid}: ok={res.ok} "
                f"failure={None if res.failure is None else res.failure.value} "
                f"时长={0.0 if traj is None else round(traj.duration, 2)}s "
                f"弧长={0.0 if traj is None else round(traj.path_len_m, 1)}m "
                f"让路={'是' if uid in run.changed else '否'}"
                + ("  [降级交付 time_source=estimate]"
                   if traj is not None and traj.time_source.value == "estimate" else "")
            )
        for uid, m in metrics["cycles"][str(r_idx)]["per_uav"].items():
            print(
                f"    指标 机{uid}: SDF净空={m['sdf_min_m']:.2f}m "
                f"真几何={m['prism_min_m']:.2f}m 复核={m['verified']} "
                f"均速={m['mean_speed_mps']:.2f}m/s"
            )
        if run.pre_report is not None:
            pcs = metrics["cross_cycle"]["pre_conflicts"][str(r_idx)]
            print(f"    协调前冲突 {len(pcs)} 对（新-旧 "
                  f"{sum(1 for p in pcs if p['kind'] == 'new_old')}）")
        coord = metrics["cycles"][str(r_idx)]["coordination"]
        print(f"    协调: ok={coord['ok']} 协调迭代={len(coord['iterations'])} "
              f"遗留冲突={coord['remaining_conflicts']} 让路机={coord['changed']} "
              f"旧侧改动={list(run.old_changed)}")
        for rec in coord["iterations"]:
            print(f"      迭代 {rec['iteration']}: 前 {rec['conflicts_before']} → "
                  f"后 {rec['conflicts_after']} 重规划 {rec['replanned']} "
                  f"失败 {rec['failed']}")
    print(f"[{runtime.config.name}] 结果：{out_dir}")

