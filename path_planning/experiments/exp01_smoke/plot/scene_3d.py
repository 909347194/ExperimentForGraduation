# -*- coding: utf-8 -*-
"""三维可视化：建筑棱柱 + 禁飞区棱柱 + 三维轨迹。

两套输出，**同一份数据**：

* ``fig_scene_3d.png``  —— matplotlib mplot3d 静态图，**论文插图用**（可直接排版）；
* ``fig_scene_3d.html`` —— plotly 交互式，供人工旋转/缩放核对
  （plotly 静态导出依赖 kaleido，本环境未安装，故静态图不走 plotly）。

图元约定（与 theme.PALETTE 口径一致）：

    建筑      灰色棱柱（顶面高度=楼高，按高度着色）
    静态禁飞  深红半透明棱柱（无限高：0 → Z_CEIL 示意顶）
    动态禁飞  红色半透明棱柱（无限高；取各 zone 第一时段几何）
    path      蓝色实线（3D，Chaikin/Catmull-Rom 平滑；Dubins 未实现，属二期）
    route     灰色虚线 + 图节点标记（走廊）
    起点/终点 绿圆 / 红方
"""

from __future__ import annotations

import math
import os

import numpy as np
import plotly.graph_objects as go
from shapely.ops import triangulate

from .registry import PlotContext
from .scene import bgeom
from .theme import PALETTE, plt
from . import theme

__all__ = ["plot_scene_3d"]

#: 无限高禁飞区在图上的示意顶（真实约束为 math.inf，画图只能画到有限高）
Z_CEIL = 120.0

#: 三维棱柱建筑上限：整城 2 万+ 栋若全画，plotly Mesh3d / mplot3d 都会卡到不可用。
#: 超出则保留**面积最大的 N 栋**（地标性大楼最影响视觉；小碎块在公里级平面上看不出）。
MAX_BUILDINGS_MESH = 6000


def _prism_faces(poly, z0: float, z1: float) -> list[list[tuple[float, float, float]]]:
    """多边形棱柱的侧面四边形 + 顶面多边形（mplot3d 用，省掉三角化）。

    底面不需要画（视角在地面之上，永远看不到），少一半面片。
    """
    g = bgeom(poly)
    ring = np.asarray(g.exterior.coords, dtype=float)[:-1]
    n = len(ring)
    faces: list[list[tuple[float, float, float]]] = []
    for k in range(n):
        a, b = ring[k], ring[(k + 1) % n]
        faces.append([
            (a[0], a[1], z0), (b[0], b[1], z0),
            (b[0], b[1], z1), (a[0], a[1], z1),
        ])
    faces.append([(p[0], p[1], z1) for p in ring])
    return faces


