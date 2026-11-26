# -*- coding: utf-8 -*-
"""出图共用的「场景层」：读落盘结果、装配底图、放比例尺/指北针。

只做**读与画背景**，不跑算法：四个 JSON（loop_trace / metrics / audit /
trajectories）加 ``tasks.json`` 就是全部输入；唯一需要重建的是建筑轮廓与地形
（出底图用），由 ``support.field.load_guangzhou`` 提供（命中缓存时秒级）。

配色一律取自 :mod:`plot.theme`，这里不另立颜色。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...support.config import EXP_DIR, default_run_dir, load_config
from ..theme import BUILDING_EDGE, BUILDING_FACE, CYCLE_COLOR, INK, MUTED, UAV_COLOR

#: 实验根（exp01_smoke/）与默认配置
EXP_ROOT = EXP_DIR
CONFIG_PATH = EXP_ROOT / "config.yaml"


@dataclass(frozen=True)
class Ctx:
    """一次出图要用的全部输入。"""

    cfg: dict[str, Any]
    run_dir: Path
    fig_dir: Path
    meta: dict[str, Any]
    trace: dict[str, Any]
    metrics: dict[str, Any]
    audit: dict[str, Any]
    tracks: dict[str, Any]
    task_doc: dict[str, Any]
    scenario: Any = None

    @property
    def tasks(self) -> list[dict[str, Any]]:
        return list(self.task_doc["tasks"])

    @property
    def task_of(self) -> dict[int, dict[str, Any]]:
        return {int(t["id"]): t for t in self.tasks}

    @property
    def nest_sites(self) -> list[dict[str, Any]]:
        """全部机巢站点（含未启用）。"""
        return list(self.meta["context"]["all_nest_sites"])

    @property
    def active_nests(self) -> list[dict[str, Any]]:
        return [s for s in self.nest_sites if s["active"]]


def prepare(
    config: str | Path | None = None,
    run: str | Path | None = None,
    *,
    need_scene: bool = False,
) -> Ctx:
    """读配置 + 落盘结果；``need_scene`` 为真时再建一次场景（建筑/地形）。"""
    cfg = load_config(config)
    run_dir = Path(run) if run else default_run_dir(cfg)
    if not (run_dir / "loop_trace.json").is_file():
        raise FileNotFoundError(
            f"{run_dir} 里没有 loop_trace.json：先跑 "
            "python -u -m orchestration.experiments.exp01_smoke.run"
        )
    json_of = lambda name: json.loads((run_dir / name).read_text(encoding="utf-8"))
    task_doc = json.loads((EXP_ROOT / str(cfg["tasks"]["file"])).read_text(encoding="utf-8"))
    scenario = None
    if need_scene:
        from ...support.field import load_guangzhou

        scenario = load_guangzhou(cfg)
    return Ctx(
        cfg=cfg,
        run_dir=run_dir,
        fig_dir=run_dir / "figures",
        meta=json_of("run_meta.json"),
        trace=json_of("loop_trace.json"),
        metrics=json_of("metrics.json"),
        audit=json_of("audit.json"),
        tracks=json_of("trajectories.json"),
        task_doc=task_doc,
        scenario=scenario,
    )


def uav_meta() -> dict[int, dict[str, Any]]:
    """机号 → {model, nest_id, max_range_m, ...}（只读 ``uav_config.json``，已按 enabled 过滤）。"""
    from ...support.fleet import load_uav_config

    return {int(r["id"]): r for r in load_uav_config()}


# ----------------------------------------------------------------------
# 底图：建筑轮廓 + 地面高程（一次画成集合，两万栋也只画一次）
# ----------------------------------------------------------------------
def draw_buildings(ax, scenario, *, stride: int = 1, alpha: float = 0.85,
                   color: str = BUILDING_FACE, edge: str = BUILDING_EDGE,
                   lw: float = 0.2) -> None:
    from matplotlib.collections import PolyCollection
    import numpy as np

    if scenario is None or scenario.buildings is None:
        return
    polys = scenario.buildings.polygons
    verts = []
    for i, poly in enumerate(polys):
        if stride > 1 and i % stride:
            continue
        xy = np.asarray(poly.exterior.coords, dtype=float)
        if xy.shape[0] >= 3:
            verts.append(xy)
    ax.add_collection(PolyCollection(verts, facecolors=color, edgecolors=edge,
                                    linewidths=lw, alpha=alpha, zorder=1))


def draw_city_backdrop(ax, scenario, *, extent: tuple[float, float, float, float] | None = None,
                       alpha: float = 0.95, zorder: float = 1.0) -> int:
    """建筑底图：按屋顶高度分层设色的**单个** PolyCollection（与 TA 侧 exp01 同款口径）。

    为什么不再用"灰度地形 + 等色建筑"：城区地形起伏不足 10 m，灰度只提供一层噪声；
    建筑才是竖向结构的主体（1.2–225 m）。按高度上色（低→浅灰、高→深板岩）后，
    城市纹理一眼可读，同时全程低饱和，不与饱和色的航迹/任务点抢墨。

    实现沿用 exp01 的三条经验：① 单 collection 一次铺（逐栋 fill 会慢到不可用）；
    ② 按高度升序铺，高楼压在上面；③ 色阶 0→98 分位 + gamma<1，避免个别超高层
    把整幅压成浅色。返回实际铺开的栋数（0 = 无建筑，调用方按"无底图"处理）。
    """
    import numpy as np
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import LinearSegmentedColormap, PowerNorm

    if scenario is None or getattr(scenario, "buildings", None) is None:
        return 0
    polys = list(scenario.buildings.polygons)
    heights = np.asarray(getattr(scenario.buildings, "heights", []), dtype=float)
    if not polys or heights.size != len(polys):
        return 0

    cmap = LinearSegmentedColormap.from_list("city_gray", [
        (0.00, "#e7ecef"), (0.18, "#d8dee3"), (0.38, "#c3cbd3"),
        (0.58, "#a6b0ba"), (0.78, "#8692a0"), (1.00, "#616e7c"),
    ])
    keep: list[int] = []
    for i, poly in enumerate(polys):
        xy = np.asarray(poly.exterior.coords, dtype=float)
        if xy.shape[0] < 3:
            continue
        if extent is not None:
            l, r, b_, t = extent
            if xy[:, 0].max() < l or xy[:, 0].min() > r:
                continue
            if xy[:, 1].max() < b_ or xy[:, 1].min() > t:
                continue
        keep.append(i)
    if not keep:
        return 0

    order = np.asarray(keep)[np.argsort(heights[keep], kind="stable")]
    verts = [np.asarray(polys[int(i)].exterior.coords, dtype=float) for i in order]
    vmax = max(float(np.percentile(heights[order], 98.0)) * 1.25, 12.0)
    norm = PowerNorm(gamma=0.55, vmin=0.0, vmax=vmax)
    ax.add_collection(PolyCollection(
        verts, facecolors=cmap(norm(heights[order])), edgecolors="#8b97a3",
        linewidths=0.2, alpha=0.92, zorder=zorder, closed=True))
    ax.set_facecolor("#f3f4f6")
    return len(verts)


def draw_ground(ax, scenario, *, downsample_m: float = 30.0, alpha: float = 0.45) -> None:
    """地面高程底图（灰度）：只给起伏感，不与航迹抢墨。"""
    import numpy as np

    if scenario is None or scenario.terrain is None:
        return
    xmin, ymin, xmax, ymax = scenario.data_bounds_xy
    n = max(int((xmax - xmin) / downsample_m), 8)
    m = max(int((ymax - ymin) / downsample_m), 8)
    xs = np.linspace(xmin, xmax, n)
    ys = np.linspace(ymin, ymax, m)
    X, Y = np.meshgrid(xs, ys)
    Z = scenario.ground_height(X.ravel(), Y.ravel()).reshape(X.shape)
    ax.imshow(Z, origin="lower", extent=(xmin, xmax, ymin, ymax),
              cmap="Greys", alpha=alpha, zorder=0, vmin=Z.min(), vmax=Z.max())


def scale_bar(ax, length_m: float = 2000.0, *, label: str | None = None,
              color: str = INK, pad_m: float = 60.0) -> None:
    """左下角比例尺（数据坐标，缩放时不会跑位）。"""
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    xr = x1 - x0
    y_ = y0 + 0.045 * (y1 - y0)
    x_ = x0 + 0.04 * xr
    ax.plot([x_, x_ + length_m], [y_, y_], color=color, lw=2.2,
            solid_capstyle="butt", zorder=5, clip_on=False)
    for xe in (x_, x_ + length_m):
        ax.plot([xe, xe], [y_, y_ + 0.012 * (y1 - y0)], color=color, lw=1.2, zorder=5)
    ax.text(x_ + length_m / 2, y_ + 0.018 * (y1 - y0),
            label or f"{length_m / 1000:.1f} km", ha="center", va="bottom",
            fontsize=11, color=color)


def visit_arrow(ax, p_from, p_to, color: str, *, lw: float = 1.5,
                alpha: float = 0.9, zorder: float = 5.4) -> None:
    """一段有向航段：细实线 + **线段中点**的方向三角（屏幕定尺寸）。

    三角放中点而不是端点：端点上站着任务/机巢标记，盖住就等于没画。
    与 ``task_allocation`` 的 ``plot/base/geo._arrow`` 同一技法。
    """
    import math

    from matplotlib.markers import MarkerStyle
    from matplotlib.transforms import Affine2D

    x0, y0 = float(p_from[0]), float(p_from[1])
    x1, y1 = float(p_to[0]), float(p_to[1])
    dx, dy = x1 - x0, y1 - y0
    if math.hypot(dx, dy) < 1e-6:
        return
    ax.plot([x0, x1], [y0, y1], "-", color=color, lw=lw, alpha=alpha,
            solid_capstyle="round", zorder=zorder)
    marker = MarkerStyle(">", transform=Affine2D().rotate_deg(
        math.degrees(math.atan2(dy, dx))))
    size = max(5.5, lw * 3.6 + 1.2)
    ax.scatter([0.5 * (x0 + x1)], [0.5 * (y0 + y1)], marker=marker, s=size ** 2,
               color=color, edgecolors="white", linewidths=0.7, alpha=alpha,
               zorder=zorder + 0.2)


def north_arrow(ax, *, color: str = INK) -> None:
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    x_ = x1 - 0.05 * (x1 - x0)
    y_ = y1 - 0.10 * (y1 - y0)
    ax.annotate("N", xy=(x_, y_ + 0.07 * (y1 - y0)), xytext=(x_, y_),
                ha="center", va="bottom", fontsize=12, fontweight="bold", color=color,
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.2))


__all__ = [
    "EXP_ROOT", "CONFIG_PATH", "Ctx", "prepare", "uav_meta",
    "draw_buildings", "draw_city_backdrop", "draw_ground", "scale_bar",
    "visit_arrow", "north_arrow",
    "UAV_COLOR", "CYCLE_COLOR", "INK", "MUTED",
]
