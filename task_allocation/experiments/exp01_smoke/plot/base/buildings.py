# -*- coding: utf-8 -*-
"""exp01_smoke 绘图底座：**建筑背景层**（加载 → 视口裁剪 → 按高度着色铺底）。

与 ``base/dem.py`` 的关系——**两套互斥的背景**，由 ``base/backdrop.py`` 裁决：

    ``dem.py``        只有地形（高原/山地场景）：真实 ASTER 瓦片 + 山体阴影晕渲
    ``buildings.py``  有建筑（城市场景）：建筑轮廓按**屋顶高度**分层着色铺底

为什么城市场景要让建筑当背景（而不是继续画 DEM）：本实验的三维航程在垂直切面
上取 ``max(地面高程, 地面 + 建筑高)`` 作障碍包络（见
:mod:`task_allocation.methods.utils.building_layer`），城市里主导竖向结构的
**是建筑**（广州实测 1.2~225 m）而非地形（同域起伏不足 10 m）；且城市场景的
solution 降采样 DEM 只有 45x45（约 200 m/格），放大到画布只是糊化。故有建筑时
建筑即底图，DEM 只作为可选地面底纹（默认关，见 ``backdrop.draw_backdrop``）。

实现要点：
    * 一次加载、进程内缓存（20k+ 栋，shapefile 解析约 3 s，多张图共享）；
    * 绘制用**单个 ``PolyCollection``**（逐栋 ``fill`` 会慢到不可用）；
    * 按高度升序绘制：高楼下压、高楼压在上面，读图顺序与高度直觉一致；
    * 视口裁剪：只画与视图范围相交的栋，缩小区间时省掉绝大部分多边形；
    * 色阶 0 → 98 分位，避免个别超高层把整幅压成一片浅色。

兼容判断（任一不满足即回退，**不抛异常**）：
    ① ``data.buildings`` 未声明 / 目录无 ``.shp``  → 该场景无建筑（合法，静默）
    ② 无 ``local_frame`` / 项目根不在 sys.path     → 无法投到局部平面
    ③ shapefile 解析失败（缺 pyshp/shapely/字段）  → 报警一次并回退
    ④ 建筑包围盒与场景/要素范围不相交             → 判为错场景，报警并回退
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from matplotlib.collections import PolyCollection
from matplotlib.colors import LinearSegmentedColormap, PowerNorm

# 只从 theme 取 plt（theme 在 plt 定义之后才 import base，见 theme.py 的导入顺序）；
# 建筑配色在本模块内自持，避免在模块顶层触碰 theme.PALETTE 形成时序耦合。
from ..theme import plt
from . import scene as _scene

__all__ = [
    "_BUILDING_CMAP",
    "BuildingScene",
    "_load_buildings",
    "_draw_buildings",
    "_buildings_extent",
    "_building_legend_patch",
]


#: 城市建筑分层设色（低→高）：浅灰低层 → 中性灰 → 深板岩。
#: 刻意全程低饱和——航迹/任务点用的是饱和色（tab20 派生），建筑必须"退后"，
#: 否则 2 万栋彩色块会把航线完全吃掉。
#: 低段没有取到"近白"：广州建筑高度中位数仅 13.7 m（98 分位 ~50 m），
#: 近白色会让绝大多数建筑在浅底上"看不见"，故浅端压到 #e6eaed 即可辨。
_BUILDING_CMAP = LinearSegmentedColormap.from_list(
    "building_urban",
    [
        (0.00, "#dde3e8"),   # 低层/裙房：浅灰（刻意不是近白，否则看不见）
        (0.18, "#cbd2d9"),
        (0.38, "#b4bcc5"),
        (0.58, "#959faa"),
        (0.78, "#767f8b"),
        (1.00, "#535e6b"),   # 高层：深板岩
    ],
)

#: 色阶非线性（gamma<1）：高度分布重尾（中位 13.7 m、极值 225 m），线性色阶
#: 会把九成建筑压在色带最浅的一头。gamma=0.65 让中低层也能读到深浅差异，
#: 同时不抬高高层——它们本来就在色带末端。
_HEIGHT_GAMMA = 0.65

#: 建筑轮廓线（细 + 半透明：给浅色低层建筑一点"栋"的边界，但不画成网格）
_EDGE_COLOR = "#6f7a86"
_EDGE_LW = 0.25
#: 建筑背景下的"空地/道路"底色（比纯白略灰，读作城市基底）
_GROUND_FACE = "#f3f4f6"


@dataclass(frozen=True)
class BuildingScene:
    """已载入局部米制平面的建筑集合（供绘制与范围计算）。"""

    verts: list[np.ndarray]      # 每栋一个 (n,2) 外环顶点数组（米制 ENU）
    heights: np.ndarray          # 与 verts 等长：屋顶相对当地地面的高度（m）
    bbox: tuple[float, float, float, float]   # (x0, x1, y0, y1)
    count: int
    source: str

    @property
    def height_p98(self) -> float:
        if self.heights.size == 0:
            return 0.0
        return float(np.percentile(self.heights, 98.0))


_BUILDING_CACHE: "dict[tuple | None, BuildingScene | None]" = {}


def _cache_key(solution: dict[str, Any] | None) -> tuple | None:
    """缓存键 = 局部坐标系原点 + 建筑数据路径集合。

    换 solution（另一个 seed 目录/另一次运行）但同一场景时，建筑几何完全一致，
    靠这个键复用解析结果（20k 栋解析 ~3 s，动画多帧/多图共享只算一次）。
    """
    lf_info = (solution or {}).get("local_frame") or {}
    if "lat0" not in lf_info or "lon0" not in lf_info:
        return None
    paths = tuple(str(p) for p in _scene.scene_dirs().get("building_paths") or ())
    return (
        round(float(lf_info["lat0"]), 6),
        round(float(lf_info["lon0"]), 6),
        paths,
    )


def _load(solution: dict[str, Any] | None) -> BuildingScene | None:
    """真正载入建筑：BuildingLayer → 外环顶点 + 高度 + 包围盒。"""
    paths = list(_scene.scene_dirs().get("building_paths") or ())
    if not paths:
        # 该场景没有建筑数据（如 lasa_chengguan）：合法回退，不报警。
        return None
    frame = _scene.local_frame(solution)
    if frame is None:
        _scene.warn_once(
            "buildings_no_frame",
            "建筑底图跳过：solution 无 local_frame（无法投到局部平面）",
        )
        return None
    if not _scene.ensure_project_root():
        _scene.warn_once(
            "buildings_no_root",
            "建筑底图跳过：项目根不在 sys.path（task_allocation 不可导入）",
        )
        return None
    try:
        from task_allocation.methods.utils import BuildingLayer
    except Exception as exc:                                # pragma: no cover
        _scene.warn_once(
            "buildings_import",
            f"建筑底图跳过：导入 BuildingLayer 失败：{type(exc).__name__}: {exc}",
        )
        return None
    try:
        layer = BuildingLayer.from_shapefiles(paths, frame)
    except Exception as exc:                                # pragma: no cover
        _scene.warn_once(
            "buildings_load",
            f"建筑底图跳过：解析 shapefile 失败：{type(exc).__name__}: {exc}",
        )
        return None
    if layer.count == 0:
        _scene.warn_once("buildings_empty", "建筑底图跳过：shapefile 内无有效建筑")
        return None

    verts: list[np.ndarray] = []
    heights: list[float] = []
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for poly, h in zip(layer._polys, layer._heights):
        try:
            xy = np.asarray(poly.exterior.coords, dtype=float)
        except Exception:
            continue
        if xy.shape[0] < 3:
            continue
        verts.append(xy)
        heights.append(float(h))
        bx0, by0, bx1, by1 = poly.bounds
        x0 = min(x0, float(bx0)); x1 = max(x1, float(bx1))
        y0 = min(y0, float(by0)); y1 = max(y1, float(by1))
    if not verts:
        return None
    return BuildingScene(
        verts=verts,
        heights=np.asarray(heights, dtype=float),
        bbox=(x0, x1, y0, y1),
        count=len(verts),
        source=str(paths[0]) if len(paths) == 1 else f"{paths[0]} 等 {len(paths)} 个",
    )


def _load_buildings(solution: dict[str, Any] | None) -> BuildingScene | None:
    """取建筑集合（带进程内缓存）；无建筑/任何兼容失败都返回 None。"""
    key = _cache_key(solution)
    if key in _BUILDING_CACHE:
        return _BUILDING_CACHE[key]
    result = _load(solution)
    _BUILDING_CACHE[key] = result
    return result


def _overlaps(box: tuple[float, float, float, float],
              extent: Sequence[float] | None) -> bool:
    """包围盒与视图范围（left,right,bottom,top）是否相交。"""
    if extent is None:
        return True
    l, r, b, t = (float(v) for v in extent)
    x0, x1, y0, y1 = box
    return not (x1 < l or x0 > r or y1 < b or y0 > t)


def _draw_buildings(
    ax,
    bld: BuildingScene,
    *,
    extent: Sequence[float] | None = None,
    zorder: float = 1.0,
    alpha: float = 0.95,
) -> tuple[Any, int]:
    """把建筑按高度着色铺到 ``ax``，返回 ``(ScalarMappable, 实际绘制栋数)``。

    ``extent`` 为视图范围（imshow 约定 ``(left,right,bottom,top)``），用于视口
    裁剪；``None`` 表示全画。``ScalarMappable`` 供色条使用（建筑高度，单位 m）。
    """
    if bld is None or not bld.verts:
        return None, 0

    # 视口裁剪：先用每栋的包围盒粗筛（比逐栋 Polygon 相交快一个量级）
    keep: list[int] = []
    for i, xy in enumerate(bld.verts):
        xs = xy[:, 0]; ys = xy[:, 1]
        box = (float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max()))
        if _overlaps(box, extent):
            keep.append(i)
    if not keep:
        return None, 0

    keep_idx = np.asarray(keep, dtype=int)
    # 按高度升序绘制：低楼先铺、高楼压在上面（读图时"越高越突出"）
    order = keep_idx[np.argsort(bld.heights[keep_idx], kind="stable")]
    verts = [bld.verts[int(i)] for i in order]
    h_sorted = bld.heights[order]

    vmax = max(float(bld.height_p98), 10.0)
    norm = PowerNorm(gamma=_HEIGHT_GAMMA, vmin=0.0, vmax=vmax)
    colors = _BUILDING_CMAP(norm(h_sorted))

    coll = PolyCollection(
        verts,
        facecolors=colors,
        edgecolors=_EDGE_COLOR,
        linewidths=_EDGE_LW,
        alpha=alpha,
        zorder=zorder,
        closed=True,
    )
    ax.add_collection(coll)
    ax.set_facecolor(_GROUND_FACE)
    ax.grid(False)
    return plt.cm.ScalarMappable(norm=norm, cmap=_BUILDING_CMAP), len(verts)


def _buildings_extent(
    bld: BuildingScene | None,
    nests: Sequence[dict[str, Any]] = (),
    tasks: Sequence[dict[str, Any]] = (),
    uavs: Sequence[dict[str, Any]] = (),
    *,
    pad_ratio: float = 0.03,
) -> tuple[float, float, float, float] | None:
    """建筑背景的视图范围：建筑包络 ∪ 要素包络，再加 3% 留白。

    取并集而不是"只取建筑包络"：任务撒点可能落在建筑群边缘之外（广州实测任务
    比建筑包络外扩约 30 m），只按建筑取景会把边缘任务裁掉。
    """
    if bld is None:
        return None
    x0, x1, y0, y1 = bld.bbox
    xs: list[float] = [x0, x1]
    ys: list[float] = [y0, y1]
    for seq in (nests, tasks, uavs):
        for item in seq or ():
            try:
                xs.append(float(item["x"])); ys.append(float(item["y"]))
            except (KeyError, TypeError, ValueError):
                continue
    l, r, b, t = min(xs), max(xs), min(ys), max(ys)
    pad_x = (r - l) * pad_ratio or 100.0
    pad_y = (t - b) * pad_ratio or 100.0
    return (l - pad_x, r + pad_x, b - pad_y, t + pad_y)


def _building_legend_patch(n_drawn: int) -> Any:
    """图例代理：一个建筑色块（取色阶中部），标注栋数与"色深 ∝ 高度"。"""
    import matplotlib.patches as mpatches

    return mpatches.Patch(
        facecolor=_BUILDING_CMAP(0.62),
        edgecolor=_EDGE_COLOR,
        linewidth=0.8,
        label=f"建筑（{n_drawn} 栋，色深 ∝ 高度）",
    )
