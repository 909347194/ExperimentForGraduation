# -*- coding: utf-8 -*-
"""失败航次诊断：按结果目录里的账，把失败航次原样重放一遍，打印下层诊断。

职责：读 ``loop_trace.json`` + ``tasks.json`` + ``run_meta.json``，重建失败航次的
      ``PlanRequest``，调用与正式运行**同一套**装配（``build_planner``）重解一次，
      把 ``PlanResult.diagnostics`` 摊开打印，并落一份 ``diagnose_<cycle>_uav<id>.json``。
边界：只解释失败，不改参数、不重跑其它航次、不写 results 里的正式账。

为什么值得单独一个脚本：``RRT_NO_PATH`` 只是结论，真因藏在"端点自检/走廊半宽/
RRT 逐种子统计"里；重跑整轮 20 min 只为看一个数不值当。

跑法::

    python -m orchestration.experiments.exp01_smoke.support.diag.failure           # 全部失败航次
    python -m orchestration.experiments.exp01_smoke.support.diag.failure --cycle 2 --uav 4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from path_planning.common.flight_budget import Budget
from path_planning.common.plan_types import PlanRequest, PlanStop

from .. import field as field_mod
from .. import fleet as fleet_mod
from .. import tasks as tasks_mod
from ..config import EXP_DIR, default_run_dir, load_config


def _resolve(p: Path) -> Path:
    return p if p.is_absolute() else EXP_DIR / p


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _failed_tickets(trace: dict[str, Any]) -> list[tuple[int, int, dict[str, Any]]]:
    out = []
    for cycle in trace["cycles"]:
        for uid, ticket in sorted(cycle["tickets"].items()):
            if not ticket["ok"]:
                out.append((int(cycle["cycle"]), int(uid), ticket))
    return out


def _request(
    *,
    ticket: dict[str, Any],
    cycle_start: float,
    tasks_doc: dict[str, Any],
    meta: dict[str, Any],
    vehicles: dict[int, Any],
) -> PlanRequest:
    """用账里的 task_ids 顺序重建航次：起点机巢 → 任务序列 → 终点机巢。"""
    uav = int(ticket["uav_id"])
    task_of = {int(t["id"]): t for t in tasks_doc["tasks"]}
    stops = [PlanStop(id=int(tid), position=(float(task_of[tid]["x"]),
                                             float(task_of[tid]["y"]),
                                             float(task_of[tid]["z"])),
                     service_time_s=float(task_of[tid]["service_time_s"]),
                     kind="task")
             for tid in (int(v) for v in ticket["task_ids"])]
    nest = _nest_of(meta, uav)
    stops.append(PlanStop(id=int(nest["id"]), position=(float(nest["x"]), float(nest["y"]),
                                                        float(nest["z"])),
                          service_time_s=0.0, kind="nest"))
    vehicle = vehicles[uav]
    return PlanRequest(
        uav_id=uav,
        vehicle=vehicle,
        start_position=(float(nest["x"]), float(nest["y"]), float(nest["z"])),
        start_velocity=(0.0, 0.0, 0.0),
        t0=float(cycle_start),
        stops=tuple(stops),
        budget=Budget(endurance_remaining_s=float(vehicle.max_time_s),
                      range_remaining_m=float(vehicle.max_range_m)),
        meta={"replay": True},
    )


def _nest_of(meta: dict[str, Any], uav_id: int) -> dict[str, Any]:
    rows = {int(r["id"]): r for r in fleet_mod.load_uav_config()}
    nest_id = int(rows[uav_id]["nest_id"])
    for site in meta["context"]["nest_sites"]:
        if int(site["id"]) == nest_id:
            return site
    raise ValueError(f"run_meta 里没有机 {uav_id} 所属的巢 {nest_id}")


def diagnose(run_dir: Path, cycles: set[int] | None, uavs: set[int] | None) -> int:
    cfg = load_config()
    trace = _load(run_dir / "loop_trace.json")
    meta = _load(run_dir / "run_meta.json")
    tasks_doc = _load(EXP_DIR / str(cfg["tasks"]["file"]))

    scenario = field_mod.load_guangzhou(cfg)
    field, _ = field_mod.load_or_build_field(cfg, scenario)
    prisms = field_mod.prisms_of(scenario, field, cfg)
    rows = fleet_mod.load_uav_config()
    vehicles = fleet_mod.lower_vehicles(rows)
    planner, _, _ = field_mod.build_planner(cfg, scenario, field, prisms, vehicles)

    targets = [
        (c, u, t) for c, u, t in _failed_tickets(trace)
        if (not cycles or c in cycles) and (not uavs or u in uavs)
    ]
    if not targets:
        print("没有匹配的失败航次。")
        return 0

    for cycle, uav, ticket in targets:
        cycle_start = next(c["t_start"] for c in trace["cycles"] if int(c["cycle"]) == cycle)
        request = _request(ticket=ticket, cycle_start=float(cycle_start),
                           tasks_doc=tasks_doc, meta=meta, vehicles=vehicles)
        result = planner.plan(request, register=False)
        print(f"\n=== 周期 {cycle} 机 {uav}（账上 {ticket['failure']}） ===")
        print(f"账上 detail：{ticket['detail']}")
        print(f"重放结果：{getattr(result.failure, 'value', result.failure)}"
              f"｜{result.detail}")
        diag = result.diagnostics or {}
        for key in ("legs",):
            for leg in diag.get(key, []) or []:
                _print_leg(leg)
        out = run_dir / f"diagnose_cycle{cycle}_uav{uav}.json"
        out.write_text(json.dumps(_jsonable(diag), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"诊断落盘：{out}")
    return 0


def _print_leg(leg: dict[str, Any]) -> None:
    cor = leg.get("corridor") or {}
    ends = leg.get("endpoints_m") or {}
    broken = leg.get("rrt_no_path") or {}
    print(f"  候选 {leg.get('candidates')} 条｜高度带 {leg.get('alt_m')} m")
    if cor:
        print(f"  走廊半宽 {cor.get('half_width_m')} m｜净空阈值 "
              f"{cor.get('clearance_m')} → 求解 {cor.get('solve_clearance_m')} m"
              f"｜窗口 {cor.get('grid_shape')} @ {cor.get('grid_origin')}")
    if ends:
        print(f"  起点 {ends.get('start')} → 终点 {ends.get('goal')}")
    if broken:
        print(f"  成因：{broken.get('reason')}")
        print(f"  起/终自由 {broken.get('start_free')}/{broken.get('goal_free')}"
              f"｜节点≤{broken.get('nodes_max')}"
              f"｜距目标最近 {broken.get('closest_to_goal_min_m')} m"
              f"｜净空拒 {broken.get('reject_clearance')}"
              f"｜窗界拒 {broken.get('reject_window')}"
              f"｜步长拒 {broken.get('reject_steer')}")
    for stat in leg.get("rrt", []) or []:
        print(f"  seed {stat.get('seed')}：ok={stat.get('ok')} 节点 {stat.get('nodes')}"
              f" 距 {stat.get('closest_to_goal_m')}")


def _jsonable(obj: Any) -> Any:
    """把诊断里的 numpy / dataclass / Enum 转成可落盘的纯量（丢大数组）。"""
    from dataclasses import asdict, is_dataclass
    from enum import Enum

    try:
        import numpy as np
    except ModuleNotFoundError:  # pragma: no cover
        return obj
    if isinstance(obj, np.ndarray):
        return f"<ndarray {obj.shape}>"
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items() if k != "fallback_path"}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer, np.bool_)):
        return obj.item()
    return obj


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="失败航次诊断（重放，不改正式账）")
    p.add_argument("--run", type=Path, default=None,
                   help="结果目录（缺省 = 配置 seed 对应的 results/seed_N）")
    p.add_argument("--cycle", type=int, default=None)
    p.add_argument("--uav", type=int, default=None)
    args = p.parse_args(argv)
    run_dir = _resolve(args.run) if args.run else default_run_dir(load_config())
    return diagnose(run_dir, {args.cycle} if args.cycle else None,
                    {args.uav} if args.uav else None)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
