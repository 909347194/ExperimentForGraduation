# -*- coding: utf-8 -*-
"""exp01_smoke 出表：``results/tables/exp01_round_results`` + ``exp01_cross_round``。

单源：数字全部从 ``round_trace.json`` / ``round_trajectories.json`` / ``metrics.json``
重算（``time_quality`` 给飞行/悬停/绕行比），不手写、不复制控制台输出。
两张表：逐 tour 性能表（行 = 轮:机），跨轮证据表（必撞触发 / 旧侧冻结 /
时间重叠 / 状态账）——后者是本实验的核心判据，单独成表不与性能混排。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .support.fleet_spec import load_fleet_input
from .support.result_store import trajectory_from_dict
from .support.runtime import load_runtime
from .support.trajectory_margins import time_quality

_HERE = Path(__file__).resolve().parent
_MAIN = "exp01_round_results"
_CROSS = "exp01_cross_round"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp01_smoke 出表")
    parser.add_argument("--config", default=str(_HERE / "config.yaml"))
    parser.add_argument("--fleet", default=str(_HERE / "uav_config.json"))
    parser.add_argument("--run", default=None, help="结果目录（缺省 = results/seed_<首个种子>）")
    parser.add_argument("--out", default=None, help="输出目录（缺省 = <run>/tables）")
    args = parser.parse_args(argv)

    runtime = load_runtime(args.config)
    fleet = load_fleet_input(args.fleet, runtime.config, runtime.scenario)
    run_dir = Path(args.run) if args.run else (
        runtime.config.results_dir / f"seed_{runtime.config.seeds[0]}"
    )
    out_dir = Path(args.out) if args.out else run_dir / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)

    trace = json.loads((run_dir / "round_trace.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    trajs = {
        key: trajectory_from_dict(payload)
        for key, payload in json.loads(
            (run_dir / "round_trajectories.json").read_text(encoding="utf-8")
        ).items()
    }
    requests = {int(r.uav_id): r for r in fleet.requests}

    # ---- 表1：逐 tour 性能（行 = 轮:机）----
    rows = []
    for key in sorted(trajs, key=lambda k: (int(k.split(":")[0]), int(k.split(":")[1]))):
        r_idx, uid = (int(v) for v in key.split(":"))
        traj = trajs[key]
        req = requests[uid]
        direct = sum(
            _dist(a.position, b.position)
            for a, b in zip((_start(req),) + tuple(req.stops), req.stops)
        )
        tq = time_quality(traj, fleet.vehicles[uid], direct_m=direct)
        ticket = _ticket(trace, r_idx, uid)
        rows.append({
            "轮:机": key,
            "时长(s)": tq["duration_s"],
            "飞行(s)": tq["flight_s"],
            "悬停(s)": tq["hover_s"],
            "弧长(m)": tq["path_len_m"],
            "直线(m)": direct,
            "绕行比": tq["detour_ratio"] if tq["detour_ratio"] is not None else float("nan"),
            "均速(m/s)": tq["mean_speed_mps"],
            "最差净空(m)": float(ticket["gate"]["worst_clearance_m"]
                              if ticket and ticket["gate"]["worst_clearance_m"] is not None
                              else float("nan")),
            "交付闸": 1.0 if (ticket and ticket["gate"]["ok"]) else 0.0,
            "让路": 1.0 if (ticket and ticket["changed_by_coordination"]) else 0.0,
        })
    _write_rows(out_dir / _MAIN, rows,
                caption="多机多轮逐 tour 规划结果（guangzhou，种子 0）",
                label="tab:exp01-rounds",
                note="由 exp01_smoke/build_tables.py 从 results 的单源重算，勿手写数字。"
                     "绕行比 = 弧长 / 逐段直线和；时长含服务悬停；让路 = 协调层改写过几何。")

    # ---- 表2：跨轮证据 ----
    cross = metrics["cross_round"]
    ev: list[dict[str, object]] = []
    for r in trace["rounds"]:
        r_idx = int(r["round"])
        for p in r.get("pre_conflicts", []) or []:
            ev.append({
                "项": f"轮{r_idx} 协调前冲突",
                "值": f"{p['pair']} {p['kind']}",
                "最小距离(m)": p["min_distance"],
                "CPA时刻(s)": p["cpa_time"],
            })
        if r_idx > 1:
            ev.append({
                "项": f"轮{r_idx} 旧侧冻结",
                "值": f"改动机号 {r['old_changed'] or '无'}",
                "最小距离(m)": "", "CPA时刻(s)": "",
            })
            for uid, sec in sorted(cross["overlap_s"].get(str(r_idx), {}).items()):
                ev.append({
                    "项": f"轮{r_idx} 在飞重叠/机{uid}", "值": f"{sec} s",
                    "最小距离(m)": "", "CPA时刻(s)": "",
                })
        busy = cross["busy_until_after"].get(str(r_idx), {})
        for uid, t in sorted(busy.items()):
            ev.append({
                "项": f"轮{r_idx} busy_until/机{uid}", "值": f"{t} s",
                "最小距离(m)": "", "CPA时刻(s)": "",
            })
    _write_rows(out_dir / _CROSS, ev,
                caption="跨轮证据：必撞触发、旧侧冻结、在飞中规划与状态账",
                label="tab:exp01-cross",
                note="由 exp01_smoke/build_tables.py 从 results 的单源重算。"
                     "新-旧冲突对必须出现（必撞设计生效），旧侧改动必须为空（机号小者冻结）。")
    print(f"[exp01] 表：{out_dir / _MAIN}.csv / .tex，{out_dir / _CROSS}.csv / .tex")
    return 0


def _start(req):
    from path_planning.common.plan_types import PlanStop
    return PlanStop(id=0, position=req.start_position, service_time_s=0.0, kind="task")


def _dist(a, b) -> float:
    return float(sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5)


def _ticket(trace: dict, r_idx: int, uid: int) -> dict | None:
    for r in trace["rounds"]:
        if int(r["round"]) == r_idx:
            for t in r["tickets"]:
                if int(t["uav_id"]) == uid:
                    return t
    return None


def _write_rows(path: Path, rows: list[dict], *, caption: str, label: str, note: str) -> None:
    cols = list(rows[0]) if rows else []
    with path.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([_cell(r[c]) for c in cols])

    n_num = sum(1 for c in cols if isinstance(rows[0][c], float)) if rows else 0
    lines = [
        f"% 由 exp01_smoke/build_tables.py 生成。{note}",
        "\\begin{table}[htbp]",
        "  \\centering",
        f"  \\caption{{{caption}}}",
        f"  \\label{{{label}}}",
        f"  \\begin{{tabular}}{{l{'r' * (len(cols) - 1)}}}",
        "    \\toprule",
        "    " + " & ".join(cols) + " \\\\",
        "    \\midrule",
    ]
    for r in rows:
        lines.append("    " + " & ".join(_cell(r[c]).replace("&", "\\&") for c in cols) + " \\\\")
    lines += ["    \\bottomrule", "  \\end{tabular}", "\\end{table}", ""]
    path.with_suffix(".tex").write_text("\n".join(lines), encoding="utf-8")


def _cell(v) -> str:
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
