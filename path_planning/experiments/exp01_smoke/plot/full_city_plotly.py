# -*- coding: utf-8 -*-
"""fig_full_city_plotly —— 整城 plotly 交互地图（覆盖全 scope，不裁剪）。

与 ``fig_full_city_3od.png``（matplotlib 静态）配套的**交互版**：同一份数据，可缩放/平移/hover。

关键难点：20,655 栋建筑若逐栋画成 plotly 矢量多边形，浏览器会卡死、HTML 也会爆到几百 MB。
故建筑底图采用**栅格化**方案——一次性用 matplotlib Agg 把全城建筑足迹渲染成一张 PNG，
再以 ``layout.images`` 作为地理配准底图铺进 plotly（用 data 坐标系的 extent 精确对齐）；
OD 轨迹 / 图节点 / 禁飞区则用矢量 ``Scatter`` 叠加（保持可缩放、可 hover）。
这是"20k 栋铺在 23 km² 上"既清晰又不卡的关键。

数据来源：``ctx.env_full``（全量建筑背景）+ ``ctx.planned``（(origin, dest, PlanResult)）
+ ``ctx.graph.nodes``（节点数标注）+ ``ctx.env``（禁飞区）。

不在 ``plot/registry.FIGURES`` 里：它是 ``run.py --full-city`` 专属（粗网格示意性全城图），
位置见 run.py 的 ``--full-city`` 分支。
"""

from __future__ import annotations

import base64
import io
import os

import numpy as np
import plotly.graph_objects as go
from PIL import Image
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas

from .scene import bgeom, draw_buildings
from .theme import OD_CYCLE, PALETTE


def _bounds(env_full) -> tuple[float, float, float, float]:
    """全量建筑的外接包围盒 (x0, y0, x1, y1)。"""
    xs: list[float] = []
    ys: list[float] = []
    for b in env_full.buildings:
        x0, y0, x1, y1 = b.poly.bounds
        xs += [x0, x1]
        ys += [y0, y1]
    return float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))


def _render_building_raster(
    env_full,
    x0: float, y0: float, x1: float, y1: float,
    target_px: int = 3000, dpi: int = 100, min_area: float = 4.0,
) -> bytes:
    """把全城建筑足迹渲染成一张 PNG（bytes），数据范围严格对齐 [x0,x1]×[y0,y1]。

    用 matplotlib Figure + Agg 后端直接出图，不依赖 pyplot 全局状态；
    ``add_axes([0,0,1,1])`` + 显式 ``set_xlim/ylim`` + ``autoscale_off`` 保证像素与数据严格 1:1 映射，
    这样后面 plotly ``layout.images`` 用 extent 铺图时才不会错位。

    注意：直接 ``fig.savefig('png')`` 产出的 RGBA PNG 在某些 PIL 版本下解码会报
    "broken data stream"，而浏览器其实能正常渲染。为避免任何兼容性隐患（也为了可校验），
    这里改从 canvas 像素缓冲 ``buffer_rgba()`` 取 RGBA 数组，用 PIL 转成纯 RGB 白底 PNG 再回写——
    彻底绕开 matplotlib 的 PNG 编码器与 PIL 的 PNG 解码器。
    """
    ex, ey = x1 - x0, y1 - y0
    px_per_m = target_px / max(ex, ey)
    fx, fy = ex * px_per_m / dpi, ey * px_per_m / dpi
    fig = Figure(figsize=(fx, fy), dpi=dpi)
    fig.patch.set_facecolor("white")  # 白底，避免透明区在 plotly 里透出底色
    canvas = FigureCanvas(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_autoscale_on(False)  # 关键：禁止 autoscale 把范围撑大导致配准错位
    ax.set_facecolor("white")
    ax.axis("off")
    # 白底；建筑填浅灰 + 细描边，密集区也分得清单栋轮廓（与 full_city_3od.png 同源口径）
    draw_buildings(
        ax, env_full,
        face="#dcdcdc", edge="#9a9a9a",
        alpha=1.0, lw=0.12, min_area=min_area,
    )
    canvas.draw()
    # 直接从渲染缓冲取像素（top→bottom 与 plotly image 顶对齐一致）
    rgba = np.asarray(canvas.buffer_rgba())  # (H, W, 4)
    img = Image.fromarray(rgba, mode="RGBA").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="png")
    return buf.getvalue()


