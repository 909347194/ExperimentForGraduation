# -*- coding: utf-8 -*-
"""task_scenario.py — 任务点场景生成（合成任务）。

职责边界与 ``common/scenario.py`` 一致::

    data/                          实测 / 跨实验共享：机巢经纬度 CSV、DEM 栅格
    experiments/expXX/config.yaml  本次实验设定：任务规模、散布、收益、时间窗
    common/task_scenario.py（本模块） 按设定生成 Task，复用机巢的同一套坐标链路

坐标链路（与机巢完全一致，见 ``methods/utils/coordinates.py``）::

    机巢锚点 (x, y) + 随机偏移 --LocalFrame--> 局部 ENU 米制 (x, y)
        --GeoTIFFDEM.sample_elevation_local--> 地形高程 z   <- Task.z

撒点域（``SyntheticTaskConfig.domain``）：

    ``region``         在 ``region_geojson`` 多边形**内**均匀撒点（每个实验各自给出
                       的撒点区域多边形；面积均匀，非凸/带洞也正确）。推荐口径：
                       任务撒点范围由实验者用多边形显式界定。
    ``dem_extent``     在 DEM 有效范围内全域均匀撒点（需求不确定的现实口径）；若设了
                       ``region_geojson`` 则退化为「DEM 包络 ∩ 区域包络内均匀、再裁到多边形」。
    ``nest_anchored``  绕机巢极坐标撒点，半径 ``spread_m``（历史行为，消融对照用）。

之所以默认改全域：任务需求**不是**由机巢位置决定的，全域撒点才对应
"需求在空间上不确定"的真实设定；落在能力约束（航程 / 时间窗）之外的任务
本就应该被可行性预筛判掉，这是约束的真实选择性压力，不是脏数据。
锚定机巢反而是人为缩小问题——盘内任务几乎一定可达，预筛与选择机制没有
压力可测，因此保留它仅作为消融对照。``region`` 域在 ``dem_extent`` 基础上
进一步把撒点范围**显式收紧到实验者给定的多边形**，是口径最清晰的一档。

实测（本仓 data/lasa_chengguan/Chengguan_DEM.tif + 6 机巢，城关区）：DEM 约 28.0×32.2 km，
机巢跨度约 16.1×10.1 km，全域任一点到最近机巢最大约 17.7 km。任务点默认
须落在 ``data.region_geojson``（城关区多边形）内——见
``SyntheticTaskConfig.region_geojson``。

约束压力两源（生产口径 ``uav_max_range=20 km``，2026-10-26 起）：
**航程**——区域边缘到最近机巢往返可超 20 km（单程 >10 km 即真几何不可达），
其比例由机巢布局与 ``uav_max_range`` 决定；**时间窗**——比例由
``deadline_cycles_*`` 与 ``problem.uav_speed`` 共同决定，是实验旋钮而非缺陷。
（旧 80 km 口径下航程 100% 满足、仅时间窗起作用的结论随 20 km 新口径作废。）

当前任务点为**合成**；后续若补 ``data/task_location_data.csv``，
只需把 ``build_synthetic_tasks`` 换成同签名的读取函数，下游无需改动。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from task_allocation.common.types import Nest, Task
from task_allocation.methods.utils import GeoTIFFDEM, LocalFrame

__all__ = [
    "SyntheticTaskConfig",
    "TASK_DOMAINS",
    "DOMAIN_NEST_ANCHORED",
    "DOMAIN_DEM_EXTENT",
    "dem_planar_extent",
    "task_domain_extent",
    "build_synthetic_tasks",
    "build_task_batches",
]

# 撒点域取值
DOMAIN_NEST_ANCHORED = "nest_anchored"
DOMAIN_DEM_EXTENT = "dem_extent"
DOMAIN_REGION = "region"
TASK_DOMAINS = (DOMAIN_NEST_ANCHORED, DOMAIN_DEM_EXTENT, DOMAIN_REGION)


@dataclass
class SyntheticTaskConfig:
    """合成任务设定（全部可由实验 yaml 的 ``tasks:`` 段覆盖）。"""

    n_tasks: int = 10
    seed: int = 42
    # 撒点域：见模块文档。``nest_anchored`` 时 spread_m 生效；
    # ``dem_extent`` 时 spread_m 不参与（全域覆盖由 DEM 范围决定）；
    # ``region`` 时任务在 ``region_geojson`` 多边形内均匀撒点。
    #
    # 【默认值保持历史行为 nest_anchored】未显式写 ``domain`` 的实验
    # （如 exp02，仅写 anchor_to_nests）不得被静默切成全域撒点；
    # 需要全域时必须显式声明 ``tasks.domain: dem_extent``；
    # 需要「多边形内撒点」显式声明 ``tasks.domain: region``。
    domain: str = DOMAIN_NEST_ANCHORED
    # DEM 全域撒点时的内缩量（米）。>0 可避开 DEM 边缘的半像素/插值外推区，
    # 等价于旧方案里的 "dem_buffered"，故不再单列一个域。
    dem_inset_m: float = 0.0
    # 拒绝采样最大重试轮数：落点出 DEM 时重采，超过则回退到机巢锚定
    max_reject_trials: int = 8
    # 相对锚点机巢的散布半径（米）；仅 domain=nest_anchored 生效
    spread_m: float = 15000.0
    # 收益区间（与输电巡检业务对齐：杆塔等级 / 缺陷等级）
    reward_min: float = 1.0
    reward_max: float = 10.0
    # 时间窗（绝对秒数，fallback）：earliest 恒为 0，
    # latest = arrival_time + U(deadline_min, deadline_max)。
    # 当 ``cycle_length`` 给定时，优先使用周期相关的 ``deadline_cycles_*``，
    # 使 latest = arrival_time + U(k_min, k_max) * cycle_length，
    # 让时间窗尺度与滚动时域对齐（见 exp01 配置注释）。
    deadline_min: float = 1800.0
    deadline_max: float = 7200.0
    deadline_cycles_min: float = 0.5
    deadline_cycles_max: float = 2.0
    # 滚动周期长度（秒）。非 None 且 >0 时时间窗按周期倍数生成。
    cycle_length: float | None = None
    # 【遗留别名】True → domain=nest_anchored；False → 绕机巢群中心撒点。
    # 仅为向后兼容旧 yaml 保留，新配置请直接写 ``domain``。
    anchor_to_nests: bool = True
    # ── 时序链（任务执行顺序约束）────────────────────────────────
    # 把一批任务组织成「必须按序执行」的链（业务对应：同一目标的 侦察 →
    # 打击 → 评估，或同一片区域的连续作业必须按先后做）。链内成员**空间
    # 相邻**取（种子 + 最近邻），不额外移动撒点，故撒点分布不变。
    #
    # ``sequence_chain_ratio``：参与链的任务占比（0 = 关闭，默认）。
    # ``sequence_chain_len``：单链任务数（<2 视为关闭）。
    #
    # 判定口径（2026-10-29）：同一条航次内，链内序号小的必须先访问；违反
    # = 硬违约。链被拆到不同无人机 / 不同周期时**不判违约**（跨机跨周期
    # 按绝对完成时刻比对，尚未实现）。
    #
    # 默认 0.0 = 关闭：既有实验的 ``Task.sequence_group`` 恒为 None，
    # 时序约束惰性退化为 0，既有基线逐位不变。
    sequence_chain_ratio: float = 0.0
    sequence_chain_len: int = 2
    # 任务区域门禁（城关区口径）：给出区域多边形 geojson（如
    # ``Chengguan_district.geojson``，相对 task_allocation/data/<scenario>/ 或绝对路径），
    # 任务点必须落在多边形内；落点出区则拒绝采样、在区域包络内重采，超时
    # 残留点回退到机巢位置（机巢恒在区域内）。None = 不做区域限制。
    # 该门禁与 ``domain`` 正交：无论 nest_anchored 还是 dem_extent，最终
    # 任务都只在区域内产生（见 ``build_synthetic_tasks``）。
    region_geojson: str | None = None


def dem_planar_extent(
    dem: GeoTIFFDEM,
    frame: LocalFrame,
    inset_m: float = 0.0,
) -> tuple[float, float, float, float]:
    """DEM 有效范围在局部平面中的包络矩形 ``(xmin, ymin, xmax, ymax)``（米）。

    注意：DEM 是**经纬度**矩形，投到局部 ENU 后是略带梯形的形状，取四角包络
    会略微放大（本仓数据实测约 0.6% 的包络面积落在 DEM 外）。因此包络只能作为
    抽样域，最终仍须用 :func:`_reject_outside_dem` 过滤一次。
    """
    left, bottom, right, top = dem.bounds
    enu = frame.to_enu(
        np.array([top, top, bottom, bottom], dtype=float),
        np.array([left, right, left, right], dtype=float),
        0.0,
    )
    xmin = float(np.min(enu[:, 0])) + inset_m
    xmax = float(np.max(enu[:, 0])) - inset_m
    ymin = float(np.min(enu[:, 1])) + inset_m
    ymax = float(np.max(enu[:, 1])) - inset_m
    if xmax <= xmin or ymax <= ymin:
        raise ValueError(
            f"dem_inset_m={inset_m} 相对 DEM 范围过大，包络退化："
            f"x[{xmin}, {xmax}] y[{ymin}, {ymax}]"
        )
    return (xmin, ymin, xmax, ymax)


def task_domain_extent(
    nests: Sequence[Nest],
    frame: LocalFrame,
    dem: GeoTIFFDEM | None,
    config: SyntheticTaskConfig,
) -> tuple[float, float, float, float]:
    """本配置下任务可能落到的平面范围 ``(xmin, ymin, xmax, ymax)``（米）。

    供下游（如 DEM 代价网格的 extent）在**不生成任务**的前提下把任务域包进去，
    避免任务落到网格外导致三维代价退化到边界值。
    ``domain=nest_anchored`` 时返回机巢包围盒外扩 ``spread_m``；
    ``domain=region`` 时直接返回区域多边形包络（不论 DEM/机巢，撒点范围就是多边形）。
    """
    if not nests:
        raise ValueError("至少需要一个机巢作为任务锚点")
    xs = np.array([n.x for n in nests], dtype=float)
    ys = np.array([n.y for n in nests], dtype=float)
    if _resolve_domain(config) == DOMAIN_REGION:
        renv = region_planar_envelope(config.region_geojson, frame)
        if renv is None:
            raise ValueError("domain=region 需要 region_geojson，但为 None")
        return renv
    if _resolve_domain(config) == DOMAIN_DEM_EXTENT:
        if dem is None:
            raise ValueError("domain=dem_extent 需要 DEM，但 dem 为 None")
        dxmin, dymin, dxmax, dymax = dem_planar_extent(dem, frame, config.dem_inset_m)
        # 全域域（DEM）∪ 机巢：机巢本身恒在 DEM 内，取并集只为健壮
        x0, y0, x1, y1 = (
            min(dxmin, float(xs.min())),
            min(dymin, float(ys.min())),
            max(dxmax, float(xs.max())),
            max(dymax, float(ys.max())),
        )
    else:
        r = max(float(config.spread_m), 1.0)
        x0, y0, x1, y1 = (
            float(xs.min()) - r,
            float(ys.min()) - r,
            float(xs.max()) + r,
            float(ys.max()) + r,
        )
    # 区域多边形门禁：任务域须把区域包络并进来（城关区口径）
    renv = region_planar_envelope(config.region_geojson, frame)
    if renv is not None:
        x0, y0, x1, y1 = min(x0, renv[0]), min(y0, renv[1]), max(x1, renv[2]), max(y1, renv[3])
    return (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# 区域多边形门禁（城关区 geojson）：任务必须在多边形内产生
# ---------------------------------------------------------------------------
# 不依赖 shapely：多边形顶点投到与机巢同一套局部 ENU 平面，用射线法做
# 批量点在多边形判定；geojson 解析用标准库 json。缓存按 (路径, frame) 去重。

_REGION_CACHE: dict = {}


def _ring_ll_to_enu(ring_ll: np.ndarray, frame: "LocalFrame") -> np.ndarray:
    """``[[lon, lat], ...]`` → 局部平面 ``[[x, y], ...]``（米）。"""
    ring_ll = np.asarray(ring_ll, dtype=float)
    lon = ring_ll[:, 0]
    lat = ring_ll[:, 1]
    enu = frame.to_enu(lat, lon, np.zeros_like(lat))
    return np.column_stack([enu[:, 0], enu[:, 1]])


def _pip(ring: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """射线法批量点在多边形环内判定（边界与顶点算内）。

    ``ring`` 形状 ``(N, 2)``（闭合与否均可，末点不要求等于首点）；
    ``pts`` 形状 ``(M, 2)``。返回 ``(M,)`` 布尔：点在环内（含边上/顶点上）为 True。

    主体为射线法穿越计数（半开规则 ``yi > py != yj > py`` 自动把水平边排除为
    不穿越）；对恰好落在边/顶点上的点，穿越计数不保证，故额外做一次
    「点到线段距离为零」的显式边界检测并并入结果。水平边除零由
    ``np.errstate`` 压制（其穿越项恒为 False，结果正确）。
    """
    ring = np.asarray(ring, dtype=float)
    n = ring.shape[0]
    m = np.asarray(pts, dtype=float).shape[0]
    if n < 3:
        return np.zeros(m, dtype=bool)
    px = pts[:, 0]
    py = pts[:, 1]
    rx = ring[:, 0]
    ry = ring[:, 1]
    inside = np.zeros(m, dtype=bool)
    on_edge = np.zeros(m, dtype=bool)
    tol = 1e-9
    j = n - 1
    with np.errstate(divide="ignore", invalid="ignore"):
        for i in range(n):
            yi = ry[i]
            yj = ry[j]
            xi = rx[i]
            xj = rx[j]
            # 穿越计数异或：仅在 (yi>py)!=(yj>py) 且 px 在交点左侧时计一次
            cross = ((yi > py) != (yj > py)) & (
                px < (xj - xi) * (py - yi) / (yj - yi) + xi
            )
            inside = inside ^ cross
            # 显式边界检测：叉积为零且投影参数落在 [0,1] ⇒ 点在该线段上
            dx = xj - xi
            dy = yj - yi
            seg2 = dx * dx + dy * dy
            if seg2 > 0.0:
                cr = dx * (py - yi) - dy * (px - xi)
                t = ((px - xi) * dx + (py - yi) * dy) / seg2
                on_edge |= (np.abs(cr) <= tol * max(seg2 ** 0.5, 1.0)) & (t >= 0.0) & (t <= 1.0)
            j = i
    return inside | on_edge


class RegionPolygon:
    """平面 ENU 米制区域（城关区多边形），用作任务撒点门禁。

    支持 MultiPolygon（每个 Polygon 含一个外部环 + 若干洞环）；点在区域内
    = 落在任一 Polygon 的外部环内且不在任何洞内。
    """

    def __init__(self, polygons: list) -> None:
        # polygons: list[(outer: ndarray(N,2), holes: list[ndarray(M,2)])]
        self._polys = polygons

    def envelope(self) -> tuple[float, float, float, float]:
        """各环顶点包络 ``(xmin, ymin, xmax, ymax)``（米）。"""
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        for outer, holes in self._polys:
            xs.append(outer[:, 0])
            ys.append(outer[:, 1])
            for h in holes:
                xs.append(h[:, 0])
                ys.append(h[:, 1])
        xs = np.concatenate(xs)
        ys = np.concatenate(ys)
        return (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))

    def contains(self, xy: np.ndarray) -> np.ndarray:
        """批量判定 ``xy``（形状 ``(M, 2)``）是否落在区域内。"""
        pts = np.asarray(xy, dtype=float)
        out = np.zeros(pts.shape[0], dtype=bool)
        for outer, holes in self._polys:
            inside = _pip(outer, pts)
            for h in holes:
                inside = inside & (~_pip(h, pts))
            out = out | inside
        return out


def _load_region(geojson_path, frame: "LocalFrame") -> "RegionPolygon | None":
    """加载区域多边形 geojson（一层缓存，按 (路径, frame) 去重）。

    geojson 为 EPSG:4490（CGCS2000，与 WGS84 亚米级一致）经纬度；顶点
    ``.to_enu(lat, lon, 0)`` 投到与机巢同一局部 ENU 平面。
    """
    if not geojson_path:
        return None
    p = Path(geojson_path)
    key = (p.resolve(), id(frame))
    cached = _REGION_CACHE.get(key)
    if cached is not None:
        return cached
    if not p.is_file():
        raise FileNotFoundError(f"区域多边形文件不存在：{p}")

    import json

    with p.open(encoding="utf-8") as fh:
        gj = json.load(fh)

    feats = gj.get("features")
    geoms: list = []
    if feats:
        for f in feats:
            g = f.get("geometry") or f
            geoms.append((g.get("type"), g.get("coordinates")))
    else:
        geoms.append((gj.get("type"), gj.get("coordinates")))

    polys: list = []
    for gtype, coords in geoms:
        if gtype == "Polygon":
            poly_specs = [coords]
        elif gtype == "MultiPolygon":
            poly_specs = coords
        else:
            continue
        for poly in poly_specs:
            outer = _ring_ll_to_enu(np.asarray(poly[0], dtype=float), frame)
            holes = (
                [_ring_ll_to_enu(np.asarray(h, dtype=float), frame) for h in poly[1:]]
                if len(poly) > 1
                else []
            )
            polys.append((outer, holes))

    if not polys:
        raise ValueError(f"geojson 未含 Polygon/MultiPolygon：{p}")
    rp = RegionPolygon(polys)
    _REGION_CACHE[key] = rp
    return rp


def region_planar_envelope(
    region_geojson, frame: "LocalFrame"
) -> "tuple[float, float, float, float] | None":
    """区域多边形在局部平面的包络（供代价网格 extent 并入任务域）。"""
    rp = _load_region(region_geojson, frame)
    return rp.envelope() if rp is not None else None


def _reject_outside_region(
    rng: np.random.Generator,
    xy: np.ndarray,
    region: "RegionPolygon",
    extent: tuple[float, float, float, float],
    max_trials: int,
    fallback_xy: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    """把落在区域多边形外的点重采，返回 ``(xy, 累计重采次数)``。

    ``extent`` 为重采采样域（区域包络或其与 DEM 包络的交）。多次重采仍
    出区时，若有 ``fallback_xy``（机巢位置，恒在区域内）则把残留点回退到
    机巢，保证任务最终产生于区域内。
    """
    xmin, ymin, xmax, ymax = extent
    n = xy.shape[0]
    pending = np.arange(n)
    rejected = 0
    for _ in range(max(1, int(max_trials))):
        inside = region.contains(xy[pending])
        rejected += int((~inside).sum())
        pending = pending[~inside]
        if pending.size == 0:
            break
        xy[pending, 0] = rng.uniform(xmin, xmax, size=pending.size)
        xy[pending, 1] = rng.uniform(ymin, ymax, size=pending.size)
    if pending.size > 0 and fallback_xy is not None:
        fb = np.asarray(fallback_xy, dtype=float)
        xy[pending] = fb[pending % fb.shape[0]]
    return xy, rejected


def _resolve_domain(config: SyntheticTaskConfig) -> str:
    """解析撒点域；旧字段 ``anchor_to_nests`` 作为遗留别名。"""
    domain = str(getattr(config, "domain", DOMAIN_NEST_ANCHORED) or "").strip().lower()
    if domain not in TASK_DOMAINS:
        raise ValueError(f"未知的 tasks.domain={domain!r}，可选：{list(TASK_DOMAINS)}")
    # 未显式指明（仍为历史默认 nest_anchored）时，尊重 anchor_to_nests 的旧语义
    if domain == DOMAIN_NEST_ANCHORED and not config.anchor_to_nests:
        return DOMAIN_NEST_ANCHORED  # 群中心撒点由 _anchors 分支处理
    return domain


def _reject_outside_dem(
    rng: np.random.Generator,
    xy: np.ndarray,
    dem: GeoTIFFDEM,
    frame: LocalFrame,
    extent: tuple[float, float, float, float],
    max_trials: int,
) -> tuple[np.ndarray, int]:
    """把落在 DEM 有效范围外的点重采，返回 ``(xy, 累计重采次数)``。

    包络矩形比 DEM 实际区域略大（经纬线收敛），必须过滤一次。
    """
    xmin, ymin, xmax, ymax = extent
    n = xy.shape[0]
    pending = np.arange(n)
    rejected = 0
    for _ in range(max(1, int(max_trials))):
        lat, lon, _ = frame.to_geodetic(xy[pending, 0], xy[pending, 1], 0.0)
        ok = ~np.isnan(dem.sample_elevation(lat, lon))
        rejected += int((~ok).sum())
        pending = pending[~ok]
        if pending.size == 0:
            break
        xy[pending, 0] = rng.uniform(xmin, xmax, size=pending.size)
        xy[pending, 1] = rng.uniform(ymin, ymax, size=pending.size)
    return xy, rejected


def build_synthetic_tasks(
    nests: Sequence[Nest],
    frame: LocalFrame,
    dem: GeoTIFFDEM | None,
    config: SyntheticTaskConfig,
    *,
    arrival_time: float = 0.0,
    id_offset: int = 0,
    n_tasks: int | None = None,
    seed: int | None = None,
    cycle_length: float | None = None,
) -> list[Task]:
    """生成一批合成任务。

    Args:
        nests:        机巢列表（作锚点 / 决定覆盖范围）。
        frame:        局部 ENU 平面，与 ``scenario.build_nest_scenario`` 的同一实例。
        dem:          DEM 数据源；None 时所有 z = 0。
        config:       合成设定。
        arrival_time: 本批任务的到达时刻。
        id_offset:    任务 id 起始值（保证跨批次不重复）。
        n_tasks:      覆盖 ``config.n_tasks``（分批次时用）。
        seed:         覆盖 ``config.seed``（分批次时用，保证可复现）。
        cycle_length: 覆盖 ``config.cycle_length``。非 None 且 >0 时时间窗按
            周期倍数生成（latest = arrival + U(k_min,k_max)*cycle_length），
            使时间窗尺度与滚动时域对齐；否则退回绝对秒数。

    Returns:
        ``list[Task]``，长度 = n_tasks。
    """
    if not nests:
        raise ValueError("至少需要一个机巢作为任务锚点")

    n = int(n_tasks if n_tasks is not None else config.n_tasks)
    if n < 0:
        raise ValueError(f"n_tasks 不能为负：{n}")
    s = int(seed if seed is not None else config.seed)

    rng = np.random.default_rng(s)
    nest_arr = np.array([[nn.x, nn.y] for nn in nests], dtype=float)
    center = nest_arr.mean(axis=0)
    spread = max(float(config.spread_m), 1.0)

    domain = _resolve_domain(config)
    n_rejected = 0
    region = _load_region(config.region_geojson, frame) if config.region_geojson else None
    region_rejected = 0
    if domain == DOMAIN_DEM_EXTENT:
        if dem is None:
            raise ValueError("domain=dem_extent 需要 DEM，但 dem 为 None")
        dem_ext = dem_planar_extent(dem, frame, float(config.dem_inset_m))
        # 采样域：DEM 包络；若设区域门禁则取与区域包络的交（提升拒绝采样
        # 效率，且出区重采不会反复落在 DEM 内但区域外的条带）。
        extent = dem_ext
        if region is not None:
            renv = region.envelope()
            xmin = max(dem_ext[0], renv[0])
            ymin = max(dem_ext[1], renv[1])
            xmax = min(dem_ext[2], renv[2])
            ymax = min(dem_ext[3], renv[3])
            if xmax <= xmin or ymax <= ymin:
                raise ValueError(
                    f"DEM 包络与区域多边形包络无交，检查数据："
                    f"DEM{dem_ext} 区域{renv}"
                )
            extent = (xmin, ymin, xmax, ymax)
        # 矩形内均匀即面积均匀，无需极坐标 sqrt 修正
        xy = np.column_stack(
            [
                rng.uniform(extent[0], extent[2], size=n),
                rng.uniform(extent[1], extent[3], size=n),
            ]
        )
        xy, n_rejected = _reject_outside_dem(
            rng, xy, dem, frame, extent, config.max_reject_trials
        )
    elif domain == DOMAIN_REGION:
        if region is None:
            raise ValueError("domain=region 需要 region_geojson，但为 None")
        renv = region.envelope()
        # 多边形外接矩形内均匀采样，再裁到多边形内（面积均匀，非凸/带洞正确）
        xy = np.column_stack(
            [
                rng.uniform(renv[0], renv[2], size=n),
                rng.uniform(renv[1], renv[3], size=n),
            ]
        )
        xy, region_rejected = _reject_outside_region(
            rng, xy, region, renv, config.max_reject_trials, fallback_xy=nest_arr
        )
    else:
        # 极坐标撒点：r = U(0, spread) 的平方根分布 → 面内近似均匀
        angles = rng.uniform(0.0, 2.0 * math.pi, size=n)
        radii = spread * np.sqrt(rng.uniform(0.0, 1.0, size=n))
        dx = radii * np.cos(angles)
        dy = radii * np.sin(angles)

        if config.anchor_to_nests:
            anchors = nest_arr[rng.integers(0, len(nest_arr), size=n)]
        else:
            anchors = np.repeat(center.reshape(1, 2), n, axis=0)

        xy = anchors + np.column_stack([dx, dy])
        # 锚定撒点时重采域用区域包络（若设门禁）
        extent = region.envelope() if region is not None else None

    # 区域多边形门禁（城关区 geojson）：落点出区则重采；超时残留点回退
    # 机巢位置（机巢恒在区域内），保证任务最终只产生于区内。
    # domain=region 已在分支内完成裁剪，这里只对 dem_extent / nest_anchored 生效。
    if region is not None and domain != DOMAIN_REGION:
        xy, region_rejected = _reject_outside_region(
            rng,
            xy,
            region,
            extent if extent is not None else region.envelope(),
            config.max_reject_trials,
            fallback_xy=nest_arr,
        )

    rewards = rng.uniform(config.reward_min, config.reward_max, size=n)

    # 时间窗：优先周期相关；否则绝对秒数（fallback）
    cl = cycle_length if cycle_length is not None else config.cycle_length
    if cl and cl > 0:
        span = (
            rng.uniform(config.deadline_cycles_min, config.deadline_cycles_max, size=n)
            * cl
        )
    else:
        span = rng.uniform(config.deadline_min, config.deadline_max, size=n)

    n_fallback = 0
    tasks: list[Task] = []
    for i in range(n):
        x = float(xy[i, 0])
        y = float(xy[i, 1])
        z = 0.0
        fell_back = False
        if dem is not None:
            sampled = dem.sample_elevation_local(x, y, frame)
            z = float(sampled)
            if math.isnan(z):
                # 落点超出 DEM 有效范围：收敛到最近机巢的高程，保证链路不断
                nearest = min(
                    nests, key=lambda nn: (nn.x - x) ** 2 + (nn.y - y) ** 2
                )
                z = float(nearest.z)
                fell_back = True
                n_fallback += 1
        tasks.append(
            Task(
                id=id_offset + i,
                x=x,
                y=y,
                z=z,
                reward=float(rewards[i]),
                earliest=0.0,
                latest=float(arrival_time + span[i]),
                arrival_time=float(arrival_time),
                meta={
                    "domain": domain,
                    "n_rejected": n_rejected,
                    "region_rejected": region_rejected,
                    "z_fallback": fell_back,
                },
            )
        )

    _assign_sequence_chains(tasks, xy, rng, config)
    return tasks


def _assign_sequence_chains(
    tasks: list[Task],
    xy: np.ndarray,
    rng: np.random.Generator,
    config: SyntheticTaskConfig,
) -> int:
    """把一部分任务组织成时序链，返回链数（0 = 未启用）。

    链内成员取「随机种子 + 其空间最近邻」，保证同一链在地理上成簇（同一
    目标/同一片区域的先后作业），且**不移动任何撒点**——只貼标签，撒点
    分布与关闭时完全一致。

    ``sequence_group`` 取链内首个成员的 task id（任务 id 全局唯一 ⇒ 组 id
    跨批次也不会撞）；``sequence_order`` 为链内序号 0..k-1。

    关闭时（ratio<=0 或 len<2 **不消耗任何随机数**），保证既有实验基线
    逐位不变。
    """
    ratio = float(getattr(config, "sequence_chain_ratio", 0.0) or 0.0)
    k = int(getattr(config, "sequence_chain_len", 2) or 0)
    if ratio <= 0.0 or k < 2:
        return 0
    n = len(tasks)
    n_chains = int(n * ratio) // k
    if n_chains <= 0:
        return 0

    remaining = list(range(n))
    chains_built = 0
    for _ in range(n_chains):
        if len(remaining) < k:
            break
        seed_pos = int(rng.integers(0, len(remaining)))
        seed = remaining.pop(seed_pos)
        members = [seed]
        if len(remaining) >= k - 1:
            d = np.sum((xy[remaining] - xy[seed]) ** 2, axis=1)
            near = np.argsort(d, kind="stable")[: k - 1]
            for pos in sorted(near, reverse=True):
                members.append(remaining.pop(int(pos)))
        else:
            break
        gid = int(tasks[seed].id)
        for order, idx in enumerate(members):
            tasks[idx].sequence_group = gid
            tasks[idx].sequence_order = order
            tasks[idx].meta["sequence_chain"] = {
                "group": gid,
                "order": order,
                "size": len(members),
            }
        chains_built += 1
    return chains_built


def build_task_batches(
    nests: Sequence[Nest],
    frame: LocalFrame,
    dem: GeoTIFFDEM | None,
    config: SyntheticTaskConfig,
    per_cycle: Sequence[int],
    cycle_length: float,
    *,
    id_offset: int = 0,
) -> dict[int, list[Task]]:
    """按周期生成任务到达批次。

    Args:
        per_cycle:    每个周期新到达的任务数（下标 0 → 第 1 周期）。
        cycle_length: 周期长度，用于设置各批次的到达时刻与时间窗尺度。

    Returns:
        ``{周期号(1-based): [Task, ...]}``
    """
    batches: dict[int, list[Task]] = {}
    next_id = int(id_offset)
    for idx, count in enumerate(per_cycle):
        cycle = idx + 1
        if count <= 0:
            continue
        arrival = float(idx * cycle_length)
        batch = build_synthetic_tasks(
            nests,
            frame,
            dem,
            config,
            arrival_time=arrival,
            id_offset=next_id,
            n_tasks=int(count),
            seed=int(config.seed) + idx,
            cycle_length=cycle_length,
        )
        batches[cycle] = batch
        next_id += len(batch)
    return batches
