# -*- coding: utf-8 -*-
"""exp01_smoke 绘图底座：**DEM 高程底图**（取数 → 补洞 → 晕渲 → 铺底）。

与 ``base/geo.py`` 的分工：

    ``base/dem.py``   **数据**：从哪里取高程（真实 ASTER 瓦片 / solution 里的降采样栅格）、
                      瓦片外的 NaN 怎么补、怎么平滑、怎么分页——一句话：**拿到一张能画的
                      高程矩阵 + 视图范围**（``_get_elevation`` / ``_dem_view_extent``）
    ``base/geo.py``   **绘制**：图标、图例、图层与排版

地形并非随手一张jpg：隐含的前提是**地形真的参与代价估算**（见 config 的 ``cost`` 段，
坡度/爬升能耗），所以底图必须如实反映真实地形，而不是装饰。取不到真实瓦片时退回
``solution.json`` 里的降采样 dem，图依然成立，只是精度下降。

做法参照 matplotlib 官方 *topographic hillshading* 示例，并对高原场景做了三处适配：

    1. 自定分层设色 ``_HYPSO``：避开原生 ``terrain`` 低段的"水蓝/鲜绿"——在
       3500–5700 m 高原，鲜绿会被误读成植被/水系；
    2. ``LightSource`` 的 dx/dy 取**真实米制格距**，``vert_exag`` 才对应真实坡度；
    3. 2%–98% 分位拉伸，避免极值把色带两端压扁。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.colors import LightSource, LinearSegmentedColormap, Normalize

from ..theme import plt

__all__ = [
    "_HYPSO",
    "_draw_dem",
    "_get_elevation",
    "_dem_view_extent",
]


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
# 真实 DEM 瓦片（ASTER GDEM v3）采样：优先用原始 tif 作底图
# ---------------------------------------------------------------------------

def _ensure_project_root() -> bool:
    """把**项目根**（含 ``task_allocation`` 包的目录）加入 ``sys.path``。

    这是「DEM 突然变糊」的根因防线。``_sample_real_dem`` 里
    ``from task_allocation.methods.utils import GeoTIFFDEM`` 依赖项目根在
    ``sys.path`` 上；而这一条件**只在从项目根启动时才成立**（``python run.py``、
    ``python -m pytest``）。一旦换成

        python -c "from plot import render_from_results; ..."   # cwd=实验目录
        cd 实验目录 && python 其它脚本

    之类入口，``sys.path[0]`` 是实验目录而非项目根，import 抛
    ``ModuleNotFoundError``，被 ``except Exception: return None`` 静默吞掉 ——>
    底图回退到 ``solution.json`` 里 115x101 的降采样栅格（约 880 m/格），
    在 1200~2800 px 的画布上被放大 10~24 倍，观感就是「DEM 又变模糊了」。

    与其要求每个入口都记得加路径，**在真正需要 import 的这里兜底**：向上找
    含 ``task_allocation/__init__.py`` 的目录并插入。返回是否成功。
    """
    import sys as _sys

    try:
        import task_allocation  # noqa: F401  已可导入就不动 sys.path
        return True
    except Exception:
        pass
    here = Path(__file__).resolve()
    proj = here
    while proj.parent != proj:
        if (proj / "task_allocation" / "__init__.py").is_file():
            if str(proj) not in _sys.path:
                _sys.path.insert(0, str(proj))
            return True
        proj = proj.parent
    return False


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
    # 数学上到不了这一步：整行全 NaN 会被列方向补掉，整列全 NaN 意味着全矩阵
    # 全 NaN（已提前返回）。留一个安全网，换成全局最小值比 0 更不像"假地形"。
    if (~np.isfinite(out)).any():
        out[~np.isfinite(out)] = float(np.nanmin(out))
    return out


_DEM_SAMPLE_CACHE: "dict[tuple | None, Any]" = {}
_WARNED_REASONS: "set[str]" = set()


def _warn_fallback(reason: str) -> None:
    """底图回退到降采样栅格时**大声报警**（同原因每进程只报一次）。

    历史教训：回退曾经完全静默，表现为「图能出，但 DEM 糊了」——115x101 的
    solution 栅格被放大 10~24 倍。任何回退都必须留下可检索的痕迹。
    """
    if reason in _WARNED_REASONS:
        return
    _WARNED_REASONS.add(reason)
    print(
        f"[exp01][DEM][warn] 底图回退到 solution.json 的降采样栅格"
        f"（约 880 m/格，放大后发糊）。原因：{reason}",
        flush=True,
    )


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

    视图 = **瓦片真实覆盖范围（ENU 包络）**，满幅铺底到真实地形边界——
    不再与「场景包围盒」相交并外扩夹紧。exp01 场景盒比瓦片略大（约 2 km/边），
    旧逻辑在瓦片四周留下一圈「最近邻延拓 + 10 次盒滤波」补洞带，观感模糊且
    已非真实 ASTER 地形；本图要求「完全按瓦片范围画」，故直接取瓦片四角包络。
    返回的 ``extent`` 为 imshow 约定的 ``(left, right, bottom, top)``。

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
        _warn_fallback("未找到 ASTER 瓦片 tif")
        return None
    if not _ensure_project_root():
        _warn_fallback("项目根不在 sys.path（task_allocation 不可导入）")
        return None
    try:
        from task_allocation.methods.utils import GeoTIFFDEM, LocalFrame
    except Exception as exc:                                # pragma: no cover
        _warn_fallback(f"导入 GeoTIFFDEM 失败：{type(exc).__name__}: {exc}")
        return None

    try:
        lf = LocalFrame(float(lf_info["lat0"]), float(lf_info["lon0"]))
        gt = GeoTIFFDEM(str(tif))
    except Exception as exc:                                # pragma: no cover
        _warn_fallback(f"打开瓦片失败：{type(exc).__name__}: {exc}")
        return None

    # 瓦片四角 -> 米制 ENU 包络
    tl, tb, tr, tt = gt.bounds  # (left, bottom, right, top)，单位：度
    corner_lat = np.array([tb, tb, tt, tt])
    corner_lon = np.array([tl, tr, tl, tr])
    enu = lf.to_enu(corner_lat, corner_lon, 0.0)
    tx0, tx1 = float(enu[..., 0].min()), float(enu[..., 0].max())
    ty0, ty1 = float(enu[..., 1].min()), float(enu[..., 1].max())

    # 视图 = 瓦片真实覆盖范围（ENU 包络），满幅铺底到真实地形边界。
    # 不再与「场景盒」相交并外扩夹紧：exp01 场景盒比瓦片略大，旧逻辑在瓦片
    # 四周留下约 2 km 的「最近邻延拓 + 10 次盒滤波」补洞带，观感模糊且已非
    # 真实 ASTER 地形。任务/机巢全部落在瓦片内，全幅瓦片即可完整容纳并留边。
    vx0, vx1 = tx0, tx1
    vy0, vy1 = ty0, ty1
    if vx1 - vx0 < 1000.0 or vy1 - vy0 < 1000.0:
        return None  # 瓦片过窄，退回降采样 dem

    width_m, height_m = vx1 - vx0, vy1 - vy0
    # 目标格网 ~40 m，最长边封顶 2500 px：exp01 场景约 100 km，~2500 px 与满幅
    # 动图显示宽（~2800 px）近 1:1，DEM 不再被双线性放大糊化；此前的 150 m/1000px
    # 上限只产出 ~640x740 源栅格，在 1200~2800 px 显示区被放大 2~4 倍而发糊。
    px = max(240, min(2500, int(round(width_m / 40.0))))
    py = max(240, min(2500, int(round(height_m / 40.0))))
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


def _elevation_cache_key(dem, solution) -> "tuple | None":
    """采样缓存键：包围盒 + 局部坐标系原点。

    只按 bounds 缓存不够：``solution`` 的 local_frame 决定米制坐标的落位，
    同一个 bounds 换个原点就是另一套 ENU；且「取数失败」也要缓存对，
    否则一次 solution=None 的失败结果会把后续正常 solution 的结果挡在缓存外。
    无 bounds 时返回 None（``_sample_real_dem`` 必然返回 None，可安全缓存）。
    """
    bounds = (dem or {}).get("bounds")
    if not bounds:
        return None
    lf_info = (solution or {}).get("local_frame") or {}
    return (
        *(round(float(v), 1) for v in bounds),
        round(float(lf_info.get("lat0", 0.0)), 4),
        round(float(lf_info.get("lon0", 0.0)), 4),
    )


def _get_elevation(dem, solution):
    """取底图高程：优先真实瓦片采样（带进程内缓存），失败回退 solution dem。"""
    key = _elevation_cache_key(dem, solution)
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
    bounds = (dem or {}).get("bounds")
    if bounds:
        left, bottom, right, top = (float(v) for v in bounds)
        return (left, right, bottom, top)
    return None


def _draw_dem(ax, dem: dict[str, Any] | None, solution: dict[str, Any] | None = None):
    """画 2D 平面 DEM 底图：真实 ASTER 高程 + 山体阴影晕渲。

    优先用真实 GeoTIFF 瓦片（按场景裁剪、~150 m 格网）作底图，
    退化为 solution.json 里的降采样 dem。返回 ``ScalarMappable`` 供色条使用；
    DEM 缺失时返回 None。

    做法参照 matplotlib 官方 *topographic hillshading* 示例：
    ``LightSource(azdeg=315, altdeg=45)`` 山体阴影，dx/dy 取与高程同单位的
    米制格距，使 ``vert_exag`` 反映真实坡度；再叠加高原分层设色（``_HYPSO``）。
    """
    # 注意用 ``is None`` 判空：elevation 可能是 ndarray，``not arr`` 会抛
    # "truth value of an array is ambiguous"。
    if not dem or dem.get("elevation") is None:
        return None

    sampled = _get_elevation(dem, solution) if solution is not None else None
    if sampled is not None:
        elev, extent = sampled
    else:
        bounds = dem.get("bounds")
        if not bounds:
            return None  # 无包围盒就无法定位，宁可不画
        left, bottom, right, top = (float(v) for v in bounds)
        extent = (left, right, bottom, top)
        elev = np.atleast_2d(np.asarray(dem["elevation"], dtype=float))
    if elev.size == 0 or not np.isfinite(elev).any():
        return None

    n_rows, n_cols = elev.shape
    dx = max((extent[1] - extent[0]) / max(n_cols - 1, 1), 1e-6)
    dy = max((extent[3] - extent[2]) / max(n_rows - 1, 1), 1e-6)
    # 2%–98% 分位拉伸，避免极值把色带两端压扁
    vmin, vmax = np.nanpercentile(elev, [2.0, 98.0])
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmin, vmax = float(np.nanmin(elev)), float(np.nanmax(elev))
    if vmax <= vmin:                       # 全平原：给一个极小色阶，避免 norm 退化
        vmax = vmin + 1.0
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