def _plot_static_png(ctx: PlotContext, out_dir: str) -> str:
    """matplotlib 静态三维图（论文插图）。

    plotly 的静态导出需要 kaleido（本环境未装），且 kaleido v1 还要额外装 Chrome，
    对"出论文插图"这个目的过重——故静态图直接用 mplot3d 画。
    """
    from matplotlib.colors import Normalize
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    env = ctx.env
    fig = plt.figure(figsize=theme.FIG_WIDE)
    ax = fig.add_subplot(111, projection="3d")

    # ── 建筑棱柱：按楼高着色（越深越高；超量时只取最大 N 栋）──────────
    buildings = _capped_buildings(env, MAX_BUILDINGS_MESH)
    heights = [float(getattr(b, "height", 0.0)) or 2.0 for b in buildings]
    zooms = np.array(heights) if heights else np.array([1.0])
    norm = Normalize(vmin=float(zooms.min()), vmax=max(float(zooms.max()), 1.0))
    cmap = plt.get_cmap("Greys")

    faces: list[list[tuple[float, float, float]]] = []
    fcols: list = []
    for b, h in zip(buildings, heights):
        fs = _prism_faces(b.poly, 0.0, h)
        faces += fs
        # 侧面稍深、顶面稍浅，立体感更明确
        face_c = cmap(0.55 + 0.4 * float(norm(h)))
        top_c = cmap(0.35 + 0.4 * float(norm(h)))
        fcols += [face_c] * (len(fs) - 1) + [top_c]
    if faces:
        ax.add_collection3d(Poly3DCollection(
            faces, facecolors=fcols, edgecolors="#8a8a8a",
            linewidths=0.12, alpha=1.0,
        ))

    # ── 禁飞区棱柱（无限高 → 画到 Z_CEIL）───────────────────────────
    def _nofly(polys, color, alpha):
        fs: list = []
        for poly in polys:
            fs += _prism_faces(poly, 0.0, Z_CEIL)
        if fs:
            ax.add_collection3d(Poly3DCollection(
                fs, facecolors=color, edgecolors="none",
                linewidths=0.0, alpha=alpha,
            ))

    _nofly([bgeom(z.poly) for z in env.static_nofly], PALETTE["nofly_stat"], 0.22)
    _nofly([bgeom(p.poly) for z in env.dynamic_nofly for p in z.periods[:1]],
           PALETTE["nofly_dyn"], 0.16)

    # ── 轨迹：route（灰虚线 + 节点）+ path（蓝实线）─────────────────
    from .theme import OD_CYCLE

    for k, (o, d, res) in enumerate(ctx.planned):
        if not (res.feasible and res.path is not None and len(res.path.pts) >= 2):
            continue
        pts = np.asarray(res.path.pts, dtype=float)
        rp = ([np.asarray(o, float)]
              + [np.asarray(ctx.graph.nodes[i], float) for i in res.route.points]
              + [np.asarray(d, float)])
        rp = np.asarray(rp, dtype=float)
        ax.plot(rp[:, 0], rp[:, 1], rp[:, 2], "--", color=PALETTE["route"],
                lw=1.0, alpha=0.9, zorder=2)
        ax.scatter(rp[:, 0], rp[:, 1], rp[:, 2], color=PALETTE["route"],
                   s=4, depthshade=False, zorder=2)
        ax.plot(pts[:, 0], pts[:, 1], pts[:, 2], "-",
                color=OD_CYCLE[k % len(OD_CYCLE)], lw=2.0, zorder=5,
                label=f"OD{k} path")
        ax.scatter([o[0]], [o[1]], [rp[0, 2]], color=PALETTE["origin"],
                   s=40, depthshade=False, zorder=6)
        ax.scatter([d[0]], [d[1]], [rp[-1, 2]], color=PALETTE["dest"],
                   s=40, marker="s", depthshade=False, zorder=6)

    # ── 视角与轴 ──────────────────────────────────────────────────
    if env.buildings:
        bx = [b.poly.bounds for b in env.buildings]
        x0, y0 = min(v[0] for v in bx), min(v[1] for v in bx)
        x1, y1 = max(v[2] for v in bx), max(v[3] for v in bx)
    else:  # 无建筑的极端情况：退回路径范围
        pts_all = [np.asarray(res.path.pts, float)
                   for _o, _d, res in ctx.planned
                   if res.path is not None and len(res.path.pts)]
        arr = np.vstack(pts_all) if pts_all else np.zeros((1, 3))
        x0, y0, x1, y1 = (arr[:, 0].min(), arr[:, 1].min(),
                          arr[:, 0].max(), arr[:, 1].max())
    zmax = max(max(heights) if heights else Z_CEIL, Z_CEIL) * 1.05
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_zlim(0.0, zmax)
    # 高度方向适当夸张（否则几十米的楼在公里级平面上看不出立体）
    ax.set_box_aspect((x1 - x0, y1 - y0, zmax * 2.2))
    ax.view_init(elev=32, azim=-58)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_zlabel("高度 z (m)")
    ax.set_title("三维场景：建筑棱柱（可飞越）+ 禁飞区（无限高，示意到 "
                 f"{Z_CEIL:.0f} m）+ 轨迹")
    if ctx.planned:
        ax.legend(loc="upper left", fontsize=theme.FS["legend"])
    return theme.save_figure(fig, out_dir, "scene_3d")


def _capped_buildings(env, cap: int | None):
    """建筑列表（超出 cap 时保留面积最大的 N 栋）。"""
    bs = list(env.buildings)
    if cap and len(bs) > int(cap):
        bs.sort(key=lambda b: float(getattr(b, "poly", None)
                                    and b.poly.area or 0.0),
                reverse=True)
        bs = bs[: int(cap)]
    return bs


