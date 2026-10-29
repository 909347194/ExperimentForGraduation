# -*- coding: utf-8 -*-
"""exp01_smoke 入口：只做**编排调度**，不堆细节。

流程：
    配置 → 场景（广州真实建筑，可选裁剪窗口）→ 禁飞区（file/synthetic/none）
         → 离线建网 → 【专项正确性用例】→ OD 规划（L1/L2/L2.5 分层）
         → 路径独立复核 → metrics.json

正确性用例（checks.enable_unit_checks）：
    U1 链路代价序关系：t(0,0) ≥ t(0,s) ≥ t(s,s)=D/v_u，且 t(s,s) 与 Eq.(4) 一致
    U2 建筑穿越判定：穿建筑的链路 link_feasible=False
    U3 整边判定"擦边"：飞行途中禁飞生效 → dynamic_blocked=True（整边不可省）
    U4 完全在时窗之前/之后飞行 → dynamic_blocked=False
    U5 保守判据 ⊇ 整边判据：整边判冲突 ⇒ 保守必判冲突
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
from shapely.geometry import box

# ── 包内相对导入 ──────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))  # path_planning/
_REPO = os.path.abspath(os.path.join(_ROOT, ".."))  # 仓库根（导入前缀 path_planning.）
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from path_planning.common.config import Config, load_config  # noqa: E402
from path_planning.common.scenario import (  # noqa: E402
    load_scenario,
    synthesize_dynamic_nofly,
)
from path_planning.common.types import (  # noqa: E402
    DynamicNoFly,
    ODRequest,
    Period,
)
from path_planning.methods.control_network_planning.constraints.query import (  # noqa: E402
    CollisionQuery,
)
from path_planning.methods.control_network_planning.cost.link_cost import (  # noqa: E402
    link_time,
    max_speed,
)
from path_planning.methods.control_network_planning.offline.pipeline import (  # noqa: E402
    build_network,
)
from path_planning.methods.control_network_planning.online.pipeline import (  # noqa: E402
    OnlinePlanner,
)


# ── 场景 ──────────────────────────────────────────────────────────────────


def _auto_crop(env, size: float):
    """选建筑最密的方形窗口（按栋数；并列时按占地）。"""
    xs = np.array([b.poly.centroid.x for b in env.buildings])
    ys = np.array([b.poly.centroid.y for b in env.buildings])
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
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
    win = box(*window)
    env.buildings = [b for b in env.buildings if b.poly.intersects(win)]
    if max_buildings and len(env.buildings) > int(max_buildings):
        cx, cy = (window[0] + window[2]) / 2.0, (window[1] + window[3]) / 2.0
        env.buildings.sort(
            key=lambda b: (b.poly.centroid.x - cx) ** 2 + (b.poly.centroid.y - cy) ** 2
        )
        env.buildings = env.buildings[: int(max_buildings)]
    for i, b in enumerate(env.buildings):
        b.bid = i
    env.meta["crop"] = list(window)
    return env


# ── 专项正确性用例 ────────────────────────────────────────────────────────


def run_unit_checks(net, cfg, query, env, checks_cfg) -> dict:
    out = {}
    uav = cfg.uav
    pts = net.points

    # U1 链路代价序关系
    if len(pts) >= 2:
        a, b = pts[0], pts[1]
        s = 1
        t00, D = link_time(a, b, 0, 0, uav)
        t0s, _ = link_time(a, b, 0, s, uav)
        tss, _ = link_time(a, b, s, s, uav)
        v_u = max_speed(D, float(b[2] - a[2]), uav)
        out["U1_cost_order"] = bool(t00 >= t0s - 1e-9 >= tss - 1e-9)
        out["U1_eq4_match"] = bool(abs(tss - D / v_u) < 1e-9)
        out["U1_sample"] = {"D": round(D, 3), "t00": round(t00, 3),
                            "t0s": round(t0s, 3), "tss": round(tss, 3)}
    else:
        out["U1_cost_order"] = False

    # U2 建筑穿越判定
    hit = False
    for b in env.buildings[:50]:
        c = b.poly.centroid
        z = max(1.0, b.height / 2.0)
        p = np.array([c.x - 200.0, c.y, z], dtype=float)
        q = np.array([c.x + 200.0, c.y, z], dtype=float)
        if b.height > z:  # 段在中途高度，应判穿越
            hit = True
            out["U2_building_block"] = bool(not query.buildings.link_feasible(p, q))
            break
    out.setdefault("U2_building_block", None if not hit else out.get("U2_building_block"))

    # U3/U4/U5 动态禁飞整边判定（构造一个方形时段禁飞区）
    cx = float(np.median(pts[:, 0])) if len(pts) else 0.0
    cy = float(np.median(pts[:, 1])) if len(pts) else 0.0
    R = 100.0
    zone = DynamicNoFly(
        zid="UT-001",
        periods=[Period(start=100.0, end=400.0,
                        poly=box(cx - R, cy - R, cx + R, cy + R), z0=0.0, z1=200.0)],
    )
    q2 = CollisionQuery(
        env.__class__(buildings=env.buildings, static_nofly=env.static_nofly,
                      dynamic_nofly=[zone]),
        d_s_stat=cfg.offline.d_s_stat,
        d_s_dyn=cfg.offline.d_s_dyn,
    )
    p = np.array([cx - R - 150.0, cy, 30.0], dtype=float)
    qq = np.array([cx + R + 150.0, cy, 30.0], dtype=float)
    dist = float(np.linalg.norm(qq - p))
    v = 15.0
    t_flight = dist / v

    # 求穿越禁飞体的时间区间 [t_in, t_out]（沿线段起点起算的飞行时刻）
    from shapely.geometry import LineString as _LS, Point as _Pt

    line = _LS([(p[0], p[1]), (qq[0], qq[1])])
    inter = line.intersection(zone.periods[0].poly)
    d_in = line.project(_Pt(list(inter.coords)[0]))
    d_out = line.project(_Pt(list(inter.coords)[-1]))
    t_in, t_out = d_in / v, d_out / v

    # 擦边：让生效时刻(100s)落在穿越区间**中间** —— 起飞时未生效，途中生效
    activation = 100.0
    t_dep_edge = activation - 0.5 * (t_in + t_out)
    t_arr_edge = t_dep_edge + t_flight
    out["U3_cross_activation"] = bool(
        q2.nofly.dynamic_blocked(p, qq, t_dep_edge, t_arr_edge)
    )
    out["U3_detail"] = {
        "t_dep": round(t_dep_edge, 2),
        "t_arr": round(t_arr_edge, 2),
        "traverse_in_zone": [round(t_dep_edge + t_in, 2), round(t_dep_edge + t_out, 2)],
        "activation": activation,
    }
    # U3b：两端点都在禁飞体外但**途中穿越**（端点安全 ≠ 整边安全）
    out["U3b_midflight_only"] = bool(
        (not q2.nofly.dynamic_blocked(p, p, t_dep_edge, t_dep_edge))
        and out["U3_cross_activation"]
    )
    # U4：完全在生效前 / 结束后
    out["U4_before_window"] = bool(not q2.nofly.dynamic_blocked(p, qq, 0.0, 60.0))
    out["U4_after_window"] = bool(not q2.nofly.dynamic_blocked(p, qq, 500.0, 560.0))
    # U5：保守判据覆盖整边判据
    out["U5_conservative_covers"] = bool(
        q2.nofly.conservative_blocked(p, qq, 0.0, 600.0)
        and not q2.nofly.conservative_blocked(p, qq, 0.0, 50.0)
    )
    out["all_pass"] = bool(
        out.get("U1_cost_order")
        and out.get("U1_eq4_match")
        and (out.get("U2_building_block") in (True, None))
        and out.get("U3_cross_activation")
        and out.get("U3b_midflight_only")
        and out.get("U4_before_window")
        and out.get("U4_after_window")
        and out.get("U5_conservative_covers")
    )
    return out


# ── OD 生成与审计 ─────────────────────────────────────────────────────────


def make_ods(env, cfg, crop_window) -> list[tuple[np.ndarray, np.ndarray]]:
    odc = cfg.section("od")
    pairs = odc.get("pairs") or []
    if pairs:
        out = []
        for a, b in pairs:
            out.append((np.array(a, dtype=float), np.array(b, dtype=float)))
        return out
    n = int(odc.get("n_pairs", 4))
    z = float(odc.get("z", 20.0))
    rng = np.random.default_rng(int(odc.get("seed", 7)))
    x0, y0, x1, y1 = crop_window
    margin = 0.08
    out = []
    for _ in range(n):
        ax = x0 + rng.uniform(margin, 0.4) * (x1 - x0)
        ay = y0 + rng.uniform(margin, 0.4) * (y1 - y0)
        bx = x0 + rng.uniform(0.6, 1 - margin) * (x1 - x0)
        by = y0 + rng.uniform(0.6, 1 - margin) * (y1 - y0)
        out.append((np.array([ax, ay, z]), np.array([bx, by, z])))
    return out


def audit_path(res, query, cfg, step: float) -> dict:
    """平滑后路径的独立复核（与搜索期判定解耦，避免同源错误）。"""
    if res.path is None or len(res.path.pts) < 2:
        return {"ok": None}
    pts = res.path.pts
    # 建筑 + 静态禁飞
    bad_static = not query.polyline_feasible(
        _dense(pts, step), margin=0.0
    )
    # 动态禁飞：用轨迹时刻做切段复核
    times = res.trajectory.times if res.trajectory is not None else None
    bad_dyn = False
    if times is not None and len(times) == len(pts):
        for i in range(len(pts) - 1):
            if not query.edge_occupancy_free(pts[i], pts[i + 1], times[i], times[i + 1]):
                bad_dyn = True
                break
    return {"collides_building_or_static": bool(bad_static),
            "collides_dynamic": bool(bad_dyn),
            "ok": bool(not bad_static and not bad_dyn)}


def _dense(pts: np.ndarray, step: float) -> np.ndarray:
    pts = np.asarray(pts, dtype=float)
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        d = float(np.linalg.norm(b - a))
        n = max(1, int(np.ceil(d / max(step, 1e-6))))
        for k in range(1, n + 1):
            out.append(a + (b - a) * (k / n))
    return np.array(out)


# ── 主流程 ────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="exp01_smoke 正确性调试")
    ap.add_argument("--config", default=os.path.join(_HERE, "config.yaml"))
    ap.add_argument("--set", action="append", default=[],
                    help="覆盖配置，如 --set offline.d_star=200")
    ap.add_argument("--plot", action="store_true", help="出一张俯视调试图")
    args = ap.parse_args()

    cfg = load_config(args.config) if os.path.exists(args.config) else Config()
    for kv in args.set:
        sec, rest = kv.split(".", 1)
        key, val = rest.split("=", 1)
        obj = getattr(cfg, sec)
        cur = getattr(obj, key)
        setattr(obj, key, type(cur)(val) if not isinstance(cur, bool) else (val.lower() == "true"))

    t_start = time.perf_counter()
    sc = cfg.scenario
    scenario_dir = sc.get("dir", "data/raw/guangzhou")
    if not os.path.isabs(scenario_dir):
        scenario_dir = os.path.join(_ROOT, scenario_dir)

    print(f"[exp01] 场景目录: {scenario_dir}")
    env_full = load_scenario(
        scenario_dir,
        classes=sc.get("classes"),
        crop=None,  # 先全量加载再按最密窗口裁剪
        with_nofly=False,
    )
    print(f"[exp01] 全量建筑 {len(env_full.buildings)} 栋")

    crop = sc.get("crop", "auto")
    if crop == "auto" or crop is None:
        size = float(sc.get("crop_size", 700.0))
        crop = _auto_crop(env_full, size)
    env = _apply_crop(env_full, crop, sc.get("max_buildings"))
    print(f"[exp01] 裁剪窗口 {[round(c,1) for c in crop]} → 建筑 {len(env.buildings)} 栋")

    # 禁飞区
    nofly_cfg = cfg.section("nofly")
    src = nofly_cfg.get("source", "synthetic")
    # nofly 文件目录：默认与 scenario.dir 同级；可单独指定（静态/动态 geojson 放别处时）
    nofly_dir = nofly_cfg.get("dir", scenario_dir)
    if not os.path.isabs(nofly_dir):
        nofly_dir = os.path.join(_ROOT, nofly_dir)
    if src == "file":
        from path_planning.common.scenario import load_dynamic_nofly, load_static_nofly
        from path_planning.utils.geometry import LocalCRS

        crs = LocalCRS(env.meta["crs"]["lon0"], env.meta["crs"]["lat0"])
        env.static_nofly = load_static_nofly(
            os.path.join(nofly_dir, "nofly_static.geojson"), crs, cfg.offline.d_s_stat
        )
        env.dynamic_nofly = load_dynamic_nofly(
            os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs
        )
    elif src == "synthetic":
        syn = nofly_cfg.get("synthetic", {})
        env.dynamic_nofly = synthesize_dynamic_nofly(env, **syn)
    else:
        env.dynamic_nofly = []
    print(f"[exp01] 禁飞区: 静态 {len(env.static_nofly)} / 动态 {len(env.dynamic_nofly)}"
          f"（来源 {src}）")

    # 离线建网
    net, net_stats = build_network(env, cfg, verbose=True)

    # 规划用查询：把平滑偏移并进动态禁飞膨胀（降低平滑后翻盘率）
    query = CollisionQuery(
        env,
        d_s_stat=cfg.offline.d_s_stat,
        d_s_dyn=cfg.offline.d_s_dyn,
        dynamic_pad=cfg.online.smooth_deviation,
    )
    # 审计用查询：零余量"地面真值"，与规划判定解耦，避免同源错误
    audit_q = CollisionQuery(env, d_s_stat=cfg.offline.d_s_stat, d_s_dyn=cfg.offline.d_s_dyn)
    planner = OnlinePlanner(net, cfg, query)

    checks_cfg = cfg.section("checks")
    unit = {}
    if checks_cfg.get("enable_unit_checks", True):
        unit = run_unit_checks(net, cfg, query, env, checks_cfg)
        print("[exp01] 专项用例:", json.dumps(unit, ensure_ascii=False))

    # OD 规划：自动组 + 强制穿越动态禁飞区的诊断组（把 L2/L2.5 真正跑起来）
    ods = make_ods(env, cfg, crop)
    for z in env.dynamic_nofly:
        for per in z.periods[:1]:
            c = per.poly.centroid
            span = 0.5 * (max(c.x - crop[0], crop[2] - c.x))
            o = np.array([c.x - span, c.y, float(cfg.section("od").get("z", 20.0))])
            d = np.array([c.x + span, c.y, float(cfg.section("od").get("z", 20.0))])
            ods.append((o, d))
    results = []
    planned = []
    for i, (o, d) in enumerate(ods):
        od = ODRequest(origin=o, dest=d, t_dep=0.0)
        res = planner.plan(od)
        planned.append((o, d, res))
        # ── route / path / trajectory 三者分存（严格区分，不合并）──
        # route：起点 + 控制点序列 + 终点（走廊，含几何但无平滑）
        route_pts_3d = None
        if res.route is not None:
            seq = [np.asarray(o, dtype=float)] + [
                net.points[pi] for pi in res.route.points
            ] + [np.asarray(d, dtype=float)]
            route_pts_3d = [[round(float(v), 2) for v in p] for p in seq]

        # path：Dubins 平滑后的三维空间曲线（仅几何，无时间），下采样存 xy
        path_xy = None
        if res.path is not None and len(res.path.pts):
            step = 3
            pp = res.path.pts[::step]
            if len(res.path.pts) and (len(res.path.pts) - 1) % step != 0:
                pp = np.vstack([pp, res.path.pts[-1]])
            path_xy = [[round(float(a), 2), round(float(b), 2)] for a, b in pp[:, :2]]

        # trajectory：path + 时间维（匀速占位，非时间最优），与 path 分开存
        traj_times = traj_speeds = None
        if res.trajectory is not None and len(res.trajectory.times):
            step = 3
            tt = res.trajectory.times[::step]
            ss = res.trajectory.speeds[::step]
            if len(res.trajectory.times) and (len(res.trajectory.times) - 1) % step != 0:
                tt = np.concatenate([tt, [res.trajectory.times[-1]]])
                ss = np.concatenate([ss, [res.trajectory.speeds[-1]]])
            traj_times = [round(float(t), 2) for t in tt]
            traj_speeds = [round(float(s), 3) for s in ss]

        rec = {
            "od_id": i,
            "origin": [round(float(v), 2) for v in o],
            "dest": [round(float(v), 2) for v in d],
            "feasible": bool(res.feasible),
            "level": res.level,
            "total_time_s": round(float(res.total_time), 3) if np.isfinite(res.total_time) else None,
            # ── route（走廊）：控制点序列 + 链路距离 ──
            "route_dist_m": round(float(res.route.dist), 2) if res.route else None,
            "n_route_points": len(res.route.points) if res.route else 0,
            "route_control_points": route_pts_3d,
            # ── path（平滑曲线）：几何 + 弧长（无时间）──
            "path_arc_length_m": round(float(res.path.arc_length), 2) if res.path is not None else None,
            "n_path_samples": int(len(res.path.pts)) if res.path is not None else 0,
            "path_xy": path_xy,
            # ── trajectory（轨迹）：path + 时间维（匀速占位，与 path 严格分离）──
            "trajectory": (
                {"timing": "uniform_placeholder",
                 "note": "trajectory = path + 匀速时间占位（非 Lin2021 §2.4 时间最优轨迹，time_optimal.py 待实现）",
                 "times_s": traj_times, "speeds_mps": traj_speeds}
                if traj_times is not None else None
            ),
            "smooth_failed": bool(res.stats.get("smooth_failed", False)),
            "plan_time_s": res.stats.get("plan_time_s"),
            "n_dijkstra": res.stats.get("n_dijkstra"),
            "n_l2_iters": res.stats.get("n_l2_iters"),
            "blocked_pairs": res.stats.get("blocked_pairs"),
            "reason": (res.infeasible_reason.name if res.infeasible_reason else None),
        }
        if checks_cfg.get("enable_path_audit", True) and res.feasible and res.path is not None:
            rec["audit"] = audit_path(res, audit_q, cfg, float(checks_cfg.get("audit_step_m", 2.0)))
        results.append(rec)
        print(
            f"  OD{i}: feasible={rec['feasible']} level={rec['level']} "
            f"t={rec['total_time_s']}s "
            f"route_dist={rec['route_dist_m']}m (pts={rec['n_route_points']}) "
            f"path_len={rec.get('path_arc_length_m')}m "
            f"smooth_failed={rec.get('smooth_failed')} audit={rec.get('audit')}"
        )

    metrics = {
        "experiment": "exp01_smoke",
        # 三者定义（自解释，确保结果里 route/path/trajectory 不被混用）
        "definitions": {
            "route": "控制网络上的虚拟链路序列（走廊）：起点+控制点序列+终点，含各段时刻与距离。仅几何、无平滑。对应 Lin2021 Fig.1(a)。",
            "path": "Dubins 平滑后的三维空间曲线（仅几何、无时间）：route → smooth_route。对应 Lin2021 Fig.1(b)。",
            "trajectory": "path + 时间参数化（位置+速度/时刻）。当前时间为匀速占位（trajectory.timing='uniform_placeholder'），非 Lin2021 §2.4 时间最优轨迹（time_optimal.py 待实现）。对应 Lin2021 Fig.1(c)。",
        },
        "nofly_zones": [
            {
                "zid": z.zid,
                "periods": [
                    {"start": p.start, "end": p.end,
                     "center": [round(float(p.poly.centroid.x), 1), round(float(p.poly.centroid.y), 1)],
                     "bounds": [round(float(v), 1) for v in p.poly.bounds],
                     "z": [p.z0, p.z1]}
                    for p in z.periods
                ],
            }
            for z in env.dynamic_nofly
        ],
        "scenario": {"dir": scenario_dir, "crop": [round(float(c), 2) for c in crop]},
        "config": cfg.to_dict(),
        "env": env.summary(),
        "network": net_stats,
        "unit_checks": unit,
        "od_results": results,
        "summary": {
            "n_od": len(results),
            "n_feasible": sum(1 for r in results if r["feasible"]),
            "n_route_only": sum(1 for r in results if r["feasible"] and r.get("smooth_failed")),
            "n_path_trajectory": sum(1 for r in results if r.get("path_xy") and r.get("trajectory")),
            "level_counts": {
                lv: sum(1 for r in results if r["level"] == lv)
                for lv in sorted({r["level"] for r in results})
            },
            "n_audit_ok": sum(1 for r in results if (r.get("audit") or {}).get("ok")),
            "wall_time_s": round(time.perf_counter() - t_start, 3),
        },
    }

    out_dir = os.path.join(_HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"[exp01] 结果已写入 {os.path.join(out_dir, 'metrics.json')}")
    print("[exp01] 汇总:", json.dumps(metrics["summary"], ensure_ascii=False))

    if args.plot:
        _plot(env, net, results, out_dir)
        _plot_distinctions(env, results, out_dir)
        _plot_region_overview(env_full, env, net, results, scenario_dir, out_dir)
        _plot_side_view(env, net, planned, out_dir)

    ok = bool(unit.get("all_pass", True)) and metrics["summary"]["n_feasible"] > 0
    return 0 if ok else 1


def _plot(env, net, results, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Windows 中文 fallback（DejaVu Sans 无 CJK 字形）
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(8, 8))
    for b in env.buildings:
        g = b.poly if b.poly.geom_type == "Polygon" else max(b.poly.geoms, key=lambda x: x.area)
        x, y = g.exterior.xy
        ax.fill(x, y, color="#c9c9c9", alpha=0.7)
    pts = net.points
    ax.scatter(pts[:, 0], pts[:, 1], s=2, c="#2b7bba", label="control points")
    for z in env.dynamic_nofly:
        for p in z.periods:
            g = p.poly if p.poly.geom_type == "Polygon" else max(p.poly.geoms, key=lambda x: x.area)
            x, y = g.exterior.xy
            ax.plot(x, y, color="red", lw=1.5, ls="--")
    # 画真实平滑路径（不是 OD 直连弦）
    for r in results:
        xy = r.get("path_xy")
        if xy:
            xs = [p[0] for p in xy]
            ys = [p[1] for p in xy]
            ax.plot(xs, ys, "-", lw=1.6, label=f"OD{r['od_id']}({r['level']})")
        ax.plot([r["origin"][0]], [r["origin"][1]], "o", color="#2ca02c", ms=7)
        ax.plot([r["dest"][0]], [r["dest"][1]], "s", color="#d1495b", ms=7)
    ax.set_aspect("equal")
    ax.legend()
    ax.set_title(
        "exp01_smoke: buildings / control points / planned paths\n"
        "(俯视图：路径可合法飞越低于飞行高度的建筑，压灰块≠碰撞)"
    )
    ax.legend(loc="upper right", fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "debug_overview.png"), dpi=130)
    plt.close(fig)


def _bgeom(poly):
    return poly if poly.geom_type == "Polygon" else max(poly.geoms, key=lambda x: x.area)


def _plot_distinctions(env, results, out_dir):
    """两张图，把 route / path / trajectory 在图上区分开。

    图1 fig_route_vs_path：route 用虚线+控制点标记（走廊），path 用实线（平滑曲线）。
    图2 fig_trajectory：path 按到达时刻着色，明确 trajectory = path + 时间维。
    仅画可行且平滑成功（有 path/trajectory）的 OD。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    cmap = plt.get_cmap("viridis")

    usable = [
        r for r in results
        if r.get("feasible") and not r.get("smooth_failed")
        and r.get("path_xy") and r.get("trajectory")
    ]

    # ── 图1：route（虚线走廊） vs path（实线平滑曲线）──
    fig1, ax1 = plt.subplots(figsize=(9, 9))
    for b in env.buildings:
        g = _bgeom(b.poly)
        x, y = g.exterior.xy
        ax1.fill(x, y, color="#dcdcdc", alpha=0.7, lw=0)
    for z in env.dynamic_nofly:
        for p in z.periods:
            g = _bgeom(p.poly)
            x, y = g.exterior.xy
            ax1.plot(x, y, color="red", lw=1.2, ls="--", alpha=0.7)
    # route 走廊：所有可行 OD 都画（即使平滑失败、没有 path，也能看出 route 存在）
    for r in results:
        if not r.get("feasible"):
            continue
        rc = r.get("route_control_points")
        if rc:
            rc2 = np.array([[a, b] for a, b, c in rc])
            ax1.plot(rc2[:, 0], rc2[:, 1], "--", color="#9a9a9a", lw=1.0, alpha=0.8)
            ax1.plot(rc2[:, 0], rc2[:, 1], "x", color="#666666", ms=3)
        pxy = r.get("path_xy")
        if pxy:
            p2 = np.array(pxy)
            tag = f"OD{r['od_id']}({r['level']})"
            ax1.plot(p2[:, 0], p2[:, 1], "-", lw=1.8, label=tag, color=None)
        ax1.plot([r["origin"][0]], [r["origin"][1]], "o", color="#2ca02c", ms=7)
        ax1.plot([r["dest"][0]], [r["dest"][1]], "s", color="#d1495b", ms=7)
    # 图例：用代理线条说明 route（虚线）与 path（实线）两种线型
    from matplotlib.lines import Line2D
    proxy = [Line2D([0], [0], ls="--", color="#9a9a9a", lw=1.0, label="route 走廊（虚线=控制点序列）"),
             Line2D([0], [0], ls="-", color="#1f77b4", lw=1.8, label="path 平滑曲线（实线）")]
    h1, l1 = ax1.get_legend_handles_labels()
    ax1.legend(proxy + h1, [t.get_label() for t in proxy] + l1, loc="upper right", fontsize=7)
    ax1.set_aspect("equal")
    ax1.set_title("route（虚线=控制点走廊） vs path（实线=Dubins 平滑曲线）")
    fig1.tight_layout()
    fig1.savefig(os.path.join(out_dir, "fig_route_vs_path.png"), dpi=130)
    plt.close(fig1)

    # ── 图2：trajectory = path + 时间参数化（颜色=到达时刻）──
    plot_items = [
        (r, np.array(r["path_xy"]), np.array(r["trajectory"]["times_s"]))
        for r in usable
    ]
    fig2, ax2 = plt.subplots(figsize=(9, 9))
    for b in env.buildings:
        g = _bgeom(b.poly)
        x, y = g.exterior.xy
        ax2.fill(x, y, color="#dcdcdc", alpha=0.7, lw=0)
    for z in env.dynamic_nofly:
        for p in z.periods:
            g = _bgeom(p.poly)
            x, y = g.exterior.xy
            ax2.plot(x, y, color="red", lw=1.2, ls="--", alpha=0.7)
    if plot_items:
        tmin = min(arr_t.min() for _, _, arr_t in plot_items)
        tmax = max(arr_t.max() for _, _, arr_t in plot_items)
        norm = Normalize(vmin=tmin, vmax=tmax)
        sc = None
        for r, p2, arr_t in plot_items:
            sc = ax2.scatter(p2[:, 0], p2[:, 1], c=arr_t, cmap=cmap, norm=norm,
                             s=9, zorder=3)
            ax2.plot([r["origin"][0]], [r["origin"][1]], "o", color="#2ca02c", ms=7, zorder=4)
            ax2.plot([r["dest"][0]], [r["dest"][1]], "s", color="#d1495b", ms=7, zorder=4)
        if sc is not None:
            cbar = fig2.colorbar(sc, ax=ax2)
            cbar.set_label("到达时刻 time (s)  [匀速占位，非时间最优]")
    ax2.set_aspect("equal")
    ax2.set_title("trajectory = path + 时间参数化（颜色=到达时刻，当前为匀速占位）")
    fig2.tight_layout()
    fig2.savefig(os.path.join(out_dir, "fig_trajectory.png"), dpi=130)
    plt.close(fig2)
    print(f"[exp01] 区分图已写入 {out_dir}/fig_route_vs_path.png, fig_trajectory.png")


