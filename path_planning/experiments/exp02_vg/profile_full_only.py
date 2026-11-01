# -*- coding: utf-8 -*-
"""只跑全图（23.23 km²）一次实测，复用 profile_scaling 的建图函数。

与 profile_scaling.py 的区别：不重跑 h=300..1300（已有），只做全图那一次，
结果单独写 results/profile_full_region.json，避免覆盖已有曲线。
用当前 config 的 include_dynamic_as_static（选项 A 真实部署口径）。
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from path_planning.common.config import load_config  # noqa: E402
from path_planning.common.scenario import (  # noqa: E402
    load_scenario,
    load_dynamic_nofly,
    load_static_nofly,
)
from path_planning.utils.geometry import LocalCRS  # noqa: E402
from path_planning.methods.visibility_graph import (  # noqa: E402
    VisibilityGraph,
    VisibilityGraphParams,
)
import profile_scaling as ps  # noqa: E402


def main() -> int:
    cfg = load_config(os.path.join(_HERE, "config.yaml"))
    sc = cfg.scenario
    scenario_dir = sc.get("dir", "data/raw/guangzhou")
    if not os.path.isabs(scenario_dir):
        scenario_dir = os.path.join(_ROOT, scenario_dir)

    print("[full] 场景目录:", scenario_dir, flush=True)
    env_full = load_scenario(scenario_dir, classes=sc.get("classes"),
                             crop=None, with_nofly=False)
    print(f"[full] 全量建筑 {len(env_full.buildings)} 栋", flush=True)

    xs = [b.poly.bounds[0] for b in env_full.buildings] + [b.poly.bounds[2] for b in env_full.buildings]
    ys = [b.poly.bounds[1] for b in env_full.buildings] + [b.poly.bounds[3] for b in env_full.buildings]
    X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
    cx, cy = 0.5 * (X0 + X1), 0.5 * (Y0 + Y1)
    full_half = min(cx - X0, X1 - cx, cy - Y0, Y1 - cy)
    full_area_km2 = (X1 - X0) * (Y1 - Y0) / 1e6
    print(f"[full] 全图包围盒 X[{X0:.0f},{X1:.0f}] Y[{Y0:.0f},{Y1:.0f}] "
          f"面积≈{full_area_km2:.2f} km², 中心=({cx:.0f},{cy:.0f}), 最大半宽={full_half:.0f} m", flush=True)

    nofly_cfg = cfg.section("nofly")
    nofly_dir = nofly_cfg.get("dir", scenario_dir)
    if not os.path.isabs(nofly_dir):
        nofly_dir = os.path.join(_ROOT, nofly_dir)
    crs = LocalCRS(env_full.meta["crs"]["lon0"], env_full.meta["crs"]["lat0"])
    static_nofly = load_static_nofly(os.path.join(nofly_dir, "nofly_static.geojson"), crs, 5.0)
    dynamic_nofly = load_dynamic_nofly(os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs)

    params = VisibilityGraphParams.from_dict(cfg.section("visibility_graph"))
    print(f"[full] 方法参数: {params.to_dict()}", flush=True)

    # 全图窗口
    h = int(full_half)
    win = [cx - h, cy - h, cx + h, cy + h]
    win = [max(win[0], X0), max(win[1], Y0), min(win[2], X1), min(win[3], Y1)]
    env = ps.crop_env(env_full, win, None)
    env.static_nofly = static_nofly
    env.dynamic_nofly = dynamic_nofly
    print(f"[full] 全图窗口 {[round(w, 1) for w in win]} 建筑 {len(env.buildings)} 栋", flush=True)

    # 离线建图（ON 边；不构 OFF 边以省约一半时间）
    t0 = time.perf_counter()
    obstacles = ps.SimplifiedObstacles(env.buildings, d_build=params.d_build,
                                       mode=params.simplify_mode, tol=params.simplify_tol)
    min_layer = min(params.altitude_layers)
    obstacles.filter_by_min_height(min_layer - 1.0)
    nofly = ps.NoFlyField(static_nofly, dynamic_nofly,
                          as_static=params.include_dynamic_as_static)

    nodes_n, nmeta_n, nstats_n, adj_on_n, st_on_n = ps.build_graph(
        env, params, obstacles, nofly, ps.CAP_NATURAL, True, False)
    nodes_c, _, nstats_c, adj_on_c, st_on_c = ps.build_graph(
        env, params, obstacles, nofly, ps.CAP_LITERAL, True, False)
    build_time = time.perf_counter() - t0
    print(f"[full] 建图完成: NAT 节点 {nstats_n['n_nodes']} 边 {st_on_n['n_edges']} "
          f"用时 {build_time:.1f}s", flush=True)

    # 在线查询计时（NAT 图）
    graph_n = VisibilityGraph(nodes_n, adj_on_n, obstacles, nofly, params, meta=nmeta_n)
    q_t0 = time.perf_counter()
    rng = np.random.default_rng(20261029)
    N = len(nodes_n)
    nq = 0
    for _ in range(3):
        i, j = rng.integers(0, N, 2)
        if i == j:
            continue
        graph_n.plan(nodes_n[i], nodes_n[j])
        nq += 1
    q_time = (time.perf_counter() - q_t0) / max(nq, 1)

    area_km2 = (win[2] - win[0]) * (win[3] - win[1]) / 1e6
    rec = {
        "half_width_m": int(h),
        "window": [round(w, 1) for w in win],
        "area_km2": round(area_km2, 4),
        "n_buildings": len(env.buildings),
        "nat_nodes": nstats_n["n_nodes"],
        "nat_grid": nstats_n["n_grid"],
        "nat_building": nstats_n["n_building"],
        "nat_edges_on": st_on_n["n_edges"],
        "nat_edges_off": None,
        "nat_edge_reduction_pct": None,
        "cap2000_nodes": nstats_c["n_nodes"],
        "cap2000_grid": nstats_c["n_grid"],
        "cap2000_building": nstats_c["n_building"],
        "cap2000_edges_on": st_on_c["n_edges"],
        "build_time_s": round(build_time, 3),
        "query_time_per_od_s": round(q_time, 5),
        "nodes_per_km2": round(nstats_n["n_nodes"] / area_km2, 1) if area_km2 > 0 else None,
        "edges_per_km2": round(st_on_n["n_edges"] / area_km2, 1) if area_km2 > 0 else None,
    }
    out = {
        "params_method": params.to_dict(),
        "include_dynamic_as_static_used": params.include_dynamic_as_static,
        "full_map": {
            "bounds": [round(X0, 1), round(Y0, 1), round(X1, 1), round(Y1, 1)],
            "area_km2": round(full_area_km2, 4),
            "n_buildings_total": len(env_full.buildings),
            "center": [round(cx, 1), round(cy, 1)],
            "max_half_width_m": int(full_half),
        },
        "full_region": rec,
    }
    out_dir = os.path.join(_HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "profile_full_region.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("[full] DONE →", json.dumps(rec, ensure_ascii=False), flush=True)
    print(f"[full] 建图 {rec['build_time_s']:.1f}s | 查询 {rec['query_time_per_od_s']*1000:.1f}ms | "
          f"节点 {rec['nat_nodes']} | 边 {rec['nat_edges_on']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
