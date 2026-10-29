# -*- coding: utf-8 -*-
"""全图 / 大区 规模 profiling。

目的：用「同一套方法参数」（simplify_mode / d_build / node_step / node_buffer /
altitude_layers / grid_step / grid_layers / max_edge_len / edge_margin /
protection_clearance / max_degree 全部与 exp02 一致）在尽量大的范围建网，
给出 节点数 / 边数 / 建图时间 / 查询时间 随区域面积的曲线。

这是决定「要不要分区」的唯一依据：
    - 若全图节点/边/时间超出可承受上限 → 必须分区（每片控制在可承受规模）。
    - 同时报告「字面同参数（max_nodes=2000 封顶）」下大区会变成以粗网格为主、
      建筑轮廓采样被压制的结果，以对比「要覆盖 vs 要省」的取舍。

说明：仅把 max_nodes（预算安全上限，非方法定义参数）在测量时放宽到极大值，
以使建筑轮廓节点的自然密度随面积线性增长；方法/采样参数一律不变。
"""

from __future__ import annotations

import argparse
import copy as _copy
import json
import os
import sys
import time

import numpy as np
from shapely.geometry import box

# ── 包内导入（与 exp02 / exp01 同款路径装配）─────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))          # path_planning/
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))               # 仓库根（导入前缀 path_planning.）
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

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
    build_edges,
    generate_nodes,
    SimplifiedObstacles,
    NoFlyField,
)


CAP_NATURAL = 50_000_000   # 放宽安全上限，使建筑轮廓节点自然随面积增长
CAP_LITERAL = 2000         # exp02 字面配置（max_nodes 封顶）


def crop_env(env_full, window, max_buildings=None):
    env = _copy.copy(env_full)
    env.meta = dict(env_full.meta)
    win = box(*window)
    env.buildings = [b for b in env_full.buildings if b.poly.intersects(win)]
    if max_buildings and len(env.buildings) > int(max_buildings):
        cx, cy = (window[0] + window[2]) / 2.0, (window[1] + window[3]) / 2.0
        env.buildings.sort(key=lambda b: (b.poly.centroid.x - cx) ** 2 + (b.poly.centroid.y - cy) ** 2)
        env.buildings = env.buildings[: int(max_buildings)]
    for i, b in enumerate(env.buildings):
        b.bid = i
    return env


def build_graph(env, params, obstacles, nofly, cap, with_protection=True, verbose=False):
    """按指定 max_nodes 上限建一张图，返回 (nodes, meta, nstats, adj_on, st_on)。"""
    p = _copy.copy(params)
    p.max_nodes = int(cap)
    nodes, nmeta, nstats = generate_nodes(env, p, obstacles, nofly, extra_points=None)
    adj_on, st_on = build_edges(nodes, obstacles, p,
                                nofly_blocked=nofly.blocked,
                                with_protection=with_protection, verbose=verbose)
    return nodes, nmeta, nstats, adj_on, st_on


