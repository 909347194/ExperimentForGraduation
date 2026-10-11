# -*- coding: utf-8 -*-
"""exp01_smoke 地理示意图：2D 平面 DEM 底图 + 机巢 + 无人机 + 任务。

两张图：

    ``geo_overview``    初始态势：DEM 高程底图 + 机巢 + 全量初始无人机分布 + 全部任务。
                         回答「场景长什么样、无人机一开始在哪」。
    ``geo_evolution``   逐周期演化：每个滚动周期一行（共享坐标），DEM 底图上叠加
                         本周期的航次（起点▲→任务→终点机巢）、执行的任务按承运 UAV 着色、
                         背景任务灰显，直观呈现「每一周期后的变化」。

数据来源：``results/solution.json`` 的 ``dem``（降采样高程 + 包围盒）、``uavs``（初始分布）、
``nests`` / ``tasks`` / ``cycles[].tours``。与现有 ``assignment_map`` 共享同一套配色与箭头样式，
只额外补了地形上下文与全量初始无人机层。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np
import matplotlib.path as mpath
import matplotlib.patches as mpatches
from matplotlib.colors import LightSource, LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import PathPatch
from matplotlib.legend_handler import HandlerBase

from .assignment import (
    UAV_PALETTE,
    _arrow,
    _HALO,
    _label_offset,
    _task_size,
    uav_color,
)
from .theme import PALETTE, plt, save_figure

__all__ = [
    "plot_geo_overview",
    "plot_geo_evolution",
    "plot_geo_task_lifecycle",
    "plot_geo_fleet_transition",
]


# ---------------------------------------------------------------------------
# 通用图层
# ---------------------------------------------------------------------------

# 高原分层设色（hypsometric tint，低→高）：灰橄榄谷地 → 橄榄卡其 → 卡其黄 →
# 暖沙黄 → 赭石 → 棕 → 深棕 → 近白峰顶。刻意避开 matplotlib 原生 ``terrain``/
# ``gist_earth`` 的低段"水蓝/鲜绿"——在 3500–5700 m 高原，鲜绿会被误读成
# 植被/水系，且高饱和谷地喧宾夺主；这里整体保持低饱和的大地色系，
# 只在峰顶提亮到近白，保证任意高程都有可辨识色调与立体感。
_HYPSO = LinearSegmentedColormap.from_list(
    "hypso_plateau",
    [
        (0.00, "#77784b"),   # 谷底（最低）：灰橄榄
        (0.15, "#98945a"),   # 低坡：橄榄卡其
        (0.32, "#b3a05f"),   # 山麓：卡其黄
        (0.50, "#c7a35f"),   # 缓坡：暖沙黄
        (0.66, "#b57f3e"),   # 中坡：赭石
        (0.82, "#8f5a2c"),   # 陡坡：棕
        (0.93, "#6e4523"),   # 高坡：深棕
        (1.00, "#efe7d6"),   # 峰顶：近白
    ],
)


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
    """机巢图标：屋顶三角 + 方形屋身（可辨识的「基地/家」语义）。"""
    verts = np.array([
        (0.00, 1.00),   # 屋脊
        (-0.85, 0.15),  # 左屋檐
        (-0.62, 0.15),  # 左墙顶
        (-0.62, -0.80), # 左墙底
        (0.62, -0.80),  # 右墙底
        (0.62, 0.15),   # 右墙顶
        (0.85, 0.15),   # 右屋檐
        (0.00, 1.00),
    ], float)
    codes = [mpath.Path.MOVETO] + [mpath.Path.LINETO] * 6 + [mpath.Path.CLOSEPOLY]
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
        c = (verts.min(axis=0) + verts.max(axis=0)) / 2.0
        span = float((verts.max(axis=0) - verts.min(axis=0)).max())
        s = (min(width, height) * 0.80) / (span / 2.0)
        new_verts = (verts - c) * s + np.array([x0 + width / 2.0, y0 + height / 2.0])
        patch = PathPatch(
            mpath.Path(new_verts, path.codes),
            facecolor=orig_handle.get_facecolor(),
            edgecolor=orig_handle.get_edgecolor(),
            linewidth=orig_handle.get_linewidth(),
            transform=trans,
        )
        return [patch]


# ---------------------------------------------------------------------------
# 真实 DEM 瓦片（ASTER GDEM v3）采样：优先用原始 tif 作底图
# ---------------------------------------------------------------------------

def _find_dem_tif() -> "Path | None":
    """向上查找 ASTER GDEM 瓦片（task_allocation/data/ASTGTMV003_N29E091_dem.tif）。"""
    base = Path(__file__).resolve()
    for p in [base, *base.parents]:
        direct = p / "data" / "ASTGTMV003_N29E091_dem.tif"
        if direct.is_file():
            return direct
        data_dir = p / "data"
        if data_dir.is_dir():
            hits = sorted(data_dir.glob("ASTGTMV*_dem.tif"))
            if hits:
                return hits[0]
    return None


def _nearest_fill_1d(a: np.ndarray) -> np.ndarray:
    """一维最近邻填充：每个 NaN 取左右最近的有效值（两侧等距取左）。"""
    n = a.size
    valid = np.isfinite(a)
    if not valid.any() or valid.all():
        return a
    idx = np.where(valid, np.arange(n), -1)
    fwd = np.maximum.accumulate(idx)                     # 左侧最近有效下标
    bwd = np.minimum.accumulate(np.where(idx >= 0, idx, n)[::-1])[::-1]
    bwd = np.where(bwd == n, -1, bwd)                    # 右侧最近有效下标
    ar = np.arange(n)
    d_fwd = np.where(fwd >= 0, ar - fwd, n)
    d_bwd = np.where(bwd >= 0, bwd - ar, n)
    pick = np.where(d_bwd < d_fwd, bwd, fwd)
    out = a[np.clip(pick, 0, n - 1)]
    return np.where(valid, a, out)


def _fill_nan(elev: np.ndarray) -> np.ndarray:
    """把越界（瓦片外）NaN 用最近有效高程延拓填充，让边界地形自然延伸。

    先沿行填充（覆盖西/东边缘的越界条带），再沿列兜底（南/北边缘），
    避免"填最低值"造成的整片死色边框。
    """
    out = np.array(elev, dtype=float, copy=True)
    if np.isfinite(out).all() or not np.isfinite(out).any():
        return out
    for i in range(out.shape[0]):                        # 行方向
        row = out[i]
        if not np.isfinite(row).all() and np.isfinite(row).any():
            out[i] = _nearest_fill_1d(row)
    if (~np.isfinite(out)).any():                        # 列方向兜底
        for j in range(out.shape[1]):
            col = out[:, j]
            if not np.isfinite(col).all() and np.isfinite(col).any():
                out[:, j] = _nearest_fill_1d(col)
    out[~np.isfinite(out)] = 0.0                         # 极端兜底（不应发生）
    return out


_DEM_SAMPLE_CACHE: "dict[tuple, Any]" = {}


def _box3(a: np.ndarray) -> np.ndarray:
    """3x3 盒滤波（边缘复制填充），用于轻微平滑填充区。"""
    p = np.pad(a, 1, mode="edge")
    return (
        p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
        + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
        + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]
    ) / 9.0


def _smooth_filled(elev: np.ndarray, valid: np.ndarray, iters: int = 10) -> np.ndarray:
    """只平滑瓦片外的填充区（有效区保持原值），消除最近邻延拓的条带感。"""
    smooth = elev.copy()
    for _ in range(iters):
        smooth = _box3(smooth)
    return np.where(valid, elev, smooth)


def _sample_real_dem(dem: dict[str, Any], solution: dict[str, Any] | None):
    """若可用，直接从真实 ASTER 瓦片裁剪采样（~150 m 格网），返回 (elev, extent)。

    视图取「场景包围盒 ∩ 瓦片实际覆盖范围」并外扩约 2.5 km（保证贴近瓦片
    边缘的机巢/任务完整可见），瓦片外仅剩窄条，用最近邻延拓 + 轻度平滑补齐，
    **不渲染**瓦片覆盖不到的大片虚构地形。返回的 ``extent`` 为 imshow 约定的
    ``(left, right, bottom, top)``。

    否则返回 None，由 ``_draw_dem`` 回退到 solution.json 里的降采样 dem。
    做法参照 matplotlib 官方 *topographic hillshading* 示例：用真实栅格 +
    ``LightSource`` 山体阴影，dx/dy 取与高程同单位的米制格距。
    """
    if not dem or not dem.get("bounds"):
        return None
    lf_info = (solution or {}).get("local_frame") or {}
    if "lat0" not in lf_info or "lon0" not in lf_info:
        return None
    tif = _find_dem_tif()
    if tif is None:
        return None
    try:
        from task_allocation.methods.utils import GeoTIFFDEM, LocalFrame
    except Exception:
        return None

    left, bottom, right, top = (float(v) for v in dem["bounds"])
    try:
        lf = LocalFrame(float(lf_info["lat0"]), float(lf_info["lon0"]))
        gt = GeoTIFFDEM(str(tif))
    except Exception:
        return None

    # 瓦片四角 -> 米制 ENU 包络
    tl, tb, tr, tt = gt.bounds  # (left, bottom, right, top)，单位：度
    corner_lat = np.array([tb, tb, tt, tt])
    corner_lon = np.array([tl, tr, tl, tr])
    enu = lf.to_enu(corner_lat, corner_lon, 0.0)
    tx0, tx1 = float(enu[..., 0].min()), float(enu[..., 0].max())
    ty0, ty1 = float(enu[..., 1].min()), float(enu[..., 1].max())

    # 视图 = 场景 ∩ 瓦片包络，外扩 2.5 km 且仍夹在场景内
    margin = 2500.0
    vx0 = max(left, tx0 - margin)
    vx1 = min(right, tx1 + margin)
    vy0 = max(bottom, ty0 - margin)
    vy1 = min(top, ty1 + margin)
    if vx1 - vx0 < 1000.0 or vy1 - vy0 < 1000.0:
        return None  # 场景与瓦片几乎不相交，退回降采样 dem

    width_m, height_m = vx1 - vx0, vy1 - vy0
    # 目标格网 ~150 m，最长边封顶 1000 px（兼顾地形细节与渲染速度）
    px = max(240, min(1000, int(round(width_m / 150.0))))
    py = max(240, min(1000, int(round(height_m / 150.0))))
    xs = np.linspace(vx0, vx1, px)
    ys = np.linspace(vy1, vy0, py)  # 行序北→南，与 imshow origin="upper" 一致
    gx, gy = np.meshgrid(xs, ys)
    try:
        la, lo, _ = lf.to_geodetic(gx, gy, 0.0)
        elev = np.asarray(gt.sample_elevation(la, lo, bilinear=True), dtype=float)
    except Exception:
        return None

    valid = np.isfinite(elev)
    if not valid.any():
        return None
    elev = _smooth_filled(_fill_nan(elev), valid)
    extent = (vx0, vx1, vy0, vy1)
    return elev, extent


def _get_elevation(dem, solution):
    """取底图高程：优先真实瓦片采样（带进程内缓存），失败回退 solution dem。"""
    key = tuple(round(float(v), 1) for v in dem["bounds"]) if dem else None
    if key in _DEM_SAMPLE_CACHE:
        return _DEM_SAMPLE_CACHE[key]
    result = _sample_real_dem(dem, solution)
    _DEM_SAMPLE_CACHE[key] = result
    return result


def _dem_view_extent(dem, solution):
    """底图视图范围（imshow 约定 ``(left, right, bottom, top)``）。

    与 ``_get_elevation`` 共用同一份缓存，保证 DEM 图像与视图范围严格一致
    （满幅铺底，不露底色）；拿不到真实瓦片时退回 solution dem 包围盒。
    """
    result = _get_elevation(dem, solution) if dem else None
    if result is not None:
        return result[1]
    if dem and dem.get("bounds"):
        left, bottom, right, top = (float(v) for v in dem["bounds"])
        return (left, right, bottom, top)
    return None


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


def _panel_title(ax, text: str) -> None:
    """把行标题画进地图内部左上角（圆角半透明底），替代面板外长标题。

    好处：多行图不再被「标题比地图宽」撑出大片留白，画面更紧凑现代。
    """
    ax.text(
        0.025, 0.975, text,
        transform=ax.transAxes, ha="left", va="top",
        fontsize=9.5, color="#1f2933", fontweight="bold", zorder=10,
        bbox=dict(boxstyle="round,pad=0.32", fc="white", ec="none", alpha=0.82),
    )


def _row_figsize(
    extent,
    row_h: float,
    n_rows: int,
    extra_w: float = 1.7,
) -> tuple[float, float]:
    """按底图宽高比自适应多行图的画幅，让地图尽量填满行、不再两边留白。

    ``extent`` 为 imshow 约定 ``(left, right, bottom, top)``；``row_h`` 是
    单行目标高度（英寸），``n_rows`` 为行数，``extra_w`` 预留给色条/图例/边距。
    """
    if extent is not None:
        left, right, bottom, top = extent
        aspect = float(np.clip((right - left) / max(top - bottom, 1e-6), 0.45, 2.2))
    else:
        aspect = 1.0
    return (row_h * aspect + extra_w, row_h * max(int(n_rows), 1))


def _draw_dem(ax, dem: dict[str, Any] | None, solution: dict[str, Any] | None = None):
    """画 2D 平面 DEM 底图：真实 ASTER 高程 + 山体阴影晕渲。

    优先用真实 GeoTIFF 瓦片（按场景裁剪、~150 m 格网）作底图，
    退化为 solution.json 里的降采样 dem。返回 ``ScalarMappable`` 供色条使用；
    DEM 缺失时返回 None。

    做法参照 matplotlib 官方 *topographic hillshading* 示例：
    ``LightSource(azdeg=315, altdeg=45)`` 山体阴影，dx/dy 取与高程同单位的
    米制格距，使 ``vert_exag`` 反映真实坡度；再叠加高原分层设色（``_HYPSO``）。
    """
    if not dem or not dem.get("elevation"):
        return None

    sampled = _get_elevation(dem, solution) if solution is not None else None
    if sampled is not None:
        elev, extent = sampled
    else:
        left, bottom, right, top = (float(v) for v in dem["bounds"])
        extent = (left, right, bottom, top)
        elev = np.asarray(dem["elevation"], dtype=float)
    if elev.size == 0 or not np.isfinite(elev).any():
        return None

    n_rows, n_cols = elev.shape
    dx = max((extent[1] - extent[0]) / max(n_cols - 1, 1), 1e-6)
    dy = max((extent[3] - extent[2]) / max(n_rows - 1, 1), 1e-6)
    # 2%–98% 分位拉伸，避免极值把色带两端压扁
    vmin, vmax = np.nanpercentile(elev, [2.0, 98.0])
    if not np.isfinite(vmin) or vmax <= vmin:
        vmin, vmax = float(np.nanmin(elev)), float(np.nanmax(elev))
    norm = Normalize(vmin=float(vmin), vmax=float(vmax))

    # 西北 45° 光照；dx/dy 为真实米制格距，vert_exag 据此反映真实坡度。
    # 高高原水平跨度大、相对起伏缓，vert_exag≈1.8 即可显出立体感。
    try:
        ls = LightSource(azdeg=315, altdeg=45)
        shaded = ls.shade(
            elev, cmap=_HYPSO, norm=norm,
            blend_mode="soft", vert_exag=1.8, dx=dx, dy=dy,
        )
        ax.imshow(
            shaded, extent=extent, origin="upper",
            aspect="auto", zorder=0, interpolation="bilinear",
        )
    except Exception:
        # 兜底：无法晕渲时退回普通分层设色
        ax.imshow(
            elev, extent=extent, origin="upper", cmap=_HYPSO, norm=norm,
            aspect="auto", zorder=0, alpha=0.95,
        )

    ax.set_facecolor("#77784b")  # 谷底色；万一越界未填也不露白洞
    ax.grid(False)               # 关闭全局网格线，避免压在地形上
    return plt.cm.ScalarMappable(norm=norm, cmap=_HYPSO)


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
        s=[150.0 + 26.0 * float(n.get("capacity", 1)) for n in nests],
        marker=_G_HOUSE, color=PALETTE["nest"], edgecolors="white",
        linewidths=1.4, zorder=zorder,
    )
    for n in nests:
        # 相邻机巢标签左右交替，避免纵向相邻的巢 5/巢 6 之类互相压字
        to_left = int(n["id"]) % 2 == 1
        label = f"巢 {n['id']}"
        if counts:
            label += f"\n{int(counts.get(int(n['id']), 0))} 架"
        ax.annotate(
            label, (float(n["x"]), float(n["y"])),
            textcoords="offset points", xytext=(-15 if to_left else 15, 12),
            ha="right" if to_left else "left",
            fontsize=8.5, color="#0b2545",
            fontweight="bold", zorder=zorder + 1, bbox=_HALO,
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
    if dem and dem.get("bounds"):
        l, b, r, tp = (float(v) for v in dem["bounds"])
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


def _draw_task_lifecycle(
    ax,
    tasks: Sequence[dict[str, Any]],
    record: dict[str, Any],
    now: float,
) -> dict[str, int]:
    """绘制一个周期末的任务状态，且只显示当前已到达任务。

    颜色语义固定：灰色=尚未到达，橙色=本周期留待后续，绿色=本周期完成，
    深灰=此前已完成。由 metrics 的 completed/deferred id 驱动，而不是通过最终
    task.status 反推，避免多周期图把所有任务都误画成最终状态。
    """
    completed_now = {int(i) for i in record.get("completed_ids") or []}
    deferred = {int(i) for i in record.get("deferred_ids") or []}
    completed_before = {
        int(t["id"])
        for t in tasks
        if t.get("completed_at") is not None and float(t["completed_at"]) < now - 1e-9
    }
    groups = {
        "future": [t for t in tasks if float(t.get("arrival_time", 0.0)) > now],
        "done_before": [t for t in tasks if int(t["id"]) in completed_before],
        "done_now": [t for t in tasks if int(t["id"]) in completed_now],
        "deferred": [t for t in tasks if int(t["id"]) in deferred],
    }
    # 已到达、但未进入以上集合的任务：例如候选筛选前被排除，仍为待执行。
    known_other = [
        t for t in tasks
        if float(t.get("arrival_time", 0.0)) <= now
        and int(t["id"]) not in (completed_before | completed_now | deferred)
    ]
    groups["pending"] = known_other
    styles = {
        "future": ("#d8dee6", "#c3cbd4", "尚未到达", 0.30),
        "pending": ("none", "#9aa5b1", "待执行", 0.85),
        "deferred": ("#f58518", "white", "本周期延期", 0.96),
        "done_before": ("#7f8c8d", "white", "此前完成", 0.64),
        "done_now": ("#54a24b", "white", "本周期完成", 1.0),
    }
    for name, items in groups.items():
        if not items:
            continue
        face, edge, _, alpha = styles[name]
        ax.scatter(
            [float(t["x"]) for t in items], [float(t["y"]) for t in items],
            s=[_task_size(float(t.get("reward", 1.0))) for t in items],
            facecolors=face, edgecolors=edge, linewidths=1.0, alpha=alpha, zorder=3,
        )
        # 只为本周期相关任务标注，避免未来任务和已完成历史任务淹没主图。
        if name in {"done_now", "deferred", "pending"}:
            for task in items:
                ax.annotate(
                    f"T{task['id']}", (float(task["x"]), float(task["y"])),
                    textcoords="offset points", xytext=_label_offset(int(task["id"])),
                    ha="center", fontsize=6.8, color="#1f2933", zorder=7, bbox=_HALO,
                )
    return {key: len(value) for key, value in groups.items()}


def _draw_cycle_tours(
    ax,
    cyc: dict[str, Any],
    task_by_id: dict[int, dict[str, Any]],
    nest_by_id: dict[int, dict[str, Any]],
) -> set[int]:
    """画单周期航次：起点▲ → 任务 → 终点机巢，执行的任务按承运 UAV 着色。

    返回本周期用到的 UAV id 集合（供图例）。
    """
    owner: dict[int, int] = {}
    uavs_used: set[int] = set()
    for tour in cyc.get("tours") or []:
        u = int(tour.get("uav_id", -1))
        uavs_used.add(u)
        for tid in tour.get("task_ids") or []:
            owner[int(tid)] = u

    # 背景任务（本周期未执行）
    others = [t for t in task_by_id.values() if int(t["id"]) not in owner]
    if others:
        ax.scatter(
            [float(t["x"]) for t in others],
            [float(t["y"]) for t in others],
            s=[_task_size(t["reward"]) * 0.6 for t in others],
            facecolors="none", edgecolors="#c3cbd4", linewidths=1.1, zorder=2,
        )
        for t in others:
            ax.annotate(
                f"T{t['id']}", (float(t["x"]), float(t["y"])),
                textcoords="offset points", xytext=_label_offset(t["id"]),
                ha="center", fontsize=6.4, color="#7b8794", zorder=2, bbox=_HALO,
            )

    # 航次
    for tour in cyc.get("tours") or []:
        u = int(tour.get("uav_id", -1))
        color = uav_color(u)
        start = tour.get("start") or [0.0, 0.0, 0.0]
        pts: list[tuple[float, float]] = [(float(start[0]), float(start[1]))]
        for tid in tour.get("task_ids") or []:
            t = task_by_id.get(int(tid))
            if t is not None:
                pts.append((float(t["x"]), float(t["y"])))
        end = (
            nest_by_id.get(int(tour["end_nest_id"]))
            if tour.get("end_nest_id") is not None
            else None
        )
        if end is not None:
            pts.append((float(end["x"]), float(end["y"])))
        for p0, p1 in zip(pts, pts[1:]):
            _arrow(ax, p0, p1, color)
        ax.scatter(
            [pts[0][0]], [pts[0][1]], marker=_G_DRONE, s=135, color=color,
            edgecolors="white", linewidths=1.3, zorder=5,
        )
        ax.annotate(
            f"UAV {u}", pts[0], textcoords="offset points", xytext=(9, 9),
            ha="left", fontsize=7.2, color="#1f2933", fontweight="bold",
            zorder=8, bbox=_HALO,
        )
        if end is not None:
            ax.scatter(
                [float(end["x"])], [float(end["y"])], s=210,
                facecolors="none", edgecolors=color, linewidths=1.9, zorder=4,
            )

    # 本周期执行的任务（按承运 UAV 着色）
    for tid, u in owner.items():
        t = task_by_id.get(tid)
        if t is None:
            continue
        ax.scatter(
            [float(t["x"])], [float(t["y"])],
            s=_task_size(t["reward"]), color=uav_color(u),
            edgecolors="white", linewidths=1.0, zorder=6,
        )
        ax.annotate(
            f"T{tid}", (float(t["x"]), float(t["y"])),
            textcoords="offset points", xytext=_label_offset(tid),
            ha="center", fontsize=7.4, color="#1f2933",
            fontweight="bold", zorder=8, bbox=_HALO,
        )
    return uavs_used


# ---------------------------------------------------------------------------
# 图 1：初始态势
# ---------------------------------------------------------------------------

def plot_geo_overview(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """初始态势：DEM 底图 + 机巢（标注初始无人机数）+ 全部任务。"""
    from collections import Counter

    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    uavs = list(solution.get("uavs") or [])
    dem = solution.get("dem")
    if not nests:
        return []

    # 初始无人机本就停在各自机巢：按 nest_id 计数，直接在巢上标数量
    count_by_nest = Counter(int(u.get("nest_id", -1)) for u in uavs)
    n_initial = len(uavs)

    fig, ax = plt.subplots(figsize=(10.5, 9.0), layout="constrained")
    sm = _draw_dem(ax, dem, solution)

    # 全部任务（灰显，作为背景）
    if tasks:
        ax.scatter(
            [float(t["x"]) for t in tasks],
            [float(t["y"]) for t in tasks],
            s=[_task_size(t["reward"]) for t in tasks],
            color="#9aa5b1", edgecolors="white", linewidths=0.8, zorder=6,
            label="任务（全部）",
        )

    _draw_nests(ax, nests, counts=count_by_nest)

    if sm is not None:
        cb = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02, shrink=0.75)
        cb.set_label("高程 (m)", fontsize=9)
        cb.ax.tick_params(labelsize=8)

    handles = [
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.2,
                   label=f"机巢（含初始无人机 {n_initial} 架）"),
        _IconPatch(_G_DOT, fc="#9aa5b1", ec="white", lw=1.0, label="任务（全部）"),
    ]
    ax.legend(
        handles=handles, loc="upper right", fontsize=9.5,
        handler_map={_IconPatch: _IconHandler()},
        frameon=True, fancybox=True, framealpha=0.96,
        edgecolor="#e3e7ec", facecolor="white", borderpad=0.7,
    )

    _set_scene_lim(ax, nests, tasks, uavs, dem, extent=_dem_view_extent(dem, solution))
    ax.set_aspect("equal", adjustable="box")
    _strip_axes(ax)
    ax.set_title("初始态势 · 机巢 / 任务 / DEM 地形", fontsize=13, fontweight="bold", color="#1f2933")

    stem = Path(out_dir) / "geo_overview"
    return save_figure(fig, stem, formats, dpi=dpi)


# ---------------------------------------------------------------------------
# 图 2：逐周期演化
# ---------------------------------------------------------------------------

def plot_geo_evolution(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """逐周期演化：每个滚动周期一行，DEM 底图上叠加本周期航次与变化。"""
    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    cycles = list(solution.get("cycles") or [])
    dem = solution.get("dem")
    if not nests or not cycles:
        return []

    task_by_id = {int(t["id"]): t for t in tasks}
    nest_by_id = {int(n["id"]): n for n in nests}
    metric_by_cycle = {
        int(c.get("cycle", -1)): c for c in (metrics.get("cycles") or [])
    }
    n_cycles = len(cycles)

    # 底图宽高比自适应画幅：地图填满每行，避免两侧大片留白
    extent = _dem_view_extent(dem, solution)
    row_h = 5.0
    fig, axes = plt.subplots(
        n_cycles, 1, figsize=_row_figsize(extent, row_h, n_cycles),
        squeeze=False, sharex=True, sharey=True, layout="constrained",
    )
    axes = axes.ravel()
    sm0 = None
    all_uavs_used: set[int] = set()

    for idx, (ax, cyc) in enumerate(zip(axes, cycles)):
        sm = _draw_dem(ax, dem, solution)
        if idx == 0:
            sm0 = sm
        uavs_used = _draw_cycle_tours(ax, cyc, task_by_id, nest_by_id)
        all_uavs_used |= uavs_used
        _draw_nests(ax, nests)

        sel = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("selection", {})
        muas = metric_by_cycle.get(int(cyc.get("cycle", -1)), {}).get("muas", {})
        # 标题画进地图左上角，不再外置长标题
        _panel_title(
            ax,
            f"周期 {cyc.get('cycle')} @ {float(cyc.get('current_time', 0.0)):.0f}s"
            f"｜任务 {sel.get('n_tasks_in', 0)}→{sel.get('n_selected', 0)}→{len(uavs_used)}"
            f"｜里程 {float(muas.get('f2_cost', 0.0)) / 1000.0:.0f} km"
            f"｜航次 {len(cyc.get('tours') or [])}",
        )
        _strip_axes(ax)

    # 共享坐标范围
    _set_scene_lim(axes[0], nests, tasks, [], dem, extent=_dem_view_extent(dem, solution))
    for ax in axes:
        ax.set_aspect("equal", adjustable="box")

    # 图例合并到全图底部一行：所有周期用到的 UAV + 通用符号，
    # 不再每行挂一个图例框压在地图上。
    handles = [
        _IconPatch(_G_DRONE, fc=uav_color(u), ec="white", lw=1.1, label=f"UAV {u}")
        for u in sorted(all_uavs_used)
    ]
    handles += [
        _IconPatch(_G_DRONE, fc="#4b5563", ec="white", lw=1.1, label="UAV 起点"),
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.2, label="机巢"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="none",
               markeredgecolor="#2d3748", markersize=10, label="终点机巢"),
        _IconPatch(_G_DOT, fc="none", ec="#c3cbd4", lw=1.2, label="未执行任务"),
    ]
    fig.legend(
        handles=handles, loc="outside lower center",
        handler_map={_IconPatch: _IconHandler()},
        ncol=min(8, len(handles)), fontsize=8.5, columnspacing=1.2,
        frameon=False,
    )

    if sm0 is not None:
        fig.colorbar(
            sm0, ax=axes.tolist(), location="right",
            shrink=0.55, pad=0.015,
        ).set_label("高程 (m)", fontsize=9)

    fig.suptitle("逐周期空间演化 · DEM 晕渲底图", fontsize=13, fontweight="bold", color="#1f2933")
    stem = Path(out_dir) / "geo_evolution"
    return save_figure(fig, stem, formats, dpi=dpi)


# ---------------------------------------------------------------------------
# 图 3：任务生命周期
# ---------------------------------------------------------------------------

def plot_geo_task_lifecycle(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """按周期展示任务从到达、延期到完成的空间演变。

    此图刻意不画航线：它用于回答“哪些目标在什么时候出现、被完成或继续滞留”，
    与 ``geo_evolution`` 中的航次细节互补。
    """
    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    cycles = list(solution.get("cycles") or [])
    dem = solution.get("dem")
    if not nests or not cycles:
        return []

    # 底图宽高比自适应画幅：地图填满每行，避免两侧大片留白
    extent = _dem_view_extent(dem, solution)
    row_h = 4.8
    fig, axes = plt.subplots(
        len(cycles), 1, figsize=_row_figsize(extent, row_h, len(cycles)),
        squeeze=False, sharex=True, sharey=True, layout="constrained",
    )
    axes = axes.ravel()
    im0 = None
    for index, (ax, cycle) in enumerate(zip(axes, cycles)):
        im = _draw_dem(ax, dem, solution)
        if index == 0:
            im0 = im
        record = _metric_cycle(metrics, int(cycle.get("cycle", -1)))
        counts = _draw_task_lifecycle(
            ax, tasks, record, float(cycle.get("current_time", 0.0))
        )
        _draw_nests(ax, nests)
        # 标题画进地图左上角，不再外置长标题
        _panel_title(
            ax,
            f"周期 {cycle.get('cycle')} @ {float(cycle.get('current_time', 0.0)):.0f}s"
            f"｜待处理 {counts['pending'] + counts['deferred']}"
            f"｜完成 {counts['done_now']}"
            f"｜延期 {counts['deferred']}",
        )
        _strip_axes(ax)
        ax.set_aspect("equal", adjustable="box")

    _set_scene_lim(axes[0], nests, tasks, [], dem, extent=_dem_view_extent(dem, solution))
    if im0 is not None:
        cb = fig.colorbar(im0, ax=axes.tolist(), location="right", shrink=0.5, pad=0.015)
        cb.set_label("高程 (m)", fontsize=9)
        cb.ax.tick_params(labelsize=8)
    handles = [
        _IconPatch(_G_DOT, fc="#d8dee6", ec="#c3cbd4", lw=1.0, label="尚未到达"),
        _IconPatch(_G_DOT, fc="none", ec="#9aa5b1", lw=1.2, label="待执行"),
        _IconPatch(_G_DOT, fc="#f58518", ec="white", lw=1.0, label="本周期延期"),
        _IconPatch(_G_DOT, fc="#54a24b", ec="white", lw=1.0, label="本周期完成"),
        _IconPatch(_G_DOT, fc="#7f8c8d", ec="white", lw=1.0, label="此前完成"),
    ]
    # constrained layout 下用 "outside" 定位，图例才会自动预留空间而不压住 x 轴标签
    fig.legend(handles=handles, loc="outside lower center", ncol=5, fontsize=9,
               handler_map={_IconPatch: _IconHandler()}, frameon=False)
    fig.suptitle("动态任务目标生命周期", fontsize=13, fontweight="bold", color="#1f2933")
    return save_figure(fig, Path(out_dir) / "geo_task_lifecycle", formats, dpi=dpi)


# ---------------------------------------------------------------------------
# 图 4：机巢占用与活动 UAV 迁移
# ---------------------------------------------------------------------------

def plot_geo_fleet_transition(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """展示活动 UAV 的起终点迁移以及每周期末机巢占用。

    圆环大小/颜色表示机巢占用率，彩色三角与路径表示实际参与本周期任务的 UAV。
    因而可把“谁飞了、从哪里出发、最后落在哪里、机巢是否拥挤”放在同一张图中。
    """
    nests = list(solution.get("nests") or [])
    tasks = {int(t["id"]): t for t in (solution.get("tasks") or [])}
    cycles = list(solution.get("cycles") or [])
    dem = solution.get("dem")
    if not nests or not cycles:
        return []
    nest_by_id = {int(n["id"]): n for n in nests}

    # 底图宽高比自适应画幅（本图无色条，预留更窄）；地图填满每行
    extent = _dem_view_extent(dem, solution)
    row_h = 5.0
    fig, axes = plt.subplots(
        len(cycles), 1,
        figsize=_row_figsize(extent, row_h, len(cycles), extra_w=1.1),
        squeeze=False, sharex=True, sharey=True, layout="constrained",
    )
    axes = axes.ravel()
    im0 = None
    for index, (ax, cycle) in enumerate(zip(axes, cycles)):
        im = _draw_dem(ax, dem, solution)
        if index == 0:
            im0 = im
        record = _metric_cycle(metrics, int(cycle.get("cycle", -1)))
        occupancy = {int(k): int(v) for k, v in (record.get("nest_occupancy") or {}).items()}

        # 机巢：底层方块固定，外圈编码周期末占用率。
        _draw_nests(ax, nests)
        for nest in nests:
            nid = int(nest["id"])
            used = occupancy.get(nid, 0)
            capacity = max(int(nest.get("capacity", 1)), 1)
            ratio = min(used / capacity, 1.0)
            ring_color = "#e45756" if ratio >= 1.0 else "#f58518" if ratio >= 0.75 else "#54a24b"
            ax.scatter(
                [float(nest["x"])], [float(nest["y"])],
                s=350 + 540 * ratio, facecolors="none", edgecolors=ring_color,
                linewidths=2.0, alpha=0.9, zorder=4,
            )
            ax.annotate(
                f"{used}/{capacity}", (float(nest["x"]), float(nest["y"])),
                textcoords="offset points", xytext=(0, -17), ha="center",
                fontsize=7.2, color=ring_color, fontweight="bold", zorder=9, bbox=_HALO,
            )

        active = list(cycle.get("tours") or [])
        for tour in active:
            uid = int(tour.get("uav_id", -1))
            color = uav_color(uid)
            start = tour.get("start") or [0.0, 0.0]
            start_xy = (float(start[0]), float(start[1]))
            path = [start_xy]
            for tid in tour.get("task_ids") or []:
                task = tasks.get(int(tid))
                if task is not None:
                    path.append((float(task["x"]), float(task["y"])))
            end = nest_by_id.get(int(tour["end_nest_id"])) if tour.get("end_nest_id") is not None else None
            if end is not None:
                path.append((float(end["x"]), float(end["y"])))
            for p0, p1 in zip(path, path[1:]):
                _arrow(ax, p0, p1, color, lw=2.5)
            ax.scatter([start_xy[0]], [start_xy[1]], marker=_G_DRONE, s=140, color=color, edgecolors="white", linewidths=1.3, zorder=7)
            ax.annotate(f"U{uid}", start_xy, textcoords="offset points", xytext=(8, 8), fontsize=7.3, fontweight="bold", zorder=9, bbox=_HALO)
            if end is not None:
                ax.scatter([float(end["x"])], [float(end["y"])], s=120, facecolors="none", edgecolors=color, linewidths=2.0, zorder=8)

        # 标题画进地图左上角（无人机已用四旋翼图标，不再写 ▲/▼ 符号说明）
        _panel_title(
            ax,
            f"周期 {cycle.get('cycle')}｜活动 UAV={len(active)}｜圆环 = 周期末机巢占用",
        )
        _strip_axes(ax)
        ax.set_aspect("equal", adjustable="box")

    _set_scene_lim(axes[0], nests, list(tasks.values()), [], dem, extent=_dem_view_extent(dem, solution))
    # 本图强调迁移与机巢容量，DEM 仅作背景；高程色标见 geo_overview / geo_evolution，
    # 此处不再挤占横向空间。
    handles = [
        _IconPatch(_G_DRONE, fc="#4c78a8", ec="white", lw=1.1, label="活动 UAV（起→终）"),
        Line2D([0], [0], marker="o", color="#54a24b", markerfacecolor="none", markersize=10, label="机巢占用率 < 75%"),
        Line2D([0], [0], marker="o", color="#f58518", markerfacecolor="none", markersize=10, label="机巢占用率 ≥ 75%"),
        Line2D([0], [0], marker="o", color="#e45756", markerfacecolor="none", markersize=10, label="机巢满载"),
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=4, fontsize=9,
               handler_map={_IconPatch: _IconHandler()}, frameon=False)
    fig.suptitle("无人机—机巢迁移与周期末容量状态", fontsize=13, fontweight="bold", color="#1f2933")
    return save_figure(fig, Path(out_dir) / "geo_fleet_transition", formats, dpi=dpi)
