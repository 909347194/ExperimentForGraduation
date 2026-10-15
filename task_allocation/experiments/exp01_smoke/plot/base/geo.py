# -*- coding: utf-8 -*-
"""exp01_smoke 绘图底座 **之一**：地理要素绘制件（DEM 底图 · 图标 · 图层排版 · UAV 配色）。

本模块**不是一张图**——它不产出任何文件，也不登记进 ``registry.FIGURES``，
只提供所有带地理坐标的图共享的绘制件：

    共享样式   UAV 稳定配色 ``uav_color`` / 任务点尺寸 ``_task_size`` /
               标签避让 ``_label_offset`` / 白衬底 ``_HALO`` / 有向航段 ``_arrow``
    矢量图标   机巢=房屋、无人机=四旋翼、任务=圆点，以及让图标能如实出现在图例里的
               ``_IconPatch`` / ``_IconHandler``
    图层排版   ``_draw_nests`` / ``_set_scene_lim`` / ``_strip_axes`` /
               ``_panel_title`` / ``_row_figsize`` / ``_metric_cycle``

为什么单独抽出：整理前这些件散落在 ``assignment.py`` 里、被地理类图**反向 import**
私有符号。底座化之后依赖方向变成单行 Dependency::

    scene/*.py  ─┐
    process/*.py ──►  base/{geo,dem}.py  ──►  theme.py

同类约定见 ``base/__init__.py``。
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import matplotlib.path as mpath
import matplotlib.patches as mpatches
from matplotlib.patches import PathPatch
from matplotlib.legend_handler import HandlerBase

from ..theme import PALETTE

__all__ = [
    # 共享样式
    "UAV_PALETTE",
    "uav_color",
    "_task_size",
    "_label_offset",
    "_HALO",
    "_arrow",
    # 图标与图例
    "_G_HOUSE",
    "_G_DRONE",
    "_G_DOT",
    "_IconPatch",
    "_IconHandler",
    # 图层与排版
    "_draw_nests",
    "_set_scene_lim",
    "_strip_axes",
    "_panel_title",
    "_row_figsize",
    "_metric_cycle",
]


# ---------------------------------------------------------------------------
# 共享样式：UAV 稳定着色与航段绘制
# ---------------------------------------------------------------------------

#: 与 allocation 保持一致：按 UAV 稳定着色（tab20 前 20 色循环）
UAV_PALETTE = (
    "#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2",
    "#b279a2", "#ff9da6", "#9d755d", "#8c6bb1", "#2ca02c",
    "#d62728", "#17becf", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#393b79", "#637939", "#843c39", "#5254a3",
)


def uav_color(uav_id: int):
    return UAV_PALETTE[int(uav_id) % len(UAV_PALETTE)]


def _task_size(reward: float) -> float:
    return 22.0 + 11.0 * float(reward)


#: 标签避让：按 id 轮转偏移，避免同一机巢簇内标签堆在一起
_LABEL_OFFSETS = (
    (0, -13), (12, 7), (-12, 7), (11, -9), (-11, -9),
    (0, 14), (15, 0), (-15, 0), (10, 12), (-10, -13),
)


def _label_offset(key: int):
    return _LABEL_OFFSETS[int(key) % len(_LABEL_OFFSETS)]


#: 标签统一加白色衬底，压在航线/圆点上也能读
_HALO = dict(boxstyle="round,pad=0.16", fc="white", ec="none", alpha=0.72)


def _arrow(ax, p_from, p_to, color: str, *, lw: float = 2.1) -> None:
    """画一段有向航段：整条实线 + **线段中点**的三角箭头（表示行进方向）。

    箭头放在线段中点——既标明航向，又不会压住端点处的起点/任务/机巢标记。
    箭头子段长取段长的 ~24% 并夹在 [800m, 2600m]，避免过短看不见或过长征
    满整段；极短段（<800m）仍给固定长度，保证方向可读。
    零长航段（起终点重合）直接跳过——否则 ``solid_capstyle="round"`` 会画出
    一个多余的圆点，盖住本来要表达的任务/机巢标记。
    """
    x0, y0 = float(p_from[0]), float(p_from[1])
    x1, y1 = float(p_to[0]), float(p_to[1])
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return
    # 整条线（不带端点箭头）
    ax.plot([x0, x1], [y0, y1], "-", color=color, lw=lw, alpha=0.95,
            solid_capstyle="round", zorder=3)
    ux, uy = dx / L, dy / L
    mx, my = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    ah = min(max(L * 0.24, 800.0), 2600.0)   # 箭头子段长（米）
    s0 = (mx - ux * ah / 2.0, my - uy * ah / 2.0)
    s1 = (mx + ux * ah / 2.0, my + uy * ah / 2.0)
    ax.annotate(
        "", xy=s1, xytext=s0,
        arrowprops=dict(
            arrowstyle="-|>", color=color, linewidth=lw,
            shrinkA=0.0, shrinkB=0.0,
            mutation_scale=max(12.0, min(22.0, lw * 11.0)),
            alpha=0.95,
        ),
        zorder=4,
    )


# ---------------------------------------------------------------------------
# 通用图层
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 现代图标（矢量字形）：机巢=房屋、无人机=四旋翼、任务=圆点
# ---------------------------------------------------------------------------

def _center_path(path: mpath.Path) -> mpath.Path:
    """把字形路径的包围盒中心平移到原点，scatter/图例里居中更稳。"""
    verts = np.asarray(path.vertices, dtype=float)
    c = (verts.min(axis=0) + verts.max(axis=0)) / 2.0
    new = verts - c
    return mpath.Path(new, path.codes)


def _circle(cx: float, cy: float, r: float, n: int = 22):
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    verts = np.column_stack([cx + np.cos(t) * r, cy + np.sin(t) * r])
    verts = np.vstack([verts, verts[0]])  # 闭合（CLOSEPOLY 复用首点）
    codes = [mpath.Path.MOVETO] + [mpath.Path.LINETO] * (n - 1) + [mpath.Path.CLOSEPOLY]
    return verts, codes


def _rect(cx: float, cy: float, w: float, h: float):
    verts = np.array([
        (cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
        (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2),
        (cx - w / 2, cy - h / 2),
    ], float)
    codes = [mpath.Path.MOVETO] + [mpath.Path.LINETO] * 3 + [mpath.Path.CLOSEPOLY]
    return verts, codes


def _house_path() -> mpath.Path:
    """机巢图标：正三角（学术标记语言里的「基地/站点」，克制不花哨）。"""
    verts = np.array([
        (0.00, 0.95),   # 顶点
        (-0.90, -0.65), # 左底
        (0.90, -0.65),  # 右底
        (0.00, 0.95),
    ], float)
    codes = [mpath.Path.MOVETO] + [mpath.Path.LINETO] * 2 + [mpath.Path.CLOSEPOLY]
    return _center_path(mpath.Path(verts, codes))


def _drone_path() -> mpath.Path:
    """无人机图标：中央机身 + 四角旋翼 + 十字机臂（四旋翼剪影）。"""
    parts = []
    for dx, dy in [(0.62, 0.62), (-0.62, 0.62), (0.62, -0.62), (-0.62, -0.62)]:
        parts.append(_circle(dx, dy, 0.26, 18))
    parts.append(_circle(0.0, 0.0, 0.20, 18))          # 机身
    parts.append(_rect(0.0, 0.0, 1.24, 0.12))          # 横臂
    parts.append(_rect(0.0, 0.0, 0.12, 1.24))          # 竖臂
    verts = np.vstack([p[0] for p in parts])
    codes = np.concatenate([p[1] for p in parts])
    return _center_path(mpath.Path(verts, codes))


def _dot_path(r: float = 0.82, n: int = 24) -> mpath.Path:
    """任务/圆点图标（实心或空心由 facecolor 控制）。"""
    v, c = _circle(0.0, 0.0, r, n)
    return _center_path(mpath.Path(v, c))


# 预生成（scatter marker 与图例共用同一份字形）
_G_HOUSE = _house_path()
_G_DRONE = _drone_path()
_G_DOT = _dot_path()


class _IconPatch(mpatches.Patch):
    """图例代理：携带一个矢量字形，配合 ``_IconHandler`` 在图例里画出图标。"""

    def __init__(self, path: mpath.Path, fc, ec="white", lw: float = 1.2, *,
                 label: str | None = None):
        super().__init__(facecolor=fc, edgecolor=ec, linewidth=lw, label=label)
        self.icon_path = path


class _IconHandler(HandlerBase):
    """把 ``_IconPatch`` 的字形如实在图例框内绘制（而非退化为普通标记）。"""

    def create_artists(self, legend, orig_handle, x0, y0, width, height, fontsize, trans):
        path = orig_handle.icon_path
        verts = np.asarray(path.vertices, dtype=float)
        lo, hi = verts.min(axis=0), verts.max(axis=0)
        c = (lo + hi) / 2.0
        span = float((hi - lo).max())
        # span 极小时给下限，防止除零/放大到天文数字
        s = (min(width, height) * 0.80) / max(span / 2.0, 1e-9)
        new_verts = (verts - c) * s + np.array([x0 + width / 2.0, y0 + height / 2.0])
        patch = PathPatch(
            mpath.Path(new_verts, path.codes),
            facecolor=orig_handle.get_facecolor(),
            edgecolor=orig_handle.get_edgecolor(),
            linewidth=orig_handle.get_linewidth(),
            transform=trans,
        )
        return [patch]


def _strip_axes(ax) -> None:
    """去掉坐标轴的刻度/数字/标签/边框，仅保留绘图内容（地图式呈现）。

    与 ``_set_scene_lim`` / ``set_aspect`` 不冲突：本函数只关掉可见的
    坐标轴装饰，数据范围与等比例缩放照常生效。
    """
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("")
    ax.set_ylabel("")
    for spine in ax.spines.values():
        spine.set_visible(False)


def _panel_title(ax, text: str, *, fontsize: float = 8.5) -> None:
    """把行标题画进地图内部左上角（圆角半透明底），替代面板外长标题。

    好处：多行图不再被「标题比地图宽」撑出大片留白，画面更紧凑现代。
    """
    ax.text(
        0.03, 0.97, text,
        transform=ax.transAxes, ha="left", va="top",
        fontsize=fontsize, color="#1f2933", zorder=10,
        bbox=dict(boxstyle="round,pad=0.30", fc="white", ec="none", alpha=0.82),
    )


def _row_figsize(
    extent,
    row_h: float,
    n_rows: int,
    extra_w: float = 1.7,
    min_width: float = 6.0,
) -> tuple[float, float]:
    """按底图宽高比自适应多行图的画幅，让地图尽量填满行、不再两边留白。

    ``extent`` 为 imshow 约定 ``(left, right, bottom, top)``；``row_h`` 是
    单行目标高度（英寸），``n_rows`` 为行数，``extra_w`` 预留给色条/图例/边距。

    ``min_width`` 是**版面下限**（单栏档 6 in）：窄高地形（aspect 被裁到
    0.45）算出的画幅可能只有 5–6 in 宽。触发下限时按 aspect 反算行高，地图
    比例不变、整体放大。下限刻意只取 6 in——多行地理图与 11.5 in 的数值
    面板并排时字号观感差一倍以上，但若按 11.5 in 撑宽，行高会跟着翻倍、
    总高度冲到 20 in 以上，反而没法看；且 ``save_figure`` 用
    ``bbox_inches="tight"`` 会裁掉画布空白，实际宽度还会再小一截。
    地理图归单栏档、数值面板归通栏档，各按各的栏宽设计字号。
    """
    if extent is not None:
        left, right, bottom, top = extent
        aspect = float(np.clip((right - left) / max(top - bottom, 1e-6), 0.45, 2.2))
    else:
        aspect = 1.0
    width = row_h * aspect + extra_w
    if width < min_width:
        row_h = (min_width - extra_w) / aspect
        width = min_width
    return (width, row_h * max(int(n_rows), 1))


def _draw_nests(
    ax,
    nests: Sequence[dict[str, Any]],
    *,
    counts: dict[int, int] | None = None,
    zorder: int = 7,
) -> None:
    """机巢：房屋图标（面积 ∝ 容量），标注「巢 {id}」及初始无人机数。

    ``counts`` 为 ``{nest_id: 初始无人机数}``；传 None 时只标巢号。
    初始无人机本就停在各自机巢，故在巢上直接标数量，而非另撒一堆三角点。
    """
    if not nests:
        return
    ax.scatter(
        [float(n["x"]) for n in nests],
        [float(n["y"]) for n in nests],
        s=[120.0 + 22.0 * float(n.get("capacity", 1)) for n in nests],
        marker=_G_HOUSE, color=PALETTE["nest"], edgecolors="white",
        linewidths=1.2, zorder=zorder,
    )
    for n in nests:
        # 相邻机巢标签左右交替，避免纵向相邻的巢 5/巢 6 之类互相压字
        to_left = int(n["id"]) % 2 == 1
        label = f"巢{n['id']}"
        if counts is not None:
            label += f"\n{int(counts.get(int(n['id']), 0))} 架"
        ax.annotate(
            label, (float(n["x"]), float(n["y"])),
            textcoords="offset points", xytext=(-13 if to_left else 13, 10),
            ha="right" if to_left else "left",
            fontsize=7.6, color="#0b2545", zorder=zorder + 1, bbox=_HALO,
        )


def _set_scene_lim(
    ax,
    nests: Sequence[dict[str, Any]],
    tasks: Sequence[dict[str, Any]],
    uavs: Sequence[dict[str, Any]],
    dem: dict[str, Any] | None,
    extent: tuple[float, float, float, float] | None = None,
) -> None:
    """取视图范围。

    ``extent``（imshow 约定 ``(left, right, bottom, top)``）优先——与
    ``_dem_view_extent`` 共用同一缓存，保证 DEM 底图与视图严格一致、满幅铺底。
    其次取 DEM 包围盒；无 DEM 时退化为要素并集 + 少量留白。
    """
    if extent is not None:
        l, r, b, tp = extent
        ax.set_xlim(l, r)
        ax.set_ylim(b, tp)
        return
    bounds = (dem or {}).get("bounds")
    if bounds:
        l, b, r, tp = (float(v) for v in bounds)
        ax.set_xlim(l, r)
        ax.set_ylim(b, tp)
        return
    xs: list[float] = []
    ys: list[float] = []
    for n in nests:
        xs.append(float(n["x"])); ys.append(float(n["y"]))
    for t in tasks:
        xs.append(float(t["x"])); ys.append(float(t["y"]))
    for u in uavs:
        xs.append(float(u["x"])); ys.append(float(u["y"]))
    if xs and ys:
        pad_x = (max(xs) - min(xs)) * 0.06 or 1000.0
        pad_y = (max(ys) - min(ys)) * 0.06 or 1000.0
        ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
        ax.set_ylim(min(ys) - pad_y, max(ys) + pad_y)


def _metric_cycle(metrics: dict[str, Any], cycle: int) -> dict[str, Any]:
    """返回与 solution 周期对应的数值记录。"""
    for record in metrics.get("cycles") or []:
        if int(record.get("cycle", -1)) == int(cycle):
            return record
    return {}