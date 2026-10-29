# -*- coding: utf-8 -*-
"""exp02_vg：可见性图 / 稀疏路图规划器 验证。

验证「建筑简化 + 轮廓采样节点 + LOS 边 + 机动保护区」思路在真实广州场景上的可行性：
    - 节点总数是否压到 < 2000 目标
    - 边是否只连视线可达且不太远的节点对，离线一次碰撞检测
    - 机动保护区是否显著删掉高风险近距边，而路径长度仅边际增加
    - 规划出的路径是否确实避开建筑与禁飞区（独立审计）
    - 与「直线」对照：被障碍/禁飞阻断的 OD，图是否能绕行

只动 path_planning 内代码；不依赖 NetworkX（自实现邻接表 + 堆优化 Dijkstra/A*）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
from shapely.geometry import box

# ── 包内导入（与 exp01 同款路径装配）────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # path_planning/
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))        # 仓库根（导入前缀 path_planning.）
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from path_planning.common.config import Config, load_config  # noqa: E402
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
from path_planning.methods.control_network_planning.constraints.query import (  # noqa: E402
    CollisionQuery,
)


# ── 场景裁剪 ──────────────────────────────────────────────────────────────

def _auto_crop(env, size: float):
    xs = np.array([b.poly.centroid.x for b in env.buildings])
    ys = np.array([b.poly.centroid.y for b in env.buildings])
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    step = max(size / 2.0, 1.0)
    best, best_score = None, -1.0
    for cx in np.arange(x0, x1 - size + 1e-6, step):
        for cy in np.arange(y0, y1 - size + 1e-6, step):
            w = box(cx, cy, cx + size, cy + size)
            sel = [b for b in env.buildings if w.contains(b.poly.centroid)]
            score = len(sel) + 1e-6 * sum(b.poly.area for b in sel)
            if score > best_score:
                best_score, best = score, (cx, cy, cx + size, cy + size)
    return best


def _apply_crop(env, window, max_buildings=None):
    import copy as _copy
    env = _copy.copy(env)
    env.meta = dict(env.meta)
    win = box(*window)
    env.buildings = [b for b in env.buildings if b.poly.intersects(win)]
    if max_buildings and len(env.buildings) > int(max_buildings):
        cx, cy = (window[0] + window[2]) / 2.0, (window[1] + window[3]) / 2.0
        env.buildings.sort(key=lambda b: (b.poly.centroid.x - cx) ** 2 + (b.poly.centroid.y - cy) ** 2)
        env.buildings = env.buildings[: int(max_buildings)]
    for i, b in enumerate(env.buildings):
        b.bid = i
    return env


# ── OD 生成 ────────────────────────────────────────────────────────────────

def select_ods_from_nodes(nodes: np.ndarray, cfg, audit_q):
    """从已生成图节点中选取 OD 对。

    端点天然是图节点 → 规划时直接接入自身，杜绝随机落点落在建筑/禁飞内
    导致不可达。优先选「直线被建筑或禁飞阻断」的对，以展示图的绕行能力。
    """
    odc = cfg.section("od")
    n = int(odc.get("n_pairs", 8))
    rng = np.random.default_rng(int(odc.get("seed", 7)))
    min_d = float(odc.get("min_dist", 150.0))
    max_d = float(odc.get("max_dist", 550.0))
    N = len(nodes)
    if N < 2:
        return []
    blocked, clear = [], []
    attempts = 0
    while (len(blocked) + len(clear) < n) and attempts < 40000:
        attempts += 1
        i, j = rng.integers(0, N, 2)
        if i == j:
            continue
        p, q = nodes[i], nodes[j]
        d = float(np.linalg.norm(q - p))
        if not (min_d <= d <= max_d):
            continue
        is_blocked = (not audit_q.buildings.link_feasible(p, q, margin=0.0)) or \
                     audit_q.nofly.static_blocked(p, q) or \
                     audit_q.nofly.dynamic_blocked(p, q, 0.0, 600.0)
        if is_blocked:
            blocked.append((p.copy(), q.copy()))
        else:
            clear.append((p.copy(), q.copy()))
    return (blocked + clear)[:n]


# ── 直线对照 + 审计 ──────────────────────────────────────────────────────────

def straight_line(env, origin, dest, query):
    p = np.asarray(origin, float)
    q = np.asarray(dest, float)
    L = float(np.linalg.norm(q - p))
    blocked_b = not query.buildings.link_feasible(p, q, margin=0.0)
    blocked_s = query.nofly.static_blocked(p, q)
    blocked_d = query.nofly.dynamic_blocked(p, q, 0.0, 600.0)
    return L, (blocked_b or blocked_s or blocked_d)


def audit_path(poly, query, step):
    if poly is None or len(poly) < 2:
        return None
    pts = np.array([np.asarray(p, float) for p in poly])
    dense = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        d = float(np.linalg.norm(b - a))
        k = max(1, int(np.ceil(d / max(step, 1e-6))))
        for i in range(1, k + 1):
            dense.append(a + (b - a) * (i / k))
    dense = np.array(dense)
    ok = query.polyline_feasible(dense, margin=0.0)
    return bool(ok)


def dynamic_demo(graph, env, query, audit_step):
    """动态禁飞时间窗演示（选项 A）。

    在裁剪窗口中心注入一个**合成动态禁飞区**（用于验证机制，不改动共享数据文件），
    取一对「直线横穿该区」的图节点作 OD，分别在其 active / idle 时刻规划：
        - idle（该区不活跃）：路径应可行且大致直线；
        - active（该区生效）：动态门阻断穿越该区的边，迫使绕行（代价↑）或不可达。
    从而证明动态禁飞在「查询时」按时间窗生效，且建好的图（仅含建筑+静态禁飞）无需重建。
    """
    from shapely.geometry import LineString, Polygon
    from path_planning.common.types import DynamicNoFly, Period

    nodes = graph.nodes
    if len(nodes) < 2:
        return None
    # 在图节点质心附近放一个 120 m 见方的合成动态区（z 无限高不可飞越）
    cx, cy = float(np.mean(nodes[:, 0])), float(np.mean(nodes[:, 1]))
    hx, hy = 60.0, 60.0
    zone_poly = Polygon([(cx - hx, cy - hy), (cx + hx, cy - hy),
                         (cx + hx, cy + hy), (cx - hx, cy + hy)])
    bx = zone_poly.bounds
    # 找一对「直线横穿该区」的图节点（端点本身可达，idle/active 两端都接入自身）
    cand = [i for i in range(len(nodes))
            if bx[0] - 150 <= nodes[i, 0] <= bx[2] + 150
            and bx[1] - 150 <= nodes[i, 1] <= bx[3] + 150]
    if len(cand) < 2:
        return None
    best = None
    rng = np.random.default_rng(1)
    order = rng.permutation(cand)[:250]
    for a in order:
        for b in order:
            if a == b:
                continue
            if LineString([(nodes[a, 0], nodes[a, 1]),
                          (nodes[b, 0], nodes[b, 1])]).intersects(zone_poly):
                best = (a, b)
                break
        if best:
            break
    if best is None:
        return None
    o, d = nodes[best[0]].copy(), nodes[best[1]].copy()

    # 注入合成动态区（生效窗 [0,300)s）到图的查询期动态场。
    # 注意：图为选项 A 建成（动态未烤进边），仅查询期检查，故注入对已有边无影响。
    syn = DynamicNoFly(
        zid="SYN_DEMO",
        periods=[Period(start=0.0, end=300.0, poly=zone_poly, z0=0.0, z1=1e9)],
    )
    graph.nofly.dynamic.append(syn)
    graph.nofly.has_dynamic = True

    t_active = 50.0   # 落在 [0,300) 内 → 动态区生效
    t_idle = 500.0    # 落在生效窗之后 → 动态区不活跃
    p_a, r_a, c_a = graph.plan(o, d, depart_time=t_active)
    p_i, r_i, c_i = graph.plan(o, d, depart_time=t_idle)
    return {
        "zone_id": "SYN_DEMO",
        "zone_center_local_m": [round(cx, 1), round(cy, 1)],
        "zone_size_m": [2 * hx, 2 * hy],
        "period_start": 0.0,
        "period_end": 300.0,
        "t_active": t_active,
        "t_idle": t_idle,
        "straight_crosses_zone": True,
        "active": {
            "feasible": p_a is not None, "reason": r_a,
            "cost_m": round(c_a, 2) if p_a is not None else None,
            "audit_ok": audit_path(p_a, query, audit_step),
        },
        "idle": {
            "feasible": p_i is not None, "reason": r_i,
            "cost_m": round(c_i, 2) if p_i is not None else None,
            "audit_ok": audit_path(p_i, query, audit_step),
        },
    }


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="exp02_vg 可见性图规划器验证")
    ap.add_argument("--config", default=os.path.join(_HERE, "config.yaml"))
    ap.add_argument("--plot", action="store_true", help="出一张俯视调试图")
    args = ap.parse_args()

    cfg = load_config(args.config)
    sc = cfg.scenario
    scenario_dir = sc.get("dir", "data/raw/guangzhou")
    if not os.path.isabs(scenario_dir):
        scenario_dir = os.path.join(_ROOT, scenario_dir)

    print(f"[exp02] 场景目录: {scenario_dir}")
    t_start = time.perf_counter()
    env_full = load_scenario(scenario_dir, classes=sc.get("classes"),
                             crop=None, with_nofly=False)
    print(f"[exp02] 全量建筑 {len(env_full.buildings)} 栋")

    crop = sc.get("crop", "auto")
    if crop == "auto" or crop is None:
        crop = _auto_crop(env_full, float(sc.get("crop_size", 600.0)))
    env = _apply_crop(env_full, crop, sc.get("max_buildings"))
    print(f"[exp02] 裁剪窗口 {[round(float(c), 1) for c in crop]} → 建筑 {len(env.buildings)} 栋")

    # 禁飞区（file）
    nofly_cfg = cfg.section("nofly")
    nofly_dir = nofly_cfg.get("dir", scenario_dir)
    if not os.path.isabs(nofly_dir):
        nofly_dir = os.path.join(_ROOT, nofly_dir)
    crs = LocalCRS(env.meta["crs"]["lon0"], env.meta["crs"]["lat0"])
    env.static_nofly = load_static_nofly(os.path.join(nofly_dir, "nofly_static.geojson"), crs, 5.0)
    env.dynamic_nofly = load_dynamic_nofly(os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs)
    print(f"[exp02] 禁飞区: 静态 {len(env.static_nofly)} / 动态 {len(env.dynamic_nofly)}")

    # 主循环规划时刻：选在所有动态禁飞事件之后（动态不活跃），以纯净对比「机动保护区 开/关」。
    # （动态禁飞的效果由独立的 dynamic_demo 显式验证，不混入保护区的路径膨胀口径。）
    depart_time = 0.0
    if env.dynamic_nofly:
        try:
            depart_time = max(p.end for z in env.dynamic_nofly for p in z.periods) + 1000.0
        except Exception:
            depart_time = 0.0

    params = VisibilityGraphParams.from_dict(cfg.section("visibility_graph"))

    # ── 建图（同一节点集合，分别测 机动保护区 开/关）──────────────────────
    obstacles = SimplifiedObstacles(env.buildings, d_build=params.d_build,
                                    mode=params.simplify_mode, tol=params.simplify_tol)
    # 丢弃楼高低于最低飞行层的障碍（其顶面低于所有节点，不会阻挡节点间连线）
    min_layer = min(params.altitude_layers)
    obstacles.filter_by_min_height(min_layer - 1.0)
    nofly = NoFlyField(env.static_nofly, env.dynamic_nofly,
                       as_static=params.include_dynamic_as_static)
    # 先生成节点；端点若为图节点，规划时直接接入自身，绕过随机落点不可达
    nodes, node_meta, node_stats = generate_nodes(env, params, obstacles, nofly,
                                                  extra_points=None)

    # 审计器（独立于建图逻辑，用于 OD 筛选 + 路径复核）
    audit_q = CollisionQuery(env, d_s_stat=5.0, d_s_dyn=2.0)
    # OD：从图节点中选取，优先直线被建筑/禁飞阻断的对以展示绕行
    ods = select_ods_from_nodes(nodes, cfg, audit_q)
    print(f"[exp02] OD 数 {len(ods)} (取自图节点，优先直线被阻断对)")

    adj_on, stats_on = build_edges(nodes, obstacles, params,
                                   nofly_blocked=nofly.blocked, with_protection=True)
    adj_off, stats_off = build_edges(nodes, obstacles, params,
                                     nofly_blocked=nofly.blocked, with_protection=False)

    graph_on = VisibilityGraph(nodes, adj_on, obstacles, nofly, params, meta=node_meta)
    graph_off = VisibilityGraph(nodes, adj_off, obstacles, nofly, params, meta=node_meta)

    # ── 规划 + 对照 + 审计 ────────────────────────────────────────────────
    audit_step = float(cfg.section("checks").get("audit_step_m", 2.0))

    recs = []
    for i, (o, d) in enumerate(ods):
        poly_on, reason_on, cost_on = graph_on.plan(o, d, depart_time=depart_time)
        poly_off, reason_off, cost_off = graph_off.plan(o, d, depart_time=depart_time)
        L, blocked = straight_line(env, o, d, audit_q)
        rec = {
            "od": i,
            "origin": [float(v) for v in o],
            "dest": [float(v) for v in d],
            "straight_len_m": round(L, 2),
            "straight_blocked": bool(blocked),
            "on": {
                "feasible": poly_on is not None,
                "reason": reason_on,
                "cost_m": round(cost_on, 2) if poly_on is not None else None,
                "n_nodes_path": len(poly_on) if poly_on else None,
                "audit_ok": audit_path(poly_on, audit_q, audit_step),
            },
            "off": {
                "feasible": poly_off is not None,
                "reason": reason_off,
                "cost_m": round(cost_off, 2) if poly_off is not None else None,
            },
        }
        if poly_on is not None and L > 1e-6:
            rec["detour_ratio"] = round(cost_on / L, 4)
        recs.append(rec)
        tag = "BLOCKED" if blocked else "clear  "
        print(f"  OD{i} [{tag}] straight={L:.0f}m | ON feasible={rec['on']['feasible']} "
              f"cost={rec['on']['cost_m']} audit={rec['on']['audit_ok']} | "
              f"OFF feasible={rec['off']['feasible']} cost={rec['off']['cost_m']}")

    # ── 动态禁飞时间窗演示（选项 A 验证）──────────────────────────────────
    dyn_demo = dynamic_demo(graph_on, env, audit_q, audit_step)
    if dyn_demo:
        a, i = dyn_demo["active"], dyn_demo["idle"]
        print(f"\n[动态演示] 区 {dyn_demo['zone_id']} 生效窗 [{dyn_demo['period_start']:.0f},{dyn_demo['period_end']:.0f}]s")
        print(f"  t_idle ={dyn_demo['t_idle']:.0f}s : feasible={i['feasible']} cost={i['cost_m']} audit={i['audit_ok']}")
        print(f"  t_active={dyn_demo['t_active']:.0f}s : feasible={a['feasible']} cost={a['cost_m']} reason={a['reason']} audit={a['audit_ok']}")
        if a["feasible"] and i["feasible"]:
            print(f"  → 动态门使该 OD 路径代价变化 {(a['cost_m']/i['cost_m']-1)*100:+.1f}% （idle 直线 vs active 绕行）")

    # ── 汇总 ──────────────────────────────────────────────────────────────
    n_on = sum(1 for r in recs if r["on"]["feasible"])
    n_off = sum(1 for r in recs if r["off"]["feasible"])
    costs_on = [r["on"]["cost_m"] for r in recs if r["on"]["feasible"]]
    costs_off = [r["off"]["cost_m"] for r in recs if r["off"]["feasible"]]
    detours = [r["detour_ratio"] for r in recs if "detour_ratio" in r]
    audits = [r["on"]["audit_ok"] for r in recs if r["on"]["audit_ok"] is not None]

    edge_red = 1.0 - (stats_on["n_edges"] / stats_off["n_edges"]) if stats_off["n_edges"] else 0.0
    # ── 路径膨胀（可直接与文献对比的口径）─────────────────────────────────
    # 文献口径：对每个 OD 计算 (L_on / L_off − 1)，再对所有 OD 取 均值 ± 标准差
    # （即「受保护区后路径长度平均增加百分之几」）；中位数为稳健补充。
    # 另保留 pooled 口径（总长增量 / 总长）作连续性参考。
    inflations = []
    paired = []
    for r in recs:
        if r["on"]["feasible"] and r["off"]["feasible"] and r["off"]["cost_m"] and r["off"]["cost_m"] > 1e-6:
            inflations.append(r["on"]["cost_m"] / r["off"]["cost_m"] - 1.0)
            paired.append((r["on"]["cost_m"], r["off"]["cost_m"]))
    n_infl = len(inflations)
    mean_infl = float(np.mean(inflations)) if inflations else None
    std_infl = float(np.std(inflations)) if inflations else None
    median_infl = float(np.median(inflations)) if inflations else None
    path_delta_pooled = (sum(a - b for a, b in paired) / sum(b for _, b in paired)) if paired else 0.0
    # 受保护区后的连通性损失：OFF 可行但 ON 不可达的 OD 数
    n_off_feas = sum(1 for r in recs if r["off"]["feasible"])
    n_both = sum(1 for r in recs if r["on"]["feasible"] and r["off"]["feasible"])
    n_only_off = n_off_feas - n_both

    summary = {
        "scenario": {"crop": [round(float(c), 1) for c in crop],
                     "n_buildings": len(env.buildings),
                     "n_static_nofly": len(env.static_nofly),
                     "n_dynamic_nofly": len(env.dynamic_nofly)},
        "params": params.to_dict(),
        "obstacles": obstacles.summary(),
        "nodes": node_stats,
        "edges_with_protection": stats_on,
        "edges_without_protection": stats_off,
        "edge_reduction_by_protection": round(edge_red, 4),
        "planning": {
            "n_od": len(ods),
            "n_feasible_on": n_on,
            "n_feasible_off": n_off,
            "mean_cost_on_m": round(float(np.mean(costs_on)), 2) if costs_on else None,
            "mean_cost_off_m": round(float(np.mean(costs_off)), 2) if costs_off else None,
            "path_length_inflation_on_vs_off": {
                "definition": "mean over OD pairs of (L_on/L_off - 1); directly comparable to literature 'path length increases ~X%'",
                "mean_pct": round(mean_infl * 100, 3) if mean_infl is not None else None,
                "std_pct": round(std_infl * 100, 3) if std_infl is not None else None,
                "median_pct": round(median_infl * 100, 3) if median_infl is not None else None,
                "n_pairs": n_infl,
                "n_infeasible_only_under_protection": int(n_only_off),
                "feasibility_preserved_rate": round(n_both / n_off_feas, 4) if n_off_feas else None,
            },
            "path_length_inflation_pooled_pct": round(float(path_delta_pooled) * 100, 3),
            "mean_detour_ratio_vs_straight": round(float(np.mean(detours)), 4) if detours else None,
            "audit_all_clear": bool(audits) and all(audits),
            "n_audited": len(audits),
        },
        "dynamic_nofly_option": "A_query_time_window" if not params.include_dynamic_as_static else "B_baked_static",
        "dynamic_demo": dyn_demo,
        "wall_time_s": round(time.perf_counter() - t_start, 3),
    }

    out_dir = os.path.join(_HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "ods": recs}, f, indent=2, ensure_ascii=False)

    print("\n================ exp02_vg 汇总 ================")
    print(f"节点数: {node_stats['n_nodes']}  (目标 < {params.max_nodes})")
    print(f"  网格骨干 {node_stats['n_grid']} / 建筑轮廓 {node_stats['n_building']} / 额外 {node_stats['n_extra']}"
          f"（落点剔除 建筑内 {node_stats['n_drop_obstacle']} / 禁飞内 {node_stats['n_drop_nofly']}）")
    print(f"边(开保护区): {stats_on['n_edges']}  (水平 {stats_on['n_horizontal_edges']} + 竖向 {stats_on['n_vertical_edges']})"
          f"  建图耗时 {stats_on['build_time_s']}s")
    print(f"边(关保护区): {stats_off['n_edges']}  → 机动保护区删边 {edge_red*100:.1f}%")
    print(f"可行 OD: ON={n_on}/{len(ods)}  OFF={n_off}/{len(ods)}")
    if mean_infl is not None:
        print(f"路径膨胀(ON vs OFF, 文献口径=各OD (L_on/L_off-1) 均值): "
              f"均值 {mean_infl*100:.2f}%  ± {std_infl*100:.2f}%  "
              f"(中位 {median_infl*100:.2f}%, 共 {n_infl} 对；"
              f"其中 {n_only_off} 对仅受保护区后不可达 → 连通性保持率 "
              f"{n_both/n_off_feas*100:.1f}%)")
    print(f"平均路径长度(ON): {summary['planning']['mean_cost_on_m']} m  "
          f"(pooled 口径 +{path_delta_pooled*100:.2f}%)")
    if detours:
        print(f"相对直线平均绕行比: {summary['planning']['mean_detour_ratio_vs_straight']} (被阻断 OD 的绕行)")
    print(f"路径审计(建筑+静态禁飞零余量): 全清 = {summary['planning']['audit_all_clear']} ({len(audits)} 条)")
    print(f"总耗时: {summary['wall_time_s']}s  → 结果: {out_dir}/metrics.json")

    if args.plot:
        _plot(graph_on, obstacles, nofly, ods, recs, os.path.join(out_dir, "figures"))

    # 判定
    ok = (node_stats["n_nodes"] < params.max_nodes) and (n_on > 0) and \
         summary["planning"]["audit_all_clear"]
    return 0 if ok else 1


def _plot(graph, obstacles, nofly, ods, recs, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 10))
    # 障碍
    for p in obstacles.polys:
        try:
            xs, ys = p.exterior.xy
            ax.fill(xs, ys, color="#cccccc", alpha=0.5, linewidth=0)
        except Exception:
            pass
    # 禁飞
    for p in nofly.polys:
        try:
            xs, ys = p.exterior.xy
            ax.fill(xs, ys, color="#ff9999", alpha=0.35, linewidth=0)
        except Exception:
            pass
    # 边
    pos = graph.nodes
    npos = len(pos)
    for i, adj in enumerate(graph.adj):
        if i >= npos:
            continue
        for (j, _) in adj:
            if j > i and j < npos:
                ax.plot([pos[i, 0], pos[j, 0]], [pos[i, 1], pos[j, 1]],
                        color="#6699cc", linewidth=0.15, alpha=0.25)
    # 节点
    ax.scatter(pos[:, 0], pos[:, 1], s=3, c="#224466", alpha=0.6)
    # OD 与一条路径
    for k, (o, d) in enumerate(ods):
        ax.scatter([o[0], d[0]], [o[1], d[1]], c="green", s=12, marker="x")
    for r in recs:
        if r["on"]["feasible"]:
            o, d = np.asarray(r["origin"]), np.asarray(r["dest"])
            ax.plot([o[0], d[0]], [o[1], d[1]], c="red", lw=0.6, alpha=0.7)
            break
    ax.set_aspect("equal")
    ax.set_title("Visibility graph (top view): nodes + LOS edges + one planned path")
    path = os.path.join(out_dir, "visibility_graph_topview.png")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    print(f"[exp02] 俯视图已写入 {path}")


if __name__ == "__main__":
    raise SystemExit(main())
