# -*- coding: utf-8 -*-
"""exp01_smoke 入口：只做**编排调度**，不堆细节。

流程：
    配置 → 场景（广州真实建筑，可选裁剪窗口）→ 禁飞区（file/synthetic/none）
         → 离线建网 → 【专项正确性用例】→ OD 规划（L1/L2/L2.5 分层）
         → 路径独立复核 → metrics.json（组装在 metrics.py）→ 图（出图在 plot/）

分工（docs/architecture.md §5：run.py 只编排，config/metrics/plot 分离）：
    config.yaml   全部参数
    metrics.py    指标组装 + 终端报表（内存对象 → 可机读 dict）
    plot/         绘图（registry 图清单 + theme 样式 + 分图模块）

正确性用例（checks.enable_unit_checks）：
    U1 链路代价序关系：t(0,0) ≥ t(0,s) ≥ t(s,s)=D/v_u，且 t(s,s) 与 Eq.(4) 一致
    U2 建筑穿越判定：穿建筑的链路 link_feasible=False
    U3 整边判定"擦边"：飞行途中禁飞生效 → dynamic_blocked=True（整边不可省）
    U4 完全在时窗之前/之后飞行 → dynamic_blocked=False
    U5 保守判据 ⊇ 整边判据：整边判冲突 ⇒ 保守必判冲突
"""

from __future__ import annotations

import argparse
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

import metrics as exp_metrics  # noqa: E402
import plot as exp_plot  # noqa: E402
from plot.registry import PlotContext  # noqa: E402


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
    # 先浅拷贝：调用方常以 env_full 传入并另存全量引用（供整区域图用），
    # 绝不能原地修改污染全量建筑集。
    import copy as _copy

    env = _copy.copy(env)
    env.meta = dict(env.meta)
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


def _diag_od(crop, bounds, z_od, min_side: float = 45.0, edge: float = 15.0,
             reach: float = 150.0):
    """为动态禁飞区生成一条**横穿**它的诊断 OD（端点在区外、窗口内）。

    水平优先（本场景 OD 主流向是东西向），放不下才垂直横穿；
    端点距禁飞区边 = min(reach, 60% 该侧空间)，太贴边会落进控制点稀疏带/
    保守剪枝带 → UNREACHABLE；且离窗口边至少 edge。两轴都不合法 → None。
    """
    x0, y0, x1, y1 = crop
    bx0, by0, bx1, by1 = bounds
    cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
    axes = {
        "h": (bx0 - x0, x1 - bx1),   # 水平横穿：左/右空间
        "v": (by0 - y0, y1 - by1),   # 垂直横穿：下/上空间
    }
    # 水平优先（本场景 OD 主流向是东西向）；水平放不下才垂直
    best = "h" if min(axes["h"]) >= min_side else (
        "v" if min(axes["v"]) >= min_side else None)
    if best is None:
        return None
    side_a, side_b = axes[best]
    if min(side_a, side_b) < min_side:
        return None
    da, db = min(reach, 0.6 * side_a), min(reach, 0.6 * side_b)
    if best == "h":
        o = (bx0 - da, cy)
        d = (bx1 + db, cy)
    else:
        o = (cx, by0 - da)
        d = (cx, by1 + db)
    for px, py in (o, d):
        if not (x0 + edge <= px <= x1 - edge and y0 + edge <= py <= y1 - edge):
            return None
    return (np.array([o[0], o[1], z_od]), np.array([d[0], d[1], z_od]))


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
    print(f"[exp01] 裁剪窗口 {[round(float(c), 1) for c in crop]} → 建筑 {len(env.buildings)} 栋")

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

    # OD 规划：auto 组（窗口内随机 OD）+ diag 组（强制穿越动态禁飞区，
    # 把 L2/L2.5 真正跑起来）+ time_control 组（同 diag 的 OD、但推迟到
    # 禁飞时段结束后起飞——验证时间窗判定不过度阻断）
    odc = cfg.section("od")
    z_od = float(odc.get("z", 20.0))
    jobs = [(o, d, 0.0, "auto") for o, d in make_ods(env, cfg, crop)]
    for z in env.dynamic_nofly:
        for per in z.periods[:1]:
            pair = _diag_od(crop, per.poly.bounds, z_od)
            if pair is None:
                print(f"[exp01] 跳过 {z.zid} 诊断 OD：窗口内放不下横穿端点")
                continue
            o, d = pair
            jobs.append((o, d, 0.0, "diag"))
            # time_control：同一条 OD，推到该禁飞区全部时段结束后起飞
            t_late = max(p.end for p in z.periods) + 10.0
            jobs.append((o.copy(), d.copy(), t_late, "time_control"))
    results = []
    planned = []
    for i, (o, d, t_dep, group) in enumerate(jobs):
        od = ODRequest(origin=o, dest=d, t_dep=t_dep)
        res = planner.plan(od)
        planned.append((o, d, res))
        rec = exp_metrics.od_record(i, o, d, res, net, t_dep=t_dep, group=group)
        if checks_cfg.get("enable_path_audit", True) and res.feasible and res.path is not None:
            rec["audit"] = audit_path(res, audit_q, cfg, float(checks_cfg.get("audit_step_m", 2.0)))
        results.append(rec)
        print(
            f"  OD{i}[{group}]: feasible={rec['feasible']} level={rec['level']} "
            f"t={rec['total_time_s']}s "
            f"route_dist={rec['route_dist_m']}m (pts={rec['n_route_points']}) "
            f"path_len={rec.get('path_arc_length_m')}m "
            f"smooth_failed={rec.get('smooth_failed')} audit={rec.get('audit')}"
            + (f" reason={rec['reason']}" if rec.get("reason") else "")
        )

    metrics = exp_metrics.build_metrics(
        cfg=cfg,
        env=env,
        net_stats=net_stats,
        unit=unit,
        results=results,
        scenario_dir=scenario_dir,
        crop=crop,
        wall_time_s=time.perf_counter() - t_start,
    )

    out_dir = os.path.join(_HERE, "results")
    path = exp_metrics.write_metrics(metrics, out_dir)
    print(f"[exp01] 结果已写入 {path}")
    exp_metrics.print_report(metrics)

    if args.plot:
        ctx = PlotContext(
            env=env, env_full=env_full, net=net,
            results=results, planned=planned, scenario_dir=scenario_dir,
        )
        saved = exp_plot.make_plots(ctx, os.path.join(out_dir, "figures"))
        print(f"[exp01] 共 {len(saved)} 张图已写入 {os.path.join(out_dir, 'figures')}")

    ok = bool(unit.get("all_pass", True)) and metrics["summary"]["n_feasible"] > 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
