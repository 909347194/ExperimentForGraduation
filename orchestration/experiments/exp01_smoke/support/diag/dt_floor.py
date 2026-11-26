# -*- coding: utf-8 -*-
"""短航段离散退化诊断：只扫 n × dt_min，不改生产代码、不改正式账。

对象：2026-11-24 冻结的**历史**航段（旧 `tasks.json` v1 的 T302 → T202，水平 ~17.5 m，
      两端 SDF ~10 m，直线初值）。旧清单已删，当前 T1–T30 里没有这两个点；
      本脚本保留作「dt_min 地板」机制的复现凭据，不是当前实验的诊断入口。

为什么成立：这条航段的交付曲线出现竖直拱形。怀疑是固定 n=120 配上
``(n-1)·dt_min = 5.95 s`` 的时长地板把 ``min ΣΔt`` 压平，z 方向失去拉直力。
本脚本用与正式运行**同一套**走廊窗口、SDF 插值器、NlpConfig 构造，只替换
``n_nodes`` 与 ``dt_min_s`` 两个字段，比较 ΣΔt / 弧长 / z 峰谷。

跑法::

    python -m orchestration.experiments.exp01_smoke.support.diag.dt_floor
    python -m orchestration.experiments.exp01_smoke.support.diag.dt_floor --n 120,80 --dt 0.05,0.01

输出：results/diagnose/diagnose_dt_floor.csv（追加前先删旧文件由脚本自己覆盖）。
      刻意不写 seed_<N>/：那份目录只放正式运行，诊断不占正式账。
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from orchestration.experiments.exp01_smoke.support import field as field_mod
from orchestration.experiments.exp01_smoke.support import fleet as fleet_mod
from orchestration.experiments.exp01_smoke.support.config import EXP_DIR, load_config
from path_planning.methods.spatial.corridor_window import corridor_window
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.sdf_casadi import SdfOnGrid
from path_planning.methods.timeline.initial_guess import resample_path
from path_planning.methods.timeline.nlp_config import NlpConfig
from path_planning.methods.timeline.trajectory_assembly import hermite_axis
from path_planning.methods.timeline.trajectory_optimizer import (
    _solve_once,
    adaptive_dt_min_s,
    optimize_trajectory,
)

# 302 → 202（周期 3 机 6 的末段）；坐标取自 tasks.json，未做任何 round。
START = np.array([-135.50498671648768, -1371.0186749794302, 31.755594555989475])
GOAL = np.array([-152.17164555454997, -1376.2034067186971, 29.43963952657796])
UAV_ID = 6
T0 = 3600.0


def _trajectory_shape(traj: Any) -> dict[str, float]:
    """交付曲线的 ΣΔt、弧长与 z 峰谷（段内按 21 点采样，含端点）。"""
    total_dt = 0.0
    zs: list[float] = []
    for seg in traj.segments:
        dt = float(seg.t1 - seg.t0)
        total_dt += dt
        for k in range(21):
            s = k / 20.0
            zs.append(float(hermite_axis(seg.p0[2], seg.v0[2], seg.p1[2], seg.v1[2], dt, s)))
    return {
        "sum_dt_s": total_dt,
        "arc_m": float(traj.path_len_m),
        "z_min_m": min(zs),
        "z_max_m": max(zs),
        "z_span_m": max(zs) - min(zs),
        "end_z_span_m": abs(float(START[2]) - float(GOAL[2])),
    }


def _fmt(seq: list[int] | list[float]) -> str:
    return ",".join(str(v) for v in seq)


def diagnose(ns: list[int], dt_mins: list[float], out_csv: Path,
             verify_fix: bool = False) -> int:
    cfg = load_config()
    scenario = field_mod.load_guangzhou(cfg)
    field, info = field_mod.load_or_build_field(cfg, scenario)
    print(f"场来源 {info['source']}（{info['elapsed_s']:.1f}s）｜shape={field.grid.shape}")

    params0 = field_mod.plan_params(cfg)
    band = field_mod.absolute_band(field, params0)
    window = corridor_window(
        tuple(float(v) for v in START), tuple(float(v) for v in GOAL),
        field_mod.corridor_rule(cfg), tuple(float(v) for v in field.grid.spacing),
        float(band[0]), float(band[1]),
    )
    window_field = SdfField(window.grid, field.window_sdf(window.grid))
    sdf = SdfOnGrid(window_field)
    print(f"走廊半宽 {window.half_width_m:.3f} m｜窗口 {window.grid.shape}"
          f"｜高度带 {band}")
    print(f"端点 SDF {field.at_one(*START):.3f} / {field.at_one(*GOAL):.3f} m")

    rows = fleet_mod.load_uav_config()
    vehicle = fleet_mod.lower_vehicles(rows)[UAV_ID]
    base_cfg = NlpConfig.from_params(
        params0, vehicle,
        clearance_m=float(params0.clearance_m) + float(field.grid.spacing[2]),
        half_width_m=float(window.half_width_m),
        corridor_normal_xy=(float(window.normal[0]), float(window.normal[1])),
        corridor_pivot_xy=(float(window.start_xy[0]), float(window.start_xy[1])),
        max_time_s=None,
        alt_range_m=band,
    )
    floor_s = (int(ns[0]) - 1) * dt_mins[0]
    print(f"基准时长地板 (n-1)·dt_min = {floor_s:.3f} s（n={ns[0]}, dt={dt_mins[0]}）\n")

    if verify_fix:
        path = resample_path(np.array([START, GOAL]), int(base_cfg.n_nodes))
        dt_eff = adaptive_dt_min_s(path, base_cfg)
        print(f"验证修复：配置 dt_min={base_cfg.dt_min_s:.3f} → 自适应 {dt_eff:.6f} s"
              f"（地板 {(base_cfg.n_nodes - 1) * dt_eff:.3f} s）")
        res = optimize_trajectory(path, window_field, base_cfg,
                                  uav_id=UAV_ID, t0=T0, sdf_cache={})
        if res.trajectory is None:
            print(f"修复后求解失败：{res.solver_status}｜{res.message}")
            return 1
        shape = _trajectory_shape(res.trajectory)
        print(f"修复后 Σdt={shape['sum_dt_s']:.4f}s 弧长={shape['arc_m']:.3f}m "
              f"z=[{shape['z_min_m']:.3f},{shape['z_max_m']:.3f}] "
              f"端点 z 上界 {max(START[2], GOAL[2]):.3f}")
        return 0

    header = ["n", "dt_min_s", "floor_s", "sum_dt_s", "arc_m", "z_min_m",
              "z_max_m", "z_span_m", "status", "iters", "solve_s"]
    results: list[dict[str, Any]] = []
    for n in ns:
        for dt_min in dt_mins:
            params = replace(params0, n_nodes=int(n), dt_min_s=float(dt_min))
            cfg_use = replace(base_cfg, n_nodes=int(n), dt_min_s=float(dt_min))
            path = resample_path(np.array([START, GOAL]), int(n))
            t0 = time.perf_counter()
            res = _solve_once(path, sdf, cfg_use, int(n), int(params.max_iter),
                              None, UAV_ID, T0)
            elapsed = time.perf_counter() - t0
            if res.trajectory is None:
                row = dict(zip(header, [n, dt_min, (n - 1) * dt_min, "", "", "", "", "",
                                        res.solver_status, res.iterations, round(elapsed, 3)]))
                row["message"] = res.message
            else:
                shape = _trajectory_shape(res.trajectory)
                row = dict(zip(header, [n, dt_min, (n - 1) * dt_min,
                                        round(shape["sum_dt_s"], 4), round(shape["arc_m"], 3),
                                        round(shape["z_min_m"], 3), round(shape["z_max_m"], 3),
                                        round(shape["z_span_m"], 3),
                                        res.solver_status, res.iterations, round(elapsed, 3)]))
                row["message"] = res.message
            results.append(row)
            print(f"n={n:3d} dt={dt_min:.3f} 地板={(n-1)*dt_min:6.3f}s "
                  f"Σdt={row['sum_dt_s']!s:>8} 弧长={row['arc_m']!s:>9} "
                  f"z=[{row['z_min_m']!s},{row['z_max_m']!s}] "
                  f"{row['status']} ({elapsed:.2f}s)")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header + ["message"])
        w.writeheader()
        w.writerows(results)
    print(f"\n落盘：{out_csv}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="302→202 短航段 n × dt_min 诊断")
    p.add_argument("--n", default="120,80,60,40")
    p.add_argument("--dt", default="0.05,0.02,0.01")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--verify-fix", action="store_true",
                   help="走生产入口 optimize_trajectory，验证自适应 dt_min 是否消除拱形")
    a = p.parse_args(argv)
    out = a.out or (EXP_DIR / "results" / "diagnose" / "diagnose_dt_floor.csv")
    return diagnose([int(x) for x in a.n.split(",")],
                    [float(x) for x in a.dt.split(",")], out, a.verify_fix)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