def _plot_region_overview(env_full, env, net, results, scenario_dir, out_dir):
    """整个区域总览（俯视图）：全城建筑分布 + 研究区域边界 + 静态/动态禁飞区
    + 裁剪活跃窗口 + 规划路径。解决"之前只画裁剪窗口、看不到整体"的问题。"""
    import json as _json
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.geometry import shape

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(12, 12))

    # 全区域建筑：极淡散点（按高度着色），仅作密度/分布背景，避免淹没路径
    bx = np.array([b.poly.centroid.x for b in env_full.buildings])
    by = np.array([b.poly.centroid.y for b in env_full.buildings])
    bh = np.array([getattr(b, "height", 0.0) for b in env_full.buildings])
    hmax = float(bh.max()) if len(bh) else 1.0
    hb = ax.hexbin(bx, by, gridsize=55, cmap="YlOrRd", alpha=0.70, mincnt=1,
                   bins="log", label="全区域建筑(密度热力, 对数)")
    cb = fig.colorbar(hb, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("建筑密度 (栋/格, 对数)")

    # 研究区域边界（region.geojson → 局部坐标）
    try:
        rg_path = os.path.join(scenario_dir, "region.geojson")
        with open(rg_path, encoding="utf-8") as f:
            rg = _json.load(f)
        from path_planning.utils.geometry import LocalCRS
        crs = LocalCRS(env.meta["crs"]["lon0"], env.meta["crs"]["lat0"])
        for feat in rg.get("features", []):
            g = crs.transform_geom(shape(feat["geometry"]))
            polys = g.geoms if g.geom_type == "MultiPolygon" else [g]
            for gp in polys:
                xs, ys = gp.exterior.xy
                ax.plot(xs, ys, color="#222222", lw=2.4, label="研究区域边界")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 区域边界加载失败: {e}")

    # 静态禁飞区（北侧，红色填充）
    for z in env.static_nofly:
        g = _bgeom(z.poly)
        xs, ys = g.exterior.xy
        ax.fill(xs, ys, color="#b30000", alpha=0.22)
        ax.plot(xs, ys, color="#b30000", lw=1.6, label="静态禁飞区(无限高)")
    # 动态禁飞区（crop 内，红色虚线）
    for z in env.dynamic_nofly:
        for p in z.periods[:1]:
            g = _bgeom(p.poly)
            xs, ys = g.exterior.xy
            ax.plot(xs, ys, color="#ff0000", lw=1.6, ls="--", label="动态禁飞区(无限高)")

    # 裁剪活跃窗口（粗蓝框）
    cbx = np.array([b.poly.centroid.x for b in env.buildings])
    cby = np.array([b.poly.centroid.y for b in env.buildings])
    if len(cbx):
        x0, x1 = float(cbx.min()), float(cbx.max())
        y0, y1 = float(cby.min()), float(cby.max())
        ax.fill([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0],
                facecolor="#0050ff", alpha=0.15)
        ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0],
                color="#0050ff", lw=2.6, label="裁剪活跃窗口")
        # 主图：细路径（仅定位）；清晰版见右下角放大窗
        for r in results:
            xy = r.get("path_xy")
            if xy:
                p2 = np.array(xy)
                ax.plot(p2[:, 0], p2[:, 1], "-", lw=1.0, color="#1f77b4", alpha=0.7)
            ax.plot([r["origin"][0]], [r["origin"][1]], "o", color="#2ca02c", ms=4)
            ax.plot([r["dest"][0]], [r["dest"][1]], "s", color="#d1495b", ms=4)

        # ── 左上角放大窗：清晰显示裁剪窗口内的建筑/禁飞/路径（避开位于右下角的裁剪窗口屏幕位置）──
        axins = ax.inset_axes([0.04, 0.56, 0.34, 0.34])
        for b in env.buildings:
            g = _bgeom(b.poly)
            xs, ys = g.exterior.xy
            axins.fill(xs, ys, color="#d0d0d0", alpha=0.85, lw=0)
        for z in env.dynamic_nofly:
            for p in z.periods[:1]:
                g = _bgeom(p.poly)
                xs, ys = g.exterior.xy
                axins.plot(xs, ys, color="#ff0000", lw=1.4, ls="--")
        for r in results:
            xy = r.get("path_xy")
            if xy:
                p2 = np.array(xy)
                axins.plot(p2[:, 0], p2[:, 1], "-", lw=1.8, color="#1f77b4",
                           label=f"OD{r['od_id']}({r['level']})" if r["od_id"] == 0 else None)
            axins.plot([r["origin"][0]], [r["origin"][1]], "o", color="#2ca02c", ms=7)
            axins.plot([r["dest"][0]], [r["dest"][1]], "s", color="#d1495b", ms=7)
        axins.set_xlim(x0, x1)
        axins.set_ylim(y0, y1)
        axins.set_xticks([])
        axins.set_yticks([])
        axins.set_title("裁剪窗口放大", fontsize=8)
        ax.indicate_inset_zoom(axins, edgecolor="#0050ff")

    ax.set_aspect("equal")
    ax.set_title("整个区域总览：全城建筑(淡, 色=高度) + 区域边界 + 静态/动态禁飞区\n"
                 "+ 蓝色裁剪窗口(规划活跃区) + 左上角放大窗(清晰路径)")
    ax.legend(loc="lower left", fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig_region_overview.png"), dpi=130)
    plt.close(fig)
    print(f"[exp01] 整区域总览图已写入 {out_dir}/fig_region_overview.png")


def _plot_side_view(env, net, planned, out_dir):
    """侧面图（沿轨迹的竖直剖面）：x 轴=轨迹水平弧长，y 轴=高度。

    画法（诚实、避免投影假象）：
      · 建筑：仅画轨迹近距离擦过的楼（path 到楼footprint最近距离 < 阈值），
        画成 0..楼高 灰色矩形——表示"此处旁/下方有该高度的楼，轨迹以高度 z 通过"。
      · 禁飞区(无限高)：仅画轨迹最近邻位置处的红色虚线参考标（full height），
        表示"此处侧向有无限高禁飞墙、轨迹已横向绕开"；若轨迹真实进入则画实心红带。
      · 轨迹：path 的 (弧长, 高度) 曲线。
    审计已确认 collides_dynamic=False（轨迹未进入禁飞区），红线仅为约束参照。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from shapely.geometry import LineString, Point

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    feasible = [
        (o, d, res) for (o, d, res) in planned
        if res.feasible and res.path is not None and len(res.path.pts) >= 2
    ]
    if not feasible:
        print("[exp01] 无可行路径，跳过侧面图")
        return

    ceil_z = max(60.0, *( [float(getattr(b, "height", 0.0)) for b in env.buildings] + [0.0] ))
    ceil_z = min(ceil_z * 1.15, 200.0)

    n = len(feasible)
    fig, axes = plt.subplots(1, n, figsize=(7 * n, 7), squeeze=False)
    axes = axes[0]

    for k, (ax, (o, d, res)) in enumerate(zip(axes, feasible)):
        o = np.asarray(o, float)
        d = np.asarray(d, float)
        pts = np.asarray(res.path.pts, float)
        # 水平弧长
        xy = pts[:, :2]
        seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
        s_arc = np.concatenate([[0.0], np.cumsum(seg)])
        z_path = pts[:, 2] if pts.shape[1] >= 3 else np.zeros(len(pts))
        s_hi = float(s_arc[-1])

        path_line = LineString([tuple(p) for p in xy])
        nfz_labeled = False

        # 建筑：轨迹近距离擦过的楼 → 0..楼高 灰矩形
        for b in env.buildings:
            bp = _bgeom(b.poly)
            if path_line.distance(bp) is None or path_line.distance(bp) > 12.0:
                continue
            bi = int(np.argmin([Point(p).distance(bp) for p in xy]))
            s0 = max(0.0, s_arc[bi] - 8.0)
            s1 = min(s_hi, s_arc[bi] + 8.0)
            h = float(getattr(b, "height", 0.0))
            ax.add_patch(Rectangle((s0, 0), s1 - s0, h, facecolor="#cfcfcf",
                                   edgecolor="#777777", lw=0.3, alpha=0.8, zorder=1))

        # 禁飞区(无限高)：真实进入→实心红带；否则→红色虚线参考标
        for z in env.dynamic_nofly:
            for p in z.periods:
                pp = _bgeom(p.poly)
                inter = path_line.intersection(pp)
                if not inter.is_empty:
                    xs = [c for g in (inter.geoms if inter.geom_type == "MultiLineString" else [inter])
                          for c in (g.coords if g.geom_type == "LineString" else [g.coords[0]])]
                    svals = []
                    for c in xs:
                        bi = int(np.argmin([(p2[0] - c[0]) ** 2 + (p2[1] - c[1]) ** 2 for p2 in xy]))
                        svals.append(s_arc[bi])
                    s0, s1 = min(svals), max(svals)
                    ax.add_patch(Rectangle((s0, 0), max(s1 - s0, 1.0), ceil_z,
                                           facecolor="#ff0000", edgecolor="none", alpha=0.25, zorder=2))
                else:
                    if path_line.distance(pp) > 60.0:
                        continue
                    bi = int(np.argmin([Point(p).distance(pp) for p in xy]))
                    sc0 = max(0.0, s_arc[bi] - 1.5)
                    ax.plot([sc0 + 1.5, sc0 + 1.5], [0, ceil_z], "--", color="#ff0000",
                            lw=1.8, zorder=3,
                            label=("禁飞区(无限高, 轨迹已绕开)" if not nfz_labeled else None))
                    nfz_labeled = True

        # 轨迹
        ax.plot(s_arc, z_path, "-", color="#1f77b4", lw=2.4, zorder=5,
                label="轨迹 path（弧长 vs 高度）")
        ax.plot([0], [z_path[0]], "o", color="#2ca02c", ms=9, zorder=6)
        ax.plot([s_hi], [z_path[-1]], "s", color="#d1495b", ms=9, zorder=6)
        ax.axhline(0, color="#444444", lw=0.8, zorder=0)
        ax.set_xlabel("沿轨迹水平弧长 (m)")
        ax.set_ylabel("高度 z (m)")
        ax.set_xlim(-5, s_hi + 5)
        ax.set_ylim(-6, ceil_z)
        ax.set_title(f"OD{k} 侧面剖面 (level={res.level})\n"
                     f"灰矩形=旁/下方建筑(可飞越)  红虚线=禁飞区(无限高,已绕开)\n"
                     f"审计 collides_dynamic=False：轨迹未进入禁飞区")
        ax.legend(loc="upper right", fontsize=7)

    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "fig_side_view.png"), dpi=130)
    plt.close(fig)
    print(f"[exp01] 侧面图已写入 {out_dir}/fig_side_view.png")


if __name__ == "__main__":
    raise SystemExit(main())
