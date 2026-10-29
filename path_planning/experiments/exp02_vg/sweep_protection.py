# -*- coding: utf-8 -*-
"""机动保护区灵敏度扫描：固定图结构，扫 protection_clearance，验证「删边 ~70% + 路径仅略增」。

复用 run.py 的场景装配与 OD 选择逻辑；只改变 build_edges 的保护净空，
度量：① 删边比例（对照 OFF=无保护）② ON 相对 OFF 的最优路径长度增量。
"""
import os, sys
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import copy as _copy
from shapely.geometry import box
from path_planning.common.config import load_config
from path_planning.common.scenario import (load_scenario, load_dynamic_nofly, load_static_nofly)
from path_planning.utils.geometry import LocalCRS
from path_planning.methods.visibility_graph import (
    VisibilityGraphParams, generate_nodes, build_edges,
    SimplifiedObstacles, NoFlyField, VisibilityGraph,
)
from path_planning.methods.control_network_planning.constraints.query import CollisionQuery
from path_planning.experiments.exp02_vg.run import select_ods_from_nodes, audit_path

cfg = load_config(os.path.join(_HERE, "config.yaml"))
sc = cfg.scenario
scenario_dir = os.path.join(_ROOT, sc.get("dir")) if not os.path.isabs(sc.get("dir")) else sc.get("dir")

env_full = load_scenario(scenario_dir, classes=sc.get("classes"), crop=None, with_nofly=False)
crop = sc.get("crop")
env = _copy.copy(env_full); env.meta = dict(env_full.meta)
win = box(*crop)
env.buildings = [b for b in env_full.buildings if b.poly.intersects(win)]
env.buildings.sort(key=lambda b: (b.poly.centroid.x - (crop[0]+crop[2])/2)**2 + (b.poly.centroid.y - (crop[1]+crop[3])/2)**2)
env.buildings = env.buildings[: int(sc.get("max_buildings"))]
for i, b in enumerate(env.buildings):
    b.bid = i

nofly_dir = os.path.join(_ROOT, cfg.section("nofly").get("dir"))
crs = LocalCRS(env.meta["crs"]["lon0"], env.meta["crs"]["lat0"])
env.static_nofly = load_static_nofly(os.path.join(nofly_dir, "nofly_static.geojson"), crs, 5.0)
env.dynamic_nofly = load_dynamic_nofly(os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs)

params = VisibilityGraphParams.from_dict(cfg.section("visibility_graph"))
obstacles = SimplifiedObstacles(env.buildings, d_build=params.d_build, mode=params.simplify_mode, tol=params.simplify_tol)
min_layer = min(params.altitude_layers)
obstacles.filter_by_min_height(min_layer - 1.0)
nofly = NoFlyField(env.static_nofly, env.dynamic_nofly, as_static=params.include_dynamic_as_static)
nodes, node_meta, node_stats = generate_nodes(env, params, obstacles, nofly, extra_points=None)
audit_q = CollisionQuery(env, d_s_stat=5.0, d_s_dyn=2.0)
ods = select_oids = select_ods_from_nodes(nodes, cfg, audit_q)
print(f"节点数 {node_stats['n_nodes']}；OD 数 {len(ods)}")

print(f"\n{'clearance':>10} | {'edges_OFF':>9} {'edges_ON':>9} {'删边%':>7} | "
      f"{'ON可行':>6} {'审计全清':>6} | {'meanΔ%':>7} {'maxΔ%':>7}")
print("-" * 78)

results = []
for clr in [2.0, 4.0, 6.0, 8.0, 12.0]:
    p = _copy.copy(params)
    p.protection_clearance = clr
    adj_on, st_on = build_edges(nodes, obstacles, p, nofly_blocked=nofly.blocked, with_protection=True, verbose=False)
    adj_off, st_off = build_edges(nodes, obstacles, p, nofly_blocked=nofly.blocked, with_protection=False, verbose=False)
    g_on = VisibilityGraph(nodes, adj_on, obstacles, nofly, p, meta=node_meta)
    g_off = VisibilityGraph(nodes, adj_off, obstacles, nofly, p, meta=node_meta)

    deltas, feas, audits = [], 0, 0
    for (o, d) in ods:
        _, _, c_on = g_on.plan(o, d)
        _, _, c_off = g_off.plan(o, d)
        if c_on is not None and c_on != float("inf"):
            feas += 1
            if audit_path(g_on.plan(o, d)[0], audit_q, 2.0):
                audits += 1
            if c_off is not None and c_off != float("inf") and c_off > 1e-6:
                deltas.append((c_on - c_off) / c_off * 100.0)
    red = (1 - st_on["n_edges"] / st_off["n_edges"]) * 100 if st_off["n_edges"] else 0
    mean_d = float(np.mean(deltas)) if deltas else float("nan")
    max_d = float(np.max(deltas)) if deltas else float("nan")
    print(f"{clr:>10.1f} | {st_off['n_edges']:>9} {st_on['n_edges']:>9} {red:>6.1f}% | "
          f"{feas:>6}/{len(ods)} {audits:>6}/{feas} | {mean_d:>6.2f}% {max_d:>6.2f}%")
    results.append({"clearance": clr, "edge_reduction_pct": round(red, 2),
                    "n_edges_on": st_on["n_edges"], "n_edges_off": st_off["n_edges"],
                    "feasible_on": feas, "audited_clear": audits,
                    "mean_path_increase_pct": round(mean_d, 2), "max_path_increase_pct": round(max_d, 2)})

out_dir = os.path.join(_HERE, "results")
os.makedirs(out_dir, exist_ok=True)
import json
with open(os.path.join(out_dir, "sweep_protection_clearance.json"), "w", encoding="utf-8") as f:
    json.dump({"nodes": node_stats["n_nodes"], "n_od": len(ods), "sweep": results}, f, indent=2, ensure_ascii=False)
print(f"\n扫描结果已写入 {out_dir}/sweep_protection_clearance.json")
