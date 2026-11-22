# -*- coding: utf-8 -*-
"""exp07_two_rounds 出表：``results/tables/exp07_main_results.{csv,tex}``。

单源：数字全部从 ``round_trace.json`` 与落盘航迹重算（``time_quality`` 给飞行/悬停/绕行比），
不手写、不复制控制台输出。一张表两块：逐轮指标（列 = 轮1/轮2/合计），
链式接口三个量单列一张小表——它们是本实验的核心判据，不能混进性能表里当普通行。
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
_MAIN = "exp07_main_results"
_CHAIN = "exp07_chain_results"

_HEAD_ROWS = (
    ("时长", "s", 2),
    ("飞行", "s", 2),
    ("悬停/服务", "s", 2),
    ("弧长", "m", 2),
    ("直线", "m", 2),
    ("绕行比", "", 4),
    ("均速", "m/s", 2),
    ("最差净空", "m", 2),
    ("交付闸", "", 0),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp07_two_rounds 出表")
    parser.add_argument("--config", default=str(_HERE / "config.yaml"))
    parser.add_argument("--fleet", default=str(_HERE / "uav_config.json"))
    parser.add_argument("--run", default=None, help="结果目录（缺省 = results/seed_<首个种子>）")
    parser.add_argument("--out", default=None, help="输出目录（缺省 = <run>/tables）")
    args = parser.parse_args(argv)

    runtime = load_runtime(args.config)
    fleet = load_fleet_input(args.fleet, runtime.config, runtime.scenario, require_tours=False)
    run_dir = Path(args.run) if args.run else (
        runtime.config.results_dir / f"seed_{runtime.config.seeds[0]}"
    )
    out_dir = Path(args.out) if args.out else run_dir / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)

    trace = json.loads((run_dir / "round_trace.json").read_text(encoding="utf-8"))
    trajs = {
        key: trajectory_from_dict(payload)
        for key, payload in json.loads(
            (run_dir / "round_trajectories.json").read_text(encoding="utf-8")
        ).items()
    }
    rounds = trace["rounds"]
    veh = fleet.vehicles[int(rounds[0]["uav_id"])]

    cols: list[dict[str, float]] = []
    for rec in rounds:
        traj = trajs[f"{rec['round']}:{rec['uav_id']}"]
        tq = time_quality(traj, veh)
        cols.append({
            "时长": tq["duration_s"],
            "飞行": tq["flight_s"],
            "悬停/服务": tq["hover_s"],
            "弧长": tq["path_len_m"],
            "直线": tq["direct_m"],
            "绕行比": tq["detour_ratio"] if tq["detour_ratio"] is not None else float("nan"),
            "均速": tq["mean_speed_mps"],
            "最差净空": float(rec["gate"]["worst_clearance_m"]),
            "交付闸": 1.0 if rec["gate"]["ok"] else 0.0,
        })
    total = _total(rounds, cols)
    headers = [f"轮{r['round']}" for r in rounds] + ["合计"]

    _write_main(out_dir / _MAIN, headers, cols, total, rounds)
    _write_chain(out_dir / _CHAIN, trace)
    print(f"[exp07] 表：{out_dir / _MAIN}.csv / .tex，{out_dir / _CHAIN}.csv / .tex")
    return 0


def _total(rounds: list[dict], cols: list[dict[str, float]]) -> dict[str, float]:
    """合计列：时间/里程相加；绕行比、均速按合计重算（不是逐轮比值的平均）；净空与闸取最不利。"""
    dur = sum(float(r["duration_s"]) for r in rounds)
    fly = sum(c["飞行"] for c in cols)
    hov = sum(c["悬停/服务"] for c in cols)
    path = sum(c["弧长"] for c in cols)
    direct = sum(c["直线"] for c in cols)
    return {
        "时长": dur,
        "飞行": fly,
        "悬停/服务": hov,
        "弧长": path,
        "直线": direct,
        "绕行比": path / direct if direct > 0 else float("nan"),
        "均速": path / fly if fly > 0 else float("nan"),
        "最差净空": min(c["最差净空"] for c in cols),
        "交付闸": min(c["交付闸"] for c in cols),
    }


def _write_main(path: Path, headers: list[str], cols: list[dict[str, float]],
                total: dict[str, float], rounds: list[dict]) -> None:
    rows = []
    for label, unit, dec in _HEAD_ROWS:
        values = [c[label] for c in cols] + [total[label]]
        rows.append((label, unit, dec, values))

    with path.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "unit", *headers])
        for label, unit, dec, values in rows:
            w.writerow([label, unit, *[f"{v:.{dec}f}" for v in values]])

    lines = [
        "% 由 exp07_two_rounds/build_tables.py 从 results/seed_0 的单源重算，勿手写数字。",
        "% 口径：时长含服务停留；绕行比 = 弧长 / 逐段直线和；合计列的时间/里程为逐轮相加，",
        "% 绕行比与均速按合计重新算（不是逐轮比值的平均）；最差净空与交付闸取最不利。",
        "\\begin{table}[htbp]",
        "  \\centering",
        "  \\caption{单机两轮链式航迹结果（guangzhou，种子 0；轮1 停在任务点，轮2 从该点续飞）}",
        "  \\label{tab:exp07-main}",
        "  \\begin{tabular}{lrrr}",
        "    \\toprule",
        "    指标 & " + " & ".join(headers) + " \\\\",
        "    \\midrule",
    ]
    for label, unit, dec, values in rows:
        name = label + (f" ({unit})" if unit else "")
        cells = [f"{v:.{dec}f}" for v in values]
        lines.append("    " + name.replace("&", "\\&") + " & " + " & ".join(cells) + " \\\\")
    lines += ["    \\bottomrule", "  \\end{tabular}", "\\end{table}", ""]
    path.with_suffix(".tex").write_text("\n".join(lines), encoding="utf-8")


def _write_chain(path: Path, trace: dict) -> None:
    """链式接口三个量 + 时间窗：本实验的核心判据，单独成表。"""
    rounds = trace["rounds"]
    prev, cur = rounds[0], rounds[1]
    pos_gap = (sum((a - b) ** 2 for a, b in zip(prev["end_position"],
                                                cur["start_position"])) ** 0.5)
    t_gap = float(cur["t0"]) - float(prev["t_end"])
    v = sum(x * x for x in cur["start_velocity"]) ** 0.5
    rows = [
        ("轮1 → 轮2 位置差", "m", pos_gap),
        ("轮1 → 轮2 时刻差", "s", t_gap),
        ("轮2 起飞速度模", "m/s", v),
    ]
    with path.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["metric", "unit", "value"]]
                                + [[m, u, f"{val:.6f}"] for m, u, val in rows])
    lines = [
        "% 由 exp07_two_rounds/build_tables.py 生成。三项全 0 才说明两轮是「同一点、同刻、静止」接上的。",
        "\\begin{table}[htbp]",
        "  \\centering",
        "  \\caption{两轮链式衔接的三项接口量（全 0 = 上一轮落点被下一轮原样接住）}",
        "  \\label{tab:exp07-chain}",
        "  \\begin{tabular}{lrr}",
        "    \\toprule",
        "    接口量 & 单位 & 实测 \\\\",
        "    \\midrule",
    ]
    for m, u, val in rows:
        lines.append(f"    {m} & {u} & {val:.6f} \\\\")
    lines += [
        f"    \\midrule",
        f"    轮1 时间窗 & s & [{prev['t0']:.2f}, {prev['t_end']:.2f}] \\\\",
        f"    轮2 时间窗 & s & [{cur['t0']:.2f}, {cur['t_end']:.2f}] \\\\",
        "    \\bottomrule", "  \\end{tabular}", "\\end{table}", "",
    ]
    path.with_suffix(".tex").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
