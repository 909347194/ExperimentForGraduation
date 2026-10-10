# -*- coding: utf-8 -*-
"""task_scenario.py — 任务点场景生成（合成任务）。

职责边界与 ``common/scenario.py`` 一致::

    data/                          实测 / 跨实验共享：机巢经纬度 CSV、DEM 栅格
    experiments/expXX/config.yaml  本次实验设定：任务规模、散布、收益、时间窗
    common/task_scenario.py（本模块） 按设定生成 Task，复用机巢的同一套坐标链路

坐标链路（与机巢完全一致，见 ``methods/utils/coordinates.py``）::

    机巢锚点 (x, y) + 随机偏移 --LocalFrame--> 局部 ENU 米制 (x, y)
        --GeoTIFFDEM.sample_elevation_local--> 地形高程 z   <- Task.z

之所以锚在机巢附近：巡检任务本就沿输电线分布于机巢覆盖范围内，
直接全域均匀撒点会得到大量无人机够不着的任务，不利于验证分配链路。

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
    "build_synthetic_tasks",
    "build_task_batches",
]


@dataclass
class SyntheticTaskConfig:
    """合成任务设定（全部可由实验 yaml 的 ``tasks:`` 段覆盖）。"""

    n_tasks: int = 10
    seed: int = 42
    # 相对锚点机巢的散布半径（米）
    spread_m: float = 15000.0
    # 收益区间（与输电巡检业务对齐：杆塔等级 / 缺陷等级）
    reward_min: float = 1.0
    reward_max: float = 10.0
    # 时间窗：earliest 恒为 0，latest = arrival_time + U(deadline_min, deadline_max)
    deadline_min: float = 1800.0
    deadline_max: float = 7200.0
    # 任务均匀锚定到机巢；False 时绕机巢群中心撒点
    anchor_to_nests: bool = True


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
    deadlines = rng.uniform(config.deadline_min, config.deadline_max, size=n)

    tasks: list[Task] = []
    for i in range(n):
        x = float(xy[i, 0])
        y = float(xy[i, 1])
        z = 0.0
        if dem is not None:
            sampled = dem.sample_elevation_local(x, y, frame)
            z = float(sampled)
            if math.isnan(z):
                # 落点超出 DEM 有效范围：收敛到最近机巢的高程，保证链路不断
                nearest = min(
                    nests, key=lambda nn: (nn.x - x) ** 2 + (nn.y - y) ** 2
                )
                z = float(nearest.z)
        tasks.append(
            Task(
                id=id_offset + i,
                x=x,
                y=y,
                z=z,
                reward=float(rewards[i]),
                earliest=0.0,
                latest=float(arrival_time + deadlines[i]),
                arrival_time=float(arrival_time),
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
        cycle_length: 周期长度，用于设置各批次的到达时刻。

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
        )
        batches[cycle] = batch
        next_id += len(batch)
    return batches
