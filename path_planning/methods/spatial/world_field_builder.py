# -*- coding: utf-8 -*-
"""全域建场：场景障碍 → 轴对齐栅格 → 两遍 EDT → z-major SDF。

职责：决定栅格包络（水平取场景有数据区 + pad，竖直取飞行真高带并兜住最高楼顶），
      把建筑棱柱与静态禁飞区交给 ``occupancy_sdf`` 栅格化，产出 ``WorldField``。
边界：不做取样与覆盖检查（``world_field.py``）、不生成走廊。
相关：``occupancy_sdf.py``、``world_field.py``、``common/scenario.py``。

为什么一次算完（而不是每条 OD 现算）：一条 OD 要试多个初值、每个初值可能重规划多轮，
现算时每个航段都要重新栅格化 + 两遍 EDT（2.8 km 约 3–4 s），建场开销会压过优化本身。
全域一次约 85 s，之后每条 OD 取样约 17 ms。代价见 ``docs/architecture.md`` §2。
"""

from __future__ import annotations

import math

import numpy as np

from path_planning.common.grid_spec import GridSpec
from path_planning.methods.spatial.occupancy_sdf import (
    ObstaclePrism,
    build_occupancy,
    build_sdf,
)
from path_planning.methods.spatial.world_field import WorldField

__all__ = [
    "DEFAULT_AGL_MAX_M",
    "DEFAULT_AGL_MIN_M",
    "build_world_field",
    "z_envelope_from_ground",
]

#: 飞行真高带下界（AGL，米）。0 = 从地面起算，保证起降段也在场内。
DEFAULT_AGL_MIN_M = 0.0
#: 飞行真高带上界（AGL，米）；更高部分由"最高楼顶"兜住。
DEFAULT_AGL_MAX_M = 120.0


def z_envelope_from_ground(
    ground_min_m: float, agl_min_m: float, agl_max_m: float
) -> tuple[float, float]:
    """真高带 → 绝对 z 包络。

    天花板按**场内最低地面**算（而不是最高）：任何一列都飞不到``agl_max``以上，
    高地只会更保守。按最高地面算会把层数从 121 撑到 190+，SDF 成本翻倍。
    代价是高地可用真高低于 ``agl_max``，属已知取舍。
    """
    return float(ground_min_m) + float(agl_min_m), float(ground_min_m) + float(agl_max_m)


def obstacle_prisms(
    scenario,
    *,
    z_lo: float,
    z_hi: float,
    include_static_nofly: bool = True,
) -> list[ObstaclePrism]:
    """从场景构出障碍棱柱表（建筑 + 静态禁飞区）。

    **必须与建场共用这一个入口**：真几何净空按同一批棱柱量，若两处各建一套
    （比如底高程一处取质心地面、一处取最小地面），SDF 与真几何会就"这栋楼多高"
    永远吵不起来，但净空差会静默出现 1–2 m。

    Args:
        scenario: ``common.scenario_types.Scenario``
        z_lo, z_hi: 竖直包络，仅用于"禁飞区不设高度界"时的兜底上下界
        include_static_nofly: 有时窗（``active``）的禁飞区不冻结进静态障碍——
            它们属于冲突层的时间维度，不属于几何场。
    """
    prisms: list[ObstaclePrism] = []
    if scenario.buildings is not None and scenario.buildings.count > 0:
        polys = scenario.buildings.polygons
        heights = scenario.buildings.heights
        # 质心一次批量取地面：逐栋单点插值要做 2 万次
        cx = np.array([p.centroid.x for p in polys], dtype=np.float64)
        cy = np.array([p.centroid.y for p in polys], dtype=np.float64)
        base = np.asarray(scenario.ground_height(cx, cy), dtype=np.float64)
        for k, poly in enumerate(polys):
            z_bot = float(base[k])
            z_top = z_bot + float(heights[k])
            prisms.append(
                ObstaclePrism(
                    np.asarray(poly.exterior.coords, dtype=np.float64),
                    z_bot,
                    z_top,
                    kind="building",
                )
            )

    if include_static_nofly:
        for zone in getattr(scenario, "nofly", ()):
            if getattr(zone, "active", None) is not None:
                continue  # 有时窗的禁飞区归冲突层，不冻结进静态场
            poly = _solve_nofly_polygon(zone)
            if poly is None:
                continue
            z_bot = z_lo if zone.z_min is None else float(zone.z_min)
            z_top = z_hi if zone.z_max is None else float(zone.z_max)
            if z_top <= z_bot:
                continue
            prisms.append(
                ObstaclePrism(
                    np.asarray(poly.exterior.coords, dtype=np.float64),
                    z_bot,
                    z_top,
                    kind="nofly",
                )
            )
    return prisms


