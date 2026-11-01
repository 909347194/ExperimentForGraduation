# -*- coding: utf-8 -*-
"""全城可见性图建图 + 存盘（一次性，~21min）。

与 exp02_vg/run.py 的区别：不裁剪到小窗，直接在全量 20655 栋上建图；
关掉 max_nodes 上限（用 5e7 兜底）；只存最小可重建零件（节点 / 边 / 参数 / 元信息），
障碍物与禁飞在规划脚本里从同一份数据快速重建，避免 pickle 巨大对象（STRtree 等）。

产物：results/full_graph/{nodes.npy, edges.npz, params.json, meta.json}
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # path_planning/
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))  # 仓库根（导入前缀 path_planning.）
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
    build_visibility_graph,
)


def main() -> int:
    cfg = load_config(os.path.join(_HERE, "config.yaml"))
    sc = cfg.scenario
    scenario_dir = sc.get("dir", "data/raw/guangzhou")
    if not os.path.isabs(scenario_dir):
        scenario_dir = os.path.join(_ROOT, scenario_dir)

    print("[build_full] 场景目录:", scenario_dir, flush=True)
    env_full = load_scenario(scenario_dir, classes=sc.get("classes"),
                             crop=None, with_nofly=False)
    print(f"[build_full] 全量建筑 {len(env_full.buildings)} 栋", flush=True)

    # 全图包围盒（用于后续规划选跨城 OD 与出图范围）
    xs = [b.poly.bounds[0] for b in env_full.buildings] + [b.poly.bounds[2] for b in env_full.buildings]
    ys = [b.poly.bounds[1] for b in env_full.buildings] + [b.poly.bounds[3] for b in env_full.buildings]
    X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
    print(f"[build_full] 全图包围盒 X[{X0:.0f},{X1:.0f}] Y[{Y0:.0f},{Y1:.0f}] "
          f"≈{(X1-X0)*(Y1-Y0)/1e6:.2f} km²", flush=True)

    # 禁飞区（file，选项 A 真实口径）
    nofly_cfg = cfg.section("nofly")
    nofly_dir = nofly_cfg.get("dir", scenario_dir)
    if not os.path.isabs(nofly_dir):
        nofly_dir = os.path.join(_ROOT, nofly_dir)
    crs = LocalCRS(env_full.meta["crs"]["lon0"], env_full.meta["crs"]["lat0"])
    static_nofly = load_static_nofly(os.path.join(nofly_dir, "nofly_static.geojson"), crs, 5.0)
    dynamic_nofly = load_dynamic_nofly(os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs)
    env_full.static_nofly = static_nofly
    env_full.dynamic_nofly = dynamic_nofly
    print(f"[build_full] 禁飞区: 静态 {len(static_nofly)} / 动态 {len(dynamic_nofly)}", flush=True)

    params = VisibilityGraphParams.from_dict(cfg.section("visibility_graph"))
    # 关掉节点上限，允许全城规模（实测 104k 节点 < 5e7 兜底）
    params.max_nodes = 50_000_000
    print(f"[build_full] 方法参数: {params.to_dict()}", flush=True)

    t0 = time.perf_counter()
    graph, stats = build_visibility_graph(env_full, params, verbose=True)
    build_time = time.perf_counter() - t0
    print(f"[build_full] 建图完成: 节点 {stats['n_nodes']} 边(ON) {stats.get('n_edges_on', stats.get('n_edges'))} "
          f"用时 {build_time:.1f}s", flush=True)

    # ── 存盘（仅最小可重建零件）────────────────────────────────────
    out_dir = os.path.join(_HERE, "results", "full_graph")
    os.makedirs(out_dir, exist_ok=True)

    nodes = graph.nodes
    adj = graph.adj
    N = len(nodes)
    i_idx, j_idx, w_val = [], [], []
    for u, lst in enumerate(adj):
        for (v, w) in lst:
            if u < v:  # 无向边只存一次
                i_idx.append(u)
                j_idx.append(v)
                w_val.append(w)
    i_idx = np.asarray(i_idx, dtype=np.int64)
    j_idx = np.asarray(j_idx, dtype=np.int64)
    w_val = np.asarray(w_val, dtype=np.float64)

    np.save(os.path.join(out_dir, "nodes.npy"), nodes)
    np.savez(os.path.join(out_dir, "edges.npz"), i=i_idx, j=j_idx, w=w_val)
    with open(os.path.join(out_dir, "params.json"), "w", encoding="utf-8") as f:
        json.dump(params.to_dict(), f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "bounds": [round(X0, 1), round(Y0, 1), round(X1, 1), round(Y1, 1)],
            "area_km2": round((X1 - X0) * (Y1 - Y0) / 1e6, 4),
            "n_buildings": len(env_full.buildings),
            "n_nodes": int(N),
            "n_edges_stored": int(len(i_idx)),
            "build_time_s": round(build_time, 3),
            "params_method": params.to_dict(),
        }, f, ensure_ascii=False, indent=2)

    print(f"[build_full] DONE → {out_dir}")
    print(f"[build_full] 节点 {N} | 边 {len(i_idx)} | 建图 {build_time:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
