# -*- coding: utf-8 -*-
"""实验主流程：``plan_fleet`` → 指标 → 四份 JSON → 摘要打印（四个实验共用）。

职责：把实验之间**完全相同**的那段流程收在一处；实验差异（额外的断言、额外的
      记录、跨周期复跑）由各实验的 ``run.py`` 在调用前后自己做。
边界：不装配运行时（``runtime.py``）、不审计（``audit_checks.py``）、不画图。
相关：``runtime.py``、``flight_metrics.py``、``result_store.py``、``path_planning/api.py``。

``effective_config`` 写的是**本次实际运行**的完整参数快照，不是"配置文件原文"：
配置里可能留空（如 ``corridor.half_width_m=null``），跑完必须能回答"半宽到底
被解析成了多少"——那由走廊对象按段计算，不写下来就查不清。
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any, Mapping

from .flight_metrics import fleet_metrics
from .result_store import (
    plan_result_to_dict,
    write_run_outputs,
)
from .runtime import Runtime

__all__ = ["print_summary", "run_fleet_experiment"]


def run_fleet_experiment(
    runtime: Runtime,
    fleet,
    out_dir: str | Path,
    *,
    solution_extra: Mapping[str, Any] | None = None,
    meta_extra: Mapping[str, Any] | None = None,
) -> tuple[Any, dict[str, Any], dict[str, str]]:
    """跑一批请求并落盘；返回 ``(fleet_plan, metrics, file_paths)``。"""
    t0 = time.time()
    plan = runtime.planner.plan_fleet(fleet.requests)
    plan_s = time.time() - t0

    delivered = {
        uid: traj
        for uid in plan.results
        if (traj := plan.trajectory_of(uid)) is not None
    }
    metrics = fleet_metrics(
        delivered,
        vehicles=fleet.vehicles,
        requests=fleet.requests,
        field=runtime.field,
        prisms=runtime.prisms,
        params=runtime.config.params,
        outcome=plan.outcome,
        changed_ids=plan.changed,
        alt_range_m=runtime.ctx.alt_range_m,
    )
    solution: dict[str, Any] = {
        "fleet": {
            "ok": bool(plan.ok),
            "changed": sorted(int(k) for k in plan.changed),
            "coordinated": plan.outcome is not None,
        },
        "results": {
            str(uid): plan_result_to_dict(
                # 落盘必须是**最终交付**的航迹：协调层换了让路几何后 ``PlanResult``
                # 里仍然挂着首轮原航迹，照原对象写出去，审计读回来还是那条会撞的线
                # （exp02 实测审计报"冲突 1 对"、协调层报 0）。
                dataclasses.replace(res, trajectory=delivered.get(uid, res.trajectory))
            )
            for uid, res in plan.results.items()
        },
        "registry_busy_until": {
            str(uid): runtime.planner.busy_until(uid) for uid in plan.results
        },
    }
    if solution_extra:
        solution.update(dict(solution_extra))

    run_meta = {
        "experiment": runtime.config.name,
        "seed": int(runtime.ctx.seed),
        "field": runtime.field_info,
        "field_summary": runtime.field.summary(),
        "timings_s": {**runtime.timings_s, "planning_s": plan_s},
        **({} if meta_extra is None else dict(meta_extra)),
    }
    files = write_run_outputs(
        out_dir,
        effective_config=_effective_config(runtime),
        run_meta=run_meta,
        metrics=metrics,
        solution=solution,
    )
    return plan, metrics, files


def print_summary(runtime: Runtime, plan: Any, metrics: Mapping[str, Any], out_dir: str | Path) -> None:
    """控制台摘要：每机一行 + 指标一行 + 协调一行。"""
    print(f"[{runtime.config.name}] 场景 {runtime.config.scenario.data_dir.name}  "
          f"场 {runtime.field_info['source']}")
    for uid, res in plan.results.items():
        traj = plan.trajectory_of(uid)  # 打印交付几何，不是首轮原几何
        print(
            f"  机 {uid}: ok={res.ok} deliverable={res.deliverable} "
            f"failure={None if res.failure is None else res.failure.value} "
            f"时长={0.0 if traj is None else round(traj.duration, 2)}s "
            f"弧长={0.0 if traj is None else round(traj.path_len_m, 1)}m"
        )
    for uid, m in metrics["per_uav"].items():
        print(
            f"  指标 机{uid}: SDF净空={m['sdf_min_m']:.2f}m 真几何={m['prism_min_m']:.2f}m "
            f"复核={m['verified']} 均速={m['mean_speed_mps']:.2f}m/s"
        )
    coord = metrics["coordination"]
    print(
        f"  协调: ok={coord['ok']} 轮次={len(coord['rounds'])} "
        f"遗留冲突={coord['remaining_conflicts']} 让路机={coord['changed']}"
    )
    for r in coord["rounds"]:
        print(f"    轮 {r['round']}: 前 {r['conflicts_before']} → 后 {r['conflicts_after']} "
              f"重规划 {r['replanned']} 失败 {r['failed']}")
    print(f"[{runtime.config.name}] 结果：{out_dir}")


def _effective_config(runtime: Runtime) -> dict[str, Any]:
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
            "takeoff_agl_m": cfg.takeoff_agl_m,
        },
    }
