# -*- coding: utf-8 -*-
"""实验主流程：两轮多机 tour 规划 + 跨轮既有航迹协调 → 指标 → 落盘（本实验私有）。

职责：
1. 轮1：同批多机 ``plan_fleet``（新-新冲突协调），交付航迹注册进 ``TrajectoryRegistry``；
2. 轮2：新机 ``plan``（register=False）→ 与 Registry 里的**既有航迹**一起检测
   （新-旧）→ ``coordinate`` 消解（旧侧机号小故冻结、新侧让路）→ 注册新航迹；
3. 逐轮指标 + 跨轮证据（触发的冲突对、旧侧冻结、时间重叠、busy_until/prune 账）落盘。

边界：不装配运行时（``runtime.py``）、不审计（``audit_checks.py`` / ``audit.py``）、
      不画图。点位预检在 ``run.py``（跑之前把选址口径守住）。
相关：``path_planning/api.py``、``methods/trajectory_registry.py``、
      ``support/flight_metrics.py``、``support/result_store.py``。

**为什么轮2 不能走 ``plan_fleet``**：``plan_fleet`` 只协调批内航迹，看不见 Registry 里的
既有航迹——"第二轮在 DEM、建筑上增加已有轨迹"正是要在协调输入里把旧航迹带上，
且旧侧必须冻结（协调层"机号大的让路"，本实验轮2 机号严格大于轮1）。
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from path_planning.common.plan_types import Trajectory
from path_planning.methods.conflict.conflict_detector import (
    DetectionConfig,
    DetectionReport,
    detect_conflicts,
)
from .flight_metrics import fleet_metrics
from .result_store import dump_json, plan_result_to_dict, trajectory_to_dict
from .runtime import Runtime

__all__ = ["RoundRun", "print_summary", "run_rounds_experiment"]


@dataclass(frozen=True)
class RoundRun:
    """一轮的运行结果（给 ``run.py`` 打印/落盘用）。"""

    index: int
    results: Mapping[int, Any]                 # uid → PlanResult
    delivered: Mapping[int, Trajectory]        # uid → 最终交付航迹
    changed: Mapping[int, Trajectory]          # 被让路改写过的机号
    outcome: Any                               # CoordinationOutcome | None
    pre_report: DetectionReport | None         # 轮2+：协调前检测（触发证据）
    existing_before: tuple[Trajectory, ...]    # 轮2+：Registry 快调（旧侧）
    old_changed: tuple[int, ...]               # 旧侧被改动的机号（应恒为空）
    pruned: tuple[int, ...]                    # 本轮开始前落地结算的机号


def run_rounds_experiment(
    runtime: Runtime,
    fleet,
    out_dir: str | Path,
    *,
    meta_extra: Mapping[str, Any] | None = None,
) -> tuple[dict[int, RoundRun], dict[str, Any], dict[str, str]]:
    """跑全部轮次并落盘；返回 ``(逐轮结果, metrics, 文件路径)``。"""
    out_dir = Path(out_dir)
    t_start = time.time()
    planner = runtime.planner
    cycle = float(fleet.cycle_length_s)

    runs: dict[int, RoundRun] = {}
    traj_dicts: dict[str, Any] = {}
    trace_rounds: list[dict[str, Any]] = []
    metrics_rounds: dict[str, Any] = {}
    cross: dict[str, Any] = {
        "cycle_length_s": cycle,
        "pruned": {},
        "existing_before": {},
        "pre_conflicts": {},
        "old_frozen": {},
        "overlap_s": {},
        "busy_until_after": {},
    }

    for r_idx in sorted(fleet.rounds):
        reqs = fleet.rounds[r_idx]
        now = (r_idx - 1) * cycle
        pruned = planner.prune(now)
        cross["pruned"][str(r_idx)] = list(pruned)

        if r_idx == 1:
            plan = planner.plan_fleet(reqs)
            results = plan.results
            delivered = {
                uid: traj for uid in plan.results
                if (traj := plan.trajectory_of(uid)) is not None
            }
            changed = dict(plan.changed)
            outcome = plan.outcome
            pre_report = None
            existing_before: tuple[Trajectory, ...] = ()
            old_changed: tuple[int, ...] = ()
        else:
            existing_before = planner.registry.trajectories()
            results = {}
            for req in reqs:
                results[req.uav_id] = planner.plan(req, register=False)
            new_trajs = tuple(
                res.trajectory for res in results.values() if res.trajectory is not None
            )
            det = _detection(runtime, fleet, tuple(new_trajs) + existing_before)
            pre_report = (
                detect_conflicts(tuple(new_trajs) + existing_before, det)
                if new_trajs else None
            )
            outcome = (
                planner.coordinate(
                    list(new_trajs) + list(existing_before),
                    vehicles=fleet.vehicles,
                    detection=det,
                )
                if new_trajs else None
            )
            delivered = {}
            for uid, res in results.items():
                traj = (None if outcome is None else outcome.trajectories.get(uid)) or res.trajectory
                if traj is not None:
                    delivered[uid] = traj
            changed = {
                uid: traj for uid, traj in delivered.items()
                if traj is not results[uid].trajectory
            }
            # 协调后把新航迹登记为"已发布时空障碍"；旧侧机号小、不参与让路，
            # 若仍被改动就是编排出了问题——如实记录（audit 会判失败）。
            for uid, traj in delivered.items():
                planner.registry.replace(traj, source="planned")
            old_ids = {t.uav_id for t in existing_before}
            old_changed = tuple(
                sorted(
                    uid for uid in old_ids
                    if outcome is not None
                    and outcome.trajectories.get(uid) is not _by_id(existing_before, uid)
                )
            )

        runs[r_idx] = RoundRun(
            index=r_idx, results=results, delivered=delivered, changed=changed,
            outcome=outcome, pre_report=pre_report, existing_before=existing_before,
            old_changed=old_changed, pruned=pruned,
        )

        # ---- 落盘素材 ----
        for uid, traj in sorted(delivered.items()):
            traj_dicts[f"{r_idx}:{uid}"] = trajectory_to_dict(traj)

        trace_rounds.append(_trace_payload(r_idx, reqs, runs[r_idx], cycle))
        metrics_rounds[str(r_idx)] = fleet_metrics(
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
        cross["overlap_s"][str(r_idx)] = _overlaps(delivered, existing_before)
        cross["busy_until_after"][str(r_idx)] = {
            str(uid): planner.busy_until(uid) for uid in sorted(delivered)
        }

    elapsed = time.time() - t_start
    metrics = {"rounds": metrics_rounds, "cross_round": cross}
    solution = {
        "experiment": runtime.config.name,
        "cycle_length_s": cycle,
        "rounds": {
            str(r.index): {
                "t0": (r.index - 1) * cycle,
                "results": {
                    str(uid): plan_result_to_dict(
                        # 落盘必须是最终交付几何（协调层换了让路几何后 PlanResult
                        # 里仍挂首轮原航迹，照原对象写出去审计就会读到会撞的线）
                        dataclasses.replace(res, trajectory=r.delivered.get(uid, res.trajectory))
                    )
                    for uid, res in r.results.items()
                },
                "changed": sorted(int(k) for k in r.changed),
                "existing_before": [t.uav_id for t in r.existing_before],
                "old_changed": list(r.old_changed),
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
        "rounds": [
            {
                "index": r_idx,
                "t0": (r_idx - 1) * cycle,
                "uavs": [req.uav_id for req in fleet.rounds[r_idx]],
                "stops": [
                    {"uav_id": req.uav_id,
                     "stops": [{"id": s.id, "kind": s.kind,
                                "position": list(s.position),
                                "service_time_s": s.service_time_s}
                               for s in req.stops]}
                    for req in fleet.rounds[r_idx]
                ],
            }
            for r_idx in sorted(fleet.rounds)
        ],
        "field": runtime.field_info,
        "field_summary": runtime.field.summary(),
        "timings_s": {**runtime.timings_s, "rounds_s": round(elapsed, 3)},
        **({} if meta_extra is None else dict(meta_extra)),
    }

    files = write_outputs(
        out_dir,
        runtime=runtime,
        solution=solution,
        metrics=metrics,
        run_meta=run_meta,
        trace={"rounds": trace_rounds, "elapsed_s": round(elapsed, 3)},
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
    """六份 JSON 落盘（含多轮专属的 round_trace / round_trajectories）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "solution": out_dir / "solution.json",
        "metrics": out_dir / "metrics.json",
        "round_trace": out_dir / "round_trace.json",
        "round_trajectories": out_dir / "round_trajectories.json",
        "run_meta": out_dir / "run_meta.json",
        "effective_config": out_dir / "effective_config.json",
    }
    dump_json(paths["solution"], solution)
    dump_json(paths["metrics"], metrics)
    dump_json(paths["round_trace"], trace)
    dump_json(paths["round_trajectories"], trajectories)
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
            "agl_range_m": list(cfg.agl_range_m),
            "agl_min_m": cfg.field.agl_min_m,
            "agl_max_m": cfg.field.agl_max_m,
            "pad_m": cfg.field.pad_m,
            "include_static_nofly": cfg.field.include_static_nofly,
            "sample_bias_m": cfg.field.sample_bias_m,
        },
        "corridor": {
            "half_width_m": cfg.corridor.half_width_m,
            "width_ratio": cfg.corridor.width_ratio,
            "margin_m": cfg.corridor.margin_m,
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
        },
    }


