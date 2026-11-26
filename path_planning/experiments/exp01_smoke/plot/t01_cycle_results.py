# -*- coding: utf-8 -*-
"""T01：逐航次性能表（行 = 周期:机号）。

数字全部从 ``cycle_trajectories.json`` + ``cycle_trace.json`` 单源重算
（``time_quality`` 给飞行/悬停/绕行比），不手写、不抄控制台。
"""

from __future__ import annotations

import sys

from path_planning.common.plan_types import PlanStop

from ..support.trajectory_margins import time_quality
from ._table import write_rows

NAME = "T01_cycle_results"


def build(ctx) -> list:
    rows = []
    for key in sorted(ctx.trajs, key=lambda k: (int(k.split(":")[0]), int(k.split(":")[1]))):
        r_idx, uid = (int(v) for v in key.split(":"))
        traj = ctx.trajs[key]
        req = ctx.requests[uid]
        direct = sum(
            _dist(a.position, b.position)
            for a, b in zip((_start(req),) + tuple(req.stops), req.stops)
        )
        tq = time_quality(traj, ctx.fleet.vehicles[uid], direct_m=direct)
        ticket = _ticket(ctx.trace, r_idx, uid)
        gate_ok = bool(ticket and ticket["gate"]["ok"])
        rows.append({
            "周期:机": key,
            "时长(s)": tq["duration_s"],
            "飞行(s)": tq["flight_s"],
            "悬停(s)": tq["hover_s"],
            "弧长(m)": tq["path_len_m"],
            "直线(m)": direct,
            "绕行比": tq["detour_ratio"] if tq["detour_ratio"] is not None else float("nan"),
            "均速(m/s)": tq["mean_speed_mps"],
            "闸净空(m)": float(ticket["gate"]["worst_clearance_m"]
                              if ticket and ticket["gate"]["worst_clearance_m"] is not None
                              else float("nan")),
            "交付闸": 1.0 if gate_ok else 0.0,
            "让路": 1.0 if (ticket and ticket["changed_by_coordination"]) else 0.0,
            "失败码": (ticket or {}).get("failure") or "-",
        })

    ctx.tab_dir.mkdir(parents=True, exist_ok=True)
    return write_rows(
        ctx.tab_dir / NAME, rows,
        caption=f"多机多周期逐航次规划结果（guangzhou，{ctx.run_dir.name}）",
        label="tab:exp01-cycles",
        note="由 exp01_smoke/plot/t01_cycle_results.py 从 results 单源重算，勿手写数字。"
             "绕行比 = 弧长 / 逐段直线和；时长含服务悬停；让路 = 协调层改写过几何；"
             "闸净空 = 逐航段交付闸在窗口场上的 0.2 m 稠密最小值（含端点豁免圈内），"
             "故可能低于审计的“稠密净空”（后者已扣除端点豁免区）；"
             "失败码非空者即降级交付（交付闸仍须 PASS，时间不可用于冲突判定）。",
    )


def _start(req) -> PlanStop:
    return PlanStop(id=0, position=req.start_position, service_time_s=0.0, kind="task")


def _dist(a, b) -> float:
    return float(sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5)


def _ticket(trace: dict, r_idx: int, uid: int) -> dict | None:
    for r in trace["cycles"]:
        if int(r["cycle"]) == r_idx:
            for t in r["tickets"]:
                if int(t["uav_id"]) == uid:
                    return t
    return None


if __name__ == "__main__":  # pragma: no cover
    from ._shared import run_single
    sys.exit(run_single("exp01 T01 逐航次性能表", build, None))

