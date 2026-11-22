# -*- coding: utf-8 -*-
"""规则三维栅格坐标契约（轴对齐与斜栅格统一）。

职责：描述一张规则 ENU 三维栅格的**几何**：原点、间距、形状、水平朝向；
      提供"下标 ↔ ENU 连续坐标"的双向换算与包络。
边界：只做几何换算，不持有数据、不做插值、不做障碍判定。
相关：``methods/spatial/occupancy_sdf.py``（按本栅格栅格化障碍）、
      ``methods/spatial/world_field.py``（全域场用它描述全场）、
      ``methods/spatial/corridor_window.py``（走廊用它描述斜栅格）。

为什么要"朝向"这一维：走廊沿起终点连线生成，其栅格轴不是 ENU 的 x/y 轴，
而是把 ``(x, y)`` 绕原点转过 ``yaw_rad``。全域场是轴对齐的 ``yaw_rad=0``，
走廊是斜的——两者必须用同一个类型描述，否则取样代码要写两套，而两套代码
迟早在"下标 0 到底在原点还是格心"这种细节上分歧。

轴序约定：``shape = (nx, ny, nz)``，**第 2 轴是高度**（``z``）。这条不能反：
``methods/spatial/occupancy_sdf.py`` 的两遍 EDT 与 ``sdf`` 的 z-major 转置
全都依赖它，接反了不会抛异常，只会让"水平 4 m、竖直 2 m"的间距被当成
"水平 2 m、竖直 4 m"，净空判据静默偏乐观/悲观。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = ["GridSpec"]


@dataclass(frozen=True)
class GridSpec:
    """规则三维栅格。

    Attributes:
        origin:  下标 (0,0,0) 处的 ENU 坐标 ``(x, y, z)``（米）
        spacing: 三个轴的步长 ``(sx, sy, sz)``（米，必须全为正）
        shape:   节点数 ``(nx, ny, nz)``
        yaw_rad: 水平栅格相对 ENU x 轴的旋转（逆时针，弧度）；0 = 轴对齐
    """

    origin: tuple[float, float, float]
    spacing: tuple[float, float, float]
    shape: tuple[int, int, int]
    yaw_rad: float = 0.0

    def __post_init__(self) -> None:
        if len(self.shape) != 3 or min(self.shape) < 1:
            raise ValueError(f"shape 必须是三个正整数：{self.shape}")
        if min(self.spacing) <= 0:
            raise ValueError(f"spacing 必须全为正：{self.spacing}")
        if len(self.origin) != 3:
            raise ValueError(f"origin 必须是三维：{self.origin}")

    # --- 轴 -----------------------------------------------------------

    @property
    def axes_xy(self) -> tuple[np.ndarray, np.ndarray]:
        """水平两轴的单位向量 ``(a0, a1)``（分别沿 i、j 方向）。"""
        c, s = math.cos(self.yaw_rad), math.sin(self.yaw_rad)
        a0 = np.array([c, s], dtype=np.float64)
        a1 = np.array([-s, c], dtype=np.float64)
        return a0, a1

    @property
    def node_count(self) -> int:
        nx, ny, nz = self.shape
        return int(nx * ny * nz)

    @property
    def bounds(self) -> tuple[float, float, float, float, float, float]:
        """``(xmin, ymin, zmin, xmax, ymax, zmax)``（节点包络，轴对齐包围盒）。

        斜栅格时取四角的包络：这不是"栅格自身范围"，而是它在 ENU 下的外接盒，
        用于快速剔除，**不可**当作有效下标范围使用。
        """
        nx, ny, nz = self.shape
        sx, sy, sz = self.spacing
        ox, oy, oz = self.origin
        a0, a1 = self.axes_xy
        xs = np.array([0.0, (nx - 1) * sx])
        ys = np.array([0.0, (ny - 1) * sy])
        corners_x = (
            ox + xs[:, None] * a0[0] + ys[None, :] * a1[0]
        ).ravel()
        corners_y = (
            oy + xs[:, None] * a0[1] + ys[None, :] * a1[1]
        ).ravel()
        return (
            float(corners_x.min()),
            float(corners_y.min()),
            float(oz),
            float(corners_x.max()),
            float(corners_y.max()),
            float(oz + (nz - 1) * sz),
        )

    # --- 换算 ---------------------------------------------------------

    def index_to_enu(self, idx: np.ndarray) -> np.ndarray:
        """下标 ``(n,3)`` → ENU 坐标 ``(n,3)``。"""
        i = np.atleast_2d(np.asarray(idx, dtype=np.float64))
        if i.shape[1] != 3:
            raise ValueError(f"idx 应为 (n,3)：{i.shape}")
        sx, sy, sz = self.spacing
        ox, oy, oz = self.origin
        a0, a1 = self.axes_xy
        x = ox + i[:, 0] * sx * a0[0] + i[:, 1] * sy * a1[0]
        y = oy + i[:, 0] * sx * a0[1] + i[:, 1] * sy * a1[1]
        z = oz + i[:, 2] * sz
        return np.column_stack([x, y, z])

    def enu_to_index(self, pts: np.ndarray) -> np.ndarray:
        """ENU 坐标 ``(n,3)`` → 连续下标 ``(n,3)``（可为小数、可越界）。

        越界**不夹持**：调用方要靠越界判定"窗口是否落在场内"，夹持会把
        部署事故悄悄变成一个贴边的错误解。
        """
        p = np.atleast_2d(np.asarray(pts, dtype=np.float64))
        if p.shape[1] != 3:
            raise ValueError(f"pts 应为 (n,3)：{p.shape}")
        sx, sy, sz = self.spacing
        ox, oy, oz = self.origin
        a0, a1 = self.axes_xy
        dx = p[:, 0] - ox
        dy = p[:, 1] - oy
        u = (dx * a0[0] + dy * a0[1]) / sx
        v = (dx * a1[0] + dy * a1[1]) / sy
        w = (p[:, 2] - oz) / sz
        return np.column_stack([u, v, w])

    def covers(self, pts: np.ndarray, *, tol_cells: float = 0.0) -> np.ndarray:
        """逐点判断是否落在节点范围内 ``[0, shape-1]``（可放宽 ``tol_cells`` 格）。"""
        idx = self.enu_to_index(pts)
        nx, ny, nz = self.shape
        lo = -tol_cells
        return np.all(
            (idx >= lo) & (idx <= np.array([nx - 1, ny - 1, nz - 1]) + tol_cells),
            axis=1,
        )

    def summary(self) -> dict[str, float | int | tuple]:
        return {
            "origin": tuple(round(float(v), 2) for v in self.origin),
            "spacing": tuple(float(v) for v in self.spacing),
            "shape": tuple(int(v) for v in self.shape),
            "yaw_deg": round(math.degrees(self.yaw_rad), 3),
            "nodes": self.node_count,
        }