def _nofly_traces(env, first_only: bool = True):
    """禁飞区矢量轮廓（plotly Scatter，闭合 + 淡填充）。"""
    traces = []
    dyn = env.dynamic_nofly if env is not None else []
    stat = env.static_nofly if env is not None else []
    for z in dyn:
        periods = z.periods[:1] if first_only else z.periods
        for p in periods:
            g = bgeom(p.poly)
            x, y = g.exterior.xy
            traces.append(go.Scatter(
                x=list(x), y=list(y), mode="lines",
                fill="toself", fillcolor="rgba(255,0,0,0.16)",
                line=dict(color=PALETTE["nofly_dyn"], dash="dash", width=1.6),
                name="动态禁飞区(无限高)", hoverinfo="skip",
                legendgroup="nofly_dyn", showlegend=not traces,
            ))
    for z in stat:
        g = bgeom(z.poly)
        x, y = g.exterior.xy
        traces.append(go.Scatter(
            x=list(x), y=list(y), mode="lines",
            fill="toself", fillcolor="rgba(179,0,0,0.20)",
            line=dict(color=PALETTE["nofly_stat"], width=1.6),
            name="静态禁飞区(无限高)", hoverinfo="skip",
            legendgroup="nofly_stat", showlegend=True,
        ))
    return traces