def main() -> int:
    ap = argparse.ArgumentParser(description="exp02_vg 大区规模 profiling")
    ap.add_argument("--config", default=os.path.join(_HERE, "config.yaml"))
    ap.add_argument("--sizes", default="",
                    help="逗号分隔的半宽(m)；留空则自动取几何级数并夹到全图范围")
    ap.add_argument("--max-buildings", type=int, default=None,
                    help="每片最大建筑数（默认不限）")
    ap.add_argument("--no-full", action="store_true",
                    help="不强制追加全图最大半宽（用于快速出曲线）")
    ap.add_argument("--on-only", action="store_true",
                    help="只建 ON(保护区) 边，省去 OFF 边构建（节点/边/时间曲线不需要 OFF）")
    ap.add_argument("--n-query", type=int, default=20, help="每片在线查询计时次数")
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    sc = cfg.scenario
    scenario_dir = sc.get("dir", "data/raw/guangzhou")
    if not os.path.isabs(scenario_dir):
        scenario_dir = os.path.join(_ROOT, scenario_dir)

    print(f"[profile] 场景目录: {scenario_dir}")
    env_full = load_scenario(scenario_dir, classes=sc.get("classes"),
                             crop=None, with_nofly=False)
    print(f"[profile] 全量建筑 {len(env_full.buildings)} 栋")

    # 全图包围盒与中心
    xs = [b.poly.bounds[0] for b in env_full.buildings] + [b.poly.bounds[2] for b in env_full.buildings]
    ys = [b.poly.bounds[1] for b in env_full.buildings] + [b.poly.bounds[3] for b in env_full.buildings]
    X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
    cx, cy = 0.5 * (X0 + X1), 0.5 * (Y0 + Y1)
    full_half = min(cx - X0, X1 - cx, cy - Y0, Y1 - cy)
    full_area_km2 = (X1 - X0) * (Y1 - Y0) / 1e6
    print(f"[profile] 全图包围盒 X[{X0:.0f},{X1:.0f}] Y[{Y0:.0f},{Y1:.0f}] "
          f"面积≈{full_area_km2:.2f} km², 中心=({cx:.0f},{cy:.0f}), 最大半宽={full_half:.0f} m")

    # 禁飞区（每片复用同一份全量，STRtree 查询开销可忽略）
    nofly_cfg = cfg.section("nofly")
    nofly_dir = nofly_cfg.get("dir", scenario_dir)
    if not os.path.isabs(nofly_dir):
        nofly_dir = os.path.join(_ROOT, nofly_dir)
    crs = LocalCRS(env_full.meta["crs"]["lon0"], env_full.meta["crs"]["lat0"])
    static_nofly = load_static_nofly(os.path.join(nofly_dir, "nofly_static.geojson"), crs, 5.0)
    dynamic_nofly = load_dynamic_nofly(os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs)

    params = VisibilityGraphParams.from_dict(cfg.section("visibility_graph"))
    print(f"[profile] 方法参数(与 exp02 一致): {params.to_dict()}")

    # 半宽序列（夹到全图范围，并强制包含全图最大半宽作为末点）
    if args.sizes.strip():
        req = [int(float(s)) for s in args.sizes.split(",") if s.strip()]
    else:
        req = [300, 500, 750, 1000, 1300, 1700, 2200]
    req = sorted(set([min(h, int(full_half)) for h in req] + ([int(full_half)] if not args.no_full else [])))
    req = [h for h in req if h >= 50]
    print(f"[profile] 将扫描半宽(夹到全图): {req}")

    t_total = time.perf_counter()
    results = []
    for h in req:
        win = [cx - h, cy - h, cx + h, cy + h]
        win = [max(win[0], X0), max(win[1], Y0), min(win[2], X1), min(win[3], Y1)]
        env = crop_env(env_full, win, args.max_buildings)
        if len(env.buildings) < 1:
            print(f"  skip h={h} (无建筑)")
            continue
        env.static_nofly = static_nofly
        env.dynamic_nofly = dynamic_nofly

        # ── 离线建图（计时：障碍简化 + 节点 + 两种边）─────────────────────
        t0 = time.perf_counter()
        obstacles = SimplifiedObstacles(env.buildings, d_build=params.d_build,
                                        mode=params.simplify_mode, tol=params.simplify_tol)
        min_layer = min(params.altitude_layers)
        obstacles.filter_by_min_height(min_layer - 1.0)
        nofly = NoFlyField(static_nofly, dynamic_nofly,
                           as_static=params.include_dynamic_as_static)

        # 自然密度（安全上限放宽）
        nodes_n, nmeta_n, nstats_n, adj_on_n, st_on_n = build_graph(
            env, params, obstacles, nofly, CAP_NATURAL, True, False)
        if args.on_only:
            st_off_n = None
        else:
            adj_off_n, st_off_n = build_edges(
                nodes_n, obstacles, params, nofly_blocked=nofly.blocked,
                with_protection=False, verbose=False)
        # 字面同参数（max_nodes=2000 封顶）
        nodes_c, _, nstats_c, adj_on_c, st_on_c = build_graph(
            env, params, obstacles, nofly, CAP_LITERAL, True, False)

        build_time = time.perf_counter() - t0

        # ── 在线查询计时（自然密度图，固定种子取若干 OD）─────────────────
        graph_n = VisibilityGraph(nodes_n, adj_on_n, obstacles, nofly, params, meta=nmeta_n)
        q_t0 = time.perf_counter()
        rng = np.random.default_rng(20261029)
        N = len(nodes_n)
        nq = 0
        for _ in range(args.n_query):
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
            # 自然密度（放宽上限）
            "nat_nodes": nstats_n["n_nodes"],
            "nat_grid": nstats_n["n_grid"],
            "nat_building": nstats_n["n_building"],
            "nat_edges_on": st_on_n["n_edges"],
            "nat_edges_off": (st_off_n["n_edges"] if st_off_n else None),
            "nat_edge_reduction_pct": (round((1 - st_on_n["n_edges"] / st_off_n["n_edges"]) * 100, 2)
                                       if st_off_n and st_off_n["n_edges"] else None),
            # 字面同参数（max_nodes=2000 封顶）
            "cap2000_nodes": nstats_c["n_nodes"],
            "cap2000_grid": nstats_c["n_grid"],
            "cap2000_building": nstats_c["n_building"],
            "cap2000_edges_on": st_on_c["n_edges"],
            # 代价
            "build_time_s": round(build_time, 3),
            "query_time_per_od_s": round(q_time, 5),
            "nodes_per_km2": round(nstats_n["n_nodes"] / area_km2, 1) if area_km2 > 0 else None,
            "edges_per_km2": round(st_on_n["n_edges"] / area_km2, 1) if area_km2 > 0 else None,
        }
        results.append(rec)
        red_pct = rec['nat_edge_reduction_pct']
        red_str = f"-{red_pct:.1f}%" if red_pct is not None else "(off 未构建)"
        print(f"  h={h:5d}m  area={area_km2:6.2f}km²  bld={len(env.buildings):5d} | "
              f"NAT nodes={rec['nat_nodes']:7d} (grid {rec['nat_grid']}/{rec['nat_building']} bld) "
              f"edges={rec['nat_edges_on']:8d} ({red_str}) | "
              f"cap2000 nodes={rec['cap2000_nodes']:6d} | build={rec['build_time_s']:.2f}s "
              f"query={rec['query_time_per_od_s']*1000:.2f}ms")

    wall = time.perf_counter() - t_total

    # ── 全图外推 / 分区建议 ──────────────────────────────────────────────
    rec_full = results[-1] if results else None
    suggestion = None
    if rec_full is not None:
        target_per_tile = 2000
        tiles = max(1, int(np.ceil(rec_full["nat_nodes"] / target_per_tile)))
        suggestion = {
            "max_half_width_m": rec_full["half_width_m"],
            "max_area_km2": rec_full["area_km2"],
            "max_nat_nodes": rec_full["nat_nodes"],
            "max_build_time_s": rec_full["build_time_s"],
            "suggested_tile_nodes_target": target_per_tile,
            "suggested_n_tiles": tiles,
            "note": "若全图规模超出可承受上限，则按 ~target_per_tile 节点/片划分网格瓦片。",
        }

    out = {
        "params_method": params.to_dict(),
        "max_nodes_cap_natural": CAP_NATURAL,
        "max_nodes_cap_literal": CAP_LITERAL,
        "full_map": {
            "bounds": [round(X0, 1), round(Y0, 1), round(X1, 1), round(Y1, 1)],
            "area_km2": round(full_area_km2, 4),
            "n_buildings_total": len(env_full.buildings),
            "center": [round(cx, 1), round(cy, 1)],
        },
        "sizes_scanned": req,
        "results": results,
        "partition_suggestion": suggestion,
        "wall_time_s": round(wall, 3),
    }
    out_dir = os.path.join(_HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "profile_scaling.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("\n================ profiling 汇总 ================")
    print(f"全图: {full_area_km2:.2f} km², {len(env_full.buildings)} 栋建筑")
    for r in results:
        print(f"  半宽 {r['half_width_m']:5d}m | NAT 节点 {r['nat_nodes']:7d} | "
              f"边 {r['nat_edges_on']:8d} | 建图 {r['build_time_s']:7.2f}s | "
              f"查询 {r['query_time_per_od_s']*1000:6.2f}ms")
    if suggestion:
        print(f"分区建议: 全图≈{suggestion['max_nat_nodes']} 节点 / "
              f"{suggestion['max_build_time_s']:.1f}s → 建议拆成 ~{suggestion['suggested_n_tiles']} 片 "
              f"(目标 {suggestion['suggested_tile_nodes_target']} 节点/片)")
    print(f"结果 → {out_dir}/profile_scaling.json")

    if args.plot:
        _plot(results, out_dir)

    return 0


def _plot(results, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hs = [r["half_width_m"] for r in results]
    areas = [r["area_km2"] for r in results]
    nat_n = [r["nat_nodes"] for r in results]
    cap_n = [r["cap2000_nodes"] for r in results]
    nat_e = [r["nat_edges_on"] for r in results]
    cap_e = [r["cap2000_edges_on"] for r in results]
    bt = [r["build_time_s"] for r in results]
    qt = [r["query_time_per_od_s"] * 1000 for r in results]

    fig, axs = plt.subplots(2, 2, figsize=(13, 10))

    ax = axs[0, 0]
    ax.plot(areas, nat_n, "o-", label="自然密度 (上限放宽)")
    ax.plot(areas, cap_n, "s--", label="字面同参数 (max_nodes=2000)")
    ax.set_xlabel("区域面积 (km²)")
    ax.set_ylabel("节点数")
    ax.set_title("节点数 vs 面积")
    ax.legend(); ax.grid(alpha=0.3); ax.set_yscale("log")

    ax = axs[0, 1]
    ax.plot(areas, nat_e, "o-", label="自然密度 (ON 保护区)")
    ax.plot(areas, cap_e, "s--", label="字面同参数 (max_nodes=2000)")
    ax.set_xlabel("区域面积 (km²)")
    ax.set_ylabel("边数")
    ax.set_title("边数 vs 面积")
    ax.legend(); ax.grid(alpha=0.3); ax.set_yscale("log")

    ax = axs[1, 0]
    ax.plot(areas, bt, "o-", color="purple")
    ax.set_xlabel("区域面积 (km²)")
    ax.set_ylabel("离线建图时间 (s)")
    ax.set_title("建图时间 vs 面积")
    ax.grid(alpha=0.3)

    ax = axs[1, 1]
    ax.plot(areas, qt, "o-", color="teal")
    ax.set_xlabel("区域面积 (km²)")
    ax.set_ylabel("在线查询 (ms/OD)")
    ax.set_title("查询时间 vs 面积")
    ax.grid(alpha=0.3)

    fig.suptitle("Visibility-graph scaling (fixed method params)")
    fig.tight_layout()
    path = os.path.join(out_dir, "figures", "profile_scaling.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    print(f"[profile] 曲线图 → {path}")


if __name__ == "__main__":
    raise SystemExit(main())
