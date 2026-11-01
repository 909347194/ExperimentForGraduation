# -*- coding: utf-8 -*-
"""exp01_smoke 入口：只做**编排调度**，不堆细节。

流程（第 4 章 B 路线：可见性图 / 稀疏路图）：
    配置 → 场景（广州真实建筑，可选裁剪窗口）→ 禁飞区（file/synthetic/none）
         → 离线建图（PathPlanner.from_env，图只建一次、长期复用）
         → OD 规划（auto 随机 + diag 强制穿越动态禁飞区 + time_control 延迟起飞）
         → 路径独立复核 → metrics.json（组装在 metrics.py）→ 图（出图在 plot/）

分工（docs/architecture.md §5：run.py 只编排，config/metrics/plot 分离）：
    config.yaml   全部参数（含 visibility_graph 段）
    metrics.py    指标组装 + 终端报表（内存对象 → 可机读 dict）
    plot/         绘图（registry 图清单 + theme 样式 + 分图模块）

正确性用例（checks.enable_unit_checks，针对可见性图语义）：
    U2 建筑穿越判定：穿建筑的线段 segment_clear=False
    U3 动态禁飞「擦边」：飞行途中禁飞生效 → dynamic_blocked=True
    U4 完全在时窗之前/之后飞行 → dynamic_blocked=False
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
from shapely.geometry import LineString as _LS, Point as _Pt, box

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
    load_static_nofly,
    load_dynamic_nofly,
)
from path_planning.common.types import ODRequest  # noqa: E402
from path_planning.interface.planner import PathPlanner  # noqa: E402
from path_planning.methods.visibility_graph.params import VisibilityGraphParams  # noqa: E402
from path_planning.utils.geometry import LocalCRS  # noqa: E402

import metrics as exp_metrics  # noqa: E402
import plot as exp_plot  # noqa: E402
from plot.registry import PlotContext  # noqa: E402


# ── 场景 ──────────────────────────────────────────────────────────────────


def _auto_crop(env, size: float):
    """选建筑最密的方形窗口（按栋数）。

    用积分图在网格上 O(网格) 求最密窗口，避免朴素的「窗口数 × 建筑数」双重循环
    （全量 23km 范围 × 2 万栋会让该循环超时）。
    """
    xs = np.array([b.poly.centroid.x for b in env.buildings], dtype=float)
    ys = np.array([b.poly.centroid.y for b in env.buildings], dtype=float)
    if len(xs) == 0:
        return None
    x0, x1 = float(xs.min()), float(xs.max())
    y0, y1 = float(ys.min()), float(ys.max())
    if x1 - x0 < size or y1 - y0 < size:
        return (x0, y0, x1, y1)

    bin_size = max(size / 12.0, 10.0)
    edges_x = np.arange(x0, x1 + bin_size, bin_size)
    edges_y = np.arange(y0, y1 + bin_size, bin_size)
    counts, _, _ = np.histogram2d(xs, ys, bins=[edges_x, edges_y])  # (nx, ny)
    nx, ny = counts.shape
    integ = np.zeros((nx + 1, ny + 1), dtype=float)
    integ[1:, 1:] = np.cumsum(np.cumsum(counts, axis=0), axis=1)
    kw = max(1, int(round(size / bin_size)))

    best, best_score = None, -1.0
    for ii in range(nx - kw + 1):
        for jj in range(ny - kw + 1):
            s = (integ[ii + kw, jj + kw] - integ[ii, jj + kw]
                 - integ[ii + kw, jj] + integ[ii, jj])
            if s > best_score:
                best_score, best = s, (
                    float(edges_x[ii]), float(edges_y[jj]),
                    float(edges_x[ii + kw]), float(edges_y[jj + kw]),
                )
    return best


def _apply_crop(env, window, max_buildings=None):
    # 浅拷贝：绝不能原地修改污染全量建筑集（整区域图要用）。
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


# ── 专项正确性用例（可见性图语义）──────────────────────────────────────────


def run_unit_checks(graph, env) -> dict:
    out = {}
    obs = graph.obstacles
    nf = graph.nofly

    # U2 建筑穿越判定：穿建筑的线段 segment_clear=False
    hit = False
    for b in env.buildings:
        if b.height <= 0:
            continue
        c = b.poly.centroid
        z = b.height / 2.0
        p = np.array([c.x - 200.0, c.y, z], dtype=float)
        q = np.array([c.x + 200.0, c.y, z], dtype=float)
        if b.poly.intersects(_LS([(p[0], p[1]), (q[0], q[1])])):
            hit = True
            out["U2_building_block"] = bool(not obs.segment_clear(p, q))
            break
    out.setdefault("U2_building_block", None if not hit else out.get("U2_building_block"))

    # U3/U4 动态禁飞时间窗判定
    if env.dynamic_nofly:
        z = env.dynamic_nofly[0]
        per = z.periods[0]
        cx, cy = per.poly.centroid.x, per.poly.centroid.y
        bx0, by0, bx1, by1 = per.poly.bounds
        R = max(bx1 - bx0, by1 - by0) / 2.0 + 10.0
        p = np.array([cx - R - 150.0, cy, 30.0], dtype=float)
        q = np.array([cx + R + 150.0, cy, 30.0], dtype=float)
        dist = float(np.linalg.norm(q - p))
        v = 15.0
        t_flight = dist / v
        line = _LS([(p[0], p[1]), (q[0], q[1])])
        inter = line.intersection(per.poly)
        if not inter.is_empty:
            d_in = line.project(_Pt(list(inter.coords)[0]))
            d_out = line.project(_Pt(list(inter.coords)[-1]))
            t_in, t_out = d_in / v, d_out / v
            activation = per.start
            # 擦边：让生效时刻落在穿越区间中间（起飞时未生效，途中生效）
            t_dep = activation - 0.5 * (t_in + t_out)
            t_arr = t_dep + t_flight
            out["U3_cross_activation"] = bool(nf.dynamic_blocked(p, q, t_dep, t_arr))
            # U4 仅在禁飞区前有真实"空闲窗"时才校验（合成区可能 t_start=0，无前窗）
            if activation >= 60.0:
                out["U4_before_window"] = bool(
                    not nf.dynamic_blocked(p, q, max(0.0, activation - 60.0), activation - 1.0)
                )
            else:
                out["U4_before_window"] = None  # 无前窗，跳过
            out["U4_after_window"] = bool(
                not nf.dynamic_blocked(p, q, per.end + 50.0, per.end + 110.0)
            )
            out["U3_detail"] = {
                "t_dep": round(t_dep, 2),
                "t_arr": round(t_arr, 2),
                "traverse_in_zone": [round(t_dep + t_in, 2), round(t_dep + t_out, 2)],
                "activation": activation,
            }
        else:
            out["U3_cross_activation"] = None
            out["U4_before_window"] = True
            out["U4_after_window"] = True
    else:
        out["U3_cross_activation"] = None
        out["U4_before_window"] = None
        out["U4_after_window"] = None

    out["all_pass"] = bool(
        (out.get("U2_building_block") in (True, None))
        and (out.get("U3_cross_activation") in (True, None))
        and (out.get("U4_before_window") in (True, None))
        and (out.get("U4_after_window") in (True, None))
    )
    return out


# ── OD 生成 ───────────────────────────────────────────────────────────────


def _diag_od(crop, bounds, z_od, min_side: float = 45.0, edge: float = 15.0,
             reach: float = 150.0):
    """为动态禁飞区生成一条**横穿**它的诊断 OD（端点在区外、窗口内）。

    水平优先（本场景 OD 主流向是东西向），放不下才垂直横穿；
    端点距禁飞区边 = min(reach, 60% 该侧空间)，太贴边会落进节点稀疏带 → UNREACHABLE；
    且离窗口边至少 edge。两轴都不合法 → None。
    """
    x0, y0, x1, y1 = crop
    bx0, by0, bx1, by1 = bounds
    cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
    axes = {
        "h": (bx0 - x0, x1 - bx1),
        "v": (by0 - y0, y1 - by1),
    }
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


def _cross_city_ods(graph, env, z: float, margin: float = 0.0):
    """整城跨城 OD：全图包围盒的四角组合（SW→NE / SE→NW / SW→SE）。

    端点**吸附到最近的图节点**：包围盒顶点常落在图覆盖之外（外围建筑稀疏、无可连节点），
    直接用顶点会 UNREACHABLE。这是整城 demo 唯一必要的容错。
    """
    nodes = np.asarray(graph.nodes, dtype=float)
    xs: list[float] = []
    ys: list[float] = []
    for b in env.buildings:
        x0, y0, x1, y1 = b.poly.bounds
        xs += [x0, x1]
        ys += [y0, y1]
    X0, X1 = min(xs) + margin, max(xs) - margin
    Y0, Y1 = min(ys) + margin, max(ys) - margin

    def snap(x: float, y: float) -> np.ndarray:
        d = np.linalg.norm(nodes[:, :2] - np.array([x, y]), axis=1)
        best = nodes[int(np.argmin(d))]
        return np.array([best[0], best[1], z], dtype=float)

    pairs = [((X0, Y0), (X1, Y1)), ((X1, Y0), (X0, Y1)), ((X0, Y0), (X1, Y0))]
    return [(snap(ox, oy), snap(dx, dy)) for (ox, oy), (dx, dy) in pairs]


def audit_path(res, graph, step: float) -> dict:
    """平滑后路径的独立复核（与搜索期判定解耦，避免同源错误）。"""
    if res.path is None or len(res.path.pts) < 2:
        return {"ok": None}
    pts = res.path.pts
    # 建筑 + 静态禁飞（零余量"地面真值"，与规划判定解耦）
    dense = _dense(pts, step)
    for i in range(len(dense) - 1):
        if not graph.obstacles.segment_clear(dense[i], dense[i + 1], margin=0.0):
            return {"ok": False, "where": "building"}
        if graph.nofly.blocked(dense[i], dense[i + 1], margin=0.0):
            return {"ok": False, "where": "nofly"}
    # 动态禁飞：用轨迹时刻做切段复核
    times = res.trajectory.times if res.trajectory is not None else None
    if times is not None and len(times) == len(pts):
        for i in range(len(pts) - 1):
            if graph.nofly.dynamic_blocked(pts[i], pts[i + 1], times[i], times[i + 1]):
                return {"ok": False, "where": "dynamic"}
    return {"ok": True}


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


def _apply_overrides(cfg: Config, overrides: list[str]) -> None:
    """把 --set section.key=value 写入 cfg.raw（嵌套字典），支持可见性图参数。"""
    for kv in overrides:
        if "=" not in kv:
            continue
        sec, rest = kv.split(".", 1)
        key, val = rest.split("=", 1)
        d = cfg.raw.setdefault(sec, {})
        if val.lower() in ("true", "false"):
            d[key] = (val.lower() == "true")
            continue
        for cast in (int, float):
            try:
                d[key] = cast(val)
                break
            except ValueError:
                continue
        else:
            d[key] = val


def main() -> int:
    ap = argparse.ArgumentParser(description="exp01_smoke 正确性调试（可见性图）")
    ap.add_argument("--config", default=os.path.join(_HERE, "config.yaml"))
    ap.add_argument("--set", action="append", default=[],
                    help="覆盖配置，如 --set visibility_graph.max_nodes=6000")
    ap.add_argument("--plot", action="store_true", help="出 registry 登记的 6 张标准图")
    ap.add_argument("--full-city", action="store_true",
                    help="整城粗网格演示（秒级）：跨城四角 OD + fig_full_city_3od.png；"
                         "强制 crop=full 并套用 config.yaml 里 full_city_viz.graph 的粗参数")
    args = ap.parse_args()

    cfg = load_config(args.config) if os.path.exists(args.config) else Config()
    _apply_overrides(cfg, args.set)

    # --full-city：参数一律来自 config.yaml（full_city_viz 段），不在脚本里内联，
    # 避免与正式档 visibility_graph 形成两套互不一致的标定。
    if args.full_city:
        fcv = cfg.section("full_city_viz") or {}
        cfg.raw.setdefault("scenario", {})["crop"] = "full"
        gov = dict(fcv.get("graph") or {})
        if gov:
            cfg.raw.setdefault("visibility_graph", {}).update(gov)
        print(f"[exp01] --full-city 模式：crop=full，套用 {len(gov)} 项粗网格参数（来自 config）")

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

    cropsrc = sc.get("crop", "auto")
    if cropsrc in ("full", "none", "all"):
        # 整城：不裁剪。OD 规划窗取全量建筑外接范围。
        crop = None
        env = env_full
        for i, b in enumerate(env.buildings):
            b.bid = i
        bx = np.array(
            [b.poly.bounds for b in env.buildings if b.poly is not None],
            dtype=float,
        )
        crop_window = (float(bx[:, 0].min()), float(bx[:, 1].min()),
                       float(bx[:, 2].max()), float(bx[:, 3].max()))
        print(f"[exp01] 裁剪: 全量（不裁剪）→ 建筑 {len(env.buildings)} 栋")
    elif cropsrc == "auto" or cropsrc is None:
        size = float(sc.get("crop_size", 600.0))
        crop = _auto_crop(env_full, size)
        env = _apply_crop(env_full, crop, sc.get("max_buildings"))
        crop_window = crop
        print(f"[exp01] 裁剪窗口（auto 最密）{[round(float(c), 1) for c in crop]} "
              f"→ 建筑 {len(env.buildings)} 栋")
    else:
        crop = [float(v) for v in cropsrc]
        env = _apply_crop(env_full, crop, sc.get("max_buildings"))
        crop_window = crop
        print(f"[exp01] 裁剪窗口（指定）{[round(float(c), 1) for c in crop]} "
              f"→ 建筑 {len(env.buildings)} 栋")

    # 禁飞区
    nofly_cfg = cfg.section("nofly")
    src = nofly_cfg.get("source", "none")
    nofly_dir = nofly_cfg.get("dir", scenario_dir)
    if not os.path.isabs(nofly_dir):
        nofly_dir = os.path.join(_ROOT, nofly_dir)
    if src == "file":
        crs = LocalCRS(env.meta["crs"]["lon0"], env.meta["crs"]["lat0"])
        env.static_nofly = load_static_nofly(
            os.path.join(nofly_dir, "nofly_static.geojson"), crs,
            float(nofly_cfg.get("d_s_stat", 5.0)),
        )
        env.dynamic_nofly = load_dynamic_nofly(
            os.path.join(nofly_dir, "nofly_dynamic.geojson"), crs
        )
    elif src == "synthetic":
        syn = nofly_cfg.get("synthetic", {})
        env.dynamic_nofly = synthesize_dynamic_nofly(env, **syn)
        env.static_nofly = []
    else:
        env.dynamic_nofly = []
        env.static_nofly = []
    print(f"[exp01] 禁飞区: 静态 {len(env.static_nofly)} / 动态 {len(env.dynamic_nofly)}"
          f"（来源 {src}）")

    # 离线建图（图只建一次，长期复用；选项 A 核心优势）
    vg_cfg = cfg.section("visibility_graph") or {}
    params = VisibilityGraphParams.from_dict(vg_cfg)
    planner = PathPlanner.from_env(env, params, verbose=True)
    graph = planner.graph

    # 专项正确性用例
    checks_cfg = cfg.section("checks")
    unit = {}
    if checks_cfg.get("enable_unit_checks", True):
        unit = run_unit_checks(graph, env)

    # OD 规划：auto 组（窗口内随机 OD）+ diag 组（强制穿越动态禁飞区）+ time_control 组
    odc = cfg.section("od")
    z_od = float(odc.get("z", 20.0))
    if args.full_city:
        fcv = cfg.section("full_city_viz") or {}
        z_fc = float(fcv.get("z", z_od))
        t_fc = float(fcv.get("depart_time", 0.0))
        jobs = [(o, d, t_fc, "cross_city")
                for o, d in _cross_city_ods(graph, env, z_fc)]
        print(f"[exp01] --full-city：{len(jobs)} 条跨城 OD，起飞 t={t_fc}s（晚于动态禁飞时段）")
    else:
        jobs = [(o, d, 0.0, "auto") for o, d in make_ods(env, cfg, crop_window)]
        for z in env.dynamic_nofly:
            for per in z.periods[:1]:
                pair = _diag_od(crop_window, per.poly.bounds, z_od)
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
        rec = exp_metrics.od_record(i, o, d, res, t_dep=t_dep, group=group)
        if checks_cfg.get("enable_path_audit", True) and res.feasible and res.path is not None:
            rec["audit"] = audit_path(res, graph, float(checks_cfg.get("audit_step_m", 2.0)))
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
        graph_stats=planner.build_stats,
        planner_params=graph.params.to_dict(),
        unit=unit,
        results=results,
        scenario_dir=scenario_dir,
        crop=crop_window,
        wall_time_s=time.perf_counter() - t_start,
    )

    out_dir = os.path.join(_HERE, "results")
    path = exp_metrics.write_metrics(metrics, out_dir)
    print(f"[exp01] 结果已写入 {path}")
    exp_metrics.print_report(metrics)

    figures_dir = os.path.join(out_dir, "figures")
    ctx = PlotContext(
        env=env, env_full=env_full, graph=graph,
        results=results, planned=planned, scenario_dir=scenario_dir,
    )
    if args.full_city:
        # 整城 demo 只出全范围图：6 张标准图是围绕窗口内 OD 与动态禁飞设计的，
        # 在整城粗网格下没有叙事意义（硬画只会得到一堆看不清的全域缩略）。
        from plot.full_city_3od import plot_full_city_3od  # noqa: PLC0415
        from plot.full_city_plotly import plot_full_city_plotly  # noqa: PLC0415
        from plot.scene_3d import plot_scene_3d  # noqa: PLC0415

        # 与 exp_plot.make_plots 保持一致：本图被直接调用，需自己套一次全局样式（含 CJK 字体链）
        exp_plot.setup_style()
        saved = plot_full_city_3od(ctx, figures_dir)
        print(f"[exp01] 整城静态图已写入 {saved[0]}")
        # plotly 交互版（20,655 栋建筑栅格化底图 + 矢量 OD 叠加，可缩放/hover）
        saved_html = plot_full_city_plotly(ctx, figures_dir)
        print(f"[exp01] 整城 plotly 交互图已写入 {saved_html[0]}")
        # 三维场景（建筑棱柱按高着色 + 禁飞棱柱 + 3D 轨迹）：plotly 交互 HTML + mplot3d 静态 PNG。
        # 已对 20k 建筑做保护——超出 MAX_BUILDINGS_MESH 时只保留面积最大 N 栋，建图/渲染不卡。
        saved_3d = plot_scene_3d(ctx, figures_dir)
        print(f"[exp01] 整城三维场景已写入 {saved_3d}")
    elif args.plot:
        saved = exp_plot.make_plots(ctx, figures_dir)
        print(f"[exp01] 共 {len(saved)} 张图已写入 {figures_dir}")

    ok = bool(unit.get("all_pass", True)) and metrics["summary"]["n_feasible"] > 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
