# -*- coding: utf-8 -*-
"""全城 3-OD 规划 + 出全范围图（加载 build_full_graph 的产物，秒级）。

流程：加载 nodes/edges/params → 从同一份数据快速重建 obstacles+nofly → 复原 VisibilityGraph
→ 在 4 角/中心选 3 对跨城 OD（取离目标坐标最近的图节点作端点）
→ 全图规划 → 画「全城建筑底图（质心散点）+ 3 条跨城路径 + 起终点」。

注意：只画 3 条路径 + 建筑质心散点，绝不画 2.4M 条边（会卡死且糊成一团）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

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
    SimplifiedObstacles,
    NoFlyField,
)


def _nearest_node(nodes: np.ndarray, x: float, y: float) -> np.ndarray:
    c = np.array([x, y], dtype=float)
    d = np.linalg.norm(nodes[:, :2] - c, axis=1)
    return nodes[int(np.argmin(d))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(_HERE, "config.yaml"))
    ap.add_argument("--gdir", default=os.path.join(_HERE, "results", "full_graph"))
    ap.add_argument("--od", type=int, default=3, help="跨城 OD 对数")
    args = ap.parse_args()

    cfg = load_config(args.config)
    sc = cfg.scenario
    scenario_dir = sc.get("dir", "data/raw/guangzhou")
    if not os.path.isabs(scenario_dir):
        scenario_dir = os.path.join(_ROOT, scenario_dir)

    # ── 加载图零件 ───────────────────────────────────────────────
    gdir = args.gdir
    nodes = np.load(os.path.join(gdir, "nodes.npy"))
    E = np.load(os.path.join(gdir, "edges.npz"))
    i_idx, j_idx, w_val = E["i"], E["j"], E["w"]
    with open(os.path.join(gdir, "params.json"), encoding="utf-8") as f:
        params = VisibilityGraphParams.from_dict(json.load(f))
    with open(os.path.join(gdir, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    X0, Y0, X1, Y1 = meta["bounds"]
    print(f"[plan_full] 载入图: 节点 {len(nodes)} 边 {len(i_idx)} | 范围 X[{X0:.0f},{X1:.0f}] Y[{Y0:.0f},{Y1:.0f}]", flush=True)

    # ── 从同一份数据快速重建 obstacles + nofly（避免 pickle 巨大对象）──
    env_full = load_scenario(scenario_dir, classes=sc.get("classes"), crop=None, with_nofly=False)
    nofly_cfg = cfg.section("nofly")
    nofly_dir = nofly_cfg.get("dir", scenario_dir)
    if not os.path.isabs(nofly_dir):
        nofly_dir = os.path.join(_ROOT, nofly_dir)
    crs = LocalCRS(env_full.meta["crs"]["lon0"], env_full.meta["crs"]["lat0"])
    static = load_static_nofly(os.path.join(nofly_dir, "nofly_static.geojson"), crs, 5.0)
    dynamic = load_dynamic_nofly(os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs)
    obstacles = SimplifiedObstacles(env_full.buildings, d_build=params.d_build,
                                    mode=params.simplify_mode, tol=params.simplify_tol)
    obstacles.filter_by_min_height(min(params.altitude_layers) - 1.0)
    nofly = NoFlyField(static, dynamic, as_static=params.include_dynamic_as_static)

    N = len(nodes)
    adj = [[] for _ in range(N)]
    for a, b, c in zip(i_idx, j_idx, w_val):
        adj[int(a)].append((int(b), float(c)))
        adj[int(b)].append((int(a), float(c)))
    graph = VisibilityGraph(nodes, adj, obstacles, nofly, params)
    print(f"[plan_full] 图复原完成: adj 长度 {len(adj)}", flush=True)

    # ── 选跨城 OD（4 角 + 中心，取最近图节点）──
    cx, cy = 0.5 * (X0 + X1), 0.5 * (Y0 + Y1)
    z = float(cfg.section("od").get("z", 20.0))
    pairs = [
        ("SW→NE", (X0, Y0), (X1, Y1)),
        ("SE→NW", (X1, Y0), (X0, Y1)),
        ("C→SW", (cx, cy), (X0, Y0)),
    ]
    # 若只要更少对，截断
    pairs = pairs[: max(1, args.od)]

    depart = 10000.0  # 落于所有动态禁飞窗之后 → 动态不活跃，路径干净（选项 A 验证用 idle 口径）
    results = []
    for name, (ox, oy), (dx, dy) in pairs:
        on = _nearest_node(nodes, ox, oy)
        dn = _nearest_node(nodes, dx, dy)
        o = np.array([on[0], on[1], z], dtype=float)
        d = np.array([dn[0], dn[1], z], dtype=float)
        res = graph.plan(o, d, depart_time=depart, smooth=False)
        poly = res.path.pts if res.path else None
        reason = res.infeasible_reason.name if not res.feasible else "ok"
        cost = res.route.dist if res.route else float("inf")
        results.append((name, o, d, poly, reason, cost))
        print(f"  {name}: feasible={poly is not None} reason={reason} cost={cost:.1f}m "
              f"(od 直线≈{float(np.linalg.norm(d-o)):.0f}m)", flush=True)

    # ── 出全范围图 ───────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(16, 16))
    # 建筑质心散点（快速、显示城市范围与密度）
    bx = [b.poly.centroid.x for b in env_full.buildings]
    by = [b.poly.centroid.y for b in env_full.buildings]
    ax.scatter(bx, by, s=1.0, c="#bbbbbb", alpha=0.35, linewidths=0, label="建筑质心")
    colors = ["#d62728", "#2ca02c", "#1f77b4", "#ff7f0e", "#9467bd"]
    for k, (name, o, d, poly, reason, cost) in enumerate(results):
        col = colors[k % len(colors)]
        if poly is not None:
            arr = np.array([np.asarray(p, float) for p in poly])
            ax.plot(arr[:, 0], arr[:, 1], color=col, lw=1.4,
                    label=f"{name}  {cost:.0f}m ({len(arr)}pts)")
        ax.scatter([o[0], d[0]], [o[1], d[1]], c=col, marker="x", s=60, linewidths=1.5)
    ax.set_xlim(X0, X1)
    ax.set_ylim(Y0, Y1)
    ax.set_aspect("equal")
    ax.set_xlabel("X (m, local)")
    ax.set_ylabel("Y (m, local)")
    ax.set_title(f"全城可见性图 3-OD 规划（{N:,} 节点 / {len(i_idx):,} 边，整图范围）")
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)

    out_dir = os.path.join(_HERE, "results", "figures")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "full_city_3od.png")
    fig.savefig(out_path, dpi=90, bbox_inches="tight")
    print(f"[plan_full] 全范围图已写入 {out_path}", flush=True)

    # 同时存一份 OD 明细
    detail = {
        "depart_time_s": depart,
        "ods": [
            {"name": name, "feasible": poly is not None, "reason": reason,
             "cost_m": round(float(cost), 2) if poly is not None else None,
             "n_points": len(poly) if poly else None,
             "origin": [float(v) for v in o], "dest": [float(v) for v in d]}
            for (name, o, d, poly, reason, cost) in results
        ],
    }
    with open(os.path.join(out_dir, "full_city_3od.json"), "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