def _prism(poly, z0: float, z1: float):
    """多边形棱柱网格（顶/底盖 + 侧壁），返回 (x,y,z,i,j,k)。

    顶/底盖用 shapely ``triangulate``（C 实现，远快于纯 Python 耳切）三角化；
    侧壁仍用外环四边形。禁飞区与建筑棱柱共用本函数。
    """
    g = bgeom(poly)
    ring = np.asarray(g.exterior.coords)[:-1]  # 去重尾点
    n = len(ring)
    tris = triangulate(g)
    # 归一化：triangulate 在部分 shapely 版本返回 GeometryCollection，
    # 部分版本直接返回 list；统一压平成三角面列表。
    if hasattr(tris, "geoms"):
        tris = list(tris.geoms)
    elif not isinstance(tris, (list, tuple)):
        tris = [tris]
    tris = [t for t in tris if getattr(t, "geom_type", None) == "Polygon"]

    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    ii: list[int] = []
    jj: list[int] = []
    kk: list[int] = []

    # ── 顶/底盖：每个三角形复制一份到 z0 与 z1（顶点重复不影响 Mesh3d）────
    for tri in tris:
        c = np.asarray(tri.exterior.coords)[:-1]  # 3×2
        b = len(xs)
        for px, py in c:
            xs.append(px); ys.append(py); zs.append(z0)
        for px, py in c:
            xs.append(px); ys.append(py); zs.append(z1)
        ii += [b, b + 3]
        jj += [b + 1, b + 4]
        kk += [b + 2, b + 5]

    # ── 侧壁：外环顶点各加一份 z0 / z1，逐边成两个三角形 ───────────────
    rb = len(xs)
    for px, py in ring:
        xs.append(px); ys.append(py); zs.append(z0)
    for px, py in ring:
        xs.append(px); ys.append(py); zs.append(z1)
    for v in range(n):
        w = (v + 1) % n
        a, b, c, d = rb + v, rb + w, rb + n + w, rb + n + v
        ii += [a, c]; jj += [b, d]; kk += [c, a]

    return (np.asarray(xs), np.asarray(ys), np.asarray(zs),
            np.asarray(ii), np.asarray(jj), np.asarray(kk))


