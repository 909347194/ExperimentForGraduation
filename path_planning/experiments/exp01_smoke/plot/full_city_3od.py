# -*- coding: utf-8 -*-
"""fig_full_city_3od —— 整城跨城 OD 俯视图（覆盖全 scope，不裁剪）。

为什么**不在** ``plot/registry.FIGURES`` 里：
    正常跑（默认整城正确性验证 / auto 小窗）出的是 6 张标准论文图，各自有明确叙事位；
    本图是 ``run.py --full-city`` 专属——那是一套**独立的粗网格参数**（建图秒级，
    用于展示全城规划可行性与绕行比例），画在常规 runs 里没有意义。
    故由 run.py 在该模式下直接调用，位置见 run.py 的 ``--full-city`` 分支。

它是"示意性全城路径图"：纯网格（跳过建筑壳节点）、关闭机动保护区、建筑外扩降到 3m，
路径绕开建筑本体但不带常规飞行余量——**不是**交付用的精细图
（精细全图见 exp02_vg 的 104k 节点验证）。参数全部来自 config.yaml 的 ``full_city_viz`` 段。

数据来源：``ctx.env_full``（全量建筑背景）+ ``ctx.planned``（(origin, dest, PlanResult)）
+ ``ctx.graph.nodes``（节点数标注）。
"""

from __future__ import annotations

import os

import numpy as np

from .scene import draw_buildings
from .theme import OD_CYCLE, plt


def _bounds(env_full) -> tuple[float, float, float, float]:
    """全量建筑的外接包围盒 (x0, y0, x1, y1)。"""
    xs: list[float] = []
    ys: list[float] = []
    for b in env_full.buildings:
        x0, y0, x1, y1 = b.poly.bounds
        xs += [x0, x1]
        ys += [y0, y1]
    return float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))


def plot_full_city_3od(ctx, out_dir: str) -> list[str]:
    """整范围俯视图：全城建筑底图 + 每条跨城 OD 的平滑 path。

    每个 OD 标注 ``route 距离 / path 弧长 / 相对直线绕行百分比``，
    绕行率是评估"全城绕行代价"的直观量（与 exp02_vg 结论可比）。
    """
    env_full, planned = ctx.env_full, ctx.planned
    X0, Y0, X1, Y1 = _bounds(env_full)
    area_km2 = (X1 - X0) * (Y1 - Y0) / 1e6
    n_nodes = int(len(ctx.graph.nodes)) if ctx.graph is not None else 0

    fig, ax = plt.subplots(figsize=(16, 16))

    # 建筑：真实足迹多边形填充（单次 PolyCollection）。
    # 早先用 s=1 的散点 + alpha 0.35 表示建筑，20k 栋铺在 23 km² 上渲染出来近乎全白，
    # 看不出任何城市肌理——这里改为填充 + 细描边，路线的"地面背景"才成立。
    # min_area 只滤掉 4 m² 以下的地图噪声碎块（不影响可辨的楼）。
    coll = draw_buildings(ax, env_full, face="#d9d9d9", edge="#a8a8a8",
                          alpha=0.95, lw=0.12, zorder=1, min_area=4.0)
    n_drawn = len(coll.get_paths()) if coll is not None else 0
    ax.plot([], [], color="#d9d9d9", lw=6, label=f"buildings ({n_drawn:,})")

    for k, (o, d, res) in enumerate(planned):
        col = OD_CYCLE[k % len(OD_CYCLE)]
        straight = float(np.linalg.norm(np.asarray(d, float)[:2]
                                        - np.asarray(o, float)[:2]))
        if res.feasible and res.path is not None and len(res.path.pts) >= 2:
            arr = np.asarray(res.path.pts, dtype=float)
            infl = (res.path.arc_length / straight - 1.0) * 100.0 if straight > 1e-6 else 0.0
            ax.plot(arr[:, 0], arr[:, 1], color=col, lw=1.6,
                    label=f"OD{k}  path={res.path.arc_length:.0f}m (绕行 {infl:+.1f}%)")
        else:
            reason = res.infeasible_reason.name if res.infeasible_reason else "-"
            ax.plot([], [], color=col, lw=1.6, label=f"OD{k}  不可达（{reason}）")
        ax.scatter([o[0], d[0]], [o[1], d[1]], c=col, marker="x", s=70, linewidths=1.8)

    # 留 2% 边距：贴边 OD（如沿底边的 SW→SE）否则压在坐标轴上看不见
    mx, my = 0.02 * (X1 - X0), 0.02 * (Y1 - Y0)
    ax.set_xlim(X0 - mx, X1 + mx)
    ax.set_ylim(Y0 - my, Y1 + my)
    ax.set_aspect("equal")
    ax.set_xlabel("X (m, local)")
    ax.set_ylabel("Y (m, local)")
    ax.set_title(f"整城可见性图跨城 OD（{n_nodes:,} 节点 / {area_km2:.1f} km²）")
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "fig_full_city_3od.png")
    fig.savefig(out_path, dpi=90, bbox_inches="tight")
    plt.close(fig)
    return [out_path]
