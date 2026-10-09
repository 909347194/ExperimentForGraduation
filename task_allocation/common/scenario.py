# -*- coding: utf-8 -*-
"""scenario.py — 场景实例构建：把 data/ 的机巢位置与 DEM 组装成 Nest 列表。

职责边界（见 ``experiments/README.md``）::

    data/                        实测 / 跨实验共享：机巢经纬度 CSV、DEM 栅格
    experiments/expXX/config.yaml  本次实验设定：用几个机巢、单巢容量、任务规模
    common/scenario.py（本模块）    把上面两者组装成 common.types.Nest / LocalFrame
    methods/                     算法实现，不关心数据从哪来

坐标链路（见 ``methods/utils/coordinates.py``）::

    CSV "29.6472N 91.136532E" --parse--> WGS84 经纬度
        --LocalFrame.to_enu--> 局部 ENU 米制平面 (x, y)   <- Nest.x / Nest.y
        --GeoTIFFDEM.sample_elevation--> 地形高程 z        <- Nest.z

注意：机巢**位置**是实测事实，放 data/；机巢**个数与容量**是实验设定，
放 config.yaml，便于做规模 / 容量灵敏度实验时只改配置不改数据。
"""

from __future__ import annotations

import csv as csv_lib
import math
from dataclasses import dataclass
from pathlib import Path

from task_allocation.common.types import Nest
from task_allocation.methods.utils import GeoTIFFDEM, LocalFrame, parse_coordinate

__all__ = ["NestScenario", "load_nest_latlon", "build_nest_scenario"]

DEFAULT_CAPACITY = 10


@dataclass(frozen=True)
class NestScenario:
    """一次算例的机巢场景。

    attributes:
        nests: 机巢列表（x / y 为局部平面米制坐标，z 为地形高程）
        frame: 机巢坐标所用的局部 ENU 平面（原点默认取机巢群中心）
        dem: 地形数据源；未提供 DEM 时为 None 且所有 z = 0
    """

    nests: list[Nest]
    frame: LocalFrame
    dem: GeoTIFFDEM | None = None

    @property
    def count(self) -> int:
        return len(self.nests)


def load_nest_latlon(
    csv_path: str | Path,
    column: str = "Coordinate",
) -> list[tuple[float, float]]:
    """读取机巢位置 CSV，返回 ``[(纬度, 经度), ...]``。

    CSV 首行为表头，默认在 ``column`` 列（如 ``Coordinate``）中放
    ``29.6472N 91.136532E`` 这类文本坐标，逐行一个机巢。

    兼容 Excel 导出：按 ``utf-8-sig`` 读取以去掉 BOM，列名与字段值都会去空白；
    因此带 BOM 或 CRLF 的 CSV 与纯 UTF-8 的结果一致。
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"机巢位置文件不存在：{path}")
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv_lib.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV 没有表头：{path}")
        # Excel 导出可能带 BOM / 首尾空白，列名统一清洗后再匹配
        field_map = {name.strip().lstrip("\ufeff"): name for name in reader.fieldnames}
        if column not in field_map:
            raise ValueError(
                f"CSV 缺少列 {column!r}，实际列：{list(field_map)}（{path}）"
            )
        key = field_map[column]
        texts = [
            (row.get(key) or "").strip()
            for row in reader
        ]
    texts = [t for t in texts if t]
    if not texts:
        raise ValueError(f"CSV 中没有机巢记录：{path}")
    return [parse_coordinate(t) for t in texts]


def build_nest_scenario(
    csv_path: str | Path,
    *,
    count: int | None = None,
    capacity: int = DEFAULT_CAPACITY,
    column: str = "Coordinate",
    dem_path: str | Path | None = None,
    frame: LocalFrame | None = None,
    id_offset: int = 0,
) -> NestScenario:
    """构建机巢场景。

    参数：
        csv_path: 机巢位置 CSV（``data/nest_location_data.csv``）
        count: 取前 ``count`` 个机巢；None 表示全部
        capacity: 单机巢容量（架）
        column: 坐标列名
        dem_path: DEM GeoTIFF 路径；给出则用它采样机巢高程 z，否则 z = 0
        frame: 指定局部平面原点；默认取选中机巢的经纬度中心
        id_offset: 机巢 id 起始值

    返回：
        :class:`NestScenario`
    """
    if capacity < 1:
        raise ValueError(f"capacity 必须 >= 1：{capacity}")
    if id_offset < 0:
        raise ValueError(f"id_offset 必须 >= 0：{id_offset}")

    coords = load_nest_latlon(csv_path, column=column)
    if count is not None:
        if count < 1:
            raise ValueError(f"count 必须 >= 1：{count}")
        if count > len(coords):
            raise ValueError(f"请求 {count} 个机巢，但 {csv_path} 只有 {len(coords)} 个")
        coords = coords[:count]

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    if frame is None:
        frame = LocalFrame.from_geodetic_points(lats, lons)

    dem = GeoTIFFDEM(dem_path) if dem_path is not None else None

    nests: list[Nest] = []
    for k, (lat, lon) in enumerate(coords):
        enu = frame.to_enu(lat, lon, 0.0)
        x, y = float(enu[0]), float(enu[1])
        if dem is None:
            z = 0.0
        else:
            z = float(dem.sample_elevation(lat, lon))
            if math.isnan(z):
                raise ValueError(
                    f"机巢 {id_offset + k} ({lat}, {lon}) 超出 DEM 范围，采样为 nan"
                )
        nests.append(
            Nest(
                id=id_offset + k,
                x=x,
                y=y,
                z=z,
                capacity=capacity,
                meta={"lat": lat, "lon": lon},
            )
        )

    return NestScenario(nests=nests, frame=frame, dem=dem)