def plot_scene_3d(ctx: PlotContext, out_dir: str) -> list[str]:
    """plotly 交互式三维场景：建筑 + 禁飞区 + route/path/trajectory。"""
    env, graph = ctx.env, ctx.graph
    fig = go.Figure()

    # ── 建筑棱柱（单一 mesh，按高度着色；超量时只取最大 N 栋）─────────
    xs, ys, zs, ii, jj, kk, cols = [], [], [], [], [], [], []
    base = 0
    for b in _capped_buildings(env, MAX_BUILDINGS_MESH):
        h = float(getattr(b, "height", 0.0)) or 2.0
        x, y, z, i, j, k = _prism(b.poly, 0.0, h)
        xs.append(x); ys.append(y); zs.append(z)
        ii.append(i + base); jj.append(j + base); kk.append(k + base)
        cols.append(np.full(len(x), h))
        base += len(x)
    if xs:
        height = np.concatenate(cols)
        hmax = max(float(height.max()), 1.0)
        fig.add_trace(go.Mesh3d(
            x=np.concatenate(xs), y=np.concatenate(ys), z=np.concatenate(zs),
            i=np.concatenate(ii), j=np.concatenate(jj), k=np.concatenate(kk),
            vertexcolor=_height_colors(height),
            color="lightgray", flatshading=True,
            name="建筑", showlegend=True,
            lighting=dict(ambient=0.75, diffuse=0.35, specular=0.05),
            hoverinfo="skip",
        ))
        _ = hmax

    # ── 禁飞区棱柱（无限高 → 画到 Z_CEIL 示意）────────────────────
    def _nofly_trace(polys, color, name, alpha):
        xs2, ys2, zs2, i2, j2, k2 = [], [], [], [], [], []
        base = 0
        for poly in polys:
            x, y, z, i, j, k = _prism(poly, 0.0, Z_CEIL)
            xs2.append(x); ys2.append(y); zs2.append(z)
            i2.append(i + base); j2.append(j + base); k2.append(k + base)
            base += len(x)
        if not xs2:
            return
        fig.add_trace(go.Mesh3d(
            x=np.concatenate(xs2), y=np.concatenate(ys2), z=np.concatenate(zs2),
            i=np.concatenate(i2), j=np.concatenate(j2), k=np.concatenate(k2),
            color=color, opacity=alpha, flatshading=True,
            name=name, showlegend=True,
            lighting=dict(ambient=1.0, diffuse=0.0, specular=0.0),
            hoverinfo="skip",
        ))

    _nofly_trace([bgeom(z.poly) for z in env.static_nofly],
                 PALETTE["nofly_stat"], "静态禁飞区(无限高)", 0.40)
    dyn = [bgeom(p.poly) for z in env.dynamic_nofly for p in z.periods[:1]]
    _nofly_trace(dyn, PALETTE["nofly_dyn"], "动态禁飞区(无限高)", 0.30)

    # ── 轨迹：route（灰虚线+图节点）+ path（蓝实线 3D）────────────
    shown_route = shown_path = False
    for o, d, res in ctx.planned:
        if not (res.feasible and res.path is not None and len(res.path.pts) >= 2):
            continue
        pts = np.asarray(res.path.pts, float)
        fig.add_trace(go.Scatter3d(
            x=pts[:, 0], y=pts[:, 1], z=pts[:, 2],
            mode="lines", line=dict(color=PALETTE["path"], width=6),
            name="path（3D 平滑曲线）" if not shown_path else None,
            showlegend=not shown_path, legendgroup="path",
        ))
        shown_path = True
        rp = [np.asarray(o, float)] + [np.asarray(graph.nodes[i], float) for i in res.route.points] \
            + [np.asarray(d, float)]
        rp = np.asarray(rp, float)
        fig.add_trace(go.Scatter3d(
            x=rp[:, 0], y=rp[:, 1], z=rp[:, 2],
            mode="lines+markers",
            line=dict(color=PALETTE["route"], width=3, dash="dash"),
            marker=dict(size=2.5, color=PALETTE["route"]),
            name="route（图节点走廊）" if not shown_route else None,
            showlegend=not shown_route, legendgroup="route",
        ))
        shown_route = True
        fig.add_trace(go.Scatter3d(
            x=[o[0]], y=[o[1]], z=[rp[0, 2]],
            mode="markers", marker=dict(size=5, color=PALETTE["origin"], symbol="circle"),
            name="起点" if res is ctx.planned[0][2] else None,
            showlegend=res is ctx.planned[0][2], legendgroup="origin",
        ))
        fig.add_trace(go.Scatter3d(
            x=[d[0]], y=[d[1]], z=[rp[-1, 2]],
            mode="markers", marker=dict(size=5, color=PALETTE["dest"], symbol="square"),
            name="终点" if res is ctx.planned[0][2] else None,
            showlegend=res is ctx.planned[0][2], legendgroup="dest",
        ))

    fig.update_layout(
        title="三维场景：建筑棱柱（可飞越）+ 禁飞区棱柱（无限高，示意到 %d m）+ 轨迹" % Z_CEIL,
        scene=dict(
            aspectmode="data",
            xaxis_title="x (m)", yaxis_title="y (m)", zaxis_title="高度 z (m)",
        ),
        legend=dict(itemsizing="constant"),
        margin=dict(l=0, r=0, t=44, b=0),
    )

    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "fig_scene_3d.html")
    fig.write_html(html_path, include_plotlyjs="cdn")

    # 静态 PNG（论文插图）——先出，保证即使交互版写盘失败也有可用图
    png_path = _plot_static_png(ctx, out_dir)
    return [png_path, html_path]


def _height_colors(height: np.ndarray) -> np.ndarray:
    """按楼高把顶点着成灰阶（低=浅灰，高=深灰），供 Mesh3d.vertexcolor。"""
    hmax = max(float(height.max()), 1e-6)
    t = np.clip(height / hmax, 0.0, 1.0)
    v = (0.90 - 0.45 * t)  # 0.90→0.45 灰阶
    rgb = np.stack([v, v, v], axis=1)
    return [f"rgb({int(r * 255)},{int(g * 255)},{int(b * 255)})"
            for r, g, b in rgb]


_ = math  # 保留 import 占位（未来弧长标注用）