def build_world_field(
    scenario,
    *,
    spacing: tuple[float, float, float] = (4.0, 4.0, 2.0),
    agl_min_m: float = DEFAULT_AGL_MIN_M,
    agl_max_m: float = DEFAULT_AGL_MAX_M,
    pad_m: float = 0.0,
    include_static_nofly: bool = True,
    keep_occupancy: bool = True,
    sample_bias_m: float = 0.0,
    max_nodes: int = 220_000_000,
) -> WorldField:
    """一次算完全域 occupancy / SDF。

    Args:
        scenario: ``common.scenario_types.Scenario``
        spacing:  体素间距 ``(dx, dy, dz)``
        agl_min_m / agl_max_m: 飞行真高带，决定竖直包络下界与上界
        pad_m:    水平额外外扩（须为间距的公倍数，否则全网格点整体平移）
        include_static_nofly: 静态禁飞区是否作为障碍
        keep_occupancy: 是否保留布尔占位（约多占 1 字节/节点，供离线检查）
        sample_bias_m: 取样保守偏置
        max_nodes: 节点上限（默认 2.2 亿 ≈ 1.8 GB float32）

    Raises:
        ValueError: 间距非正、``pad_m`` 不是间距的公倍数、地面采样含 NaN、包络退化、节点数超限。
    """
    dx, dy, dz = (float(v) for v in spacing)
    if dx <= 0 or dy <= 0 or dz <= 0:
        raise ValueError(f"spacing 必须全为正：{spacing}")
    # pad 是"把原点往外挪 pad_m"，挪的量不是间距整数倍时，整张网格会相对
    # 机巢/任务坐标整体错位（场没错、点查询却落在别的体素上，且错位量随 pad 变）。
    if pad_m < 0:
        raise ValueError(f"pad_m 不能为负：{pad_m}")
    for name, step in (("x", dx), ("y", dy)):
        k = round(float(pad_m) / step)
        if abs(float(pad_m) - k * step) > 1e-9:
            raise ValueError(
                f"pad_m={pad_m:g} 不是 {name} 向间距 {step:g} 的公倍数"
                f"（差 {float(pad_m) - k * step:+g} m；取 {k * step:g} 或 {(k + 1) * step:g}）"
            )

    x0, y0, x1, y1 = (float(v) for v in scenario.data_bounds_xy)
    x0, y0, x1, y1 = x0 - pad_m, y0 - pad_m, x1 + pad_m, y1 + pad_m
    nx = int(math.ceil((x1 - x0) / dx)) + 1
    ny = int(math.ceil((y1 - y0) / dy)) + 1

    gx_axis = x0 + np.arange(nx, dtype=np.float64) * dx
    gy_axis = y0 + np.arange(ny, dtype=np.float64) * dy
    gx, gy = np.meshgrid(gx_axis, gy_axis, indexing="ij")
    ground_m = np.asarray(
        scenario.ground_height(gx.ravel(), gy.ravel()), dtype=np.float64
    ).reshape(nx, ny)
    if not np.all(np.isfinite(ground_m)):
        raise ValueError("全域地面采样出现 NaN：地形存在未填充空洞或场景边界非法")
    ground_min = float(ground_m.min())
    ground_max = float(ground_m.max())

    z_lo, z_hi = z_envelope_from_ground(ground_min, agl_min_m, agl_max_m)
    z_hi = max(z_hi, ground_max) + dz

    prisms = obstacle_prisms(
        scenario, z_lo=z_lo, z_hi=z_hi, include_static_nofly=include_static_nofly
    )
    top_of_buildings = max(
        (p.z_high for p in prisms if p.kind == "building"), default=ground_max
    )
    # 最高楼顶必须在包络内，否则高楼被裁掉一截、SDF 乐观
    z_hi = max(z_hi, top_of_buildings + dz)

    nz = int(math.ceil((z_hi - z_lo) / dz)) + 1
    if nx * ny * nz > max_nodes:
        raise ValueError(
            f"全域场节点数 {nx * ny * nz} 超过上限 {max_nodes}"
            f"（shape={(nx, ny, nz)}）；请放粗 spacing 或收窄 agl 带"
        )

    grid = GridSpec(
        origin=(x0, y0, z_lo), spacing=(dx, dy, dz), shape=(nx, ny, nz), yaw_rad=0.0
    )
    occupancy = build_occupancy(prisms, grid, ground_m)
    sdf_xyz = build_sdf(occupancy, grid.spacing)
    # 转 z-major：在线按层取样时每个平面连续
    sdf = np.ascontiguousarray(sdf_xyz.transpose(2, 0, 1))
    del sdf_xyz

    return WorldField(
        grid=grid,
        sdf=sdf,
        occupancy=occupancy if keep_occupancy else None,
        sample_bias_m=float(sample_bias_m),
    )


def _solve_nofly_polygon(zone):
    """禁飞区顶点 → 合法多边形（``buffer(0)`` 修自交/退化环）。"""
    from shapely.geometry import Polygon

    poly = Polygon(np.asarray(zone.vertices, dtype=np.float64))
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    if poly.geom_type != "Polygon":
        return None
    return poly