# ---------------------------------------------------------------------------
# 报告片段
# ---------------------------------------------------------------------------


def _by_id(trajs: tuple[Trajectory, ...], uid: int) -> Trajectory | None:
    return next((t for t in trajs if t.uav_id == uid), None)


def _detection(runtime: Runtime, fleet, trajs) -> DetectionConfig:
    """同批最保守机型建检测配置（异构混合时取小 a_max 者）。"""
    if not trajs:
        raise ValueError("没有航迹可建检测配置")
    vehicle = min(
        (fleet.vehicles[t.uav_id] for t in trajs),
        key=lambda v: v.a_max_mps2,
    )
    return DetectionConfig.from_params(
        runtime.config.params, vehicle, e_geo_m=runtime.config.e_geo_m
    )


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
        "rounds": [
            {
                "round": int(r.round_index),
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


def _trace_payload(r_idx: int, reqs, run: RoundRun, cycle: float) -> dict[str, Any]:
    """round_trace.json 的一轮：逐机票 + 协调 + 触发证据。"""
    tickets = []
    for req in reqs:
        res = run.results[req.uav_id]
        traj = run.delivered.get(req.uav_id)
        gate = (res.diagnostics or {}).get("global_gate") or {}
        tickets.append({
            "uav_id": req.uav_id,
            "t0": round(float(req.t0), 6),
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
        "round": r_idx,
        "t0": (r_idx - 1) * cycle,
        "tickets": tickets,
        "pruned": list(run.pruned),
        "existing_before": [t.uav_id for t in run.existing_before],
        "pre_conflicts": _pre_conflicts(run.pre_report, run.existing_before),
        "old_changed": list(run.old_changed),
        "coordination": _coordination(run.outcome),
    }


# ---------------------------------------------------------------------------
# 摘要打印
# ---------------------------------------------------------------------------


def print_summary(runtime: Runtime, fleet, runs: Mapping[int, RoundRun],
                  metrics: Mapping[str, Any], out_dir: str | Path) -> None:
    """控制台摘要：逐轮逐机 + 协调 + 跨轮证据。"""
    print(f"[{runtime.config.name}] 场景 {runtime.config.scenario.data_dir.name}  "
          f"场 {runtime.field_info['source']}  周期 {fleet.cycle_length_s:g} s")
    for r_idx in sorted(runs):
        run = runs[r_idx]
        print(f"  轮{r_idx}（t0={(r_idx-1)*fleet.cycle_length_s:g}）："
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
            )
        for uid, m in metrics["rounds"][str(r_idx)]["per_uav"].items():
            print(
                f"    指标 机{uid}: SDF净空={m['sdf_min_m']:.2f}m "
                f"真几何={m['prism_min_m']:.2f}m 复核={m['verified']} "
                f"均速={m['mean_speed_mps']:.2f}m/s"
            )
        if run.pre_report is not None:
            pcs = metrics["cross_round"]["pre_conflicts"][str(r_idx)]
            print(f"    协调前冲突 {len(pcs)} 对（新-旧 "
                  f"{sum(1 for p in pcs if p['kind'] == 'new_old')}）")
        coord = metrics["rounds"][str(r_idx)]["coordination"]
        print(f"    协调: ok={coord['ok']} 轮次={len(coord['rounds'])} "
              f"遗留冲突={coord['remaining_conflicts']} 让路机={coord['changed']} "
              f"旧侧改动={list(run.old_changed)}")
        for rec in coord["rounds"]:
            print(f"      轮 {rec['round']}: 前 {rec['conflicts_before']} → "
                  f"后 {rec['conflicts_after']} 重规划 {rec['replanned']} "
                  f"失败 {rec['failed']}")
    print(f"[{runtime.config.name}] 结果：{out_dir}")
