# -*- coding: utf-8 -*-
"""exp01_smoke 绘图底座：**背景层调度**（建筑优先，只有地形就画地形）。

一句话规则（用户裁定，2026-10-29）::

    有建筑 → 建筑当背景；只有 DEM → DEM 当背景；都没有 → 纯底

这是**互斥**的两套背景，不是叠加：

    * 城市场景（广州）：竖向障碍结构是建筑（1.2~225 m），地形同域起伏不足 10 m，
      且 solution 里的降采样 DEM 只有 45x45（约 200 m/格），放大 20 倍只会糊化
      画面 —— 建筑即底图；
    * 高原场景（拉萨城关）：没有建筑数据，DEM 就是唯一底图（晕渲 + 分层设色）。

需要"建筑 + 地形"两者同框时，可用 ``dem_under_buildings=True`` 把 DEM 作为建筑
之下的地面底纹（默认关：城市里它几乎不提供信息，只增加一层噪声）。

对外只有三个函数，各图一律走这里，不要各自 import ``dem`` / ``buildings``::

    _draw_backdrop(ax, solution, ...)     画背景（返回 Backdrop）
    _backdrop_extent(solution, ...)       取背景视图范围（与上面**同源**，必然一致）
    Backdrop                             (kind, label, sm, extent, buildings)

``kind`` 取值：``"buildings"`` / ``"dem"`` / ``"none"``；``label`` 是色条标签
（``"建筑高度 (m)"`` / ``"高程 (m)"`` / ``""``），调用方直接拿去标色条，不必再
判断场景类型——**判断只在这一处做**（"注意兼容判断"）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from . import buildings as _buildings
from . import dem as _dem

__all__ = [
    "Backdrop",
    "_draw_backdrop",
    "_backdrop_extent",
    "_backdrop_kind",
]


@dataclass(frozen=True)
class Backdrop:
    """一次背景绘制的结果（供色条 / 图例 / 取景使用）。

    Attributes:
        kind:       ``"buildings"`` / ``"dem"`` / ``"none"``
        label:      色条标签（中文，含单位）；``"none"`` 时为空串
        sm:         色条用的 ``ScalarMappable``；无背景时为 None
        extent:     视图范围 ``(left, right, bottom, top)``；无背景时为 None
        buildings:  实际铺到图上的建筑栋数（0 = 没画建筑）
        note:       一句话底图说明，供标题/图注使用（如 ``"建筑底图"`` / ``"地形底图"``）
    """

    kind: str
    label: str
    sm: Any | None
    extent: tuple[float, float, float, float] | None
    buildings: int = 0
    note: str = "底图"

    @property
    def has_buildings(self) -> bool:
        return self.kind == "buildings" and self.buildings > 0


def _has_dem(solution: dict[str, Any] | None) -> bool:
    dem = (solution or {}).get("dem")
    return bool(dem) and dem.get("elevation") is not None


def _buildings_usable(bld, solution: dict[str, Any] | None,
                      nests, tasks, uavs) -> bool:
    """兼容判断④：建筑包围盒必须和场景要素同域，否则判为拿错了场景数据。"""
    if bld is None:
        return False
    xs: list[float] = []
    ys: list[float] = []
    for seq in (nests, tasks, uavs):
        for item in seq or ():
            try:
                xs.append(float(item["x"])); ys.append(float(item["y"]))
            except (KeyError, TypeError, ValueError):
                continue
    if not xs:
        return True
    x0, x1, y0, y1 = bld.bbox
    # 要素中心落在建筑包络外扩 20% 的框内即认为同域
    margin_x = max((x1 - x0) * 0.20, 1000.0)
    margin_y = max((y1 - y0) * 0.20, 1000.0)
    cx = 0.5 * (min(xs) + max(xs))
    cy = 0.5 * (min(ys) + max(ys))
    return (x0 - margin_x) <= cx <= (x1 + margin_x) and \
           (y0 - margin_y) <= cy <= (y1 + margin_y)


def _draw_backdrop(
    ax,
    solution: dict[str, Any] | None,
    *,
    nests: Sequence[dict[str, Any]] = (),
    tasks: Sequence[dict[str, Any]] = (),
    uavs: Sequence[dict[str, Any]] = (),
    dem_under_buildings: bool = False,
) -> Backdrop:
    """按「建筑优先」规则铺背景，返回 :class:`Backdrop`。

    ``nests/tasks/uavs`` 只用于建筑分支的取景（建筑包络 ∪ 要素包络）与
    「是否同域」的兼容判断；DEМ 分支不需要它们。
    """
    dem = (solution or {}).get("dem")

    bld = _buildings._load_buildings(solution)
    if _buildings_usable(bld, solution, nests, tasks, uavs):
        if dem_under_buildings and _has_dem(solution):
            _dem._draw_dem(ax, dem, solution)               # 地形垫底（可选）
        extent = _buildings._buildings_extent(bld, nests, tasks, uavs)
        sm, n = _buildings._draw_buildings(ax, bld, extent=extent)
        if n > 0:
            return Backdrop("buildings", "建筑高度 (m)", sm, extent, n, "建筑底图")
        # 建筑存在但视口内一栋都没有（极少见）：退回 DEM，不留空底

    if _has_dem(solution):
        sm = _dem._draw_dem(ax, dem, solution)
        return Backdrop(
            "dem", "高程 (m)", sm, _dem._dem_view_extent(dem, solution), 0, "地形底图"
        )
    return Backdrop("none", "", None, None, 0, "底图")


def _backdrop_extent(
    solution: dict[str, Any] | None,
    nests: Sequence[dict[str, Any]] = (),
    tasks: Sequence[dict[str, Any]] = (),
    uavs: Sequence[dict[str, Any]] = (),
) -> tuple[float, float, float, float] | None:
    """取景范围：与 :func:`_draw_backdrop` 同源（同一份建筑/DEM 缓存），必然一致。

    各图应当**先画背景再问范围**（或反过来都行），但绝不能一边用建筑包络、
    一边用 DEM 包围盒——那会露白边或裁掉任务。
    """
    bld = _buildings._load_buildings(solution)
    if _buildings_usable(bld, solution, nests, tasks, uavs):
        ext = _buildings._buildings_extent(bld, nests, tasks, uavs)
        if ext is not None:
            return ext
    return _dem._dem_view_extent((solution or {}).get("dem"), solution)


def _backdrop_kind(
    solution: dict[str, Any] | None,
    nests: Sequence[dict[str, Any]] = (),
    tasks: Sequence[dict[str, Any]] = (),
    uavs: Sequence[dict[str, Any]] = (),
) -> str:
    """只问背景类型（``"buildings"`` / ``"dem"`` / ``"none"``），不绘制。

    供标题/图注提前决定措辞（如动画图标题写「建筑底图」还是「地形底图」）。
    """
    bld = _buildings._load_buildings(solution)
    if _buildings_usable(bld, solution, nests, tasks, uavs):
        return "buildings"
    return "dem" if _has_dem(solution) else "none"
