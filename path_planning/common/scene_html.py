# -*- coding: utf-8 -*-
"""全域三维的**交互** HTML 后端（Plotly）：可旋转/缩放/悬停的场景图。

职责：把落盘航迹画进可交互的全域场景（地形 + 建筑 + 航迹 + 任务点），写成一个
      自包含 ``.html``（plotly.js 内嵌，离线可开）。与静态 VTK 图（``scene_3d.py``）
      共享同一套场景口径：`data_bounds_xy` 全域、``z_scale`` 只作阅读约定、
      建筑按楼顶高程渐变、长悬停画半透明球。
边界：只读结果与场景数据，不重跑规划、不做统计；不画 PNG/PDF（那是 VTK 后端的事）。
为什么两个后端并存：静态投稿图要"整片研究区 + 遮挡关系正确"，Plotly 3D 的相机
      距离被归一化、掠视角必钻楼群（见 ``scene_3d.py`` 头注）；交互浏览恰好相反——
      用户自己转视角，相机归一化无关紧要，而 Plotly 免装 VTK、HTML 即开即看。
共享位置：exp01/exp07 都要出交互 HTML，按"共享只在 ``common/``"的约定放这里；
      机号配色/图例文字仍由各实验传入（``traj_style``），本模块不背具体实验的口径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import plotly.graph_objects as go

from path_planning.methods.planning.leg_segments import sampled_positions

__all__ = ["render_interactive"]

_LAND_COLOR = "#e4e5d6"          # 与 VTK 后端一致：低饱和暖灰绿，让楼群与航迹成为主体
_BUILDING_CSCALE = [             # 与 VTK 后端 BUILDING_CMAP 一致的四档灰蓝渐变
    [0.0, "#dfe4eb"], [0.33, "#b4c0d0"], [0.66, "#8796ac"], [1.0, "#54637b"],
]
_HOVER_RADIUS_M = 95.0           # 长悬停半透明球半径（米），与静态图口径一致
_MAX_LINE_POINTS = 4000          # 单条航迹线上限（1 m 采样超了就均匀跳点）


def render_interactive(
    trajectories: Mapping[str, Any],
    tasks: Mapping[int, Sequence[float]],
    scenario,
    *,
    out_html: Path,
    title: str,
    subtitle: str = "",
    stage_label: str = "周期",
    z_scale: float = 3.0,
    min_building_m: float = 0.0,
    terrain_step_m: float = 40.0,
    pad_m: float = 120.0,
    show_nests: bool = True,
    nest_color: str = "#c8b02e",
    task_color: str = "#b5651d",
    nest_label: str = "巢",
    traj_style: Callable[[int, int, bool, Any], tuple[str, str, bool]] | None = None,
    azimuth: float = -55.0,
    elevation: float = 33.0,
    hover_threshold_s: float = 60.0,
) -> None:
    """写一个自包含交互 HTML。

    ``trajectories`` 的键形如 ``"段:机号"``（第几周期/第几轮 : 机号），与静态后端一致。
    ``traj_style(stage, uid, single_uav, traj) -> (颜色, 图例文字, 是否虚线)``：配色与
    命名由调用方（各实验）给；估算时间（降级交付）由调用方置虚线；缺省用通用机号色。
    """
    x0, y0, x1, y1 = (float(v) for v in scenario.data_bounds_xy)
    fig = go.Figure()

    _add_terrain(fig, scenario, x0 - pad_m, y0 - pad_m, x1 + pad_m, y1 + pad_m,
                 terrain_step_m, z_scale)
    verts, tri, tops = _buildings_arrays(scenario, min_building_m, z_scale)
    if verts is not None:
        fig.add_trace(go.Mesh3d(
            x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
            i=tri[:, 0], j=tri[:, 1], k=tri[:, 2],
            intensity=tops, cmin=float(min_building_m), cmax=120.0,
            colorscale=_BUILDING_CSCALE, showscale=False,
            flatshading=True, name="建筑", hoverinfo="skip",
            lighting=dict(ambient=0.55, diffuse=0.75, specular=0.05),
        ))

    single_uav = len({int(k.split(":")[1]) for k in trajectories}) == 1
    for key in sorted(trajectories, key=lambda k: (int(k.split(":")[1]), int(k.split(":")[0]))):
        stage, uid = (int(v) for v in key.split(":"))
        traj = trajectories[key]
        pts = np.asarray(sampled_positions(traj, 1.0), dtype=float)
        if len(pts) > _MAX_LINE_POINTS:
            idx = np.linspace(0, len(pts) - 1, _MAX_LINE_POINTS).astype(int)
            pts = pts[idx]
        pts[:, 2] *= float(z_scale)
        color, label, dashed = _style(traj_style, stage, uid, single_uav, traj, stage_label)
        line = go.scatter3d.Line(color=color, width=6)
        if dashed:  # 估算时间（降级交付）：与静态图的"点线"一致
            line.dash = "dash"
        fig.add_trace(go.Scatter3d(
            x=pts[:, 0], y=pts[:, 1], z=pts[:, 2], mode="lines",
            line=line, name=label,
            hovertext=label, hoverinfo="text",
        ))
        # 长悬停（>阈值）是"必撞锚点"所在：航迹只是一条线，画个半透明球才看得见
        for seg in traj.segments:
            if seg.is_hover and (seg.t1 - seg.t0) > float(hover_threshold_s):
                _add_hover_sphere(fig, seg.p0, float(z_scale), color)
                break

    _add_points(fig, scenario, tasks, show_nests, nest_color, task_color,
                nest_label, z_scale)

    eye = 2.1 * np.array([np.cos(np.radians(elevation)) * np.cos(np.radians(azimuth)),
                          np.cos(np.radians(elevation)) * np.sin(np.radians(azimuth)),
                          np.sin(np.radians(elevation))])
    head = title if not subtitle else f"{title}<br><sup>{subtitle}</sup>"
    fig.update_layout(
        title=dict(text=head, font=dict(size=16), x=0.01, xanchor="left"),
        template="plotly_white",
        height=900,
        legend=dict(orientation="h", yanchor="bottom", y=0.01,
                    xanchor="left", x=0.01, font=dict(size=12)),
        margin=dict(l=10, r=10, t=60, b=10),
        scene=dict(
            aspectmode="data",
            camera=dict(eye=dict(x=float(eye[0]), y=float(eye[1]), z=float(eye[2])),
                        up=dict(x=0, y=0, z=1)),
            xaxis=dict(visible=False), yaxis=dict(visible=False), zaxis=dict(visible=False),
        ),
    )
    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(out_html, include_plotlyjs=True,
                   config=dict(displaylogo=False, responsive=True))


# ---------------------------------------------------------------------------


def _style(traj_style, stage: int, uid: int, single_uav: bool, traj, stage_label: str):
    if traj_style is not None:
        return traj_style(stage, uid, single_uav, traj)
    color = _DEFAULT_BASE.get(uid, "#555555")
    if single_uav:
        color = _DEFAULT_STAGE.get(stage, color)
    label = (f"无人机 {uid}·第 {stage} {stage_label}"
             f"　{traj.duration:.1f} s ／ {traj.path_len_m:.0f} m")
    dashed = traj.time_source.value == "estimate"
    if dashed:
        label += "（估算时间，虚线）"
    return color, label, dashed


_DEFAULT_BASE = {1: "#c1282d", 2: "#2166ac", 3: "#1a7f5a", 4: "#d1691a",
                 5: "#c1282d", 6: "#2166ac", 7: "#7b3294"}
_DEFAULT_STAGE = {1: "#c1282d", 2: "#2166ac"}


def _add_terrain(fig, scenario, x0: float, y0: float, x1: float, y1: float,
                 step_m: float, z_scale: float) -> None:
    gx = np.arange(x0, x1 + step_m, step_m)
    gy = np.arange(y0, y1 + step_m, step_m)
    mx, my = np.meshgrid(gx, gy, indexing="xy")
    z = np.asarray(scenario.ground_height(mx.ravel(), my.ravel()), dtype=float).reshape(mx.shape)
    z = _smooth(z)
    if z_scale != 1.0:
        z = z * float(z_scale)
    fig.add_trace(go.Surface(
        x=gx, y=gy, z=z, colorscale=[[0.0, _LAND_COLOR], [1.0, _LAND_COLOR]],
        showscale=False, name="地形", hoverinfo="skip",
        lighting=dict(ambient=0.75, diffuse=0.55, specular=0.0),
    ))


def _smooth(z: np.ndarray, passes: int = 2) -> np.ndarray:
    """3×3 均值滤波（边界复制），与静态后端同一口径（40 m 采样 DEM 有高频噪声）。"""
    out = np.asarray(z, dtype=float).copy()
    for _ in range(passes):
        p = np.pad(out, 1, mode="edge")
        out = (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
               + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
               + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9.0
    return out


def _buildings_arrays(scenario, min_height_m: float, z_scale: float):
    """建筑挤出成三角形网格：返回 (顶点, 三角索引, 逐顶点楼顶高程)；无建筑返回 (None,)*3。"""
    if scenario.buildings is None:
        return None, None, None
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    vtop: list[float] = []
    for poly, h in zip(scenario.buildings.polygons, scenario.buildings.heights):
        if float(h) < float(min_height_m):
            continue
        ring = np.asarray(poly.exterior.coords, dtype=float)[:-1]
        if ring.shape[0] < 3:
            continue
        base = float(scenario.ground_height_at(float(poly.centroid.x), float(poly.centroid.y)))
        top = base + float(h)
        n = ring.shape[0]
        b = len(verts)
        verts.extend((float(x), float(y), base) for x, y in ring)
        verts.extend((float(x), float(y), top) for x, y in ring)
        for a in range(n):  # 侧墙：每条边两个三角形
            nxt = (a + 1) % n
            tris.append((b + a, b + nxt, b + n + nxt))
            tris.append((b + a, b + n + nxt, b + n + a))
            vtop.extend((top, top, top, top))
        for i0, i1, i2 in _ear_clip(ring):  # 屋顶：耳切三角化（凹多边形也要补面）
            tris.append((b + n + i0, b + n + i1, b + n + i2))
            vtop.extend((top, top, top))
    if not tris:
        return None, None, None
    arr = np.asarray(verts, dtype=float)
    if z_scale != 1.0:
        arr[:, 2] *= float(z_scale)
    # 坐标量化到厘米级：HTML 体积主要在建筑网格，三位小数对 5 km 研究区无视觉差别
    arr = np.round(arr, 3)
    return arr, np.asarray(tris, dtype=np.int32), np.asarray(vtop, dtype=float)


def _ear_clip(ring: np.ndarray) -> list[tuple[int, int, int]]:
    """简单耳切：把 (n,2) 闭合多边形（首点不重复）三角化，返回索引三元组。"""
    n = len(ring)
    if n < 3:
        return []
    idx = list(range(n))
    area2 = 0.0
    for i in range(n):
        j = (i + 1) % n
        area2 += ring[i, 0] * ring[j, 1] - ring[j, 0] * ring[i, 1]
    if area2 < 0.0:  # 顺时针多边形反转后按 CCW 处理
        idx = idx[::-1]
    tris: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n:
        guard += 1
        clipped = False
        for m in range(len(idx)):
            i0, i1, i2 = idx[m - 1], idx[m], idx[(m + 1) % len(idx)]
            a, b, c = ring[i0], ring[i1], ring[i2]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 0.0:  # 非凸角，不是耳
                continue
            ok = True
            for k in idx:
                if k in (i0, i1, i2):
                    continue
                if _point_in_tri(ring[k], a, b, c):
                    ok = False
                    break
            if ok:
                tris.append((i0, i1, i2))
                idx.pop(m)
                clipped = True
                break
        if not clipped:  # 数值退化（共线等）：扇形兜底，避免死循环
            break
    for m in range(1, len(idx) - 1):
        tris.append((idx[0], idx[m], idx[m + 1]))
    return tris


def _point_in_tri(p, a, b, c) -> bool:
    d1 = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    d2 = (c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0])
    d3 = (a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0])
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)  # 含边界点（压在耳边上不阻断切割）


def _add_hover_sphere(fig, p, z_scale: float, color: str) -> None:
    """参数化 UV 球：半透明，标出悬停"体量"（与静态图的线框球同一语义）。"""
    r = _HOVER_RADIUS_M
    u = np.linspace(0.0, 2.0 * np.pi, 24)
    v = np.linspace(0.0, np.pi, 12)
    cx, cy = float(p[0]), float(p[1])
    cz = float(p[2]) * float(z_scale)
    x = cx + r * np.outer(np.cos(u), np.sin(v))
    y = cy + r * np.outer(np.sin(u), np.sin(v))
    z = cz + r * np.outer(np.ones_like(u), np.cos(v))
    fig.add_trace(go.Surface(
        x=x, y=y, z=z, opacity=0.28, showscale=False, name="悬停",
        colorscale=[[0.0, color], [1.0, color]], hoverinfo="skip",
        lighting=dict(ambient=1.0, diffuse=0.0, specular=0.0),
    ))


def _add_points(fig, scenario, tasks, show_nests: bool, nest_color: str,
                task_color: str, nest_label: str, z_scale: float) -> None:
    if show_nests and getattr(scenario, "nests", None):
        pts = np.asarray([(float(n.position[0]), float(n.position[1]),
                           float(n.position[2]) * z_scale) for n in scenario.nests])
        fig.add_trace(go.Scatter3d(
            x=pts[:, 0], y=pts[:, 1], z=pts[:, 2], mode="markers+text",
            marker=dict(size=9, color=nest_color, symbol="diamond"),
            text=[f"{nest_label}{int(n.id)}" for n in scenario.nests],
            textposition="top center", name=nest_label,
        ))
    if tasks:
        pts = np.asarray([(float(tasks[t][0]), float(tasks[t][1]),
                           float(tasks[t][2]) * z_scale) for t in sorted(tasks)])
        fig.add_trace(go.Scatter3d(
            x=pts[:, 0], y=pts[:, 1], z=pts[:, 2], mode="markers+text",
            marker=dict(size=6, color=task_color, symbol="diamond"),
            text=[str(t) for t in sorted(tasks)],
            textposition="top center", name="任务点",
        ))
