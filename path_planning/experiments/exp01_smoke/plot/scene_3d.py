# -*- coding: utf-8 -*-
"""三维可视化（plotly 交互式）：建筑棱柱 + 禁飞区棱柱 + 三维轨迹。

与 matplotlib 五张论文图互补：本图供**交互查看**（可旋转/缩放/点选图例），
输出 ``fig_scene_3d.html``（无 kaleido 依赖，不出 PNG）。

图元约定（与 theme.PALETTE 口径一致）：

    建筑      灰色棱柱（顶面高度=楼高，按高度着色）
    静态禁飞  深红半透明棱柱（无限高：0 → Z_CEIL 示意顶）
    动态禁飞  红色半透明棱柱（无限高；取各 zone 第一时段几何）
    path      蓝色实线（3D，Dubins 平滑曲线）
    route     灰色虚线 + 控制点标记（走廊）
    起点/终点 绿圆 / 红方
"""

from __future__ import annotations

import math
import os

import numpy as np
import plotly.graph_objects as go

from .registry import PlotContext
from .scene import bgeom
from .theme import PALETTE

__all__ = ["plot_scene_3d"]

#: 无限高禁飞区在图上的示意顶（真实约束为 math.inf，画图只能画到有限高）
Z_CEIL = 120.0


def _ear_clip(xy: np.ndarray) -> list[tuple[int, int, int]]:
    """简单多边形耳切三角化（外环；建筑/禁飞区足迹均为简单多边形）。"""
    n = len(xy)
    if n < 3:
        return []
    idx = list(range(n))
    tris: list[tuple[int, int, int]] = []
    area2 = float(
        np.sum(xy[:, 0] * np.roll(xy[:, 1], -1)
               - np.roll(xy[:, 0], -1) * xy[:, 1])
    )
    if area2 < 0:  # 逆时针化
        idx = idx[::-1]
    guard = 0
    while len(idx) > 3 and guard < 10 * n:
        guard += 1
        n_i = len(idx)
        cut = False
        for k in range(n_i):
            i0, i1, i2 = idx[(k - 1) % n_i], idx[k], idx[(k + 1) % n_i]
            a, b, c = xy[i0], xy[i1], xy[i2]
            cr = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cr <= 1e-12:  # 非凸角，非耳
                continue
            # 耳判定：其余顶点不得落在三角形内
            rest = [idx[j] for j in range(n_i) if j not in ((k - 1) % n_i, k, (k + 1) % n_i)]
            if not rest:
                tris.append((i0, i1, i2))
                idx.remove(i1)
                cut = True
                break
            m = np.array([xy[r] for r in rest])
            d0 = (b[0] - a[0]) * (m[:, 1] - a[1]) - (b[1] - a[1]) * (m[:, 0] - a[0])
            d1 = (c[0] - b[0]) * (m[:, 1] - b[1]) - (c[1] - b[1]) * (m[:, 0] - b[0])
            d2 = (a[0] - c[0]) * (m[:, 1] - c[1]) - (a[1] - c[1]) * (m[:, 0] - c[0])
            if np.any((d0 >= 0) & (d1 >= 0) & (d2 >= 0)):
                continue  # 有点在耳内，跳过
            tris.append((i0, i1, i2))
            idx.remove(i1)
            cut = True
            break
        if not cut:  # 数值兜底：剪不动就放开凸性要求（取最大角）
            break
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _prism(poly, z0: float, z1: float):
    """多边形棱柱网格（顶/底盖 + 侧壁），返回 (x,y,z,i,j,k)。"""
    g = bgeom(poly)
    ring = np.asarray(g.exterior.coords)[:-1]  # 去重尾点
    n = len(ring)
    tris = _ear_clip(ring)
    x = np.concatenate([ring[:, 0], ring[:, 0]])
    y = np.concatenate([ring[:, 1], ring[:, 1]])
    z = np.concatenate([np.full(n, z0), np.full(n, z1)])
    i, j, k = [], [], []

    def t(a, b, c):
        i.append(a); j.append(b); k.append(c)

    for a, b, c in tris:          # 顶盖（朝上）
        t(n + a, n + c, n + b)
        t(a, b, c)                # 底盖（朝下）
    for v in range(n):            # 侧壁
        w = (v + 1) % n
        t(v, w, n + w)
        t(v, n + w, n + v)
    return x, y, z, np.array(i), np.array(j), np.array(k)


def plot_scene_3d(ctx: PlotContext, out_dir: str) -> list[str]:
    """plotly 交互式三维场景：建筑 + 禁飞区 + route/path/trajectory。"""
    env, net = ctx.env, ctx.net
    fig = go.Figure()

    # ── 建筑棱柱（单一 mesh，按高度着色）───────────────────────────
    xs, ys, zs, ii, jj, kk, cols = [], [], [], [], [], [], []
    base = 0
    for b in env.buildings:
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

    # ── 轨迹：route（灰虚线+控制点）+ path（蓝实线 3D）────────────
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
        rp = [np.asarray(o, float)] + [net.points[i] for i in res.route.points] \
            + [np.asarray(d, float)]
        rp = np.asarray(rp, float)
        fig.add_trace(go.Scatter3d(
            x=rp[:, 0], y=rp[:, 1], z=rp[:, 2],
            mode="lines+markers",
            line=dict(color=PALETTE["route"], width=3, dash="dash"),
            marker=dict(size=2.5, color=PALETTE["route"]),
            name="route（控制点走廊）" if not shown_route else None,
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
    path = os.path.join(out_dir, "fig_scene_3d.html")
    fig.write_html(path, include_plotlyjs="cdn")
    return [path]


def _height_colors(height: np.ndarray) -> np.ndarray:
    """按楼高把顶点着成灰阶（低=浅灰，高=深灰），供 Mesh3d.vertexcolor。"""
    hmax = max(float(height.max()), 1e-6)
    t = np.clip(height / hmax, 0.0, 1.0)
    v = (0.90 - 0.45 * t)  # 0.90→0.45 灰阶
    rgb = np.stack([v, v, v], axis=1)
    return [f"rgb({int(r * 255)},{int(g * 255)},{int(b * 255)})"
            for r, g, b in rgb]


_ = math  # 保留 import 占位（未来弧长标注用）
