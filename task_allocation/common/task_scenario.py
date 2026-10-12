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

    ``nest_anchored``  绕机巢极坐标撒点，半径 ``spread_m``（历史行为，消融对照用）
    ``dem_extent``     在 DEM 有效范围内全域均匀撒点（默认，需求不确定的现实口径）

之所以默认改成全域：任务需求**不是**由机巢位置决定的，全域撒点才对应
"需求在空间上不确定"的真实设定；落在能力约束（航程 / 时间窗）之外的任务
本就应该被可行性预筛判掉，这是约束的真实选择性压力，不是脏数据。
锚定机巢反而是人为缩小问题——盘内任务几乎一定可达，预筛与选择机制没有
压力可测，因此保留它仅作为消融对照。

实测（本仓 data/ASTGTMV003_N29E091_dem.tif + 8 机巢）：DEM 约 97.5×110.9 km，
机巢跨度 69×86.7 km，全域任一点到最近机巢最大仅 41.7 km ⇒ 航程约束 100%
满足（120 km 满电，单任务往返 83.4 km）；真正被触发的是**时间窗**
（900~2400 s @ 15 m/s → 约 30% 天然不可达），其比例由 ``deadline_cycles_*``
与 ``problem.uav_speed`` 共同决定，是实验旋钮而非缺陷。

当前任务点为**合成**；后续若补 ``data/task_location_data.csv``，
只需把 ``build_synthetic_tasks`` 换成同签名的读取函数，下游无需改动。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
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
TASK_DOMAINS = (DOMAIN_NEST_ANCHORED, DOMAIN_DEM_EXTENT)


@dataclass
class SyntheticTaskConfig:
    """合成任务设定（全部可由实验 yaml 的 ``tasks:`` 段覆盖）。"""

    n_tasks: int = 10
    seed: int = 42
    # 撒点域：见模块文档。``nest_anchored`` 时 spread_m 生效；
    # ``dem_extent`` 时 spread_m 不参与（全域覆盖由 DEM 范围决定）。
    #
    # 【默认值保持历史行为 nest_anchored】未显式写 ``domain`` 的实验
    # （如 exp02，仅写 anchor_to_nests）不得被静默切成全域撒点；
    # 需要全域时必须显式声明 ``tasks.domain: dem_extent``。
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
    ``domain=nest_anchored`` 时返回机巢包围盒外扩 ``spread_m``。
    """
    if not nests:
        raise ValueError("至少需要一个机巢作为任务锚点")
    xs = np.array([n.x for n in nests], dtype=float)
    ys = np.array([n.y for n in nests], dtype=float)
    if _resolve_domain(config) == DOMAIN_DEM_EXTENT:
        if dem is None:
            raise ValueError("domain=dem_extent 需要 DEM，但 dem 为 None")
        dxmin, dymin, dxmax, dymax = dem_planar_extent(dem, frame, config.dem_inset_m)
        # 全域域（DEM）∪ 机巢：机巢本身恒在 DEM 内，取并集只为健壮
        return (
            min(dxmin, float(xs.min())),
            min(dymin, float(ys.min())),
            max(dxmax, float(xs.max())),
            max(dymax, float(ys.max())),
        )
    r = max(float(config.spread_m), 1.0)
    return (
        float(xs.min()) - r,
        float(ys.min()) - r,
        float(xs.max()) + r,
        float(ys.max()) + r,
    )


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
    if domain == DOMAIN_DEM_EXTENT:
        if dem is None:
            raise ValueError("domain=dem_extent 需要 DEM，但 dem 为 None")
        extent = dem_planar_extent(dem, frame, float(config.dem_inset_m))
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
                    "z_fallback": fell_back,
                },
            )
        )
    return tasks


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
