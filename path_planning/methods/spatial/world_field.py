# -*- coding: utf-8 -*-
"""全域场对象：覆盖契约与三次查询（窗口取样 / 点插值 / 逐层切片）。

职责：持有全域栅格与 z-major SDF，提供窗口取样、点插值与覆盖检查。
边界：不建场（``world_field_builder.py``）、不生成走廊、不判障碍几何。
相关：``field_contract.py``（越界异常）、``field_artifact.py``（指纹）、
      ``common/grid_spec.py``（窗口栅格）。

``sdf`` 按 **z-major ``(nz, nx, ny)``** 存放：按层取样时每个 z 平面是连续内存。
若存 C 序 ``(nx, ny, nz)``，横向步长是 ``nz`` 个元素，1.7 亿节点的取样会掉到
随机访存（实测慢一个量级）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import map_coordinates

from path_planning.common.grid_spec import GridSpec
from path_planning.methods.spatial.field_contract import WindowOutsideField

__all__ = ["WorldField"]


@dataclass(frozen=True)
class WorldField:
    """全域场：轴对齐栅格上的 SDF（米，正 = 自由）。

    Attributes:
        grid:          全域栅格（``yaw_rad`` 恒为 0）
        sdf:           ``(nz, nx, ny)``、``float32``、外正内负
        occupancy:     可选 ``(nx, ny, nz)`` 布尔占位，只作离线检查
        sample_bias_m: 取样保守偏置（从 SDF 中扣掉）
    """

    grid: GridSpec
    sdf: np.ndarray
    occupancy: np.ndarray | None = None
    sample_bias_m: float = 0.0

    def __post_init__(self) -> None:
        nx, ny, nz = self.grid.shape
        if tuple(self.sdf.shape) != (nz, nx, ny):
            raise ValueError(f"sdf 应为 z-major {(nz, nx, ny)}，得到 {self.sdf.shape}")
        if self.occupancy is not None and tuple(self.occupancy.shape) != (nx, ny, nz):
            raise ValueError(
                f"occupancy 应为 {(nx, ny, nz)}，得到 {self.occupancy.shape}"
            )
        if self.grid.yaw_rad != 0.0:
            raise ValueError("全域场必须是轴对齐栅格（yaw_rad=0）")

    # --- 几何 ---------------------------------------------------------

    @property
    def nbytes(self) -> int:
        total = int(self.sdf.nbytes)
        if self.occupancy is not None:
            total += int(self.occupancy.nbytes)
        return total

    @property
    def axis_bounds(self) -> tuple[float, float, float, float, float, float]:
        ox, oy, oz = (float(v) for v in self.grid.origin)
        sx, sy, sz = (float(v) for v in self.grid.spacing)
        nx, ny, nz = self.grid.shape
        return ox, oy, oz, ox + (nx - 1) * sx, oy + (ny - 1) * sy, oz + (nz - 1) * sz

    def summary(self) -> dict[str, Any]:
        return {
            **self.grid.summary(),
            "sdf_range": (round(float(np.min(self.sdf)), 2), round(float(np.max(self.sdf)), 2)),
            "nbytes_mb": round(self.nbytes / 1e6, 1),
            "sample_bias_m": self.sample_bias_m,
        }

    # --- 覆盖契约 -----------------------------------------------------

    def check_coverage(self, window: GridSpec) -> None:
        """窗口八角点须落在全场节点范围内（容差半格），否则抛错。

        容差取 ``0.5 * spacing`` 而不是 0：两套栅格原点未必对齐，角点恰好落在节点
        之间时用 0 会把合法贴边窗口误判为越界；再放大就会放过真正出界 1–2 格的
        窗口，取样静默取到边缘值。

        Raises:
            WindowOutsideField: 窗口任一角点出界超过半格。
        """
        nx, ny, nz = window.shape
        idx = np.array(
            [[i, j, k]
             for i in (0.0, nx - 1.0)
             for j in (0.0, ny - 1.0)
             for k in (0.0, nz - 1.0)],
            dtype=np.float64,
        )
        pts = window.index_to_enu(idx)
        x0, y0, z0, x1, y1, z1 = self.axis_bounds
        sx, sy, sz = (float(v) for v in self.grid.spacing)
        tol = np.array([0.5 * sx, 0.5 * sy, 0.5 * sz])
        lo = np.array([x0, y0]) - tol[:2]
        hi = np.array([x1, y1]) + tol[:2]
        if (
            np.any(pts[:, :2].min(axis=0) < lo)
            or np.any(pts[:, :2].max(axis=0) > hi)
            or float(pts[:, 2].min()) < z0 - tol[2]
            or float(pts[:, 2].max()) > z1 + tol[2]
        ):
            raise WindowOutsideField(
                f"窗口 ENU 范围 {np.round(pts.min(axis=0), 1)}–{np.round(pts.max(axis=0), 1)} "
                f"超出全域场 {np.round([x0, y0, z0], 1)}–{np.round([x1, y1, z1], 1)}"
            )

    # --- 取样 ---------------------------------------------------------

    def window_sdf(self, window: GridSpec) -> np.ndarray:
        """按窗口节点取样，``float32``、形状同 ``window.shape``。

        逐层做：``map_coordinates`` 要三组等长坐标，立体一次调用会把 1.7 亿点展开成
        三个 ``float64`` 数组（约 4 GB）；逐层只有 ``(nx, ny)`` 一组，且相邻层共用
        全场平面可缓存。
        """
        self.check_coverage(window)
        nx, ny, nz = window.shape
        ox, oy, oz = (float(v) for v in window.origin)
        sx, sy, sz = (float(v) for v in window.spacing)
        a0, a1 = window.axes_xy
        gx0, gy0, gz0 = (float(v) for v in self.grid.origin)
        gsx, gsy, gsz = (float(v) for v in self.grid.spacing)
        nz_global = self.grid.shape[2]

        ii = np.arange(nx, dtype=np.float64)[:, None] * sx
        jj = np.arange(ny, dtype=np.float64)[None, :] * sy
        x = ox + ii * a0[0] + jj * a1[0]
        y = oy + ii * a0[1] + jj * a1[1]
        gu = ((x - gx0) / gsx).ravel()
        gv = ((y - gy0) / gsy).ravel()

        out = np.empty((nx, ny, nz), dtype=np.float32)
        planes: dict[int, np.ndarray] = {}

        def plane(k: int) -> np.ndarray:
            cached = planes.get(k)
            if cached is None:
                cached = map_coordinates(
                    self.sdf[k], [gu, gv], order=1, mode="nearest"
                ).reshape(nx, ny).astype(np.float32, copy=False)
                planes[k] = cached
            return cached

        for k in range(nz):
            gz = (oz + k * sz - gz0) / gsz
            k0 = min(max(int(math.floor(gz)), 0), nz_global - 1)
            k1 = min(k0 + 1, nz_global - 1)
            t = float(gz - k0) if k1 != k0 else 0.0
            if k1 == k0:
                out[:, :, k] = plane(k0)
            else:
                out[:, :, k] = (1.0 - t) * plane(k0) + t * plane(k1)
            for stale in [key for key in planes if key < k0 - 1]:
                del planes[stale]

        if self.sample_bias_m:
            out -= np.float32(self.sample_bias_m)
        return out

    def at(self, points: np.ndarray) -> np.ndarray:
        """任意 ENU 点的插值 SDF（场外按边缘值，不外推）。"""
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        idx = self.grid.enu_to_index(pts)
        values = map_coordinates(
            self.sdf, [idx[:, 2], idx[:, 0], idx[:, 1]], order=1, mode="nearest"
        )
        out = np.asarray(values, dtype=np.float64)
        if self.sample_bias_m:
            out -= self.sample_bias_m
        return out

    def at_one(self, x: float, y: float, z: float) -> float:
        return float(self.at(np.array([[x, y, z]]))[0])

    def slice_z(self, z: float) -> np.ndarray:
        """最近一层的二维切片 ``(nx, ny)``，供可视化。"""
        oz = float(self.grid.origin[2])
        sz = float(self.grid.spacing[2])
        nz = self.grid.shape[2]
        k = int(np.clip(round((z - oz) / max(sz, 1e-12)), 0, nz - 1))
        return self.sdf[k]
