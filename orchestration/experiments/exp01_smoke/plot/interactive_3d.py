# -*- coding: utf-8 -*-
"""可选：把航迹导成单文件交互 HTML（Plotly），供答辩演示旋转/缩放。

与 PP01 的静态图同源：只读 ``trajectories.json`` / ``tasks.json`` / ``run_meta.json``
和实验侧场景（建筑），不重跑规划。输出 ``figures/tracks_3d.html``，plotly.js
内嵌，一个文件离线可开。

图上右上角有周期下拉：默认全部叠加，可切到单个周期。切到单周期时，任务点的
"已到达/未到达/未排程"也按该周期重算（与 PP01 每周期一个子图的口径一致）。

建筑只画**涉及的那批**：航迹、任务点、启用机巢周围 ``BUILDING_BUFFER_M`` 米内的
建筑，用真高度挤出、半透明显示；全城两万栋全画既拖慢浏览器，也会把 20–90 m
的航迹淹没。筛选用空间索引，口径写在图上。

跑法::

    python -m orchestration.experiments.exp01_smoke.plot.interactive_3d
    python -m orchestration.experiments.exp01_smoke.plot.interactive_3d --building-buffer-m 150
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from .base import ledger as iv
from .base.scene import Ctx, prepare
from .theme import FAIL, UAV_COLOR, add_common, style

NAME = "tracks_3d"
#: “涉及建筑”的筛选半径（米）：覆盖航线走廊与任务作业点邻域
BUILDING_BUFFER_M = 80.0
#: 航迹抽稀步长（米），只用于建筑筛选查询，不改变出图航迹本身
QUERY_STEP_M = 25.0
BUILDING_OPACITY = 0.42


def build(ctx: Ctx, *, building_buffer_m: float = BUILDING_BUFFER_M) -> list[Path]:
    import plotly.graph_objects as go

    cycles = [int(c["cycle"]) for c in ctx.trace["cycles"]]
    data: list[go.Scatter3d] = []
    #: 每条图迹归属的周期；None = 建筑/机巢等与周期无关的公共层
    trace_cycles: list[int | None] = []

    # 建筑先入列：Plotly 3D 里后画的航迹压在半透明体块之上，可读性更好
    n_buildings = _add_nearby_buildings(data, ctx, float(building_buffer_m))
    trace_cycles.append(None)

    for cycle in cycles:
        for uid_str, tr in sorted(ctx.tracks.get(str(cycle), {}).items()):
            uid = int(uid_str)
            data.append(go.Scatter3d(
                x=tr["x"], y=tr["y"], z=tr["z"], mode="lines",
                line=dict(color=UAV_COLOR.get(uid, "#333333"), width=4),
                name=f"周期{cycle}·机{uid}", legendgroup=f"u{uid}",
                showlegend=(cycle == cycles[0]),
                hovertemplate=(f"周期{cycle} 机{uid}<br>x=%{{x:.0f}} y=%{{y:.0f}} "
                               "z=%{z:.1f} m<extra></extra>"),
            ))
            trace_cycles.append(cycle)

    # 任务点按周期分组：单周期视图里状态与 PP01 逐周期子图一致
    inv = iv.build(ctx)   # 同一份台账 → 同一套任务号 T#
    task_keys = ("任务点·已到达", "任务点·未到达（航次失败）", "任务点·未排程")
    shown_task_legends: set[str] = set()
    for cycle in cycles:
        plan = next(c for c in ctx.trace["cycles"] if int(c["cycle"]) == cycle)
        flown = {
            int(tid)
            for ticket in plan["tickets"].values() if ticket["ok"]
            for tid in ticket["task_ids"]
        }
        assigned = {int(t) for tids in plan["assignments"].values() for t in tids}
        groups = {k: ([], [], [], []) for k in task_keys}
        for t in ctx.tasks:
            if int(t.get("cycle", 0)) != cycle:
                continue
            tid = int(t["id"])
            key = ("任务点·已到达" if tid in flown else
                   "任务点·未到达（航次失败）" if tid in assigned else "任务点·未排程")
            g = groups[key]
            g[0].append(t["x"]); g[1].append(t["y"]); g[2].append(t["z"])
            g[3].append(tid)  # 第 4 位暂借放 id，下面构造 text 时取出
        for key in task_keys:
            xs, ys, zs, extra = groups[key]
            if not xs:
                continue
            ids = list(extra)
            data.append(go.Scatter3d(
                x=xs, y=ys, z=zs, mode="markers", name=key,
                legendgroup=f"task_{key}", showlegend=(key not in shown_task_legends),
                marker=dict(size=4, color=_task_color(key),
                            line=dict(width=1, color="white")),
                hovertemplate="%{text}<br>x=%{x:.0f} y=%{y:.0f} z=%{z:.1f} m<extra></extra>",
                text=[inv.tag(i) for i in ids],
            ))
            shown_task_legends.add(key)
            trace_cycles.append(cycle)

    sites = [s for s in ctx.meta["context"]["all_nest_sites"] if s["active"]]
    data.append(go.Scatter3d(
        x=[s["x"] for s in sites], y=[s["y"] for s in sites], z=[s["z"] for s in sites],
        mode="markers", name="启用机巢",
        marker=dict(size=8, color="#111111", symbol="diamond"),
    ))
    trace_cycles.append(None)

    subtitle = (f"航迹/任务点/机巢周围 {building_buffer_m:g} m 内的建筑 "
                f"{n_buildings} 栋（真高度、半透明）；z 为真实高程")
    fig = go.Figure(data=data)
    fig.update_layout(
        title=_title(cycles, subtitle),
        template="plotly_white",
        scene=dict(
            xaxis_title="东向 x（m）", yaxis_title="北向 y（m）", zaxis_title="高程 z（m）",
            aspectmode="data",
            camera=dict(eye=dict(x=1.3, y=-1.3, z=0.9)),
        ),
        legend=dict(x=0.01, y=0.99),
        margin=dict(l=0, r=0, t=64, b=0),
        height=780,
        updatemenus=[dict(
            type="dropdown", direction="down", showactive=True,
            x=0.99, y=0.99, xanchor="right", yanchor="top",
            bgcolor="#FFFFFF", bordercolor="#B4B4B4", borderwidth=1,
            buttons=[
                dict(
                    label="全部周期",
                    method="update",
                    args=[{"visible": [True] * len(data)},
                          {"title.text": _title(cycles, subtitle)}],
                ),
                *[
                    dict(
                        label=f"周期 {c}",
                        method="update",
                        args=[{"visible": [tc is None or tc == c for tc in trace_cycles]},
                              {"title.text": _title([c], subtitle)}],
                    )
                    for c in cycles
                ],
            ],
        )],
    )
    out = ctx.fig_dir / f"{NAME}.html"
    fig.write_html(out, include_plotlyjs=True, full_html=True,
                   config=dict(displaylogo=False, responsive=True))
    return [out]


def _task_color(key: str) -> str:
    return {"任务点·已到达": "#2E8B57",
            "任务点·未到达（航次失败）": FAIL,
            "任务点·未排程": "#8C8C8C"}[key]


def _title(cycles: list[int], subtitle: str) -> str:
    scope = (f"周期 {cycles[0]}–{cycles[-1]}" if len(cycles) > 1
             else f"周期 {cycles[0]}")
    return f"exp01 三维航迹（{scope}，可旋转/缩放）<br><sup>{subtitle}</sup>"


def _add_nearby_buildings(data: list, ctx: Ctx, buffer_m: float) -> int:
    """把航线邻域内的建筑挤成半透明体块；返回实际画出的栋数。"""
    import numpy as np
    import plotly.graph_objects as go
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    # 复用 path_planning/common 里已在 exp01 验证过的多边形挤出 + 耳切三角化；
    # 只喂给它筛选后的子集，避免各实验各写一份几何代码。
    from path_planning.common.scene_html import _buildings_arrays  # noqa: PLC2701

    scenario = ctx.scenario
    if scenario is None or scenario.buildings is None or buffer_m <= 0.0:
        return 0
    layer = scenario.buildings
    polys = list(layer.polygons)
    heights = np.asarray(layer.heights, dtype=float)
    if not polys:
        return 0

    # 查询点：航迹按 25 m 抽稀 + 任务点 + 启用巢。只影响筛选，不改变出图数据。
    pts: list[Point] = []
    for by in ctx.tracks.values():
        for tr in by.values():
            xyz = np.column_stack((tr["x"], tr["y"], tr["z"]))
            if len(xyz) < 2:
                if len(xyz) == 1:
                    pts.append(Point(float(xyz[0, 0]), float(xyz[0, 1])))
                continue
            run = np.concatenate(([0.0], np.cumsum(
                np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1))))
            grid = np.arange(0.0, float(run[-1]) + QUERY_STEP_M, QUERY_STEP_M)
            idx = np.unique(np.searchsorted(run, grid, side="left")).clip(max=len(xyz) - 1)
            pts.extend(Point(float(x), float(y)) for x, y in xyz[idx, :2])
    pts.extend(Point(float(t["x"]), float(t["y"])) for t in ctx.tasks)
    pts.extend(Point(float(s["x"]), float(s["y"]))
               for s in ctx.meta["context"]["all_nest_sites"] if s["active"])
    if not pts:
        return 0

    tree = STRtree(polys)
    hit: set[int] = set()
    queries = [p.buffer(float(buffer_m)) for p in pts]
    for arr in tree.query(queries):
        hit.update(int(i) for i in np.atleast_1d(arr))
    if not hit:
        return 0

    indices = sorted(hit)
    subset = SimpleNamespace(
        buildings=SimpleNamespace(
            polygons=[polys[i] for i in indices],
            heights=[float(heights[i]) for i in indices],
        ),
        ground_height_at=scenario.ground_height_at,
    )
    verts, tri, tops = _buildings_arrays(subset, 0.0, 1.0)
    if verts is None:
        return 0
    data.append(go.Mesh3d(
        x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
        i=tri[:, 0], j=tri[:, 1], k=tri[:, 2],
        intensity=tops,
        cmin=float(np.min(tops)), cmax=float(np.max(tops)),
        colorscale=[[0.0, "#dfe4eb"], [0.33, "#b4c0d0"],
                    [0.66, "#8796ac"], [1.0, "#54637b"]],
        showscale=False, flatshading=True,
        opacity=BUILDING_OPACITY, name="建筑（航线邻域）", hoverinfo="skip",
        lighting=dict(ambient=0.55, diffuse=0.75, specular=0.05),
    ))
    return len(indices)


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="exp01 交互三维航迹（HTML，含航线邻域建筑）")
    add_common(parser)
    parser.add_argument("--building-buffer-m", type=float, default=BUILDING_BUFFER_M,
                        help="建筑筛选半径（米），默认 80；传 0 关闭建筑")
    args = parser.parse_args()
    if args.building_buffer_m < 0.0:
        parser.error("--building-buffer-m 不能为负")
    style()
    ctx = prepare(args.config, args.run, need_scene=True)
    for p in build(ctx, building_buffer_m=args.building_buffer_m):
        print(f"[exp01] 已出 {p.relative_to(ctx.run_dir)}")
    sys.exit(0)
