# -*- coding: utf-8 -*-
"""三维场景的几何后端（VTK）：地形网格 + 建筑棱柱（``scene_3d.py`` 的唯一几何来源）。

职责：把整片研究区（`data_bounds_xy`）的 DEM 做成结构化网格；把高于阈值的建筑多边形挤成
      棱柱合并成**一个** ``PolyData``，逐栋高程做标量用于高度渐变着色。
边界：只读场景数据；不挑相机、不画航迹、不做统计、不写文件。
为什么是 VTK 而不是 Plotly：Plotly 3D 的相机距离被内部归一化，掠视/俯视角下必然钻进楼群；
      VTK 有真实深度缓冲与可控相机，才能出"整片研究区 + 楼群遮挡关系正确"的论文图。
竖向放大：``z_scale`` 直接作用在几何 z 上（相机与几何同一坐标系），放大倍数写进图注，
      只作阅读约定，不改数据口径。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pyvista as pv

__all__ = ["BUILDING_CMAP", "TERRAIN_CMAP", "buildings_mesh", "terrain_mesh"]

BUILDING_CMAP = ["#dfe4eb", "#b4c0d0", "#8796ac", "#54637b"]


def terrain_mesh(scenario, bounds: Sequence[float], *, step_m: float = 40.0,
                 z_scale: float = 1.0) -> pv.StructuredGrid:
    """研究区地形：按 ``step_m`` 采 DEM，做成结构化网格。

    高程先过一遍 3×3 均值：40 m 采样的 DEM 有高频噪声，直接光照会在图上留下"云斑"，
    平滑一档后只剩真实起伏（坡向、台地），与"竖向放大"叠加也不会失真。
    """
    x0, y0, x1, y1 = (float(v) for v in bounds)
    gx = np.arange(x0, x1 + step_m, step_m)
    gy = np.arange(y0, y1 + step_m, step_m)
    mx, my = np.meshgrid(gx, gy, indexing="xy")
    z = np.asarray(scenario.ground_height(mx.ravel(), my.ravel()), dtype=float).reshape(mx.shape)
    z = _smooth(z)
    grid = pv.StructuredGrid(mx, my, z)
    grid["elev"] = np.asarray(grid.points[:, 2], dtype=float)
    if z_scale != 1.0:
        grid.points[:, 2] *= float(z_scale)
    return grid


def _smooth(z: np.ndarray, passes: int = 2) -> np.ndarray:
    """3×3 均值滤波（边界复制），压掉采样噪声又不削掉真实坡度。"""
    out = np.asarray(z, dtype=float).copy()
    for _ in range(passes):
        p = np.pad(out, 1, mode="edge")
        out = (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
               + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
               + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9.0
    return out


def buildings_mesh(scenario, *, min_height_m: float = 30.0,
                   z_scale: float = 1.0) -> pv.PolyData | None:
    """高于 ``min_height_m`` 的建筑挤成棱柱，合并成一个 PolyData；逐面标量 = 该栋楼顶高程。"""
    if scenario.buildings is None:
        return None
    pts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    tops: list[float] = []
    owner: list[int] = []
    for poly, h in zip(scenario.buildings.polygons, scenario.buildings.heights):
        if float(h) < float(min_height_m):
            continue
        ring = np.asarray(poly.exterior.coords, dtype=float)[:-1]
        if ring.shape[0] < 3:
            continue
        base = float(scenario.ground_height_at(float(poly.centroid.x), float(poly.centroid.y)))
        top = base + float(h)
        n = ring.shape[0]
        b = len(pts)
        pts.extend((float(x), float(y), base) for x, y in ring)
        pts.extend((float(x), float(y), top) for x, y in ring)
        bld = len(tops)
        for a in range(n):  # 侧墙：四条边（quad）
            nxt = (a + 1) % n
            faces.append((4, b + a, b + nxt, b + n + nxt, b + n + a))
            owner.append(bld)
        faces.append((n, *range(b + n, b + 2 * n)))  # 屋顶（凸多边形单面）
        owner.append(bld)
        tops.append(top)
    if not faces:
        return None

    arr = np.asarray(pts, dtype=float)
    if z_scale != 1.0:
        arr[:, 2] *= float(z_scale)
    flat = [int(v) for f in faces for v in f]
    mesh = pv.PolyData(arr, faces=np.asarray(flat, dtype=np.int64))
    mesh.cell_data["top"] = np.asarray(tops, dtype=float)[np.asarray(owner, dtype=int)]
    # 屋顶是 n 边面：凹多边形的扇形三角化会漏面（图上"楼顶缺一块"），交给 vtkTriangleFilter
    # 做正确的耳切三角化；quad 侧墙被一分为二不影响外观，标量随单元一起带过去。
    return mesh.triangulate()