def plot_full_city_plotly(ctx, out_dir: str) -> list[str]:
    """整范围 plotly 交互地图：全城建筑底图 + 每条跨城 OD 的平滑 path。

    每个 OD 标注 ``route 距离 / path 弧长 / 相对直线绕行百分比``，
    绕行率是评估"全城绕行代价"的直观量（与 exp02_vg 结论可比）。
    """
    env_full, env, planned = ctx.env_full, ctx.env, ctx.planned
    X0, Y0, X1, Y1 = _bounds(env_full)
    area_km2 = (X1 - X0) * (Y1 - Y0) / 1e6
    n_build = len(env_full.buildings)
    n_nodes = int(len(ctx.graph.nodes)) if ctx.graph is not None else 0

    # ── 建筑底图（栅格化，避免 20k 矢量多边形卡死浏览器）──────────────
    png = _render_building_raster(env_full, X0, Y0, X1, Y1)
    img_b64 = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    fig = go.Figure()

    # ── 建筑图例占位（不可见 trace，仅用于图例一项）──────────────────
    fig.add_trace(go.Scatter(
        x=[None], y=[None], mode="markers",
        marker=dict(color="#dcdcdc", size=10, line=dict(color="#9a9a9a", width=1)),
        name=f"建筑 ({n_build:,})", hoverinfo="skip",
    ))

    # ── 禁飞区 ──────────────────────────────────────────────────────
    for tr in _nofly_traces(env):
        fig.add_trace(tr)

    # ── 图节点（淡蓝小点，可勾选隐藏）───────────────────────────────
    if ctx.graph is not None and n_nodes:
        nodes = np.asarray(ctx.graph.nodes, dtype=float)
        fig.add_trace(go.Scatter(
            x=nodes[:, 0], y=nodes[:, 1], mode="markers",
            marker=dict(color="rgba(43,123,186,0.22)", size=2.0),
            name=f"图节点 ({n_nodes:,})", hoverinfo="skip",
            legendgroup="nodes",
        ))

    # ── OD 轨迹 ────────────────────────────────────────────────────
    for k, (o, d, res) in enumerate(planned):
        col = OD_CYCLE[k % len(OD_CYCLE)]
        straight = float(np.linalg.norm(np.asarray(d, float)[:2]
                                        - np.asarray(o, float)[:2]))
        if res.feasible and res.path is not None and len(res.path.pts) >= 2:
            arr = np.asarray(res.path.pts, dtype=float)
            arc = float(res.path.arc_length)
            infl = (arc / straight - 1.0) * 100.0 if straight > 1e-6 else 0.0
            custom = [
                f"OD{k}<br>path 弧长 {arc:.0f} m<br>直线 {straight:.0f} m<br>绕行 {infl:+.1f}%"
            ] * len(arr)
            # 白色底衬，保证彩色轨迹压在灰建筑上仍清晰
            fig.add_trace(go.Scatter(
                x=arr[:, 0], y=arr[:, 1], mode="lines",
                line=dict(color="white", width=6),
                hoverinfo="skip", showlegend=False,
                legendgroup=f"od{k}",
            ))
            fig.add_trace(go.Scatter(
                x=arr[:, 0], y=arr[:, 1], mode="lines",
                line=dict(color=col, width=3),
                name=f"OD{k}  path={arc:.0f}m (绕行 {infl:+.1f}%)",
                customdata=custom, hovertemplate="%{customdata}<extra></extra>",
                legendgroup=f"od{k}",
            ))
        else:
            reason = res.infeasible_reason.name if res.infeasible_reason else "-"
            fig.add_trace(go.Scatter(
                x=[o[0], d[0]], y=[o[1], d[1]], mode="lines+markers",
                line=dict(color=col, width=2, dash="dot"),
                marker=dict(size=6),
                name=f"OD{k}  不可达（{reason}）", hoverinfo="skip",
                legendgroup=f"od{k}",
            ))
        # 起点（绿圆）/ 终点（红方）
        fig.add_trace(go.Scatter(
            x=[o[0]], y=[o[1]], mode="markers",
            marker=dict(color=PALETTE["origin"], size=11, symbol="circle",
                        line=dict(color="white", width=1)),
            name="起点" if k == 0 else None,
            showlegend=k == 0, legendgroup="ends", hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=[d[0]], y=[d[1]], mode="markers",
            marker=dict(color=PALETTE["dest"], size=11, symbol="square",
                        line=dict(color="white", width=1)),
            name="终点" if k == 0 else None,
            showlegend=k == 0, legendgroup="ends", hoverinfo="skip",
        ))

    # ── 建筑底图作为地理配准底图（置于所有矢量层之下）────────────────
    fig.update_layout(
        images=[dict(
            source=img_b64,
            xref="x", yref="y",
            x=X0, y=Y1,               # 顶左角锚定在 data (X0, Y1)
            sizex=X1 - X0, sizey=Y1 - Y0,
            xanchor="left", yanchor="top",
            sizing="stretch", layer="below",
        )],
    )

    fig.update_layout(
        title=(
            f"整城可见性图跨城 OD（{n_nodes:,} 节点 / {area_km2:.1f} km² / "
            f"{n_build:,} 栋建筑）· plotly 交互"
        ),
        xaxis=dict(
            range=[X0, X1], title="X (m, local)",
            showgrid=False, zeroline=False,
            scaleanchor="y", scaleratio=1, constrain="domain",
        ),
        yaxis=dict(
            range=[Y0, Y1], title="Y (m, local)",
            showgrid=False, zeroline=False,
            scaleanchor="x", scaleratio=1, constrain="domain",
        ),
        legend=dict(x=0.99, y=0.99, xanchor="right", yanchor="top",
                    bgcolor="rgba(255,255,255,0.85)"),
        margin=dict(l=0, r=0, t=46, b=0),
        plot_bgcolor="white",
    )

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "fig_full_city_plotly.html")
    fig.write_html(out_path, include_plotlyjs="cdn")
    return [out_path]
