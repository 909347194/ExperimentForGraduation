# -*- coding: utf-8 -*-
"""exp01_smoke 产物组装：指标汇总、运行元信息与终端报表。

只做「内存对象 → 可机读 dict」与打印，**不跑算法、不解析 yaml**（编排在 run.py）。

唯一产物::

    results/metrics.json   口径定义 + 配置快照 + 场景/网络统计 + 专项用例
                           + 逐 OD 记录（route/path/trajectory 三者分存）+ 汇总

route / path / trajectory 三者定义（``DEFINITIONS``，随产物落盘自解释）：

    route      控制网络上的虚拟链路序列（走廊），仅几何、无平滑   Lin2021 Fig.1(a)
    path       Dubins 平滑后的三维空间曲线，仅几何、无时间        Lin2021 Fig.1(b)
    trajectory path + 时间参数化（当前匀速占位，time_optimal 待实现）Lin2021 Fig.1(c)
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional, Sequence

import numpy as np

__all__ = [
    "DEFINITIONS",
    "od_record",
    "build_metrics",
    "write_metrics",
    "print_report",
]

# 三者定义（自解释，确保结果里 route/path/trajectory 不被混用）
DEFINITIONS = {
    "route": (
        "控制网络上的虚拟链路序列（走廊）：起点+控制点序列+终点，含各段时刻与距离。"
        "仅几何、无平滑。对应 Lin2021 Fig.1(a)。"
    ),
    "path": (
        "Dubins 平滑后的三维空间曲线（仅几何、无时间）：route → smooth_route。"
        "对应 Lin2021 Fig.1(b)。"
    ),
    "trajectory": (
        "path + 时间参数化（位置+速度/时刻）。当前时间为匀速占位"
        "（trajectory.timing='uniform_placeholder'），非 Lin2021 §2.4 时间最优轨迹"
        "（time_optimal.py 待实现）。对应 Lin2021 Fig.1(c)。"
    ),
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _r(v, nd: int = 2):
    return round(float(v), nd) if v is not None else None


def _downsample(arr: np.ndarray, step: int) -> np.ndarray:
    """等间隔下采样并**强制保留末点**（轨迹/路径端点不能丢）。"""
    out = arr[::step]
    if len(arr) and (len(arr) - 1) % step != 0:
        out = np.concatenate([out, arr[-1:]], axis=0)
    return out


# ---------------------------------------------------------------------------
# 逐 OD 记录
# ---------------------------------------------------------------------------


def od_record(
    od_id: int,
    o: np.ndarray,
    d: np.ndarray,
    res: Any,
    net: Any,
    *,
    t_dep: float = 0.0,
    group: str = "auto",
    audit: Optional[dict] = None,
) -> dict:
    """单个 OD 的规划结果 → 可机读记录。

    route / path / trajectory 三者**分存**（严格区分，不合并）：

    * ``route_control_points``   起点 + 控制点序列 + 终点（3D，走廊）
    * ``path_xy``                平滑曲线的 xy 采样（仅几何、无时间）
    * ``trajectory``             path + 匀速时间占位（times/speeds）
    """
    # route：起点 + 控制点序列 + 终点（走廊，含几何但无平滑）
    route_pts_3d = None
    if res.route is not None:
        seq = [np.asarray(o, dtype=float)] + [
            net.points[pi] for pi in res.route.points
        ] + [np.asarray(d, dtype=float)]
        route_pts_3d = [[_r(v) for v in p] for p in seq]

    # path：Dubins 平滑后的三维空间曲线（仅几何、无时间），下采样存 xy
    path_xy = None
    if res.path is not None and len(res.path.pts):
        pp = _downsample(np.asarray(res.path.pts, dtype=float), step=3)
        path_xy = [[_r(a), _r(b)] for a, b in pp[:, :2]]

    # trajectory：path + 时间维（匀速占位，非时间最优），与 path 分开存
    traj = None
    if res.trajectory is not None and len(res.trajectory.times):
        tt = _downsample(np.asarray(res.trajectory.times, dtype=float), step=3)
        ss = _downsample(np.asarray(res.trajectory.speeds, dtype=float), step=3)
        traj = {
            "timing": "uniform_placeholder",
            "note": (
                "trajectory = path + 匀速时间占位（非 Lin2021 §2.4 时间最优轨迹，"
                "time_optimal.py 待实现）"
            ),
            "times_s": [_r(t) for t in tt],
            "speeds_mps": [_r(s, 3) for s in ss],
        }

    rec = {
        "od_id": od_id,
        "group": group,
        "t_dep": _r(t_dep),
        "origin": [_r(v) for v in o],
        "dest": [_r(v) for v in d],
        "feasible": bool(res.feasible),
        "level": res.level,
        "total_time_s": (
            _r(res.total_time, 3) if np.isfinite(res.total_time) else None
        ),
        # ── route（走廊）：控制点序列 + 链路距离 ──
        "route_dist_m": _r(res.route.dist) if res.route else None,
        "n_route_points": len(res.route.points) if res.route else 0,
        "route_control_points": route_pts_3d,
        # ── path（平滑曲线）：几何 + 弧长（无时间）──
        "path_arc_length_m": _r(res.path.arc_length) if res.path is not None else None,
        "n_path_samples": int(len(res.path.pts)) if res.path is not None else 0,
        "path_xy": path_xy,
        # ── trajectory（轨迹）：path + 时间维（匀速占位，与 path 严格分离）──
        "trajectory": traj,
        "smooth_failed": bool(res.stats.get("smooth_failed", False)),
        "plan_time_s": res.stats.get("plan_time_s"),
        "n_dijkstra": res.stats.get("n_dijkstra"),
        "n_l2_iters": res.stats.get("n_l2_iters"),
        "blocked_pairs": res.stats.get("blocked_pairs"),
        "reason": (res.infeasible_reason.name if res.infeasible_reason else None),
    }
    if audit is not None:
        rec["audit"] = audit
    return rec


# ---------------------------------------------------------------------------
# 汇总与落盘
# ---------------------------------------------------------------------------


def build_metrics(
    *,
    cfg: Any,
    env: Any,
    net_stats: dict,
    unit: dict,
    results: Sequence[dict],
    scenario_dir: str,
    crop: Sequence[float],
    wall_time_s: float,
) -> dict:
    """一次运行的全部事实 → metrics.json 顶层结构。"""
    summary = {
        "n_od": len(results),
        "n_feasible": sum(1 for r in results if r["feasible"]),
        "n_route_only": sum(1 for r in results if r["feasible"] and r.get("smooth_failed")),
        "n_path_trajectory": sum(
            1 for r in results if r.get("path_xy") and r.get("trajectory")
        ),
        "level_counts": {
            lv: sum(1 for r in results if r["level"] == lv)
            for lv in sorted({r["level"] for r in results})
        },
        "n_audit_ok": sum(1 for r in results if (r.get("audit") or {}).get("ok")),
        "wall_time_s": round(wall_time_s, 3),
    }
    # 分组透视（auto / diag / time_control）：一眼看清各组通过情况
    by_group: dict[str, dict] = {}
    for r in results:
        g = by_group.setdefault(r.get("group", "auto"), {"n": 0, "n_feasible": 0})
        g["n"] += 1
        g["n_feasible"] += int(bool(r["feasible"]))
    summary["by_group"] = by_group

    return {
        "experiment": "exp01_smoke",
        "definitions": dict(DEFINITIONS),
        "nofly_zones": [
            {
                "zid": z.zid,
                "periods": [
                    {
                        "start": p.start,
                        "end": p.end,
                        "center": [_r(p.poly.centroid.x, 1), _r(p.poly.centroid.y, 1)],
                        "bounds": [_r(v, 1) for v in p.poly.bounds],
                        "z": [p.z0, p.z1],
                    }
                    for p in z.periods
                ],
            }
            for z in env.dynamic_nofly
        ],
        "scenario": {"dir": scenario_dir, "crop": [_r(c) for c in crop]},
        "config": cfg.to_dict(),
        "env": env.summary(),
        "network": net_stats,
        "unit_checks": unit,
        "od_results": list(results),
        "summary": summary,
    }


def write_metrics(metrics: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "metrics.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    return path


def print_report(metrics: dict) -> None:
    """终端报表：专项用例 + 汇总（逐 OD 的进度行由 run.py 在规划时实时打印）。"""
    unit = metrics.get("unit_checks") or {}
    if unit:
        print("[exp01] 专项用例:", json.dumps(unit, ensure_ascii=False))
    print("[exp01] 汇总:", json.dumps(metrics["summary"], ensure_ascii=False))
