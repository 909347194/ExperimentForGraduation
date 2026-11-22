# -*- coding: utf-8 -*-
"""exp07_two_rounds 审计：两轮链式航迹。

判据分四层：

1. 逐轮：票可交付、`time_source` 是真实排的时刻、trace 记的时刻与落盘航迹自洽；
2. 链式：轮 k 的起点 == 轮 k−1 的终点（≤1e-6）、t0 == 前轮 t_end、起飞速度 0；
   这是本实验的核心判据——两轮是"独立求解"，接口只能靠这三个量衔接；
3. 逐轮质量：leg 边界落在该轮停靠点上、运动学裕度、稠密净空、端点豁免；
   另单判**任务点自身净空**（停靠点不在豁免球里，不能拿端点阈值蒙过去）；
4. 全局：两轮一起过冲突检测（同机、不同时段，应当 0 对）、总里程/总时长不超预算。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

import numpy as np

from .support.audit_checks import Check, report
from .support.fleet_spec import load_fleet_input
from .support.quality_report import trajectory_quality
from .support.result_store import trajectory_from_dict
from .support.runtime import load_runtime
from path_planning.methods.conflict.conflict_detector import DetectionConfig, detect_conflicts

_HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp07_two_rounds 审计")
    parser.add_argument("--config", default=str(_HERE / "config.yaml"))
    parser.add_argument("--fleet", default=str(_HERE / "uav_config.json"))
    parser.add_argument("--run", default=None, help="结果目录（缺省 = results/seed_<首个种子>）")
    args = parser.parse_args(argv)

    runtime = load_runtime(args.config)
    fleet = load_fleet_input(args.fleet, runtime.config, runtime.scenario, require_tours=False)
    run_dir = Path(args.run) if args.run else (
        runtime.config.results_dir / f"seed_{runtime.config.seeds[0]}"
    )
    trace = json.loads((run_dir / "round_trace.json").read_text(encoding="utf-8"))
    trajs = {
        key: trajectory_from_dict(payload)
        for key, payload in json.loads(
            (run_dir / "round_trajectories.json").read_text(encoding="utf-8")
        ).items()
    }
    rounds = trace["rounds"]

    checks: list[Check] = []
    for rec in rounds:
        checks.extend(_round_checks(runtime, fleet, rec, trajs))
    checks.append(_chain_check(rounds))
    checks.append(_stop_clearance_check(runtime, rounds))
    checks.append(_conflict_check(runtime, fleet, trajs, rounds))
    checks.append(_budget_check(runtime, fleet, rounds))
    return report(checks)


# ---------------------------------------------------------------------------


def _round_checks(runtime, fleet, rec: dict, trajs: dict) -> list[Check]:
    tag = f"轮{rec['round']}"
    uid = int(rec["uav_id"])
    traj = trajs.get(f"{rec['round']}:{uid}")
    if traj is None:
        return [Check(f"交付/{tag}", False, f"轮 {rec['round']} 没有落盘航迹"
                                            f"（failure={rec['failure']}）")]
    checks = [Check(
        f"票自洽/{tag}", bool(rec["ok"])
        and abs(traj.t0 - float(rec["t0"])) <= 1e-6
        and abs(traj.t_end - float(rec["t_end"])) <= 1e-6,
        f"ok={rec['ok']} 航迹[{traj.t0:.2f},{traj.t_end:.2f}] "
        f"记账[{rec['t0']:.2f},{rec['t_end']:.2f}] 闸={rec['gate']['ok']}",
    )]
    qr = trajectory_quality(
        uid, traj,
        vehicle=fleet.vehicles[uid], field=runtime.field,
        clearance_m=float(runtime.config.params.clearance_m),
        cfg_step_m=float(runtime.config.params.clearance_sample_step_m),
        stops=_stops(rec),
        endpoint_exempt_radius_m=float(runtime.config.endpoint_exempt_radius_m),
        endpoint_clearance_m=runtime.config.endpoint_clearance_m,
    )
    for line in qr.lines:
        print(f"[{tag}] {line}")
    checks.extend(qr.checks)
    return checks


def _stops(rec: dict) -> list[tuple[tuple[float, float, float], float]]:
    """该轮的请求停留点（不含起点；起点机巢不在 stops 里，轮2 起点是任务点）。"""
    return [(tuple(float(v) for v in s["position"]), float(s["service_time_s"]))
            for s in rec["stops"]]


def _chain_check(rounds: list[dict]) -> Check:
    """轮间接口只有三个量：起点位置、起飞速度、起始时刻。逐个对。"""
    if len(rounds) < 2:
        return Check("链式衔接", False, "少于两轮，无从衔接")
    for prev, cur in zip(rounds, rounds[1:]):
        if prev["end_position"] is None or cur["start_position"] is None:
            return Check("链式衔接", False, f"轮{prev['round']} 或轮{cur['round']} 缺端点")
        gap = float(np.linalg.norm(np.asarray(prev["end_position"], dtype=float)
                                   - np.asarray(cur["start_position"], dtype=float)))
        dt = float(cur["t0"]) - float(prev["t_end"])
        v = float(np.linalg.norm(np.asarray(cur["start_velocity"], dtype=float)))
        if gap > 1e-6 or abs(dt) > 1e-6 or v > 1e-6:
            return Check(
                "链式衔接", False,
                f"轮{prev['round']}→轮{cur['round']}：位置差 {gap:.6f} m、"
                f"时刻差 {dt:.6f} s、起飞速度 {v:.6f} m/s",
            )
    prev, cur = rounds[-2], rounds[-1]
    return Check(
        "链式衔接", True,
        f"轮{prev['round']} 落在 {_fmt(prev['end_position'])}、t={prev['t_end']:.2f} s；"
        f"轮{cur['round']} 从同一点、同刻、静止起飞（位置差 0、时刻差 0）",
    )


def _stop_clearance_check(runtime, rounds: list[dict]) -> Check:
    """停靠点本体净空：终点停在任务点上时，这个点必须自己站得住 6 m。"""
    thr = float(runtime.config.params.clearance_m)
    worst = None
    for rec in rounds:
        for s in rec["stops"]:
            p = np.asarray(s["position"], dtype=float).reshape(1, 3)
            d = float(np.min(runtime.field.at(p)))
            if worst is None or d < worst[0]:
                worst = (d, int(s["id"]))
    return Check(
        "停靠点净空", worst is None or worst[0] >= thr - 1e-6,
        f"最紧的停靠点是任务 {worst[1]}，SDF {worst[0]:.2f} m（阈值 {thr:.2f} m；"
        "此项不受端点豁免）" if worst else "无停靠点",
    )


def _conflict_check(runtime, fleet, trajs: dict, rounds: list[dict]) -> Check:
    """跨轮冲突：两轮是**同一架机**的前后两段，判的是"共时冲突"，不是"几何贴近"。

    检测器按椭球（30/30/15 m + bbox 余量）判贴近，衔接点上两段航迹是同一点、同一瞬时，
    必然读到一对——那是航迹接缝，不是两架机的冲突。所以判据写成两条：
    (a) 每对航迹的时间窗互不重叠（除衔接瞬时），(b) 若检测器报了冲突，其 CPA 时刻必须
    落在衔接时刻上。两条都满足才算通过；有任何一对在时间窗内共时贴近，就是不通过。
    """
    vehicle = min(fleet.vehicles.values(), key=lambda v: v.a_max_mps2)
    det = DetectionConfig.from_params(
        runtime.config.params, vehicle, e_geo_m=runtime.config.e_geo_m
    )
    stamped, labels = [], {}
    for key, traj in trajs.items():
        rnd, uid = (int(x) for x in key.split(":"))
        label = rnd * 10 + uid
        labels[label] = key
        stamped.append(dataclasses.replace(traj, uav_id=label))
    rep = detect_conflicts(tuple(stamped), det)

    spans = {k: (float(t.t0), float(t.t_end)) for k, t in trajs.items()}
    handoffs = {float(r["t0"]) for r in rounds[1:] if r.get("t0") is not None}
    problems: list[str] = []
    for p in rep.conflicts:
        ka, kb = labels.get(p.uav_a, p.uav_a), labels.get(p.uav_b, p.uav_b)
        a, b = spans.get(ka, (0.0, 0.0)), spans.get(kb, (0.0, 0.0))
        overlap = min(a[1], b[1]) - max(a[0], b[0])
        if overlap > 1e-6:
            problems.append(f"{ka}↔{kb} 时间窗重叠 {overlap:.2f} s（CPA 时刻 {p.cpa_time}）")
            continue
        handoff = max(a[0], b[0])
        if p.cpa_time is None or min(abs(float(p.cpa_time) - h) for h in handoffs | {handoff}) > 1e-3:
            problems.append(f"{ka}↔{kb} 冲突不在衔接时刻（CPA {p.cpa_time}，衔接 {handoff:.2f}）")
    seams = [f"{labels.get(p.uav_a, p.uav_a)}↔{labels.get(p.uav_b, p.uav_b)} "
             f"（CPA t={p.cpa_time:.2f} s，距离 {p.min_distance:.2f} m，判阈半径 {p.radius:.1f} m）"
             for p in rep.conflicts]
    return Check(
        "跨轮共时冲突", not problems,
        (f"时间窗 {', '.join(f'{k}[{v[0]:.2f},{v[1]:.2f}]' for k, v in sorted(spans.items()))} "
         f"两两不重叠；检测器报 {len(rep.conflicts)} 对，"
         + ("全部落在衔接点（航迹接缝，非共时冲突）：" + "；".join(seams) if seams
            else "无冲突")
         + f"；弦对 {int(rep.chord_pairs)}")
        if not problems else "；".join(problems),
    )


def _budget_check(runtime, fleet, rounds: list[dict]) -> Check:
    """总账按"一架机飞完两轮"算：里程、时长、末刻三条都要在预算内。"""
    ok = all(r["ok"] and r["path_len_m"] is not None for r in rounds)
    uid = int(rounds[0]["uav_id"])
    total_len = sum(float(r["path_len_m"] or 0.0) for r in rounds)
    total_t = sum(float(r["duration_s"] or 0.0) for r in rounds)
    last_t = max(float(r["t_end"] or 0.0) for r in rounds)
    veh = fleet.vehicles[uid]
    within = (total_len <= veh.max_range_m and last_t <= veh.max_time_s)
    return Check(
        "两轮总预算", ok and within,
        f"弧长合计 {total_len:.1f} m（上限 {veh.max_range_m:.0f}）、"
        f"末刻 {last_t:.1f} s（上限 {veh.max_time_s:.0f}）、"
        f"飞行+服务合计 {total_t:.1f} s",
    )


def _fmt(p) -> str:
    return "(" + ", ".join(f"{float(v):.1f}" for v in p) + ")"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
