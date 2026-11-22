# -*- coding: utf-8 -*-
"""障碍棱柱 → 三维占位栅格 → 符号距离场（两遍 EDT）。

职责：把建筑/禁飞区的竖直棱柱栅格化成布尔占位，再用两遍欧氏距离变换得到
      米制、外正内负的 SDF。
边界：只处理"几何 → 栅格"的离散化，不建全域场（那在 ``world_field.py``）、
      不做插值取样（``SdfField.at`` 只做单栅格内的三线性）。
相关：``common/grid_spec.py``（栅格坐标）、``methods/spatial/world_field.py``
      （全域场调用本模块）、``methods/utils/building_layer.py``（棱柱来源）。

三条口径（照参考实现 ``path_planning1/core/occupancy_sdf.py``，不得"顺手改好"）：

1. **第 2 轴是高度**。间距 ``(4,4,2)`` 的含义是水平 4 m、竖直 2 m；轴序接反
   不会抛异常，只会让净空在竖直方向按 4 m 离散、水平按 2 m，判据静默偏移。
2. **正值 = 自由空间**，单位米。
3. **在完整高度上算完再裁带**。若先裁掉飞行带再算 EDT，带底/带顶的障碍距离
   会被栅格边界截断成 0，于是贴带飞的路径被判为"全程贴障碍"。

保守膨胀（净空口径的地基）：每个 footprint 先 ``buffer(半格对角线)``、竖直
区间各外扩半格，再判**格心**是否落入。理由：只测格心时，比格距还窄的建筑可能
一个格心都不落进去（整栋漏检）；且裸格心判据让障碍最多"缩"半格，SDF 于是比
真实几何乐观。膨胀之后障碍只大不小，SDF 成为保守下界，净空不会被虚报。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import shapely
from scipy.ndimage import distance_transform_edt, map_coordinates
from shapely.geometry import Polygon

from path_planning.common.grid_spec import GridSpec

__all__ = [
    "BUFFER_QUAD_SEGS",
    "ObstaclePrism",
    "SdfField",
    "build_occupancy",
    "build_sdf",
]

#: shapely buffer 的象限段数。``quad_segs=2`` 是 8 边形，半径需按内接修正。
BUFFER_QUAD_SEGS = 2


@dataclass(frozen=True)
class ObstaclePrism:
    """一个竖直障碍棱柱。

    Attributes:
        footprint:  水平多边形顶点 ``(n, 2)``（ENU 米制）
        z_low:      底（绝对高程，米）
        z_high:     顶（绝对高程，米）
        kind:       来源标记（``"building"`` / ``"nofly"``），仅作统计
    """

    footprint: np.ndarray
    z_low: float
    z_high: float
    kind: str = "building"

    def __post_init__(self) -> None:
        fp = np.asarray(self.footprint, dtype=np.float64)
        if fp.ndim != 2 or fp.shape[1] != 2 or fp.shape[0] < 3:
            raise ValueError(f"footprint 应为 (n>=3, 2)：{fp.shape}")
        if not (self.z_high > self.z_low):
            raise ValueError(f"z_high({self.z_high}) 必须大于 z_low({self.z_low})")


def build_occupancy(
    prisms: Sequence[ObstaclePrism],
    grid: GridSpec,
    ground_m: np.ndarray | None = None,
    *,
    conservative_buffer: bool = True,
) -> np.ndarray:
    """把棱柱栅格化成布尔占位，``shape = grid.shape``，``True`` = 占据。

    Args:
        prisms: 障碍棱柱
        grid:   目标栅格（可为斜栅格，判格心时走 ``grid.index_to_enu``）
        ground_m: 地面高程 ``(nx, ny)``；给出时**地面当实体**（格点低于地面即占据）
        conservative_buffer: 是否做半格保守膨胀（默认 True，**不要关**；
            关闭仅用于对照实验，会让净空虚报）

    Returns:
        布尔数组 ``(nx, ny, nz)``
    """
    nx, ny, nz = grid.shape
    sx, sy, sz = (float(v) for v in grid.spacing)
    ox, oy, oz = (float(v) for v in grid.origin)
    half_z = 0.5 * sz
    occ = np.zeros((nx, ny, nz), dtype=bool)

    if prisms:
        a0, a1 = grid.axes_xy
        half_xy = 0.5 * float(np.hypot(sx, sy))
        # shapely 的 buffer 多边形内接于半径 r 的圆，边到中心最近只有 r·cos(π/(4·quad_segs))。
        # 要让"膨胀后区域半径 ≥ 格点最大空隙 half_xy"真正成立，名义半径必须再放大
        # 1/cos(...)；否则 quad_segs=2 时对角方向差约 8%，窄长碎片仍可能整栋漏进栅格。
        buf_r = (
            half_xy / math.cos(math.pi / (4.0 * BUFFER_QUAD_SEGS))
            if conservative_buffer
            else 0.0
        )
        ui = np.arange(nx, dtype=np.float64) * sx
        vj = np.arange(ny, dtype=np.float64) * sy
        gx_full = ox + ui[:, None] * a0[0] + vj[None, :] * a1[0]
        gy_full = oy + ui[:, None] * a0[1] + vj[None, :] * a1[1]
        origin_xy = np.array([ox, oy], dtype=np.float64)

        for prism in prisms:
            poly = Polygon(prism.footprint)
            if poly.is_empty:
                continue
            poly = poly.buffer(buf_r, quad_segs=BUFFER_QUAD_SEGS) if buf_r > 0 else poly
            if poly.is_empty:
                continue
            minx, miny, maxx, maxy = poly.bounds
            corners = np.array(
                [[minx, miny], [maxx, miny], [minx, maxy], [maxx, maxy]], dtype=np.float64
            )
            du = (corners[:, 0] - ox) * a0[0] + (corners[:, 1] - oy) * a0[1]
            dv = (corners[:, 0] - ox) * a1[0] + (corners[:, 1] - oy) * a1[1]
            i0 = max(int(np.floor(du.min() / sx)) - 1, 0)
            i1 = min(int(np.ceil(du.max() / sx)) + 1, nx)
            j0 = max(int(np.floor(dv.min() / sy)) - 1, 0)
            j1 = min(int(np.ceil(dv.max() / sy)) + 1, ny)
            if i0 >= i1 or j0 >= j1:
                continue
            k0 = max(int(np.ceil((prism.z_low - half_z - oz) / sz)), 0)
            k1 = min(int(np.floor((prism.z_high + half_z - oz) / sz)), nz - 1)
            if k0 > k1:
                continue
            # 只在该棱柱 bbox 覆盖的一小块上做点在多边形内，成本 ∝ 占位面积
            mask = shapely.contains_xy(
                poly, gx_full[i0:i1, j0:j1], gy_full[i0:i1, j0:j1]
            )
            occ[i0:i1, j0:j1, k0 : k1 + 1] |= mask[:, :, None]

    if ground_m is not None:
        g = np.asarray(ground_m, dtype=np.float64)
        if g.shape != (nx, ny):
            raise ValueError(f"ground_m 形状应为 {(nx, ny)}，得到 {g.shape}")
        z_nodes = oz + np.arange(nz, dtype=np.float64) * sz
        # 格点 ≤ 地面 → 在地面以下 → 占据（否则路径会钻地）
        occ |= g[:, :, None] >= z_nodes[None, None, :]

    return occ


def build_sdf(occupancy: np.ndarray, spacing: tuple[float, float, float]) -> np.ndarray:
    """两遍 EDT 得到有符号距离（米，正 = 自由），``float32``。

    ``edt(x)`` 是"到最近 0 体素的距离"，因此：
    自由区的距离 = ``edt(~occ)``（把障碍当 0），障碍内部深度 = ``edt(occ)``。
    这两个**别接反**——接反了整张 SDF 全是 0，路径会笔直穿楼。

    中间结果先降到 ``float32`` 再算另一半：全场 1.5 亿节点时两份 ``float64``
    同时驻留要 2.4 GB，而降精度后峰值约 1.9 GB。米制距离在 ±10 km 范围内
    ``float32`` 的绝对误差 < 1 mm，远小于体素尺寸。
    """
    sp = tuple(float(v) for v in spacing)
    d_body = distance_transform_edt(occupancy, sampling=sp).astype(np.float32)
    d_free = distance_transform_edt(~occupancy, sampling=sp).astype(np.float32)
    out = np.where(occupancy, -d_body, d_free).astype(np.float32)
    return out


@dataclass(frozen=True)
class SdfField:
    """单张栅格上的 SDF 查询封装（三线性 + 段内最值）。

    只服务于"一张栅格"（全域场或某个走廊）。全域截取走 ``world_field``。

    Attributes:
        grid: 栅格几何
        sdf:  与 ``grid.shape`` 同形的距离值
    """

    grid: GridSpec
    sdf: np.ndarray

    def __post_init__(self) -> None:
        if tuple(self.sdf.shape) != tuple(self.grid.shape):
            raise ValueError(
                f"sdf shape {self.sdf.shape} 与 grid.shape {self.grid.shape} 不一致"
            )
        if not np.issubdtype(self.sdf.dtype, np.floating):
            raise ValueError(f"sdf 必须是浮点数组，得到 {self.sdf.dtype}")

    @property
    def occupied_ratio(self) -> float:
        return float(np.count_nonzero(self.sdf < 0.0)) / float(self.sdf.size)

    def at(self, points: np.ndarray) -> np.ndarray:
        """ENU 点处的插值 SDF，形状同 ``points.shape[:-1]``。

        栅格外按**边缘值**（``mode="nearest"``）而不是外推：外推会在窗口边缘
        编造出"越远越安全"的假象，而真实答案是"这里没有数据"。
        调用方若需要区分，应先自行用 ``grid.covers`` 判范围。
        """
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        idx = self.grid.enu_to_index(pts)
        values = map_coordinates(
            self.sdf, [idx[:, 0], idx[:, 1], idx[:, 2]], order=1, mode="nearest"
        )
        return np.asarray(values, dtype=np.float64)

    def at_one(self, x: float, y: float, z: float) -> float:
        return float(self.at(np.array([[x, y, z]]))[0])

    def segment_min(self, p0, p1, sample_step_m: float) -> float:
        """线段上按 ``sample_step_m`` 采样的最小 SDF（净空复核的统一入口）。"""
        a = np.asarray(p0, dtype=np.float64)
        b = np.asarray(p1, dtype=np.float64)
        length = float(np.linalg.norm(b - a))
        n = max(2, int(np.ceil(length / max(sample_step_m, 1e-6))) + 1)
        ts = np.linspace(0.0, 1.0, n)[:, None]
        return float(self.at(a[None, :] + ts * (b - a)[None, :]).min())
