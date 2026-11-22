# -*- coding: utf-8 -*-
"""exp07_two_rounds：单机两轮链式航迹（轮1 停在任务点，轮2 接着从那里起飞）。

跑什么：轮1 从机巢0 上方起飞 → 任务 A，**终点停在 A**（下行/回巢状态机不参与）；
      轮2 以轮1 的末位置、末速度（0）、末时刻为起点 → 任务 B，终点停在 B。
      两轮各自构造一条 ``PlanRequest``，各自过一次完整流水（走廊→RRT→NLP→复核），
      互不知道对方的存在——串接只发生在"请求参数"上。
看什么：分轮独立求解能否把上一轮的落点当下一轮的起点；两轮的时间轴是否首尾相接；
      串联处的几何/动力学是否连续（末速度 0 → 下一轮起飞条件成立）。
记什么：``round_trace.json``（逐轮请求、票、闸）、``round_trajectories.json``
      （键 ``"轮:机号"``）、``run_meta.json``（轮次表与参数快照）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from path_planning.common.flight_budget import Budget
from path_planning.common.plan_types import PlanRequest, PlanStop
from .support.experiment_run import _effective_config
from .support.fleet_spec import load_fleet_input, nest_above
from .support.result_store import dump_json, trajectory_to_dict
from .support.runtime import load_runtime

_HERE = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _HERE / "config.yaml"
_DEFAULT_FLEET = _HERE / "uav_config.json"
_DEFAULT_ROUNDS = _HERE / "rounds.json"


@dataclass(frozen=True)
class RoundSpec:
    """一轮的停靠点（坐标已是 ENU 绝对值；起点不在这里）。"""

    index: int
    uav_id: int
    stops: tuple[PlanStop, ...]


def load_rounds(path: str | Path) -> tuple[RoundSpec, ...]:
    """读 ``rounds.json``；轮号必须从 1 起连续，停靠点必须非空。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("rounds") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError(f"rounds.json 缺少非空 ``rounds``：{path}")
    out: list[RoundSpec] = []
    for k, item in enumerate(items, start=1):
        idx = int(item.get("index", k))
        if idx != k:
            raise ValueError(f"轮号必须从 1 起连续：第 {k} 项写的是 {idx}")
        stops = tuple(
            PlanStop(
                id=int(s["id"]),
                position=tuple(float(v) for v in s["position"]),
                service_time_s=float(s.get("service_time_s", 0.0)),
                kind=str(s.get("kind", "task")),
            )
            for s in item["stops"]
        )
        if not stops:
            raise ValueError(f"轮 {idx} 没有停靠点")
        out.append(RoundSpec(index=idx, uav_id=int(item["uav_id"]), stops=stops))
    return tuple(out)


def load_budgets(path: str | Path) -> dict[int, Budget]:
    """预算只来自清单的剩余量（不从机体上限反推）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        int(u["id"]): Budget(
            endurance_remaining_s=float(u["budget"]["endurance_remaining_s"]),
            range_remaining_m=float(u["budget"]["range_remaining_m"]),
        )
        for u in raw["uavs"]
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp07_two_rounds：单机两轮链式航迹")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    parser.add_argument("--fleet", default=str(_DEFAULT_FLEET))
    parser.add_argument("--rounds", default=str(_DEFAULT_ROUNDS))
    parser.add_argument("--out", default=None, help="结果目录（缺省 = results/seed_<种子>）")
    args = parser.parse_args(argv)

    runtime = load_runtime(args.config)
    fleet = load_fleet_input(args.fleet, runtime.config, runtime.scenario, require_tours=False)
    budgets = load_budgets(args.fleet)
    rounds = load_rounds(args.rounds)

    out_dir = (
        Path(args.out) if args.out
        else runtime.config.results_dir / f"seed_{runtime.config.seeds[0]}"
    )
    t_start = time.time()
    records: list[dict[str, Any]] = []
    trajectories: dict[str, dict] = {}
    prev_traj = None
    ok_all = True
    for spec in rounds:
        uid = spec.uav_id
        if prev_traj is None:
            start = nest_above(fleet.home_nest[uid], fleet.nests,
                               runtime.config.takeoff_agl_m)
            t0 = 0.0
        else:
            start = tuple(float(v) for v in prev_traj.end_position)
            t0 = float(prev_traj.t_end)
        req = PlanRequest(
            uav_id=uid, vehicle=fleet.vehicles[uid],
            start_position=start, start_velocity=(0.0, 0.0, 0.0), t0=t0,
            stops=spec.stops, budget=budgets[uid],
            meta={"source": "rounds.json", "round": spec.index},
        )
        res = runtime.planner.plan(req, register=False)
        rec = _round_payload(spec, req, res)
        records.append(rec)
        print(f"[轮{spec.index}] 机{uid} ok={rec['ok']} 失败={rec['failure']} "
              f"t=[{rec['t0']:.2f},{rec['t_end'] if rec['t_end'] is not None else -1:.2f}] "
              f"时长={rec['duration_s']} 弧长={rec['path_len_m']} "
              f"闸={rec['gate']['ok']} 最差={rec['gate']['worst_clearance_m']}")
        if res.trajectory is None:
            ok_all = False
            print(f"    轮 {spec.index} 未产出航迹：{res.detail}")
            break
        trajectories[f"{spec.index}:{uid}"] = trajectory_to_dict(res.trajectory)
        prev_traj = res.trajectory
    elapsed = time.time() - t_start

    dump_json(out_dir / "round_trace.json", {
        "rounds": records,
        "chain_ok": ok_all,
        "elapsed_s": round(elapsed, 3),
    })
    dump_json(out_dir / "round_trajectories.json", trajectories)
    dump_json(out_dir / "run_meta.json", {
        "experiment": runtime.config.name,
        "rounds": [
            {"index": r.index, "uav_id": r.uav_id,
             "stops": [{"id": s.id, "kind": s.kind,
                        "position": list(s.position),
                        "service_time_s": s.service_time_s} for s in r.stops]}
            for r in rounds
        ],
        "effective_config": _effective_config(runtime),
        "timings_s": {"rounds_s": round(elapsed, 3), **runtime.timings_s},
    })
    print(f"[{runtime.config.name}] 结果：{out_dir}")
    return 0 if ok_all else 2


def _round_payload(spec: RoundSpec, req: PlanRequest, res: Any) -> dict[str, Any]:
    traj = res.trajectory
    gate = res.diagnostics.get("global_gate") or {}
    return {
        "round": spec.index,
        "uav_id": spec.uav_id,
        "t0": round(float(req.t0), 6),
        "start_position": [round(float(v), 6) for v in req.start_position],
        "start_velocity": list(req.start_velocity),
        "stops": [{"id": s.id, "kind": s.kind, "position": list(s.position),
                   "service_time_s": s.service_time_s} for s in req.stops],
        "ok": bool(res.ok),
        "failure": None if res.failure is None else res.failure.value,
        "detail": str(res.detail)[:300],
        "t_end": None if traj is None else round(float(traj.t_end), 6),
        "end_position": None if traj is None else [round(float(v), 6)
                                                   for v in traj.end_position],
        "duration_s": None if traj is None else round(float(traj.duration), 3),
        "path_len_m": None if traj is None else round(float(traj.path_len_m), 2),
        "gate": {"ok": bool(gate.get("ok", False)),
                 "worst_clearance_m": None if gate.get("worst_clearance_m") is None
                 else round(float(gate["worst_clearance_m"]), 3)},
    }


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
